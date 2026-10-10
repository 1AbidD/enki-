import os
import sys
import json
import time
import datetime
import shutil
import tempfile
import unittest
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.core.react_loop import ReActCoordinator
from enki.memory.database import MemoryDatabase
from enki.memory.profile_manager import ProfileManager
from enki.memory.recall import (
    is_recall_triggered,
    build_fts_query,
    format_recall_block
)
from tests.benchmark_common import (
    evaluate_benchmark_turn,
    BenchmarkLock,
    classify_mem_prompt,
    create_benchmark_jsonl_header,
)

def check_ollama_idle(client: OllamaClient):
    """Memeriksa kesiapan server Ollama dan status model aktif di VRAM."""
    if not client.is_ready():
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

USER_PROMPTS_FILE = ROOT_DIR / "tests" / "set4_user_prompts.txt"

# ----------------------------------------------------------------------
# 15 Prompt Buatan Asisten untuk Melengkapi Set 4A (Total 30)
# Gaya bahasa: variasi santai, typo, praktis, tanpa pemicu waktu untuk memori
# ----------------------------------------------------------------------
BUILTIN_PROMPTS_4A = [
    # 6 MEM (Memori pasif - domain baru tanpa kontaminasi kalibrasi & Set 3)
    # 3 prompt dengan marker konteks (kita, proyekku, tim kita), 3 prompt tanpa marker (none)
    # Tidak ada pemicu aktif (kemarin, pernah, ingat, tadi, dsb) atau kata pencarian eksplisit
    {
        "id": "S4A_P16_MEM",
        "category": "MEM",
        "mode": "profile",
        "prompt": "feature flag service aplikasi web kita pakai engine apa?",
        "expected_tool": None,
        "expected_key": "flipt",
        "memory_inject": ("user", "feature flag service aplikasi web kita pakai engine apa?", "assistant", "Feature flag service web kita memakai Flipt open source.")
    },
    {
        "id": "S4A_P17_MEM",
        "category": "MEM",
        "mode": "profile",
        "prompt": "backend distributed tracing transaksi di proyekku dikirim ke software apa?",
        "expected_tool": None,
        "expected_key": "jaeger",
        "memory_inject": ("user", "backend distributed tracing transaksi di proyekku dikirim ke software apa?", "assistant", "Distributed tracing transaksi proyekku dikirim dan divisualisasikan menggunakan Jaeger.")
    },
    {
        "id": "S4A_P18_MEM",
        "category": "MEM",
        "mode": "profile",
        "prompt": "sast tool scanner keamanan kode sumber tim kita pakai apa?",
        "expected_tool": None,
        "expected_key": "semgrep",
        "memory_inject": ("user", "sast tool scanner keamanan kode sumber tim kita pakai apa?", "assistant", "Scanner SAST keamanan kode sumber tim kita menggunakan Semgrep rules.")
    },
    {
        "id": "S4A_P19_MEM",
        "category": "MEM",
        "mode": "profile",
        "prompt": "tool pengujian load testing performa endpoint http apa?",
        "expected_tool": None,
        "expected_key": "k6",
        "memory_inject": ("user", "tool pengujian load testing performa endpoint http apa?", "assistant", "Pengujian load testing performa endpoint HTTP dilakukan memakai k6 script.")
    },
    {
        "id": "S4A_P20_MEM",
        "category": "MEM",
        "mode": "profile",
        "prompt": "migration runner buat update skema database pakai apa?",
        "expected_tool": None,
        "expected_key": "liquibase",
        "memory_inject": ("user", "migration runner buat update skema database pakai apa?", "assistant", "Migration runner otomatis untuk pembaruan skema database memakai Liquibase CLI.")
    },
    {
        "id": "S4A_P21_MEM",
        "category": "MEM",
        "mode": "profile",
        "prompt": "ingress controller routing trafik klaster kubernetes apa?",
        "expected_tool": None,
        "expected_key": "emissary",
        "memory_inject": ("user", "ingress controller routing trafik klaster kubernetes apa?", "assistant", "Ingress controller perutean trafik jaringan klaster Kubernetes menggunakan Emissary.")
    },

    # 2 WORKSPACE (1 FILE / LIST, 1 GIT)
    {
        "id": "S4A_P22_WS",
        "category": "FILE",
        "prompt": "coba intip file README.md, apa judul proyeknya?",
        "expected_tool": "read_file",
        "expected_arg": "README.md",
        "expected_key": "enki"
    },
    {
        "id": "S4A_P23_WS",
        "category": "GIT",
        "prompt": "tolong cek git diff sebentar, ada baris apa yg berubah?",
        "expected_tool": "git",
        "expected_arg": "diff",
        "expected_key": "uncommitted"
    },

    # 7 NEGATIF MURNI (4 Berpemicu Aktif Tanpa Topik Memori, 3 Tanpa Pemicu)
    # 4 Negatif Berpemicu Aktif (topik di luar memori: astronomi, geografi/sejarah, kuliner, zoologi):
    {
        "id": "S4A_P24_NEG",
        "category": "NEG",
        "has_trigger": True,
        "prompt": "kemarin teleskop james webb nemu galaksi tertua apa ya namanya?",
        "expected_tool": None,
        "expected_key": None
    },
    {
        "id": "S4A_P25_NEG",
        "category": "NEG",
        "has_trigger": True,
        "prompt": "kamu ingat siapa pelaut eropa pertama yang berhasil mengitari tanjung harapan afrika?",
        "expected_tool": None,
        "expected_key": None
    },
    {
        "id": "S4A_P26_NEG",
        "category": "NEG",
        "has_trigger": True,
        "prompt": "tadi aku baca sejarah rendang minangkabau, tradisi memasaknya konon dari abad berapa?",
        "expected_tool": None,
        "expected_key": None
    },
    {
        "id": "S4A_P27_NEG",
        "category": "NEG",
        "has_trigger": True,
        "prompt": "kamu ingat berapa rekor kedalaman menyelam maksimal seekor paus sperma di samudra?",
        "expected_tool": None,
        "expected_key": None
    },
    # 3 Negatif Murni Tanpa Pemicu (aritmatika, runtime js, fisika):
    {
        "id": "S4A_P28_NEG",
        "category": "NEG",
        "has_trigger": False,
        "prompt": "hitungin dong 45 dikali 12 ditambah 180 brp hasilnya?",
        "expected_tool": None,
        "expected_key": None
    },
    {
        "id": "S4A_P29_NEG",
        "category": "NEG",
        "has_trigger": False,
        "prompt": "apa itu konsep garbage collection di runtime javascript?",
        "expected_tool": None,
        "expected_key": None
    },
    {
        "id": "S4A_P30_NEG",
        "category": "NEG",
        "has_trigger": False,
        "prompt": "berapa suhu titik leleh logam tembaga murni dalam derajat celsius?",
        "expected_tool": None,
        "expected_key": None
    }
]

