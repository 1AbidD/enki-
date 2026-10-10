import os
import subprocess
import datetime
from pathlib import Path
from typing import Tuple, List, Optional, Callable, Dict, Any

from enki.config import get_data_dir
from enki.core.sanitizer import wrap_untrusted_content
from enki.core.prompt_assembler import truncate_tool_output
from enki.tools.tool_types import ToolExecutionResult, ToolResultStatus

def log_tool_audit(action: str, command: str, status: str, detail: str = "", audit_log_path: Optional[Path] = None):
    """Mencatat aksi tool ke file audit log (mendukung injeksi path atau ENKI_DATA_DIR)."""
    try:
        if audit_log_path:
            audit_file = Path(audit_log_path)
        else:
            audit_file = get_data_dir() / "tool_audit.log"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{now_str}] {action.upper()} | status={status} | cmd='{command}' | {detail}\n"
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass

def default_cli_confirm(command_str: str) -> bool:
    """Konfirmasi manual interaktif di CLI dengan default [y/N] (Enter = tolak)."""
    print("\n" + "!" * 60)
    print("[KONFIRMASI EKSEKUSI]")
    print(f"Perintah: {command_str}")
    print("!" * 60)
    try:
        ans = input("Jalankan perintah ini? [y/N]: ").strip().lower()
        return ans in ["y", "yes"]
    except (KeyboardInterrupt, EOFError):
        return False

# -------------------------------------------------------------
# Tool 1: git(action: enum["status", "log", "diff"])
# -------------------------------------------------------------
GIT_VALID_ACTIONS = {"status": ["git", "status"], "log": ["git", "log", "-n", "5"], "diff": ["git", "diff"]}

def execute_git_command(
    action: str,
    workspace_root: Path,
    confirm_func: Optional[Callable[[str], bool]] = None,
    audit_log_path: Optional[Path] = None,
    timeout_sec: int = 30
) -> ToolExecutionResult:
    """Mengeksekusi perintah git dengan argumen tetap.
    Returns: ToolExecutionResult
    """
    clean_act = (action or "").strip().lower()
    if clean_act not in GIT_VALID_ACTIONS:
        log_tool_audit("git", f"git {action}", "BLOCKED", f"Aksi '{action}' di luar enum [status, log, diff]", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] Aksi git '{action}' tidak valid. Hanya diizinkan: 'status', 'log', 'diff'.")

    exec_argv = GIT_VALID_ACTIONS[clean_act]
    cmd_display = " ".join(exec_argv)

    confirmer = confirm_func if confirm_func is not None else default_cli_confirm
    if not confirmer(cmd_display):
        log_tool_audit("git", cmd_display, "REJECTED_USER", "Dibatalkan oleh pengguna.", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] Eksekusi '{cmd_display}' dibatalkan oleh pengguna.")

    try:
        proc = subprocess.run(
            exec_argv,
            shell=False,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )
        combined = proc.stdout.strip()
        if proc.stderr.strip():
            combined += f"\n[stderr]\n{proc.stderr.strip()}"
        if not combined:
            combined = "(Perintah git selesai tanpa output teks)"

        log_tool_audit("git", cmd_display, "SUCCESS", f"ExitCode={proc.returncode}", audit_log_path=audit_log_path)
        truncated = truncate_tool_output(combined, max_tokens=1000)
        return ToolExecutionResult(ToolResultStatus.OK, wrap_untrusted_content(truncated, tag_name="untrusted_external_content", source=cmd_display))
    except subprocess.TimeoutExpired:
        log_tool_audit("git", cmd_display, "TIMEOUT", f"Timeout={timeout_sec}s", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.ERROR, f"[Tool Error: git] Perintah '{cmd_display}' melebihi batas waktu ({timeout_sec}s).")
    except Exception as e:
        log_tool_audit("git", cmd_display, "ERROR", str(e), audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.ERROR, f"[Tool Error: git] Gagal mengeksekusi: {e}")

# -------------------------------------------------------------
# Tool 2: python_version()
# -------------------------------------------------------------
def execute_python_version(
    workspace_root: Path,
    confirm_func: Optional[Callable[[str], bool]] = None,
    audit_log_path: Optional[Path] = None,
    timeout_sec: int = 15
) -> ToolExecutionResult:
    """Mengeksekusi python --version dari daftar argumen tetap.
    Returns: ToolExecutionResult
    """
    cmd_display = "python --version"
    exec_argv = ["python", "--version"]

    confirmer = confirm_func if confirm_func is not None else default_cli_confirm
    if not confirmer(cmd_display):
        log_tool_audit("python_version", cmd_display, "REJECTED_USER", "Dibatalkan oleh pengguna.", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] Eksekusi '{cmd_display}' dibatalkan oleh pengguna.")

    try:
        proc = subprocess.run(
            exec_argv,
            shell=False,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )
        combined = (proc.stdout.strip() + " " + proc.stderr.strip()).strip()
        log_tool_audit("python_version", cmd_display, "SUCCESS", f"ExitCode={proc.returncode}", audit_log_path=audit_log_path)
        truncated = truncate_tool_output(combined, max_tokens=500)
        return ToolExecutionResult(ToolResultStatus.OK, wrap_untrusted_content(truncated, tag_name="untrusted_external_content", source=cmd_display))
    except Exception as e:
        log_tool_audit("python_version", cmd_display, "ERROR", str(e), audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.ERROR, f"[Tool Error: python_version] Gagal mengeksekusi: {e}")

