#!/usr/bin/env python3
"""Skrip Smoke Test Live untuk Tahap 6 Web Tools (Kriteria W9).

Menjalankan:
1. /wiki Python (dengan konfirmasi 'y')
2. /wiki Indonesia (dengan konfirmasi 'y')
3. /wiki Linux (dengan konfirmasi 'y')
4. /web https://example.com Apa isi halaman ini? (dengan konfirmasi 'y')
5. /exit

Merekam seluruh output mentah ke docs/evidence/tahap6_live.txt.
"""

import datetime
import io
import os
import subprocess
import sys
import time
from pathlib import Path

# Add project root to sys.path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from enki.cli.console import EnkiConsole


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


class DualOutput:
    def __init__(self, original_stdout, file_handle):
        self.original_stdout = original_stdout
        self.file_handle = file_handle

    def write(self, s):
        self.original_stdout.write(s)
        self.original_stdout.flush()
        self.file_handle.write(s)
        self.file_handle.flush()

    def flush(self):
        self.original_stdout.flush()
        self.file_handle.flush()


def main():
    start_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_head, git_dirty = get_git_info()
    ollama_ps = get_ollama_ps()

    evidence_path = WORKSPACE_ROOT / "docs" / "evidence" / "tahap6_live.txt"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    with open(evidence_path, "w", encoding="utf-8") as f_out:
        dual = DualOutput(sys.stdout, f_out)
        orig_stdout = sys.stdout
        orig_stderr = sys.stderr
        sys.stdout = dual
        sys.stderr = dual

        try:
            print("=" * 80)
            print("SMOKE TEST LIVE TAHAP 6 — WEB TOOLS (/wiki, /web) (KRITERIA W9)")
            print("=" * 80)
            print(f"Timestamp Mulai (UTC): {start_utc}")
            print(f"Git HEAD             : {git_head}")
            print(f"Git Dirty            : {git_dirty}")
            print(f"Ollama Active Model  :\n{ollama_ps}")
            print("=" * 80)
            print()

            console = EnkiConsole()
            # Set mode senyap agar audio tidak memblokir di headless runner
            console.config.set_mute(True)
            # Lewati startup reflection agar tidak meminta konfirmasi refleksi lama
            console.check_startup_reflection = lambda: None

            # Antrekan input perintah dan konfirmasinya
            commands = [
                ("/wiki Python", "y"),
                ("/wiki Indonesia", "y"),
                ("/wiki Linux", "y"),
                ("/web https://example.com Apa isi halaman ini?", "y"),
            ]

            for cmd_str, conf_str in commands:
                console.submit_mid_reply_input(cmd_str)
                console.submit_mid_reply_input(conf_str)

            console.submit_mid_reply_input("/exit")

            print("[Memulai Siklus Interaksi Konsol Live]")
            console.run()

            end_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
            print()
            print("=" * 80)
            print(f"Timestamp Selesai (UTC): {end_utc}")
            print("STATUS: SMOKE TEST LIVE TAHAP 6 SELESAI")
            print("=" * 80)

        finally:
            sys.stdout = orig_stdout
            sys.stderr = orig_stderr

    print(f"\n[Selesai] Bukti empiris berhasil ditulis ke: {evidence_path}")


if __name__ == "__main__":
    main()