# ----------------------------------------------------------------------
# Set 4R: 10 Prompt Regresi dari Set 3 (TIDAK ikut gate)
# ----------------------------------------------------------------------
REGRESSION_PROMPTS_4R = [
    {
        "id": "S4R_P01_REG",
        "origin_id": "S3_P17_NEG",
        "prompt": "kapan bahasa pemrograman C pertama kali diciptakan dan oleh siapa?",
        "expected_tool": None,
        "notes": "Di Set 3 false call history | grep C"
    },
    {
        "id": "S4R_P02_REG",
        "origin_id": "S3_P18_NEG",
        "prompt": "buatkan fungsi python buat balik urutan string (reverse string)",
        "expected_tool": None,
        "notes": "Di Set 3 false call python -c"
    },
    {
        "id": "S4R_P03_REG",
        "origin_id": "S3_P23_NEG",
        "prompt": "bantu bikinin draf email izin sakit untuk bos kantor besok",
        "expected_tool": None,
        "notes": "Di Set 3 false call git status (sampai konfirmasi) dan mail"
    },
    {
        "id": "S4R_P04_REG",
        "origin_id": "S3_P24_NEG",
        "prompt": "gimana tips mengatasi insomnia dan pola tidur yg berantakan?",
        "expected_tool": None,
        "notes": "Di Set 3 timeout HTTP 120s di Run 3"
    },
    {
        "id": "S4R_P05_REG",
        "origin_id": "S3_P01_MEM",
        "prompt": "apa database utama proyek kita ya?",
        "expected_tool": None,
        "expected_key": "sqlite",
        "memory_inject": ("user", "apa database utama proyek kita?", "assistant", "Database utama proyek kita adalah SQLite dengan mode WAL dan FTS5.")
    },
    {
        "id": "S4R_P06_REG",
        "origin_id": "S3_P02_MEM",
        "prompt": "bahasa apa yg kita pake buat backend service pembayaran?",
        "expected_tool": None,
        "expected_key": "fastapi",
        "memory_inject": ("user", "layanan pembayaran pakai apa?", "assistant", "Backend service pembayaran kita dibangun dengan Python FastAPI.")
    },
    {
        "id": "S4R_P07_REG",
        "origin_id": "S3_P06_READ",
        "prompt": "isi file requirements.txt apa aja ya?",
        "expected_tool": "read_file",
        "expected_arg": "requirements.txt",
        "expected_key": "fastapi"
    },
    {
        "id": "S4R_P08_REG",
        "origin_id": "S3_P09_GIT",
        "prompt": "bisa tolong liat branch git kita yg aktif dan status commit terakhir?",
        "expected_tool": "git",
        "expected_arg": "status",
        "expected_key": "feat/enki-core-v2"
    },
    {
        "id": "S4R_P09_REG",
        "origin_id": "S3_P11_TEST",
        "prompt": "tolong jalanin unit test proyek kita dong",
        "expected_tool": "run_unittests",
        "expected_arg": None,
        "expected_key": "ok"
    },
    {
        "id": "S4R_P10_REG",
        "origin_id": "S3_P14_NEG",
        "prompt": "tolong jelaskan arsitektur Transformer dan mekanisme self-attention",
        "expected_tool": None,
        "notes": "Uji no-tool untuk topik ML teoritis"
    }
]

