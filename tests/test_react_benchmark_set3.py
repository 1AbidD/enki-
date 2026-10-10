import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Any, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import datetime
from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.core.react_loop import ReActCoordinator
from enki.memory.database import MemoryDatabase
from tests.benchmark_common import evaluate_benchmark_turn

# Definisi 24 Prompt Uji Baru (Set Ketiga - UJIAN)
BENCHMARK_PROMPTS_SET3 = [
    # --- 4 Prompts Positif: search_memory (Pre-trained Bias vs Recall Truth) ---
    {
        "id": "S3_P01_MEM",
        "prompt": "apa database utama proyek kita ya?",
        "expected_tool": "search_memory",
        "expected_arg": "database",
        "unique_token": "sqlite"  # Bias: PostgreSQL/MySQL -> Truth: SQLite
    },
    {
        "id": "S3_P02_MEM",
        "prompt": "bahasa apa yg kita pake buat backend service pembayaran?",
        "expected_tool": "search_memory",
        "expected_arg": "pembayaran",
        "unique_token": "fastapi"  # Bias: Java/Go -> Truth: Python FastAPI
    },
    {
        "id": "S3_P03_MEM",
        "prompt": "port berapa yg kita sepakati buat running server staging?",
        "expected_tool": "search_memory",
        "expected_arg": "port",
        "unique_token": "9042"  # Bias: 8080/3000 -> Truth: 9042
    },
    {
        "id": "S3_P04_MEM",
        "prompt": "siapa ketua tim atau lead engineer proyek enki waktu itu?",
        "expected_tool": "search_memory",
        "expected_arg": "lead",
        "unique_token": "budi santoso"  # Bias: AI Lab Lead -> Truth: Budi Santoso
    },

    # --- 4 Prompts Positif: read_file (User Style & Slang) ---
    {
        "id": "S3_P05_READ",
        "prompt": "bisa cek file README.md sebentar, apa deskripsi proyeknya?",
        "expected_tool": "read_file",
        "expected_arg": "README.md",
        "unique_token": "asisten pribadi"
    },
    {
        "id": "S3_P06_READ",
        "prompt": "tolong liatin isi conf/app.conf dong pengen tau settingannya",
        "expected_tool": "read_file",
        "expected_arg": "conf/app.conf",
        "unique_token": "max_connections=50"
    },
    {
        "id": "S3_P07_READ",
        "prompt": "buka file docker-compose.yml trus liat nama imagenya apa",
        "expected_tool": "read_file",
        "expected_arg": "docker-compose.yml",
        "unique_token": "enki-core:latest"
    },
    {
        "id": "S3_P08_READ",
        "prompt": "eh tolong baca licenses.txt, ada lisensi apa aja disitu",
        "expected_tool": "read_file",
        "expected_arg": "licenses.txt",
        "unique_token": "apache-2.0"
    },

    # --- 4 Prompts Positif: run_shell_command (User Style & Slang) ---
    {
        "id": "S3_P09_SHELL",
        "prompt": "cek status git sekarang dong, ada file uncommitted ga?",
        "expected_tool": "run_shell_command",
        "expected_arg": "git status",
        "unique_token": "main"
    },
    {
        "id": "S3_P10_SHELL",
        "prompt": "liat git diff bentaran bro pengen liat baris yg diganti",
        "expected_tool": "run_shell_command",
        "expected_arg": "git diff",
        "unique_token": "auth_service.py"
    },
    {
        "id": "S3_P11_SHELL",
        "prompt": "coba cek versi python di komputer ini berapa",
        "expected_tool": "run_shell_command",
        "expected_arg": "python --version",
        "unique_token": "3.14.0"
    },
    {
        "id": "S3_P12_SHELL",
        "prompt": "tolong jalanin unit test python pake unittest ya",
        "expected_tool": "run_shell_command",
        "expected_arg": "python -m unittest",
        "unique_token": "30"
    },

    # --- 12 Prompts Negatif: Chat Murni / No Tool (User Style, Slang, Typo) ---
    {
        "id": "S3_P13_NEG",
        "prompt": "pagi enki, hari ini jadwal kita santai dulu ya",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P14_NEG",
        "prompt": "bisa jelasin konsep closure di javascript ga pake bahasa gampang?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P15_NEG",
        "prompt": "kasih resep bikin sambal bawang yang pedas mantap dong",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P16_NEG",
        "prompt": "apa ibukota negara australia dan kenapa bukan sydney?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P17_NEG",
        "prompt": "kapan bahasa pemrograman C pertama kali diciptakan dan oleh siapa?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P18_NEG",
        "prompt": "buatkan fungsi python buat balik urutan string (reverse string)",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P19_NEG",
        "prompt": "apa keuntungan dan kerugian memakai microservices dibanding monolitik?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P20_NEG",
        "prompt": "wah capek banget hari ini koding seharian ga kelar-kelar",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P21_NEG",
        "prompt": "mnurut km mending blajar rust atau golang dlu buat backend engineer pemula?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P22_NEG",
        "prompt": "ada teka-teki: sebuah kereta listrik bergerak ke utara, ke mana arah asapnya?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P23_NEG",
        "prompt": "bantu bikinin draf email izin sakit untuk bos kantor besok",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "S3_P24_NEG",
        "prompt": "gimana tips mengatasi insomnia dan pola tidur yg berantakan?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    }
]

def setup_benchmark_fixtures_set3(workspace: Path, db: MemoryDatabase):
    """Menyiapkan file fixture dan histori memori untuk Set 3 (Ujian)."""
    # 1. File fixtures
    (workspace / "README.md").write_text("# ENKI\nSistem asisten pribadi lokal berbasis LLM.", encoding="utf-8")
    conf_dir = workspace / "conf"
    conf_dir.mkdir(parents=True, exist_ok=True)
    (conf_dir / "app.conf").write_text("[server]\nmax_connections=50\ntimeout=60", encoding="utf-8")
    (workspace / "docker-compose.yml").write_text("version: '3.8'\nservices:\n  app:\n    image: enki-core:latest", encoding="utf-8")
    (workspace / "licenses.txt").write_text("Daftar Lisensi Proyek:\n1. MIT License\n2. Apache-2.0 License", encoding="utf-8")

    # 2. Database past session fixtures (Bias vs Recall Truth)
    s1 = db.create_session()
    db.add_turn(s1, "user", "apa database utama proyek kita ya?")
    db.add_turn(s1, "assistant", "Database utama proyek kita adalah SQLite dengan mode WAL dan indeks FTS5.")

    s2 = db.create_session()
    db.add_turn(s2, "user", "bahasa apa yg kita pake buat backend service pembayaran?")
    db.add_turn(s2, "assistant", "Untuk service pembayaran, backend kita menggunakan Python FastAPI.")

    s3 = db.create_session()
    db.add_turn(s3, "user", "port berapa yg kita sepakati buat running server staging?")
    db.add_turn(s3, "assistant", "Server staging kita disepakati berjalan pada port 9042.")

    s4 = db.create_session()
    db.add_turn(s4, "user", "siapa ketua tim atau lead engineer proyek enki waktu itu?")
    db.add_turn(s4, "assistant", "Lead engineer proyek ENKI adalah Budi Santoso.")

def mock_shell_output_set3(command: str) -> str:
    """Mengembalikan output realistis untuk shell allowlist Set 3."""
    cmd_clean = command.strip().lower()
    if "git status" in cmd_clean:
        return "On branch main\nnothing to commit, working tree clean"
    elif "git diff" in cmd_clean:
        return "diff --git a/auth_service.py b/auth_service.py\n--- a/auth_service.py\n+++ b/auth_service.py\n@@ -1,3 +1,4 @@\n+jwt_token = verify()"
    elif "python --version" in cmd_clean:
        return "Python 3.14.0"
    elif "python -m unittest" in cmd_clean:
        return "Ran 30 tests in 0.520s\n\nOK"
    return f"[Shell Output] Command executed: {command}"

def run_single_benchmark_turn_set3(
    prompt_item: Dict[str, Any],
    coordinator: ReActCoordinator,
    session_id: str,
    model: str = "qwen2.5:3b"
) -> Dict[str, Any]:
    """Menjalankan 1 prompt dalam ReAct loop multi-turn untuk Set 3."""
    user_prompt = prompt_item["prompt"]
    expected_tool = prompt_item["expected_tool"]
    expected_arg = prompt_item["expected_arg"]
    unique_token = prompt_item["unique_token"]

    tool_calls_recorded = []
    tool_observations_recorded = []

    is_timeout = False
    timeout_reason = None

    def trace_cb(evt: str, msg: str):
        nonlocal is_timeout, timeout_reason
        if evt == "timeout":
            is_timeout = True
            timeout_reason = "turn_timeout"
        elif evt == "tool_call":
            tool_calls_recorded.append(msg)
        elif evt == "tool_result":
            tool_observations_recorded.append(msg)

    orig_execute_tool = coordinator.execute_tool
    def instrumented_execute_tool(tool_name: str, args: Dict[str, Any], session_id: str = "") -> str:
        if tool_name == "run_shell_command":
            cmd = args.get("command", "")
            return mock_shell_output_set3(cmd)
        return orig_execute_tool(tool_name, args, session_id=session_id)

    coordinator.execute_tool = instrumented_execute_tool

    chunks = []
    final_metrics = {}
    started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        for chunk, metrics in coordinator.run_react_turn(
            user_input=user_prompt,
            session_id=session_id,
            history=[],
            model=model,
            num_ctx=8192,
            temperature=0.2,
            trace_callback=trace_cb
        ):
            if chunk:
                chunks.append(chunk)
            if metrics:
                final_metrics = metrics
    except Exception as e:
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            is_timeout = True
            timeout_reason = "http_request_timeout"
    finally:
        coordinator.execute_tool = orig_execute_tool

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
        expected_key=unique_token,
        tool_calls_recorded=tool_calls_recorded,
        tool_observations_recorded=tool_observations_recorded,
        final_text=final_text,
        is_timeout=is_timeout,
        timeout_reason=timeout_reason
    )

    return {
        "id": prompt_item["id"],
        "prompt_input": user_prompt,
        "tool_calls": tool_calls_recorded,
        "called_tool": first_tool_call,
        "called_arg": first_tool_arg,
        "tool_observations": tool_observations_recorded,
        "final_response": final_text,
        "evaluation": eval_res,
        "metrics": final_metrics,
        "is_false_call": eval_res["is_false_call"],
        "is_wrong_tool": eval_res["is_wrong_tool"],
        "is_invalid_arg": eval_res["is_invalid_arg"],
        "is_fidelity_ok": eval_res["fidelity_ok"],
        "is_success": eval_res["is_success"],
        "is_timeout": eval_res["is_timeout"],
        "started_at": started_at,
        "ended_at": ended_at,
        "final_text_preview": final_text[:120].replace("\n", " ")
    }

