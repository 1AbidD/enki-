#!/usr/bin/env python3
"""Smoke test Tahap 3B dengan model real Ollama (qwen2.5:3b).
Menjalankan 6 perintah eksplisit dan 6 pesan obrolan biasa pada direktori data sementara.
"""

import sys
import os
import time
import datetime
import tempfile
import subprocess
import shutil
from pathlib import Path

# Add project root to sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from enki.config import AppConfig
from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.memory.database import MemoryDatabase
from enki.memory.profile_manager import ProfileManager
from enki.memory.reflector import ReflectorDaemon
from enki.memory.recall import is_recall_triggered, build_fts_query, format_recall_block
from enki.core.react_loop import ReActCoordinator
from enki.cli.explicit_commands import execute_explicit_command, EXPLICIT_COMMANDS

def get_git_info():
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(WORKSPACE_ROOT), text=True).strip()
    except Exception as e:
        head = f"ERROR: {e}"
    try:
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(WORKSPACE_ROOT), text=True).strip()
        dirty = bool(status)
    except Exception as e:
        dirty = f"ERROR: {e}"
    return head, dirty

def get_ollama_ps():
    try:
        out = subprocess.check_output(["ollama", "ps"], text=True).strip()
        return out
    except Exception as e:
        return f"ERROR: {e}"

