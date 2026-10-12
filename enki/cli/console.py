import sys
import time
import subprocess
from pathlib import Path
from typing import List, Dict

from enki.config import AppConfig
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT, estimate_tokens_local
from enki.core.ollama_client import OllamaClient
from enki.memory.database import MemoryDatabase
from enki.memory.profile_manager import ProfileManager
from enki.memory.reflector import ReflectorDaemon
from enki.memory.recall import is_recall_triggered, build_fts_query, format_recall_block, execute_passive_recall
from enki.core.react_loop import ReActCoordinator
from enki.cli.memory_commands import handle_memory_command, handle_reflect_command
from enki.cli.explicit_commands import handle_explicit_command, EXPLICIT_COMMANDS

def get_vram_info() -> str:
    """Mengambil status VRAM saat ini via nvidia-smi."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,memory.free", "--format=csv,noheader,nounits"],
            text=True
        ).strip()
        used, total, free = [int(x.strip()) for x in out.split(",")]
        return f"{used} MiB / {total} MiB (Free: {free} MiB)"
    except Exception:
        return "Tidak tersedia (nvidia-smi error)"

class EnkiConsole:
    def __init__(self):
        self.config = AppConfig()
        self.client = OllamaClient(base_url=self.config.ollama_url)
        self.db = MemoryDatabase()
        self.session_id = self.db.create_session()
        self.profile_mgr = ProfileManager()
        self.reflector = ReflectorDaemon()
        self.assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile=self.profile_mgr.get_profile_text())
        self.coordinator = ReActCoordinator(
            client=self.client,
            assembler=self.assembler,
            workspace_root=self.config.workspace_root,
            db=self.db
        )
        self.history: List[Dict[str, str]] = []

    def check_startup_reflection(self):
        """Memeriksa apakah ada sesi lampau yang belum direfleksi dan menampilkan status usulan pending."""
        pending_list = self.reflector.get_pending_proposals()
        print(f"[Status Memori] Antrean usulan pending saat ini: {len(pending_list)} usulan.")

        unreflected = [s for s in self.db.get_unreflected_sessions() if s != self.session_id]
        if unreflected:
            try:
                ans = input(f"[Startup] Ada {len(unreflected)} sesi lampau belum direfleksi. Reflect {len(unreflected)} sesi sekarang? [Y/n]: ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                return
            if ans in ["y", "yes", ""]:
                print(f"[Startup] Menjalankan refleksi untuk {len(unreflected)} sesi...")
                total_new = 0
                for sid in unreflected:
                    props = self.reflector.reflect_session(sid, self.db, self.client, model=self.config.model)
                    total_new += len(props)
                print(f"[Startup] Refleksi selesai. Total {total_new} usulan baru masuk antrean (/memory pending).\n")
            else:
                print("[Startup] Refleksi dilewati. Anda dapat menjalankan /reflect kapan saja.\n")
        else:
            print()

    def print_banner(self):
        print("=" * 60)
        print("[ENKI] Local AI Personal Assistant (v0.2.0 - Memory Enabled)")
        print(f"Model: {self.config.model} | Context: {self.config.num_ctx} | Mute: {self.config.is_muted}")
        print(f"Sesi Aktif: {self.session_id}")
        print(f"VRAM: {get_vram_info()}")
        print("Ketik pesan Anda untuk mulai, atau /help untuk melihat perintah.")
        print("=" * 60)

    def print_help(self):
        print("\n--- Perintah Tersedia ---")
        print("  /baca <path>     : Baca isi file dokumen/kode di dalam workspace")
        print("  /dir [path]      : Lihat daftar file dan folder di dalam workspace")
        print("  /git <aksi>      : Jalankan aksi git aman ('status', 'log', 'diff')")
        print("  /test            : Jalankan unit test sistem Python (konfirmasi [y/N])")
        print("  /versi           : Periksa versi Python sistem (konfirmasi [y/N])")
        print("  /ingat <kunci>   : Cari catatan/riwayat percakapan masa lalu di memori")
        print("  /simpan <k> | <v>: Simpan fakta diri permanen ke profil pengguna")
        print("  /memory <subcmd> : Manajemen memori (view, pending, approve, reject, edit, delete, log, stats)")
        print("  /reflect         : Jalankan refleksi atas sesi yang belum direfleksi")
        print("  /coder           : Beralih ke model coding (qwen2.5-coder:3b, mode chat-only)")
        print("  /chat            : Beralih ke model percakapan utama (qwen2.5:3b)")
        print("  /mute            : Aktifkan mode senyap (0% beban audio)")
        print("  /unmute          : Nonaktifkan mode senyap")
        print("  /status          : Tampilkan status model, context limit, mute, dan VRAM")
        print("  /clear           : Bersihkan riwayat obrolan sesi aktif")
        print("  /exit            : Keluar dari aplikasi")
        print("\n[Catatan Mode Obrolan & Perintah (Tahap 3B)]:")
        print("  Kalimat bahasa natural biasa tidak akan menjalankan alat (tool); model dipanggil tanpa tool.")
        print("  Gunakan perintah eksplisit (/baca, /dir, /git, dll.) jika ingin membaca berkas atau menjalankan tool.")
        print("  Mengingat fakta dari sesi lampau mengandalkan profil permanen (profile.json via /simpan)")
        print("  dan pencarian aktif (active recall dengan kata pemicu: 'kemarin', 'ingat', 'pernah').\n")

    def run(self):
        self.print_banner()

        if not self.client.is_ready():
            print("\n[PERINGATAN] Server Ollama tidak terdeteksi aktif di " + self.config.ollama_url)
            print("Pastikan daemon Ollama sudah berjalan (misal: 'ollama serve').\n")
        else:
            self.check_startup_reflection()

        while True:
            try:
                user_input = input("\nUser > ").lstrip("\ufeff").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nKeluar dari ENKI. Sampai jumpa!")
                break

            if not user_input:
                continue

            # Command handling
            if user_input.startswith("/"):
                parts = user_input.split()
                cmd = parts[0].lower()
                if cmd in ["/exit", "/quit"]:
                    print("Keluar dari ENKI. Sampai jumpa!")
                    break
                elif cmd == "/help":
                    self.print_help()
                    continue
                elif cmd == "/coder":
                    if self.config.model == "qwen2.5-coder:3b":
                        print("[Model Switcher] Model sudah aktif pada qwen2.5-coder:3b.")
                    else:
                        print("[Model Switcher] Memuat qwen2.5-coder:3b ke GPU...")
                        t0 = time.time()
                        self.config.set_model("qwen2.5-coder:3b")
                        try:
                            self.client.chat(model="qwen2.5-coder:3b", messages=[{"role": "user", "content": "ping"}], num_ctx=self.config.num_ctx)
                        except Exception as e:
                            print(f"[Model Switcher Peringatan] Gagal memuat model: {e}")
                        dur = round(time.time() - t0, 3)
                        print(f"[Model Switcher] Aktif: qwen2.5-coder:3b (Waktu swap: {dur}s). VRAM: {get_vram_info()}")
                    continue
                elif cmd == "/chat":
                    if self.config.model == "qwen2.5:3b":
                        print("[Model Switcher] Model sudah aktif pada qwen2.5:3b.")
                    else:
                        print("[Model Switcher] Memuat qwen2.5:3b ke GPU...")
                        t0 = time.time()
                        self.config.set_model("qwen2.5:3b")
                        try:
                            self.client.chat(model="qwen2.5:3b", messages=[{"role": "user", "content": "ping"}], num_ctx=self.config.num_ctx)
                        except Exception as e:
                            print(f"[Model Switcher Peringatan] Gagal memuat model: {e}")
                        dur = round(time.time() - t0, 3)
                        print(f"[Model Switcher] Aktif: qwen2.5:3b (Waktu swap: {dur}s). VRAM: {get_vram_info()}")
                    continue
                elif cmd == "/mute":
                    self.config.set_mute(True)
                    print("[Config] Mode senyap AKTIF (/mute). TTS dilewati 100%.")
                    continue
                elif cmd == "/unmute":
                    self.config.set_mute(False)
                    print("[Config] Mode senyap NONAKTIF (/unmute).")
                    continue
                elif cmd == "/status":
                    facts_count = len(self.profile_mgr.get_all_facts())
                    pending_count = len(self.reflector.get_pending_proposals())
                    print(f"\n--- Status Sistem ENKI ---")
                    model_display = self.config.model + (" (Chat-Only)" if "coder" in self.config.model else "")
                    print(f"  Model        : {model_display}")
                    print(f"  Num Context  : {self.config.num_ctx}")
                    print(f"  Temperature  : {self.config.temperature}")
                    print(f"  Mute Status  : {self.config.is_muted}")
                    print(f"  Sesi Aktif   : {self.session_id}")
                    print(f"  Fakta Profil : {facts_count} fakta ({self.profile_mgr.count_tokens()}/500 token)")
                    print(f"  Pending Usul : {pending_count} usulan")
                    print(f"  VRAM GPU     : {get_vram_info()}")
                    print(f"  History Turn : {len(self.history)} pesan\n")
                    continue
                elif cmd == "/clear":
                    self.history.clear()
                    print("[Sistem] Riwayat sesi obrolan dibersihkan.")
                    continue
                elif cmd == "/memory":
                    handle_memory_command(
                        args=parts[1:],
                        profile_mgr=self.profile_mgr,
                        reflector=self.reflector,
                        db=self.db,
                        client=self.client,
                        model=self.config.model
                    )
                    # Refresh profile di assembler setelah perintah /memory
                    self.assembler.profile = self.profile_mgr.get_profile_text()
                    continue
                elif cmd == "/reflect":
                    if self.config.model != "qwen2.5:3b":
                        print("[Reflect] Menjalankan refleksi menggunakan model percakapan (qwen2.5:3b)...")
                    handle_reflect_command(
                        db=self.db,
                        reflector=self.reflector,
                        client=self.client,
                        model="qwen2.5:3b"
                    )
                    continue
                elif cmd in EXPLICIT_COMMANDS:
                    raw_args = user_input[len(parts[0]):].strip()
                    handle_explicit_command(
                        cmd=cmd,
                        raw_args=raw_args,
                        workspace_root=self.config.workspace_root,
                        db=self.db,
                        session_id=self.session_id,
                        profile_mgr=self.profile_mgr
                    )
                    if cmd == "/simpan":
                        self.assembler.profile = self.profile_mgr.get_profile_text()
                    continue
                else:
                    print(f"Perintah tidak dikenal: {user_input}. Ketik /help untuk bantuan.")
                    continue

            # 1. Catat turn user ke DB secara deterministik
            self.db.add_turn(self.session_id, "user", user_input)

            # 2. Cek Recall: Aktif (Trigger Leksikal) vs Pasif (Ambang Batas Substantif)
            recall_content = None
            if is_recall_triggered(user_input):
                fts_query = build_fts_query(user_input)
                if fts_query:
                    snippets = self.db.search_fts(fts_query, exclude_session_id=self.session_id, limit=3)
                    if snippets:
                        recall_content = format_recall_block(snippets, max_tokens=300)
                        print(f"[Memory Recall Aktif] Menemukan {len(snippets)} cuplikan masa lalu yang relevan.")
            elif self.config.passive_recall:
                passive_block = execute_passive_recall(user_input, self.db, self.session_id, max_tokens=300)
                if passive_block:
                    recall_content = passive_block
                    print(f"[Memory Recall Pasif] Menyuntikkan konteks latar percakapan lama.")

            # 3. Assembler Prompt & Pre-flight check
            self.assembler.profile = self.profile_mgr.get_profile_text()
            _, meta = self.assembler.assemble_messages(
                user_input=user_input,
                history=self.history,
                recall_content=recall_content,
                num_ctx=self.config.num_ctx
            )

            est_tokens = meta["estimated_prompt_tokens"]
            if est_tokens > self.config.num_ctx * 0.95:
                print(f"[Peringatan] Estimasi token ({est_tokens}) mendekati batas num_ctx ({self.config.num_ctx}).")

            # 4. Inference Call via ReAct Loop
            def trace_cb(evt: str, msg: str):
                if evt == "tool_call":
                    print(f"\n[Tool call] {msg}")
                elif evt == "tool_result":
                    print(f"[Tool result] {msg}")
                elif evt in ["loop_guard", "timeout", "max_iterations"]:
                    print(f"\n[{evt.upper()}] {msg}")

            print("\n[Jawaban]")
            content_chunks = []
            final_metrics = {}
            try:
                for chunk, metrics in self.coordinator.run_react_turn(
                    user_input=user_input,
                    session_id=self.session_id,
                    history=self.history,
                    model=self.config.model,
                    recall_content=recall_content,
                    num_ctx=self.config.num_ctx,
                    temperature=self.config.temperature,
                    trace_callback=trace_cb,
                    enable_tools=False
                ):
                    if chunk:
                        print(chunk, end="", flush=True)
                        content_chunks.append(chunk)
                    if metrics:
                        final_metrics = metrics
                print()
            except Exception as e:
                print(f"\n[Error Inferensi] {e}")
                continue

            content = "".join(content_chunks).strip()

            # 5. Catat turn assistant ke DB secara deterministik
            self.db.add_turn(self.session_id, "assistant", content)

            # 6. Telemetri Eksekusi & Anggaran Context Riil
            prompt_eval = final_metrics.get("prompt_eval_count", 0)
            eval_count = final_metrics.get("eval_count", 0)
            speed = final_metrics.get("eval_tok_per_sec", 0.0)
            remaining_dyn = max(0, self.config.num_ctx - (prompt_eval + eval_count))

            print(f"\n-- Telemetri: Prompt: {prompt_eval} tok | Generate: {eval_count} tok ({speed} tok/s) | Sisa Context: {remaining_dyn}/{self.config.num_ctx} tok --")

            # Update history memori sesi aktif
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": content})