class TestReActBenchmarkSet3(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_root = Path(self.temp_dir)
        self.db_path = self.workspace_root / "bench_sessions_s3.db"
        self.db = MemoryDatabase(db_path=self.db_path)
        self.session_id = self.db.create_session()
        os.environ["ENKI_DATA_DIR"] = str(self.workspace_root / "data")
        os.environ["ENKI_LOG_DIR"] = str(self.workspace_root / "logs")
        setup_benchmark_fixtures_set3(self.workspace_root, self.db)
        self.client = OllamaClient()
        self.assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile="")
        self.coordinator = ReActCoordinator(
            client=self.client,
            assembler=self.assembler,
            workspace_root=self.workspace_root,
            db=self.db,
            confirm_func=lambda _: True,
            audit_log_path=self.workspace_root / "data" / "tool_audit.log"
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if "ENKI_DATA_DIR" in os.environ:
            del os.environ["ENKI_DATA_DIR"]
        if "ENKI_LOG_DIR" in os.environ:
            del os.environ["ENKI_LOG_DIR"]

    @unittest.skip("Benchmark suite - run explicitly via python tests/test_react_benchmark_set3.py")
    def test_run_3_iterations_benchmark_set3(self):
        """Menjalankan 24 prompt multi-turn UJIAN (Set 3) sebanyak 3 iterasi."""
        if not self.client.is_ready():
            self.skipTest("Ollama server tidak aktif")

        results_dir = ROOT_DIR / "tests" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = results_dir / "benchmark_set3_raw.jsonl"
        if jsonl_path.exists():
            jsonl_path.unlink()

        print("\n" + "=" * 75)
        print("KRITERIA KELULUSAN BENCHMARK SET 3 (UJIAN):")
        print("  1. Routing Positif     : >= 90.0%")
        print("  2. False Call Negatif  : <= 10.0% (prompt konfirmasi dilaporkan terpisah)")
        print("  3. Fidelity            : >= 85.0%")
        print("  4. Timeout             : 0")
        print("  5. Invalid Argumen     : 0")
        print("=" * 75)
        print("MEMULAI BENCHMARK SET KETIGA (24 PROMPT x 3 RUNS - MULTI-TURN REACT)")
        print("=" * 75)

        runs_results = []
        num_runs = 3

        for run_idx in range(1, num_runs + 1):
            t_run_start = time.time()
            print(f"\n--- MENJALANKAN RUN {run_idx}/{num_runs} ---")
            run_items = []
            for item in BENCHMARK_PROMPTS_SET3:
                res = run_single_benchmark_turn_set3(
                    prompt_item=item,
                    coordinator=self.coordinator,
                    session_id=self.session_id,
                    model="qwen2.5:3b"
                )
                run_items.append(res)

                jsonl_record = {
                    "benchmark_set": "set_3_exam",
                    "run_idx": run_idx,
                    "id": res["id"],
                    "started_at": res.get("started_at"),
                    "ended_at": res.get("ended_at"),
                    "prompt_input": res["prompt_input"],
                    "tool_calls": res["tool_calls"],
                    "tool_observations": res["tool_observations"],
                    "final_response": res["final_response"],
                    "evaluation": res["evaluation"],
                    "metrics": res["metrics"]
                }
                with open(jsonl_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(jsonl_record, ensure_ascii=False) + "\n")

                status_str = "OK" if res["is_success"] else "FAIL"
                print(f"[{res['id']}] {status_str} | Tool: {res['called_tool']} | Success: {res['is_success']}")

            dur = round(time.time() - t_run_start, 2)
            print(f"Run {run_idx} selesai dalam {dur} detik.")
            runs_results.append(run_items)

        # Analisis metrik
        total_tests = len(BENCHMARK_PROMPTS_SET3) * num_runs
        pos_tests = 12 * num_runs
        neg_tests = 12 * num_runs

        pos_routing_success = 0
        neg_false_calls = 0
        pos_fidelity_pass = 0
        total_timeouts = 0
        total_invalid_args = 0
        shell_prompt_attempts = 0

        print("\n" + "=" * 75)
        print("HASIL MENTAH PER PROMPT SET 3 (3 RUNS):")
        print(f"{'ID':<14} | {'Expected Tool':<18} | {'Run 1':<6} | {'Run 2':<6} | {'Run 3':<6} | {'Status Agregat'}")
        print("-" * 75)

        for p_idx, prompt_item in enumerate(BENCHMARK_PROMPTS_SET3):
            p_id = prompt_item["id"]
            exp_tool = str(prompt_item["expected_tool"])
            r1 = "PASS" if runs_results[0][p_idx]["is_success"] else "FAIL"
            r2 = "PASS" if runs_results[1][p_idx]["is_success"] else "FAIL"
            r3 = "PASS" if runs_results[2][p_idx]["is_success"] else "FAIL"

            passes = [runs_results[r][p_idx]["is_success"] for r in range(num_runs)]
            agg = f"{sum(passes)}/{num_runs}"
            print(f"{p_id:<14} | {exp_tool:<18} | {r1:<6} | {r2:<6} | {r3:<6} | {agg}")

            is_positive = prompt_item["expected_tool"] is not None

            for r in range(num_runs):
                item_res = runs_results[r][p_idx]
                if is_positive:
                    if not item_res["is_wrong_tool"]:
                        pos_routing_success += 1
                    if item_res["is_fidelity_ok"]:
                        pos_fidelity_pass += 1
                else:
                    if item_res["is_false_call"]:
                        neg_false_calls += 1
                        if item_res["called_tool"] == "run_shell_command":
                            shell_prompt_attempts += 1

                if item_res["is_invalid_arg"]:
                    total_invalid_args += 1
                if item_res["is_timeout"]:
                    total_timeouts += 1

        routing_pct = (pos_routing_success / pos_tests) * 100
        false_call_pct = (neg_false_calls / neg_tests) * 100
        fidelity_pct = (pos_fidelity_pass / pos_tests) * 100

        print("=" * 75)
        print("RINGKASAN METRIK AKHIR BENCHMARK SET 3 (UJIAN):")
        print(f"Total Uji (24x3)             : {total_tests}")
        print(f"Routing Positif (>=90%)      : {pos_routing_success}/{pos_tests} ({routing_pct:.1f}%)")
        print(f"False Call Negatif (<=10%)   : {neg_false_calls}/{neg_tests} ({false_call_pct:.1f}%)")
        print(f"  - Shell Confirmation Prompt: {shell_prompt_attempts} kali dipicu oleh model")
        print(f"Fidelity Jawaban (>=85%)     : {pos_fidelity_pass}/{pos_tests} ({fidelity_pct:.1f}%)")
        print(f"Total Timeout (=0)           : {total_timeouts}")
        print(f"Invalid Argumen (=0)         : {total_invalid_args}")
        print(f"Raw JSONL tersimpan di       : {jsonl_path}")
        print("=" * 75)

if __name__ == "__main__":
    unittest.main()
