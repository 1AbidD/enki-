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
from pathlib import Path
from typing import Optional, Dict, Any, List
import psutil

def evaluate_benchmark_turn(
    expected_tool: Optional[str],
    expected_arg: Optional[str],
    expected_key: Optional[str],
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
            is_success = True
            if expected_key:
                if expected_key.lower() in final_text.lower():
                    is_fidelity_ok = True
                else:
                    is_fidelity_ok = False
            else:
                is_fidelity_ok = True
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
                    is_success = True
                    if expected_key:
                        if expected_key.lower() in final_text.lower():
                            is_fidelity_ok = True
                        else:
                            is_fidelity_ok = False
                    else:
                        is_fidelity_ok = True
            else:
                is_success = True
                if expected_key:
                    if expected_key.lower() in final_text.lower():
                        is_fidelity_ok = True
                    else:
                        is_fidelity_ok = False
                else:
                    is_fidelity_ok = True

    routing_ok = (not is_false_call) and (not is_wrong_tool)
    valid_args = not is_invalid_arg

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