def main():
    start_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_head, git_dirty = get_git_info()
    ollama_ps = get_ollama_ps()

    output_lines = []
    def log(msg: str = ""):
        output_lines.append(msg)
        print(msg)

    log("=" * 80)
    log("SMOKE TEST TAHAP 3B (MODEL REAL OLLAMA qwen2.5:3b)")
    log("=" * 80)
    log(f"Waktu UTC        : {start_utc}")
    log(f"Git Head         : {git_head}")
    log(f"Git Dirty        : {git_dirty}")
    log(f"Ollama PS        :\n{ollama_ps}")
    log(f"Workspace Root   : {WORKSPACE_ROOT}")
    log(f"Model            : qwen2.5:3b")
    log("=" * 80)
    log()

    # Pastikan direktori data sementara (JANGAN menyentuh data asli!)
    tmp_dir = tempfile.mkdtemp(prefix="enki_smoke_tahap3b_")
    log(f"[ISOLASI DATA] Menggunakan direktori sementara: {tmp_dir}")
    log("[ISOLASI DATA] profile.json dan sessions.db asli dijamin tidak tersentuh.")
    log()

    timeouts = 0
    crashes = 0
    confirmations_logged = []

    def smoke_confirm(prompt: str) -> bool:
        record = f"[CONFIRM AUTO-APPROVED]: {prompt}"
        confirmations_logged.append(record)
        return True

    try:
        db_path = Path(tmp_dir) / "sessions.db"
        memory_dir = Path(tmp_dir) / "memory"
        db = MemoryDatabase(db_path=db_path)
        profile_mgr = ProfileManager(memory_dir=memory_dir)
        reflector = ReflectorDaemon(proposals_file=memory_dir / "pending_facts.json")

        # Seed data sesi lampau ke DB sementara agar pencarian memori & recall menghasilkan data
        seed_sess = db.create_session()
        db.add_turn(seed_sess, "user", "Kemarin kita bicara arsitektur sistem bahwa SQLite adalah database lokal yang cepat.")
        db.add_turn(seed_sess, "assistant", "Betul, SQLite memakai mode WAL dan FTS5 untuk pencarian kata.")
        db.add_turn(seed_sess, "user", "Tolong ingat satu kata kunci rahasia: project_alpha.")
        db.add_turn(seed_sess, "assistant", "Siap, kata kunci project_alpha dicatat.")

        session_id = db.create_session()
        client = OllamaClient(base_url="http://127.0.0.1:11434")
        assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile=profile_mgr.get_profile_text())
        coordinator = ReActCoordinator(
            client=client,
            assembler=assembler,
            workspace_root=WORKSPACE_ROOT,
            db=db,
            turn_timeout_sec=60
        )

        # -------------------------------------------------------------
        # 1. Enam Perintah Eksplisit
        # -------------------------------------------------------------
        explicit_cmds = [
            ("/baca", "README.md"),
            ("/dir", ""),
            ("/git", "status"),
            ("/test", ""),
            ("/versi", ""),
            ("/ingat", "kata")
        ]

        log("=" * 80)
        log("BAGIAN 1: EKSEKUSI 6 PERINTAH EKSPLISIT")
        log("=" * 80)

        for cmd, raw_args in explicit_cmds:
            full_display = f"{cmd} {raw_args}".strip()
            log(f"\n--- [PERINTAH]: {full_display} ---")
            t0 = time.time()
            try:
                out = execute_explicit_command(
                    cmd=cmd,
                    raw_args=raw_args,
                    workspace_root=WORKSPACE_ROOT,
                    db=db,
                    session_id=session_id,
                    profile_mgr=profile_mgr,
                    confirm_func=smoke_confirm,
                    audit_log_path=Path(tmp_dir) / "audit.log"
                )
                duration = time.time() - t0
                log(f"[Durasi: {duration:.2f}s]")
                log("[Keluaran Mentah]:")
                log(out)
            except Exception as e:
                crashes += 1
                log(f"[CRASH]: {e}")

        log()
        log("=" * 80)
        log("BAGIAN 2: EKSEKUSI 6 PESAN OBROLAN BIASA (ENABLE_TOOLS=FALSE)")
        log("=" * 80)

        chat_messages = [
            "Halo apa kabar, siapa namamu?",
            "Apakah kamu ingat apa yang kita bahas sebelumnya?",
            "Kemarin kita sempat bicara tentang arsitektur sistem, pernah dengar SQLite?",
            "Tolong baca file README.md untuk saya",
            "Coba lihat dan baca dokumen README.md",
            "Jelaskan secara singkat apa fungsi git status dalam pengembangan software"
        ]

        history = []
        for i, user_msg in enumerate(chat_messages, 1):
            log(f"\n--- [CHAT #{i}]: {user_msg} ---")
            t0 = time.time()
            try:
                # Recall aktif check
                recall_content = None
                if is_recall_triggered(user_msg):
                    fts_query = build_fts_query(user_msg)
                    if fts_query:
                        snippets = db.search_fts(fts_query, exclude_session_id=session_id, limit=3)
                        if snippets:
                            recall_content = format_recall_block(snippets)
                            log(f"[Recall Aktif Terpicu]: query='{fts_query}', cuplikan={len(snippets)}")

                chunks = []
                metrics = {}
                for chunk, m in coordinator.run_react_turn(
                    user_input=user_msg,
                    session_id=session_id,
                    history=history,
                    model="qwen2.5:3b",
                    recall_content=recall_content,
                    num_ctx=8192,
                    temperature=0.0,
                    enable_tools=False
                ):
                    if chunk:
                        chunks.append(chunk)
                    if m:
                        metrics = m

                duration = time.time() - t0
                ans_text = "".join(chunks).strip()
                log(f"[Durasi: {duration:.2f}s | Prompt: {metrics.get('prompt_eval_count', 0)} tok | Eval: {metrics.get('eval_count', 0)} tok]")
                log("[Jawaban Model]:")
                log(ans_text)

                db.add_turn(session_id, "user", user_msg)
                db.add_turn(session_id, "assistant", ans_text)
                history.append({"role": "user", "content": user_msg})
                history.append({"role": "assistant", "content": ans_text})

            except subprocess.TimeoutExpired:
                timeouts += 1
                log(f"[TIMEOUT]: Giliran chat #{i} melebihi batas waktu.")
            except Exception as e:
                crashes += 1
                log(f"[CRASH]: {e}")

        log()
        log("=" * 80)
        log("BAGIAN 3: RINGKASAN METRIK SMOKE")
        log("=" * 80)
        log(f"Total Perintah Eksplisit : {len(explicit_cmds)}")
        log(f"Total Pesan Chat         : {len(chat_messages)}")
        log(f"Konfirmasi Auto-Approved : {len(confirmations_logged)}")
        for c in confirmations_logged:
            log(f"  - {c}")
        log(f"Jumlah Timeout           : {timeouts}")
        log(f"Jumlah Crash             : {crashes}")
        log("=" * 80)

    finally:
        # Bersihkan direktori sementara
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Simpan hasil ke docs/evidence/tahap3b_smoke.txt
    evidence_path = WORKSPACE_ROOT / "docs" / "evidence" / "tahap3b_smoke.txt"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")
    print(f"\n[SELESAI] Output disimpan di {evidence_path}")

if __name__ == "__main__":
    main()
