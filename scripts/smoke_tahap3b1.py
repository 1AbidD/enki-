#!/usr/bin/env python3
"""Smoke test Tahap 3B-1 dengan model real Ollama (qwen2.5:3b).
Menguji 5 pesan obrolan natural dengan pemunculan petunjuk perintah eksplisit CLI
dan supresi petunjuk berulang dalam satu sesi.
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
from enki.core.react_loop import ReActCoordinator
from enki.cli.command_hints import get_command_hints

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
    log("SMOKE TEST TAHAP 3B-1 (PETUNJUK PERINTAH - MODEL REAL qwen2.5:3b)")
    log("=" * 80)
    log(f"Waktu UTC        : {start_utc}")
    log(f"Git Head         : {git_head}")
    log(f"Git Dirty        : {git_dirty}")
    log(f"Ollama PS        :\n{ollama_ps}")
    log(f"Workspace Root   : {WORKSPACE_ROOT}")
    log(f"Model            : qwen2.5:3b")
    log("=" * 80)
    log()

    # Direktori data sementara
    tmp_dir = tempfile.mkdtemp(prefix="enki_smoke_tahap3b1_")
    log(f"[ISOLASI DATA] Menggunakan direktori sementara: {tmp_dir}")
    log("[ISOLASI DATA] profile.json dan sessions.db asli dijamin tidak tersentuh.")
    log()

    timeouts = 0
    crashes = 0
    hints_logged = []

    chat_messages = [
        "Tolong baca file README.md untuk saya",
        "cek git status dong",
        "jalankan unit test proyek ini",
        "halo apa kabar",
        "Tolong baca file README.md untuk saya"
    ]

    try:
        db_path = Path(tmp_dir) / "sessions.db"
        memory_dir = Path(tmp_dir) / "memory"
        db = MemoryDatabase(db_path=db_path)
        profile_mgr = ProfileManager(memory_dir=memory_dir)
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

        history = []
        seen_hint_commands = set()

        log("=" * 80)
        log("EKSEKUSI 5 PESAN OBROLAN DENGAN DETEKSI PETUNJUK PERINTAH")
        log("=" * 80)

        for i, user_msg in enumerate(chat_messages, 1):
            log(f"\n--- [PESAN #{i}]: {user_msg} ---")
            t0 = time.time()
            try:
                chunks = []
                metrics = {}
                for chunk, m in coordinator.run_react_turn(
                    user_input=user_msg,
                    session_id=session_id,
                    history=history,
                    model="qwen2.5:3b",
                    recall_content=None,
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

                # Deteksi dan cetak petunjuk perintah SETELAH balasan model
                hints = get_command_hints(user_msg, seen_commands=seen_hint_commands, max_hints=2)
                if hints:
                    log("[Baris Petunjuk]:")
                    for cmd_key, hint_line in hints:
                        log(hint_line)
                        seen_hint_commands.add(cmd_key)
                        hints_logged.append((i, cmd_key, hint_line))
                else:
                    log("[Baris Petunjuk]: (Tidak ada petunjuk baru)")

                db.add_turn(session_id, "user", user_msg)
                db.add_turn(session_id, "assistant", ans_text)
                history.append({"role": "user", "content": user_msg})
                history.append({"role": "assistant", "content": ans_text})

            except subprocess.TimeoutExpired:
                timeouts += 1
                log(f"[TIMEOUT]: Giliran #{i} melebihi batas waktu.")
            except Exception as e:
                crashes += 1
                log(f"[CRASH]: {e}")

        log()
        log("=" * 80)
        log("RINGKASAN METRIK SMOKE TAHAP 3B-1")
        log("=" * 80)
        log(f"Total Pesan Chat          : {len(chat_messages)}")
        log(f"Total Petunjuk Muncul     : {len(hints_logged)}")
        for step, cmd_key, hint_line in hints_logged:
            log(f"  - Pesan #{step} [{cmd_key}]: {hint_line}")
        log(f"Pesan #5 (/baca diulang)  : {'0 petunjuk kedua (BENAR - tersupresi)' if not any(s == 5 for s, _, _ in hints_logged) else 'GAGAL - petunjuk muncul lagi'}")
        log(f"Jumlah Timeout            : {timeouts}")
        log(f"Jumlah Crash              : {crashes}")
        log("=" * 80)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    evidence_path = WORKSPACE_ROOT / "docs" / "evidence" / "tahap3b1_smoke.txt"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines) + "\n")
    print(f"\n[SELESAI] Output disimpan di {evidence_path}")

if __name__ == "__main__":
    main()