def load_user_prompts(prompts_path: Path) -> List[Dict[str, Any]]:
    """Membaca dan memvalidasi file prompt dari pengguna.
    Wajib minimal 15 baris valid, atau melempar RuntimeError.
    """
    if not prompts_path.exists():
        raise RuntimeError(f"File prompt user tidak ditemukan: {prompts_path}")

    valid_prompts = []
    lines = prompts_path.read_text(encoding="utf-8").splitlines()
    user_idx = 1

    for line in lines:
        line_clean = line.strip()
        if not line_clean or line_clean.startswith("#"):
            continue

        parts = [p.strip() for p in line_clean.split("|")]
        p_type = parts[0].upper()

        if p_type == "MEM":
            if len(parts) < 4:
                continue
            prompt_text = parts[1]
            mem_sentence = parts[2]
            key_val = parts[3]
            
            if len(parts) >= 5 and parts[4].strip().lower() in ("recall", "profile"):
                mode = parts[4].strip().lower()
            else:
                if is_recall_triggered(prompt_text) or any(w in prompt_text.lower() for w in ["riwayat", "sesi lampau", "obrolan lama", "catatan", "pernah kita", "kemarin"]):
                    mode = "recall"
                else:
                    mode = "profile"

            valid_prompts.append({
                "id": f"S4A_U{user_idx:02d}_MEM",
                "category": "MEM",
                "mode": mode,
                "prompt": prompt_text,
                "expected_tool": None,
                "expected_key": key_val,
                "memory_inject": ("user", prompt_text, "assistant", mem_sentence)
            })
            user_idx += 1

        elif p_type in ("FILE", "GIT", "TEST", "NEG"):
            if len(parts) < 2:
                continue
            prompt_text = parts[1]
            exp_tool = None
            exp_arg = None
            if p_type == "FILE":
                exp_tool = "read_file"
                exp_arg = "requirements.txt" if "requirements" in prompt_text.lower() else "README.md"
            elif p_type == "GIT":
                exp_tool = "git"
                exp_arg = "diff" if "diff" in prompt_text.lower() else "status"
            elif p_type == "TEST":
                exp_tool = "run_unittests"

            valid_prompts.append({
                "id": f"S4A_U{user_idx:02d}_{p_type}",
                "category": p_type,
                "prompt": prompt_text,
                "expected_tool": exp_tool,
                "expected_arg": exp_arg,
                "expected_key": None
            })
            user_idx += 1

    if len(valid_prompts) < 15:
        sys.stderr.write(
            f"\n[FATAL RUNNER ERROR]: {prompts_path} hanya berisi {len(valid_prompts)} prompt valid "
            f"(diperlukan minimal 15 baris: 6 MEM, 4 WS, 5 NEG).\n"
            f"Runner Set 4 GAGAL KERAS dan MENOLAK BERJALAN.\n"
        )
        sys.exit(1)

    return valid_prompts

