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
from typing import Optional, Dict, Any, List, Tuple
import psutil

BOUNDARY_TOKENS = {",", ";", ".", ":", "namun", "tetapi", "tapi"}
NEGATION_WORDS = {"tidak", "tak", "tanpa", "bukan", "belum", "0"}

def find_key_occurrences(key: str, text: str) -> List[Tuple[str, bool]]:
    r"""Mencari kemunculan kata kunci dengan batas kata (\b) dan menentukan apakah kemunculan tersebut dinegasi.
    Aturan Polaritas:
    - Jendela pencarian sebenarnya: 4 token mendahului kata kunci (spesifikasi awal 3 token tidak cocok
      dengan contoh bahasa Indonesia nyata seperti "tidak ada tes yang lulus" di mana kata negasi "tidak"
      berada 4 token sebelum "lulus").
    - Tokenisasi menyimpan tanda baca (, ; . :).
    - Negasi dibatasi klausa: pemindaian mundur terhenti jika menemukan tanda baca pembatas (, ; . :)
      atau kata hubung perlawanan (namun|tetapi|tapi).
    """
    if not key or not text:
        return []
    pattern = r'\b' + re.escape(key.lower()) + r'\b'
    res = []
    text_lower = text.lower()
    for m in re.finditer(pattern, text_lower):
        prefix = text_lower[:m.start()]
        tokens = [tm.group(0) for tm in re.finditer(r'\b[\w\./\-]+\b|[,\.;:]', prefix)]
        window = tokens[-4:] if len(tokens) >= 4 else tokens
        is_negated = False
        for tok in reversed(window):
            if tok in BOUNDARY_TOKENS:
                break
            if tok in NEGATION_WORDS:
                is_negated = True
                break
        res.append((m.group(0), is_negated))
    return res

def check_test_fidelity(final_text: str, variant: str = "passing") -> bool:
    """Mengevaluasi fidelity khusus untuk hasil uji unit (TEST) sadar polaritas:
    - Varian lulus:
      Any of 'lulus|berhasil|sukses' yang TIDAK dinegasi, ATAU
      Any of 'gagal|kegagalan|error' yang DINEGASI (misal: 'tidak ada kegagalan', '0 error').
    - Varian gagal:
      Any of 'gagal|kegagalan|fail|failed|error' yang TIDAK dinegasi, ATAU
      nama tes gagal unik 'test_deliberate_telemetry_fault' (tidak dinegasi).
    """
    if not final_text:
        return False
    if variant == "passing":
        pos_keys = ["lulus", "berhasil", "sukses"]
        neg_keys = ["gagal", "kegagalan", "error"]
        for k in pos_keys:
            if any(not is_neg for _, is_neg in find_key_occurrences(k, final_text)):
                return True
        for k in neg_keys:
            if any(is_neg for _, is_neg in find_key_occurrences(k, final_text)):
                return True
        return False
    elif variant == "failing":
        pos_keys = ["gagal", "kegagalan", "fail", "failed", "error", "test_deliberate_telemetry_fault"]
        for k in pos_keys:
            if any(not is_neg for _, is_neg in find_key_occurrences(k, final_text)):
                return True
        return False
    return False

def check_fidelity_match(
    expected_key: Any,
    final_text: str,
    check_polarity: bool = True,
    test_variant: Optional[str] = None
) -> bool:
    """Memeriksa apakah nilai kunci jawaban yang diharapkan muncul di balasan akhir.
    Aturan Ketat:
    1. Word-boundary (\b) dan case-insensitive.
    2. Panjang kunci wajib > 3 karakter (kunci <= 3 huruf seperti 'ok', 'pass' ditolak).
    3. Sadar polaritas: Jendela 4 token mendahului dengan penghenti klausa (, ; . : namun|tetapi|tapi).
    """
    if not expected_key and not test_variant:
        return True
    if not final_text:
        return False

    if test_variant in ("passing", "failing"):
        return check_test_fidelity(final_text, variant=test_variant)

    keys = []
    if isinstance(expected_key, (list, tuple, set)):
        keys = [str(k).strip() for k in expected_key if str(k).strip()]
    elif "|" in str(expected_key):
        keys = [k.strip() for k in str(expected_key).split("|") if k.strip()]
    else:
        keys = [str(expected_key).strip()]

    # Deteksi jika expected_key merupakan set sinonim test passing/failing
    if set(keys) == {"lulus", "berhasil", "sukses"}:
        return check_test_fidelity(final_text, variant="passing")
    if "test_deliberate_telemetry_fault" in keys:
        return check_test_fidelity(final_text, variant="failing")

    for key_str in keys:
        key_clean = key_str.lower()
        if len(key_clean) <= 3:
            # Ditolak sesuai aturan: tidak ada kunci <= 3 huruf
            continue

        occs = find_key_occurrences(key_clean, final_text)
        if occs:
            if check_polarity:
                if any(not is_neg for _, is_neg in occs):
                    return True
            else:
                return True

    return False

