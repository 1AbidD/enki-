import sys
import subprocess
from typing import List, Dict
from enki.config import AppConfig
from enki.core.prompt_assembler import PromptAssembler, estimate_tokens_local
from enki.core.ollama_client import OllamaClient

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
        self.assembler = PromptAssembler()
        self.history: List[Dict[str, str]] = []

    def print_banner(self):
        print("=" * 60)
        print("[ENKI] Local AI Personal Assistant (v0.1.0)")
        print(f"Model: {self.config.model} | Context: {self.config.num_ctx} | Mute: {self.config.is_muted}")
        print(f"VRAM: {get_vram_info()}")
        print("Ketik pesan Anda untuk mulai, atau /help untuk melihat perintah.")
        print("=" * 60)

    def print_help(self):
        print("\n--- Perintah Tersedia ---")
        print("  /mute    : Aktifkan mode senyap (0% beban audio)")
        print("  /unmute  : Nonaktifkan mode senyap")
        print("  /status  : Tampilkan status model, context limit, mute, dan VRAM")
        print("  /clear   : Bersihkan riwayat obrolan sesi aktif")
        print("  /exit    : Keluar dari aplikasi\n")

    def run(self):
        self.print_banner()

        if not self.client.is_ready():
            print("\n[PERINGATAN] Server Ollama tidak terdeteksi aktif di " + self.config.ollama_url)
            print("Pastikan daemon Ollama sudah berjalan (misal: 'ollama serve').\n")

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
                cmd = user_input.lower().split()[0]
                if cmd in ["/exit", "/quit"]:
                    print("Keluar dari ENKI. Sampai jumpa!")
                    break
                elif cmd == "/help":
                    self.print_help()
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
                    print(f"\n--- Status Sistem ENKI ---")
                    print(f"  Model       : {self.config.model}")
                    print(f"  Num Context : {self.config.num_ctx}")
                    print(f"  Temperature : {self.config.temperature}")
                    print(f"  Mute Status : {self.config.is_muted}")
                    print(f"  VRAM GPU    : {get_vram_info()}")
                    print(f"  History Turn: {len(self.history)} pesan\n")
                    continue
                elif cmd == "/clear":
                    self.history.clear()
                    print("[Sistem] Riwayat sesi obrolan dibersihkan.")
                    continue
                else:
                    print(f"Perintah tidak dikenal: {user_input}. Ketik /help untuk bantuan.")
                    continue

            # Pre-flight token estimation check
            messages, meta = self.assembler.assemble_messages(
                user_input=user_input,
                history=self.history,
                num_ctx=self.config.num_ctx
            )
            est_tokens = meta["estimated_prompt_tokens"]
            if est_tokens > self.config.num_ctx * 0.95:
                print(f"[Peringatan] Estimasi token ({est_tokens}) mendekati batas num_ctx ({self.config.num_ctx}).")

            # Inference Call dengan Progressive Streaming
            print("\n[Jawaban]")
            content_chunks = []
            final_metrics = {}
            try:
                for token, metrics in self.client.chat_stream(
                    model=self.config.model,
                    messages=messages,
                    num_ctx=self.config.num_ctx,
                    temperature=self.config.temperature
                ):
                    if token:
                        print(token, end="", flush=True)
                        content_chunks.append(token)
                    if metrics:
                        final_metrics = metrics
                print()
            except Exception as e:
                print(f"\n[Error Inferensi] {e}")
                continue

            content = "".join(content_chunks).strip()

            # Format Trace Tool Call (jika ada)
            tool_calls = final_metrics.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    print(f"\n[Tool call] {fn.get('name')}({fn.get('arguments')})")

            # Telemetri Eksekusi & Anggaran Context Riil
            prompt_eval = final_metrics.get("prompt_eval_count", 0)
            eval_count = final_metrics.get("eval_count", 0)
            speed = final_metrics.get("eval_tok_per_sec", 0.0)
            remaining_dyn = max(0, self.config.num_ctx - (prompt_eval + eval_count))

            print(f"\n-- Telemetri: Prompt: {prompt_eval} tok | Generate: {eval_count} tok ({speed} tok/s) | Sisa Context: {remaining_dyn}/{self.config.num_ctx} tok --")

            # Update history
            self.history.append({"role": "user", "content": user_input})
            self.history.append({"role": "assistant", "content": content})