def validate_mem_prompts_preflight(mem_prompts: List[Dict[str, Any]]) -> None:
    """Preflight check keras untuk prompt MEM Set 4:
    1. expected_key wajib muncul dalam fakta memori (memory_inject / profile fact).
    2. expected_key TIDAK BOLEH muncul dalam teks prompt itu sendiri.
    3. Teks prompt wajib memuat kata kunci topik yang ada di kalimat memori.
    Jika melanggar salah satu aturan, benchmark menolak jalan (hard fail).
    """
    for p in mem_prompts:
        pid = p.get("id", "UNKNOWN")
        prompt = p.get("prompt", "")
        key = (p.get("expected_key") or "").strip()
        if not key:
            sys.stderr.write(f"\n[PREFLIGHT VIOLATION]: Prompt MEM '{pid}' tidak memiliki 'expected_key'!\n")
            sys.exit(1)

        mem_sentence = ""
        if p.get("memory_inject"):
            inj = p["memory_inject"]
            mem_sentence = inj[3] if len(inj) > 3 else inj[1]
        elif p.get("memory_fact"):
            mem_sentence = p["memory_fact"]

        # 1. Key wajib muncul di fakta memori
        if key.lower() not in mem_sentence.lower():
            sys.stderr.write(
                f"\n[PREFLIGHT VIOLATION]: Prompt MEM '{pid}': Nilai kunci '{key}' "
                f"TIDAK ditemukan dalam fakta memori: \"{mem_sentence}\"!\n"
            )
            sys.exit(1)

        # 2. Key TIDAK BOLEH muncul di teks prompt
        if re.search(r'\b' + re.escape(key.lower()) + r'\b', prompt.lower()):
            sys.stderr.write(
                f"\n[PREFLIGHT VIOLATION]: Prompt MEM '{pid}': Nilai kunci '{key}' "
                f"BOCOR di dalam teks prompt: \"{prompt}\"!\n"
            )
            sys.exit(1)

        # 3. Kata kunci substantif prompt wajib beririsan dengan kalimat memori
        prompt_words = {w for w in re.findall(r'\b\w+\b', prompt.lower()) if len(w) > 3}
        mem_words = {w for w in re.findall(r'\b\w+\b', mem_sentence.lower()) if len(w) > 3}
        shared_keywords = prompt_words.intersection(mem_words)
        if not shared_keywords:
            sys.stderr.write(
                f"\n[PREFLIGHT VIOLATION]: Prompt MEM '{pid}': Tidak ada kata kunci topik yang beririsan "
                f"antara prompt (\"{prompt}\") dan kalimat memori (\"{mem_sentence}\")!\n"
            )
            sys.exit(1)

