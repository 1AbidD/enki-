import re
import shlex
import subprocess
import datetime
from pathlib import Path
from typing import Tuple, List, Optional, Callable

from enki.config import get_data_dir
from enki.core.sanitizer import wrap_untrusted_content
from enki.core.prompt_assembler import truncate_tool_output

# Karakter rantai dan injeksi shell yang dilarang keras
CHAINING_CHARS_REGEX = re.compile(r'[\&\|\;\<\>\^\%\$\`\r\n]')

def log_tool_audit(action: str, command: str, status: str, detail: str = ""):
    """Mencatat aksi tool ke data/tool_audit.log."""
    try:
        audit_file = get_data_dir() / "tool_audit.log"
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{now_str}] {action.upper()} | status={status} | cmd='{command}' | {detail}\n"
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass

def is_command_allowed(command_str: str) -> Tuple[bool, str, List[str]]:
    """Memvalidasi perintah shell terhadap allowlist ketat dan menolak karakter rantai."""
    if not command_str or not command_str.strip():
        return False, "Perintah shell tidak boleh kosong.", []

    cmd_clean = command_str.strip()

    # 1. Tolak karakter rantai berbahaya (&, |, ;, <, >, ^, %, $, `, newline)
    if CHAINING_CHARS_REGEX.search(cmd_clean):
        return False, "[Akses Ditolak] Perintah mengandung karakter rantai berbahaya (&, |, ;, <, >, ^, %, $, `, newline).", []

    # 2. Parse dengan shlex
    try:
        raw_tokens = shlex.split(cmd_clean, posix=False)
    except Exception as e:
        return False, f"[Akses Ditolak] Gagal mem-parse argumen shell: {e}", []

    if not raw_tokens:
        return False, "Daftar token perintah kosong.", []

    tokens = [t.strip('"\'') for t in raw_tokens]
    base_prog = tokens[0].lower()

    # 3. Allowlist Check
    # A. git status / log / diff
    if base_prog == "git":
        if len(tokens) < 2:
            return False, "[Akses Ditolak] Perintah git membutuhkan sub-perintah (status, log, diff).", []
        subcmd = tokens[1].lower()
        if subcmd not in ["status", "log", "diff"]:
            return False, f"[Akses Ditolak] Sub-perintah git '{subcmd}' tidak diizinkan. Hanya status, log, diff.", []
        # Cegah opsi git -c atau bypass konfigurasi
        return True, "Perintah git diizinkan.", raw_tokens

    # B. python --version / python -m unittest ...
    elif base_prog in ["python", "python3", "py"]:
        if len(tokens) == 2 and tokens[1].lower() == "--version":
            return True, "Perintah python --version diizinkan.", raw_tokens
        if len(tokens) >= 3 and tokens[1].lower() == "-m" and tokens[2].lower() == "unittest":
            return True, "Perintah python -m unittest diizinkan.", raw_tokens
        return False, "[Akses Ditolak] Perintah python hanya diizinkan untuk '--version' dan '-m unittest'.", []

    # C. dir
    elif base_prog == "dir":
        # Jalankan dir lewat cmd.exe /c dir tanpa shell=True
        exec_tokens = ["cmd.exe", "/c", "dir"] + raw_tokens[1:]
        return True, "Perintah dir diizinkan.", exec_tokens

    return False, f"[Akses Ditolak] Perintah '{base_prog}' tidak terdaftar dalam allowlist yang diizinkan.", []

def default_cli_confirm(command_str: str) -> bool:
    """Konfirmasi manual interaktif di CLI dengan default [y/N] (Enter = tolak)."""
    print("\n" + "!" * 60)
    print("[KONFIRMASI EKSEKUSI SHELL]")
    print(f"Perintah: {command_str}")
    print("!" * 60)
    try:
        ans = input("Jalankan perintah ini? [y/N]: ").strip().lower()
        return ans in ["y", "yes"]
    except (KeyboardInterrupt, EOFError):
        return False

def execute_shell_command(
    command_str: str,
    workspace_root: Path,
    confirm_func: Optional[Callable[[str], bool]] = None,
    timeout_sec: int = 30
) -> str:
    """Mengeksekusi perintah shell yang ter-allowlist dengan konfirmasi manual dan tanpa shell=True."""
    ok, msg, exec_tokens = is_command_allowed(command_str)
    if not ok:
        log_tool_audit("run_shell_command", command_str, "BLOCKED", msg)
        return f"[Tool Error: run_shell_command] {msg}"

    # Konfirmasi manual wajib (default [y/N])
    confirmer = confirm_func if confirm_func is not None else default_cli_confirm
    confirmed = confirmer(command_str)
    if not confirmed:
        log_tool_audit("run_shell_command", command_str, "REJECTED_USER", "Dibatalkan oleh pengguna.")
        return "[Tool Result: run_shell_command] Eksekusi perintah dibatalkan oleh pengguna."

    # Eksekusi dengan subprocess tanpa shell=True
    try:
        proc = subprocess.run(
            exec_tokens,
            shell=False,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )
        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        combined_output = stdout
        if stderr:
            combined_output += f"\n[stderr]\n{stderr}"
        if not combined_output:
            combined_output = "(Perintah selesai tanpa output teks)"

        log_tool_audit("run_shell_command", command_str, "SUCCESS", f"ExitCode={proc.returncode}")

        truncated = truncate_tool_output(combined_output, max_tokens=1000)
        return wrap_untrusted_content(
            truncated,
            tag_name="untrusted_external_content",
            source=f"run_shell_command:{exec_tokens[0]}"
        )
    except subprocess.TimeoutExpired:
        log_tool_audit("run_shell_command", command_str, "TIMEOUT", f"Timeout={timeout_sec}s")
        return f"[Tool Error: run_shell_command] Perintah melebihi batas waktu ({timeout_sec} detik)."
    except Exception as e:
        log_tool_audit("run_shell_command", command_str, "ERROR", str(e))
        return f"[Tool Error: run_shell_command] Gagal mengeksekusi: {e}"