# -------------------------------------------------------------
# Tool 3: run_unittests()
# -------------------------------------------------------------
def execute_run_unittests(
    workspace_root: Path,
    confirm_func: Optional[Callable[[str], bool]] = None,
    audit_log_path: Optional[Path] = None,
    timeout_sec: int = 60
) -> ToolExecutionResult:
    """Mengeksekusi python -m unittest dari daftar argumen tetap.
    Returns: ToolExecutionResult
    """
    start_dir = "tests" if (Path(workspace_root) / "tests").is_dir() else "."
    cmd_display = f"python -m unittest discover {start_dir}"
    exec_argv = ["python", "-m", "unittest", "discover", start_dir]

    confirmer = confirm_func if confirm_func is not None else default_cli_confirm
    if not confirmer(cmd_display):
        log_tool_audit("run_unittests", cmd_display, "REJECTED_USER", "Dibatalkan oleh pengguna.", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] Eksekusi '{cmd_display}' dibatalkan oleh pengguna.")

    try:
        proc = subprocess.run(
            exec_argv,
            shell=False,
            cwd=str(workspace_root),
            capture_output=True,
            text=True,
            timeout=timeout_sec
        )
        combined = proc.stdout.strip()
        if proc.stderr.strip():
            combined += f"\n[stderr]\n{proc.stderr.strip()}"
        log_tool_audit("run_unittests", cmd_display, "SUCCESS", f"ExitCode={proc.returncode}", audit_log_path=audit_log_path)
        truncated = truncate_tool_output(combined, max_tokens=1000)
        return ToolExecutionResult(ToolResultStatus.OK, wrap_untrusted_content(truncated, tag_name="untrusted_external_content", source=cmd_display))
    except subprocess.TimeoutExpired:
        log_tool_audit("run_unittests", cmd_display, "TIMEOUT", f"Timeout={timeout_sec}s", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.ERROR, f"[Tool Error: run_unittests] Perintah melebihi batas waktu ({timeout_sec}s).")
    except Exception as e:
        log_tool_audit("run_unittests", cmd_display, "ERROR", str(e), audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.ERROR, f"[Tool Error: run_unittests] Gagal mengeksekusi: {e}")

# -------------------------------------------------------------
# Tool 4: list_dir(path: Optional[str])
# -------------------------------------------------------------
def execute_list_dir(
    rel_path: Optional[str],
    workspace_root: Path,
    audit_log_path: Optional[Path] = None
) -> ToolExecutionResult:
    """Melihat daftar file dan folder di dalam workspace tanpa subprocess.
    Returns: ToolExecutionResult
    """
    clean_path = (rel_path or ".").strip()
    root_resolved = Path(workspace_root).resolve()

    # Cegah UNC path
    if clean_path.startswith(r"\\") or clean_path.startswith("//"):
        log_tool_audit("list_dir", clean_path, "BLOCKED", "UNC path dilarang", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] UNC path tidak diizinkan: {clean_path}")

    target = (root_resolved / clean_path).resolve()
    try:
        real_target = Path(os.path.realpath(target))
    except Exception as e:
        log_tool_audit("list_dir", clean_path, "ERROR", str(e), audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] Gagal memverifikasi path: {e}")

    # Validasi path traversal
    try:
        if not target.is_relative_to(root_resolved) or not real_target.is_relative_to(root_resolved):
            log_tool_audit("list_dir", clean_path, "BLOCKED", "Traversal di luar workspace", audit_log_path=audit_log_path)
            return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] Path berada di luar direktori kerja: {clean_path}")
    except Exception:
        return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] Path berada di luar direktori kerja: {clean_path}")

    if not target.exists():
        log_tool_audit("list_dir", clean_path, "NOT_FOUND", "Direktori tidak ditemukan", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.INVALID_ARG, f"[Validasi Gagal] Direktori '{clean_path}' tidak ditemukan.")

    if not target.is_dir():
        log_tool_audit("list_dir", clean_path, "NOT_DIR", "Bukan direktori", audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.INVALID_ARG, f"[Validasi Gagal] Path '{clean_path}' adalah file, bukan direktori.")

    try:
        entries = []
        for item in sorted(target.iterdir()):
            name = item.name
            if name.startswith("."):
                continue  # Sembunyikan hidden file/folder (.git, dll)
            if item.is_dir():
                entries.append(f"[DIR]  {name}/")
            else:
                entries.append(f"[FILE] {name} ({item.stat().st_size} bytes)")

        listing_str = "\n".join(entries) if entries else "(Direktori kosong)"
        log_tool_audit("list_dir", clean_path, "SUCCESS", f"{len(entries)} item", audit_log_path=audit_log_path)
        truncated = truncate_tool_output(listing_str, max_tokens=1000)
        return ToolExecutionResult(ToolResultStatus.OK, wrap_untrusted_content(truncated, tag_name="untrusted_external_content", source=f"list_dir:{clean_path}"))
    except Exception as e:
        log_tool_audit("list_dir", clean_path, "ERROR", str(e), audit_log_path=audit_log_path)
        return ToolExecutionResult(ToolResultStatus.ERROR, f"[Tool Error: list_dir] Gagal membaca direktori: {e}")
