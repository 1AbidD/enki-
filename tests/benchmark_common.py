"""
Modul utilitas bersama untuk benchmark harness ReAct (Set 2, Set 3, Set 4):
- Evaluator turn dengan aturan mutlak: Timeout = Gagal.
- BenchmarkLock dengan deteksi stale PID dan penolakan PID aktif.
- Klasifikasi kelompok prompt MEM (active trigger, marker, none).
"""

import os
import json
import time
import datetime
import re
import subprocess
import hashlib
import winreg
from pathlib import Path
from typing import Optional, Dict, Any, List
import psutil

def check_fidelity_match(expected_key: Any, final_text: str) -> bool:
    """Memeriksa apakah nilai kunci jawaban yang diharapkan muncul di balasan akhir.
    Mendukung string tunggal, string pemisah '|', serta tuple/list/set alternatif.
    """
    if not expected_key:
        return True
    if not final_text:
        return False
    final_lower = final_text.lower()
    if isinstance(expected_key, (list, tuple, set)):
        return any(str(k).strip().lower() in final_lower for k in expected_key)
    if "|" in str(expected_key):
        return any(k.strip().lower() in final_lower for k in str(expected_key).split("|"))
    return str(expected_key).strip().lower() in final_lower

def evaluate_benchmark_turn(
    expected_tool: Optional[str],
    expected_arg: Optional[str],
    expected_key: Optional[Any],
    tool_calls_recorded: List[str],
    tool_observations_recorded: List[str],
    final_text: str,
    is_timeout: bool = False,
    timeout_reason: Optional[str] = None
) -> Dict[str, Any]:
    """Mengevaluasi hasil 1 turn benchmark ReAct.
    ATURAN MUTLAK:
    Jika is_timeout == True, maka is_success, routing_ok, dan fidelity_ok WAJIB False.
    """
    first_tool_call = None
    first_tool_arg = None
    if tool_calls_recorded:
        raw_call = tool_calls_recorded[0]
        paren_idx = raw_call.find("(")
        if paren_idx != -1:
            first_tool_call = raw_call[:paren_idx].strip()
            first_tool_arg = raw_call[paren_idx + 1 : -1].strip()

    is_false_call = False
    is_wrong_tool = False
    is_invalid_arg = False
    is_fidelity_ok = False
    is_success = False
    sandbox_status = "NONE"

    obs_all = " ".join(tool_observations_recorded)
    if "[Kebijakan Ditolak]" in obs_all or "[Akses Ditolak]" in obs_all:
        sandbox_status = "BLOCKED"

    if expected_tool is None:
        # Prompt Negatif atau MEM (tanpa tool eksternal)
        if first_tool_call is not None:
            is_false_call = True
            if sandbox_status != "BLOCKED":
                sandbox_status = "ATTEMPTED"
        else:
            is_fidelity_ok = check_fidelity_match(expected_key, final_text)
    else:
        # Prompt Positif (mengharapkan tool)
        if first_tool_call is None:
            is_wrong_tool = True
        elif first_tool_call != expected_tool:
            is_wrong_tool = True
        else:
            if expected_arg:
                if expected_arg.lower() not in (first_tool_arg or "").lower():
                    is_invalid_arg = True
                else:
                    is_fidelity_ok = check_fidelity_match(expected_key, final_text)
            else:
                is_fidelity_ok = check_fidelity_match(expected_key, final_text)

    routing_ok = (not is_false_call) and (not is_wrong_tool)
    valid_args = not is_invalid_arg
    is_success = bool(routing_ok and valid_args and is_fidelity_ok and not is_timeout)

    # OVERRIDE MUTLAK: Timeout = GAGAL
    if is_timeout:
        is_success = False
        routing_ok = False
        is_fidelity_ok = False

    return {
        "routing_ok": routing_ok,
        "valid_args": valid_args,
        "fidelity_ok": is_fidelity_ok,
        "sandbox_status": sandbox_status,
        "is_false_call": is_false_call,
        "is_wrong_tool": is_wrong_tool,
        "is_invalid_arg": is_invalid_arg,
        "is_success": is_success,
        "is_timeout": is_timeout,
        "timeout_reason": timeout_reason
    }