def evaluate_benchmark_turn(
    expected_tool: Optional[str],
    expected_arg: Optional[str],
    expected_key: Optional[Any],
    tool_calls_recorded: List[str],
    tool_observations_recorded: List[str],
    final_text: str,
    is_timeout: bool = False,
    timeout_reason: Optional[str] = None,
    fixture_manifest: Optional[Dict[str, Any]] = None,
    fixture_variant: Optional[str] = None,
    gating_closed: bool = False
) -> Dict[str, Any]:
    """Mengevaluasi hasil 1 turn benchmark ReAct.
    ATURAN MUTLAK:
    Jika is_timeout == True, maka is_success, routing_ok, dan fidelity_ok WAJIB False.
    Jika gating_closed == True, routing_ok WAJIB False dan dicatat sebagai gating_closed.
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
    action_mismatch = False
    sandbox_status = "NONE"

    obs_all = " ".join(tool_observations_recorded)
    if "[Kebijakan Ditolak]" in obs_all or "[Akses Ditolak]" in obs_all:
        sandbox_status = "BLOCKED"

    if gating_closed:
        is_wrong_tool = True

    if expected_tool is None:
        # Prompt Negatif atau MEM (tanpa tool eksternal)
        if first_tool_call is not None:
            is_false_call = True
            if sandbox_status != "BLOCKED":
                sandbox_status = "ATTEMPTED"
        else:
            is_fidelity_ok = check_fidelity_match(expected_key, final_text, check_polarity=True)
    elif expected_tool == "git":
        # Routing GIT = tool git (aksi apa pun yang valid di enum)
        if first_tool_call is None:
            is_wrong_tool = True
        elif first_tool_call != "git":
            is_wrong_tool = True
        else:
            # LLM memanggil tool git
            called_action = ""
            raw_arg = (first_tool_arg or "").strip()
            if raw_arg.startswith("{") and raw_arg.endswith("}"):
                try:
                    parsed_json = json.loads(raw_arg)
                    if isinstance(parsed_json, dict):
                        called_action = str(parsed_json.get("action", "")).strip().lower()
                except Exception:
                    pass
            if not called_action:
                cleaned = raw_arg.strip("\"'").lower()
                if cleaned in ("status", "diff", "log"):
                    called_action = cleaned
                else:
                    m = re.search(r'["\']?action["\']?\s*:\s*["\'](status|diff|log)["\']', raw_arg, re.IGNORECASE)
                    if m:
                        called_action = m.group(1).lower()
                    else:
                        called_action = cleaned

            if called_action not in ("status", "diff", "log"):
                is_invalid_arg = True
            else:
                if expected_arg and called_action != expected_arg.strip().lower():
                    action_mismatch = True

                # Fidelity dinilai terhadap aksi yang BENAR-BENAR dipanggil
                actual_key = expected_key
                if fixture_manifest and "git" in fixture_manifest and called_action in fixture_manifest["git"]:
                    actual_key = fixture_manifest["git"][called_action].get("key", expected_key)

                is_fidelity_ok = check_fidelity_match(actual_key, final_text, check_polarity=True)
    elif expected_tool == "run_unittests":
        # Prompt run_unittests (sadar polaritas passing vs failing)
        if first_tool_call is None:
            is_wrong_tool = True
        elif first_tool_call != "run_unittests":
            is_wrong_tool = True
        else:
            variant = fixture_variant or ("failing" if (expected_key and ("failing" in str(expected_key).lower() or "gagal" in str(expected_key).lower())) else "passing")
            is_fidelity_ok = check_test_fidelity(final_text, variant=variant)
    else:
        # Prompt Positif lain (read_file, python_version, list_dir)
        if first_tool_call is None:
            is_wrong_tool = True
        elif first_tool_call != expected_tool:
            is_wrong_tool = True
        else:
            if expected_arg:
                if expected_arg.lower() not in (first_tool_arg or "").lower():
                    is_invalid_arg = True
                else:
                    is_fidelity_ok = check_fidelity_match(expected_key, final_text, check_polarity=True)
            else:
                is_fidelity_ok = check_fidelity_match(expected_key, final_text, check_polarity=True)

    routing_ok = (not is_false_call) and (not is_wrong_tool) and (not gating_closed)
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
        "action_mismatch": action_mismatch,
        "gating_closed": gating_closed,
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

def get_git_describe_tags() -> str:
    """Mengambil output `git describe --tags --always`."""
    try:
        proc = subprocess.run(["git", "describe", "--tags", "--always"], capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"

FREEZE_PATHS = [
    "enki",
    "tests/benchmark_common.py",
    "tests/test_react_benchmark_set4.py"
]

def filter_leaked_manifest_keys(expected_key: Any, prompt_text: str, prompt_id: str = "") -> str:
    """Menyaring kunci manifest yang bocor di teks pertanyaan pengguna (C1):
    Kata yang muncul di pertanyaan pengguna dikeluarkan dari himpunan kunci untuk baris itu.
    Jika himpunan kunci menjadi kosong (semua kunci bocor), fungsi GAGAL KERAS.
    """
    if not expected_key:
        return ""
    if isinstance(expected_key, (list, tuple, set)):
        keys = [str(k).strip() for k in expected_key if str(k).strip()]
    elif "|" in str(expected_key):
        keys = [k.strip() for k in str(expected_key).split("|") if k.strip()]
    else:
        keys = [str(expected_key).strip()]

    valid_keys = []
    for k in keys:
        if not re.search(r'\b' + re.escape(k) + r'\b', prompt_text, re.IGNORECASE):
            valid_keys.append(k)

    if not valid_keys:
        err_msg = (
            f"[PREFLIGHT FIDELITY VIOLATION]: Seluruh kunci manifest '{expected_key}' "
            f"bocor di dalam teks pertanyaan untuk prompt '{prompt_id}': \"{prompt_text}\"! "
            f"Himpunan kunci menjadi kosong. Gagal keras."
        )
        sys.stderr.write(f"\n{err_msg}\n")
        raise RuntimeError(err_msg)

    return "|".join(valid_keys)

def get_freeze_commit_from_lock(lock_file_path: Optional[Path] = None) -> Optional[str]:
    """Membaca FREEZE_COMMIT dari docs/set4_lock.txt."""
    lock_file = lock_file_path or (Path(__file__).resolve().parent.parent / "docs" / "set4_lock.txt")
    if not lock_file.exists():
        return None
    for line in lock_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("FREEZE_COMMIT"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                val = parts[1].strip()
                if val and not val.startswith("[") and not val.startswith("<"):
                    return val
    return None

def check_freeze(repo_root: Optional[Path] = None, lock_file_path: Optional[Path] = None) -> bool:
    """Fungsi check_freeze() sungguhan (D2):
    1. Membaca FREEZE_COMMIT dari docs/set4_lock.txt.
    2. git rev-parse gating-freeze^{commit} harus sama persis dengan FREEZE_COMMIT.
    3. git diff --quiet FREEZE_COMMIT -- <lingkup> exit code 0.
    """
    root = repo_root or Path(__file__).resolve().parent.parent
    freeze_commit = get_freeze_commit_from_lock(lock_file_path)
    if not freeze_commit:
        return False
    try:
        tag_proc = subprocess.run(
            ["git", "rev-parse", "gating-freeze^{commit}"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5
        )
        if tag_proc.returncode != 0:
            return False
        tag_commit = tag_proc.stdout.strip()
        if tag_commit.lower() != freeze_commit.lower():
            return False

        diff_cmd = ["git", "diff", "--quiet", freeze_commit, "--"] + FREEZE_PATHS
        diff_proc = subprocess.run(
            diff_cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5
        )
        return diff_proc.returncode == 0
    except Exception:
        return False

# Alias untuk kompatibilitas
check_gating_freeze_clean = check_freeze
check_gating_freeze_diff_clean = check_freeze

def get_freeze_tag_object_id(repo_root: Optional[Path] = None) -> str:
    """Mengambil object ID tag gating-freeze."""
    root = repo_root or Path(__file__).resolve().parent.parent
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "gating-freeze"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return "unknown"

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
    - git describe tags & status bersih diff gating freeze
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
        "git_describe": get_git_describe_tags(),
        "freeze_commit": get_freeze_commit_from_lock(),
        "freeze_tag_object_id": get_freeze_tag_object_id(),
        "freeze_check_clean": check_freeze(),
        "gating_diff_clean": check_gating_freeze_diff_clean(),
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