def setup_real_temporary_workspace(root_dir: Path):
    """Membuat direktori kerja nyata dengan git repo aktif, commit, uncommitted change, dan unittest."""
    root_dir.mkdir(parents=True, exist_ok=True)

    # 1. README.md
    (root_dir / "README.md").write_text("# ENKI Project\nAsisten AI lokal untuk developer pada GPU 4GB.", encoding="utf-8")

    # 2. requirements.txt
    (root_dir / "requirements.txt").write_text("fastapi==0.110.0\nuvicorn==0.28.0\npydantic==2.6.4\npytest==8.1.1\n", encoding="utf-8")

    # 3. app/main.py
    app_dir = root_dir / "app"
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "main.py").write_text("def get_status():\n    return 'OK'\n", encoding="utf-8")

    # 4. tests/test_app.py
    test_dir = root_dir / "tests"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "test_app.py").write_text(
        "import unittest\nfrom app.main import get_status\n\n"
        "class TestApp(unittest.TestCase):\n"
        "    def test_status(self):\n        self.assertEqual(get_status(), 'OK')\n"
        "    def test_health(self):\n        self.assertTrue(True)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n",
        encoding="utf-8"
    )

    # 5. Inisialisasi Git nyata
    subprocess.run(["git", "init"], cwd=str(root_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    subprocess.run(["git", "config", "user.name", "ENKI Tester"], cwd=str(root_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    subprocess.run(["git", "config", "user.email", "tester@enki.internal"], cwd=str(root_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    subprocess.run(["git", "checkout", "-b", "feat/enki-core-v2"], cwd=str(root_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    subprocess.run(["git", "add", "."], cwd=str(root_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    subprocess.run(["git", "commit", "-m", "feat: initial commit"], cwd=str(root_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # 6. Buat perubahan belum dicommit agar git diff dan git status nyata
    (app_dir / "main.py").write_text("def get_status():\n    return 'OK'\n\n# uncommitted change for benchmark test\n", encoding="utf-8")

def run_single_benchmark_turn(
    prompt_item: Dict[str, Any],
    coordinator: ReActCoordinator,
    session_id: str,
    db: MemoryDatabase,
    confirmed_commands_log: List[str],
    model: str = "qwen2.5:3b"
) -> Dict[str, Any]:
    """Menjalankan 1 prompt dalam benchmark Set 4 dengan pencatatan metrik detail."""
    user_prompt = prompt_item["prompt"]
    expected_tool = prompt_item.get("expected_tool")
    expected_arg = prompt_item.get("expected_arg")
    expected_key = prompt_item.get("expected_key")
    category = prompt_item.get("category", "NEG")

    tool_calls_recorded = []
    tool_observations_recorded = []
    timeout_reason = "none"

    def trace_cb(evt: str, msg: str):
        if evt == "tool_call":
            tool_calls_recorded.append(msg)
        elif evt == "tool_result":
            tool_observations_recorded.append(msg)
        elif evt == "timeout":
            nonlocal timeout_reason
            timeout_reason = "turn_timeout"

    # Evaluasi recall deterministik di muka (Active Recall jika ada pemicu leksikal)
    recall_injected = False
    recall_content = None
    if is_recall_triggered(user_prompt):
        fts_q = build_fts_query(user_prompt)
        if fts_q:
            snips = db.search_fts(fts_q, exclude_session_id=session_id, limit=3)
            if snips:
                recall_content = format_recall_block(snips, max_tokens=300)
                recall_injected = True

    chunks = []
    final_metrics = {}
    is_timeout = False

    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        for chunk, metrics in coordinator.run_react_turn(
            user_input=user_prompt,
            session_id=session_id,
            history=[],
            model=model,
            recall_content=recall_content,
            trace_callback=trace_cb
        ):
            if chunk:
                chunks.append(chunk)
            if metrics:
                final_metrics = metrics
    except TimeoutError:
        is_timeout = True
        timeout_reason = "no_first_token" if len(chunks) == 0 else "http_request_timeout"
    except Exception as e:
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            is_timeout = True
            timeout_reason = "no_first_token" if len(chunks) == 0 else "http_request_timeout"

    if is_timeout and len(chunks) == 0:
        timeout_reason = "no_first_token"
    elif timeout_reason == "turn_timeout" and len(chunks) == 0:
        is_timeout = True
        timeout_reason = "no_first_token"

    ended_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    final_text = "".join(chunks).strip()

    first_tool_call = None
    first_tool_arg = None
    if tool_calls_recorded:
        raw_call = tool_calls_recorded[0]
        paren_idx = raw_call.find("(")
        if paren_idx != -1:
            first_tool_call = raw_call[:paren_idx].strip()
            first_tool_arg = raw_call[paren_idx + 1 : -1].strip()

    eval_res = evaluate_benchmark_turn(
        expected_tool=expected_tool,
        expected_arg=expected_arg,
        expected_key=expected_key,
        tool_calls_recorded=tool_calls_recorded,
        tool_observations_recorded=tool_observations_recorded,
        final_text=final_text,
        is_timeout=is_timeout,
        timeout_reason=timeout_reason
    )

    return {
        "id": prompt_item["id"],
        "category": category,
        "prompt_input": user_prompt,
        "expected_tool": expected_tool,
        "expected_arg": expected_arg,
        "expected_key": expected_key,
        "called_tool": first_tool_call,
        "called_arg": first_tool_arg,
        "passive_recall_injected": recall_injected,
        "passive_recall_content": recall_content,
        "tool_calls": tool_calls_recorded,
        "tool_observations": tool_observations_recorded,
        "final_response": final_text,
        "confirmed_commands": list(confirmed_commands_log),
        "evaluation": eval_res,
        "metrics": final_metrics,
        "started_at": started_at,
        "ended_at": ended_at
    }

def run_benchmark_suite(
    suite_name: str,
    prompts: List[Dict[str, Any]],
    num_runs: int = 3,
    output_jsonl: Optional[Path] = None
):
    """Menjalankan suite benchmark Set 4A atau Set 4R secara terisolasi."""
    temp_dir = tempfile.mkdtemp(prefix=f"enki_bench_{suite_name}_")
    ws_path = Path(temp_dir) / "workspace"
    data_path = Path(temp_dir) / "data"
    log_path = Path(temp_dir) / "logs"

    data_path.mkdir(parents=True, exist_ok=True)
    log_path.mkdir(parents=True, exist_ok=True)

    os.environ["ENKI_DATA_DIR"] = str(data_path)
    os.environ["ENKI_LOG_DIR"] = str(log_path)

    setup_real_temporary_workspace(ws_path)

    db_path = log_path / "sessions.db"
    db = MemoryDatabase(db_path=db_path)

    profile_mgr = ProfileManager(memory_dir=data_path / "memory")

    # Inisialisasi memori untuk prompt MEM (mode profile vs mode recall)
    for p in prompts:
        if p.get("category") == "MEM":
            mode = p.get("mode", "profile")
            if mode == "profile":
                f_key = p["id"].lower()
                f_val = p["memory_inject"][3] if p.get("memory_inject") else p.get("expected_key")
                profile_mgr.add_fact(f_key, f_val)
            elif mode == "recall":
                if p.get("memory_inject"):
                    u_role, u_msg, a_role, a_msg = p["memory_inject"]
                    sess = db.create_session()
                    db.add_turn(sess, u_role, u_msg)
                    db.add_turn(sess, a_role, a_msg)

    client = OllamaClient()
    if not client.is_ready():
        raise RuntimeError("Server Ollama tidak aktif!")

    assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile=profile_mgr.get_profile_text())

    all_runs_results = []
    print("\n" + "=" * 80)
    print(f"MEMULAI {suite_name.upper()} ({len(prompts)} PROMPT x {num_runs} RUNS):")
    print("=" * 80)

    for run_idx in range(1, num_runs + 1):
        print(f"\n--- Menjalankan Run {run_idx}/{num_runs} ---")
        run_results = []

        for p in prompts:
            confirmed_commands = []
            def confirm_tracker(cmd: str) -> bool:
                confirmed_commands.append(cmd)
                return True  # Auto-approve untuk harness

            coordinator = ReActCoordinator(
                client=client,
                assembler=assembler,
                workspace_root=ws_path,
                db=db,
                confirm_func=confirm_tracker,
                audit_log_path=data_path / "tool_audit.log"
            )

            sess_id = db.create_session()
            t0 = time.time()
            res = run_single_benchmark_turn(
                prompt_item=p,
                coordinator=coordinator,
                session_id=sess_id,
                db=db,
                confirmed_commands_log=confirmed_commands
            )
            elapsed = time.time() - t0

            # Catat run_idx & suite_name
            res["run_idx"] = run_idx
            res["benchmark_set"] = suite_name
            run_results.append(res)

            status_str = "PASS" if res["evaluation"]["is_success"] else "FAIL"
            print(f"  [{res['id']:<14}] {status_str} ({elapsed:.2f}s) | Tool: {res['called_tool']} | Recall Inj: {res['passive_recall_injected']}")

            if output_jsonl:
                with open(output_jsonl, "a", encoding="utf-8") as f:
                    f.write(json.dumps(res, ensure_ascii=False) + "\n")

        all_runs_results.append(run_results)

    # Analisis Metrik MEM (Mode Profile vs Mode Recall)
    mem_records = [r for run in all_runs_results for r in run if r.get("category") == "MEM"]
    if mem_records:
        print("\n" + "=" * 80)
        print(f"ANALISIS METRIK KELOMPOK MEMORI ({suite_name.upper()} - {len(mem_records)} turn):")
        print("=" * 80)
        modes = {"profile": [], "recall": []}
        for r in mem_records:
            m = r.get("mode", "profile")
            modes.setdefault(m, []).append(r)

        for m_name, m_recs in modes.items():
            if not m_recs:
                continue
            n_tot = len(m_recs)
            n_succ = sum(1 for r in m_recs if r["evaluation"]["is_success"])
            n_tool_free = sum(1 for r in m_recs if r.get("called_tool") is None)
            n_key_found = sum(1 for r in m_recs if r["evaluation"].get("valid_args", False) or (r.get("expected_key", "").lower() in r.get("final_response", "").lower()))
            print(f"Mode MEM: '{m_name.upper()}' (n={n_tot} turn):")
            print(f"  Tanpa Tool (0 tool call): {n_tool_free}/{n_tot} ({n_tool_free/n_tot*100:.1f}%)")
            print(f"  Nilai Kunci Muncul      : {n_key_found}/{n_tot} ({n_key_found/n_tot*100:.1f}%)")
            print(f"  Success (Kunci & NoTool): {n_succ}/{n_tot} ({n_succ/n_tot*100:.1f}%)")

        # Kriteria 6: Evaluasi Negatif Murni & Berpemicu Aktif
        neg_records = [r for run in all_runs_results for r in run if r.get("category") == "NEG"]
        if neg_records:
            neg_leaks = sum(1 for r in neg_records if r.get("passive_recall_injected"))
            neg_false_calls = sum(1 for r in neg_records if r.get("called_tool") is not None)
            
            # Deteksi kebocoran nilai kunci memori pada jawaban prompt NEG
            all_mem_keys = [
                p.get("expected_key", "").lower()
                for p in prompts
                if p.get("category") == "MEM" and p.get("expected_key")
            ]
            key_leaks_in_neg = 0
            for r in neg_records:
                resp = r.get("final_response", "").lower()
                for k in all_mem_keys:
                    if len(k) >= 3 and re.search(r'\b' + re.escape(k) + r'\b', resp):
                        key_leaks_in_neg += 1
                        break

            print(f"\nKRITERIA 6: Negatif Murni & Berpemicu Aktif:")
            print(f"  Recall Block Terinjeksi di NEG       : {neg_leaks}/{len(neg_records)} (Status: {'LULUS' if neg_leaks == 0 else 'GAGAL'})")
            print(f"  False Tool Call di NEG               : {neg_false_calls}/{len(neg_records)} (Status: {'LULUS' if neg_false_calls == 0 else 'GAGAL'})")
            print(f"  Kebocoran Nilai Kunci Memori di NEG  : {key_leaks_in_neg}/{len(neg_records)} (Status: {'LULUS' if key_leaks_in_neg == 0 else 'GAGAL'})")

    # Bersihkan temp
    db.close()
    shutil.rmtree(temp_dir, ignore_errors=True)
    if "ENKI_DATA_DIR" in os.environ:
        del os.environ["ENKI_DATA_DIR"]
    if "ENKI_LOG_DIR" in os.environ:
        del os.environ["ENKI_LOG_DIR"]

class TestReActBenchmarkSet4(unittest.TestCase):
    @unittest.skip("Benchmark suite - run explicitly via python tests/test_react_benchmark_set4.py")
    def test_run_set4_benchmark(self):
        """Metode dummy agar terdaftar di discovery tetapi dilewati saat test suite otomatis berjalan."""
        pass

if __name__ == "__main__":
    client = OllamaClient()
    check_ollama_idle(client)

    print("=" * 80)
    print("VERIFIKASI AWAL FILE USER PROMPTS:")
    user_prompts = load_user_prompts(USER_PROMPTS_FILE)
    print(f"Berhasil memuat {len(user_prompts)} prompt valid dari {USER_PROMPTS_FILE.name}.")
    print("=" * 80)

    # Gabungkan 15 prompt user + 15 prompt buatan = 30 prompt Set 4A
    set_4a_prompts = user_prompts + BUILTIN_PROMPTS_4A
    print(f"Total prompt Set 4A (Ujian): {len(set_4a_prompts)}")
    print(f"Total prompt Set 4R (Regresi): {len(REGRESSION_PROMPTS_4R)}")

    mem_4a = [p for p in set_4a_prompts if p.get("category") == "MEM"]
    recall_mode_count = sum(1 for p in mem_4a if p.get("mode") == "recall")
    print(f"Distribusi 12 MEM: {recall_mode_count} Mode Recall, {len(mem_4a) - recall_mode_count} Mode Profile.")
    if recall_mode_count < 4:
        sys.stderr.write(
            f"\n[FATAL RUNNER ERROR]: Ditemukan hanya {recall_mode_count} prompt dengan mode recall "
            f"dari total {len(mem_4a)} prompt MEM (diperlukan minimal 4 mode recall).\n"
            f"Runner Set 4 GAGAL KERAS dan MENOLAK BERJALAN.\n"
        )
        sys.exit(1)

    print("\n[PREFLIGHT CHECK]: Memvalidasi seluruh prompt MEM terhadap aturan grounding dan anti-kebocoran...")
    validate_mem_prompts_preflight(mem_4a)
    print("Preflight check MEM 100% LULUS.")

    results_dir = ROOT_DIR / "tests" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / "benchmark_set4_raw.jsonl"
    if out_file.exists():
        out_file.unlink()
    header = create_benchmark_jsonl_header(USER_PROMPTS_FILE)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")

    with BenchmarkLock():
        # Jalankan Set 4A
        res_4a = run_benchmark_suite("set_4a_exam", set_4a_prompts, num_runs=3, output_jsonl=out_file)

        # Jalankan Set 4R
        res_4r = run_benchmark_suite("set_4r_regression", REGRESSION_PROMPTS_4R, num_runs=3, output_jsonl=out_file)

        print("\nBENCHMARK SET 4 SELESAI. Raw JSONL tersimpan di:", out_file)