class BenchmarkLock:
    """Kunci file agar dua benchmark tidak bisa dijalankan bersamaan.
    Mendukung deteksi PID mati (stale lock) untuk diambil alih,
    dan menolak jika PID masih hidup.
    """
    def __init__(self, lock_file: Optional[Path] = None):
        if lock_file is not None:
            self.lock_file = lock_file
        else:
            self.lock_file = Path(__file__).resolve().parent / ".benchmark_lock"

    def __enter__(self):
        if self.lock_file.exists():
            try:
                content = self.lock_file.read_text(encoding="utf-8").strip()
                lock_data = json.loads(content)
                pid = lock_data.get("pid")
                t_str = lock_data.get("time_str", "waktu tidak diketahui")

                is_alive = False
                if pid and isinstance(pid, int):
                    try:
                        is_alive = psutil.pid_exists(pid)
                    except Exception:
                        is_alive = False

                if is_alive:
                    raise RuntimeError(
                        f"\n[BENCHMARK LOCK ERROR] Benchmark terkunci oleh proses PID={pid} yang masih berjalan (dimulai pada {t_str}).\n"
                        f"File lock: {self.lock_file}\n"
                        f"Eksekusi dibatalkan untuk mencegah tabrakan/konkurensi."
                    )
                else:
                    print(f"\n[BENCHMARK LOCK] Mengambil alih lock basi (stale lock) dari PID={pid} (proses sudah mati, dimulai pada {t_str}).")
            except json.JSONDecodeError:
                print(f"\n[BENCHMARK LOCK] Mengambil alih lock dengan konten rusak di {self.lock_file}.")

        lock_info = {
            "pid": os.getpid(),
            "timestamp": time.time(),
            "time_str": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }
        self.lock_file.write_text(json.dumps(lock_info, indent=2), encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.lock_file.exists():
            try:
                self.lock_file.unlink()
            except Exception:
                pass

MEM_ACTIVE_TRIGGER_REGEX = re.compile(
    r'\b(kemarin|pernah|ingat|sebelumnya|tadi|waktu\s+itu|remember|earlier)\b',
    re.IGNORECASE
)
MEM_CONTEXT_MARKER_REGEX = re.compile(
    r'\b(kita|kami|dulu|tim\s+kita|proyek\s+kita|proyekku|task-?ku|repo-?ku)\b',
    re.IGNORECASE
)

def classify_mem_prompt(prompt: str) -> str:
    """Mengklasifikasikan prompt MEM menjadi:
    - 'has_active_trigger': memiliki kata pemicu aktif waktu (kemarin, pernah, dsb)
    - 'has_marker': memiliki penanda konteks berbagi (kita, kami, dsb) tanpa pemicu aktif
    - 'none': tanpa pemicu aktif dan tanpa penanda konteks
    """
    if MEM_ACTIVE_TRIGGER_REGEX.search(prompt):
        return "has_active_trigger"
    elif MEM_CONTEXT_MARKER_REGEX.search(prompt):
        return "has_marker"
    else:
        return "none"

def get_ollama_registry_env() -> Dict[str, str]:
    """Mengambil konfigurasi OLLAMA_* dari Windows Registry (HKCU:\\Environment)."""
    env_vars = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            idx = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(key, idx)
                    if name.upper().startswith("OLLAMA_"):
                        env_vars[name] = str(val)
                    idx += 1
                except OSError:
                    break
    except Exception as e:
        env_vars["error"] = str(e)
    return env_vars

def create_benchmark_jsonl_header(
    runner_num_ctx: int = 8192,
    user_prompts_file: Optional[Path] = None
) -> Dict[str, Any]:
    r"""Menghasilkan metadata header untuk baris pertama file output JSONL benchmark:
    - ollama --version
    - output ollama ps (diambil saat header dibuat, setelah warm-up model)
    - nilai OLLAMA_* dari Windows registry (HKCU:\Environment)
    - runner_num_ctx: batas konteks yang dikirim oleh runner
    - git HEAD commit hash
    """
    if isinstance(runner_num_ctx, Path):
        user_prompts_file = runner_num_ctx
        runner_num_ctx = 8192
    ollama_ver = "unknown"
    try:
        res = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=5)
        ollama_ver = res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        ollama_ver = f"error: {e}"

    ollama_ps_out = "unknown"
    try:
        res = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
        ollama_ps_out = res.stdout.strip()
    except Exception as e:
        ollama_ps_out = f"error: {e}"

    git_head = "unknown"
    git_dirty = False
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5)
        git_head = res.stdout.strip()
        st_res = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5)
        if st_res.returncode == 0:
            st_lines = [l.strip() for l in st_res.stdout.strip().splitlines() if l.strip()]
            git_dirty = (len(st_lines) > 0)
    except Exception as e:
        git_head = f"error: {e}"
        git_dirty = None

    prompts_sha256 = None
    if user_prompts_file and user_prompts_file.exists():
        try:
            content = user_prompts_file.read_bytes()
            prompts_sha256 = hashlib.sha256(content).hexdigest()
        except Exception as e:
            prompts_sha256 = f"error: {e}"

    return {
        "record_type": "benchmark_header",
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "ollama_version": ollama_ver,
        "ollama_ps": ollama_ps_out,
        "ollama_registry_env": get_ollama_registry_env(),
        "runner_num_ctx": runner_num_ctx,
        "git_head": git_head,
        "git_dirty": git_dirty,
        "set4_user_prompts_sha256": prompts_sha256
    }

def check_working_tree_clean_for_benchmark(allowed_file_suffix: str = "set4_user_prompts.txt") -> None:
    """Memeriksa apakah working tree git bersih sebelum benchmark Set 4 dijalankan.
    Menolak berjalan jika working tree kotor (dirty) selain file prompt pengguna.
    """
    proc = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5)
    if proc.returncode == 0:
        lines = [l.strip() for l in proc.stdout.strip().splitlines() if l.strip()]
        disallowed_dirty = []
        for line in lines:
            parts = line.split(maxsplit=1)
            f_path = parts[1] if len(parts) > 1 else line
            if not f_path.endswith(allowed_file_suffix):
                disallowed_dirty.append(line)
        if disallowed_dirty:
            raise RuntimeError(
                f"Working tree git sedang kotor (dirty)! File belum bersih: {disallowed_dirty}. "
                f"Runner Set 4 menolak berjalan."
            )

def check_ollama_idle(client: Any) -> None:
    """Memeriksa kesiapan server Ollama dan status model aktif di VRAM."""
    if hasattr(client, "is_ready") and not client.is_ready():
        raise RuntimeError("Server Ollama tidak aktif di http://127.0.0.1:11434!")
    try:
        proc = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            lines = [l.strip() for l in proc.stdout.strip().splitlines() if l.strip()]
            print("\n[PREFLIGHT CHECK] Status 'ollama ps':")
            if len(lines) <= 1:
                print("  Ollama VRAM: Idle / Kosong (tidak ada model yang sedang berjalan).")
            else:
                for line in lines:
                    print(f"  {line}")
        else:
            print("[PREFLIGHT CHECK] Gagal menjalankan 'ollama ps' (returncode non-zero).")
    except Exception as e:
        print(f"[PREFLIGHT CHECK] Tidak dapat menjalankan 'ollama ps': {e}")

