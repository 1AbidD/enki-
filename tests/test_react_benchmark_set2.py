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

# Definisi 20 Prompt Uji Baru (Set Kedua - DEV SET)
BENCHMARK_PROMPTS_SET2 = [
    # --- 7 Prompts: read_file (Casual / Slang) ---
    {
        "id": "P01_READ",
        "prompt": "bisa tlg bacain isi file project_status.txt dong bro",
        "expected_tool": "read_file",
        "expected_arg": "project_status.txt",
        "unique_token": "oauth2"
    },
    {
        "id": "P02_READ",
        "prompt": "liat dlu file config_prod.json isinya apaan ya",
        "expected_tool": "read_file",
        "expected_arg": "config_prod.json",
        "unique_token": "8088"
    },
    {
        "id": "P03_READ",
        "prompt": "tolong cek isi file todo_list.md di workspace kita",
        "expected_tool": "read_file",
        "expected_arg": "todo_list.md",
        "unique_token": "pembayaran"
    },
    {
        "id": "P04_READ",
        "prompt": "baca requirements.txt plis mau liat package apa aja",
        "expected_tool": "read_file",
        "expected_arg": "requirements.txt",
        "unique_token": "0.110.0"
    },
    {
        "id": "P05_READ",
        "prompt": "coba buka dan liat skrip deploy.sh ada konfigurasi apa di dalemnya",
        "expected_tool": "read_file",
        "expected_arg": "deploy.sh",
        "unique_token": "cluster-prod-east-1"
    },
    {
        "id": "P06_READ",
        "prompt": "eh cekin file notes_arsitektur.txt bentar isinya apaan",
        "expected_tool": "read_file",
        "expected_arg": "notes_arsitektur.txt",
        "unique_token": "kafka"
    },
    {
        "id": "P07_READ",
        "prompt": "tolong periksa file changelog.txt versi terakhir dong",
        "expected_tool": "read_file",
        "expected_arg": "changelog.txt",
        "unique_token": "2.1.0"
    },

    # --- 5 Prompts: run_shell_command (Casual / Slang) ---
    {
        "id": "P08_SHELL",
        "prompt": "coba liat status git kita skrg lg di branch mana",
        "expected_tool": "run_shell_command",
        "expected_arg": "git status",
        "unique_token": "main"
    },
    {
        "id": "P09_SHELL",
        "prompt": "cek git diff bentar dong ada perubahan apa yg blm di commit",
        "expected_tool": "run_shell_command",
        "expected_arg": "git diff",
        "unique_token": "app.py"
    },
    {
        "id": "P10_SHELL",
        "prompt": "tolong liat riwayat git log commit terakhir di repo ini",
        "expected_tool": "run_shell_command",
        "expected_arg": "git log",
        "unique_token": "abc123def456"
    },
    {
        "id": "P11_SHELL",
        "prompt": "coba cek versi python yg terpasang di sistem kita dong",
        "expected_tool": "run_shell_command",
        "expected_arg": "python --version",
        "unique_token": "3.14.0"
    },
    {
        "id": "P12_SHELL",
        "prompt": "tlg jalankan unit test python kita pake unittest sekarang",
        "expected_tool": "run_shell_command",
        "expected_arg": "python -m unittest",
        "unique_token": "25"
    },

    # --- 2 Prompts: search_memory (Casual / Past Context) ---
    {
        "id": "P13_MEM",
        "prompt": "kita sempat bahas apa ya soal optimasi database sqlite di sesi lampau?",
        "expected_tool": "search_memory",
        "expected_arg": "sqlite",
        "unique_token": "wal"
    },
    {
        "id": "P14_MEM",
        "prompt": "pernah gak kita catat nama server staging di riwayat obrolan lama?",
        "expected_tool": "search_memory",
        "expected_arg": "server",
        "unique_token": "staging-sg-01"
    },

    # --- 6 Prompts: Negatif / Chat Murni (No Tool) ---
    {
        "id": "P15_NEG",
        "prompt": "halo selamat pagi enki, gimana kabarmu hari ini?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "P16_NEG",
        "prompt": "jelaskan dong apa itu konsep recursion dalam pemrograman secara ringkas",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "P17_NEG",
        "prompt": "siapa pencipta bahasa pemrograman python dan kapan pertama kali dirilis?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "P18_NEG",
        "prompt": "buatkan resep singkat cara bikin nasi goreng kampung yang enak dan gurih",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "P19_NEG",
        "prompt": "menurutmu apa perbedaan mendasar antara framework dan library?",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    },
    {
        "id": "P20_NEG",
        "prompt": "tuliskan contoh fungsi fibonacci rekursif sederhana dalam bahasa python",
        "expected_tool": None,
        "expected_arg": None,
        "unique_token": None
    }
]

def setup_benchmark_fixtures(workspace: Path, db: MemoryDatabase):
    """Menyiapkan file fixture dengan teks alami realistis dan riwayat memori."""
    # 1. File fixtures
    (workspace / "project_status.txt").write_text("Status proyek: integrasi oauth2 selesai 80%.", encoding="utf-8")
    (workspace / "config_prod.json").write_text('{"env": "prod", "port": "8088"}', encoding="utf-8")
    (workspace / "todo_list.md").write_text("# TODO List\n- Deploy modul pembayaran\n- Review PR", encoding="utf-8")
    (workspace / "requirements.txt").write_text("fastapi==0.110.0\nuvicorn>=0.20", encoding="utf-8")
    (workspace / "deploy.sh").write_text("#!/bin/bash\necho Deploying to cluster-prod-east-1", encoding="utf-8")
    (workspace / "notes_arsitektur.txt").write_text("Arsitektur microservices menggunakan Kafka event stream.", encoding="utf-8")
    (workspace / "changelog.txt").write_text("Changelog v2.1.0:\n- Rilis stabil versi 2.1.0", encoding="utf-8")

    # 2. Database past session fixtures
    old_session_1 = db.create_session()
    db.add_turn(old_session_1, "user", "Bagaimana cara optimasi database sqlite kita?")
    db.add_turn(old_session_1, "assistant", "Gunakan mode WAL dan pragma synchronous=NORMAL untuk performa maksimal database sqlite.")

    old_session_2 = db.create_session()
    db.add_turn(old_session_2, "user", "Apa nama server staging kita?")
    db.add_turn(old_session_2, "assistant", "Server staging kita ada di staging-sg-01.internal.net.")

def mock_shell_output(command: str) -> str:
    """Mengembalikan output realistis alami untuk shell allowlist."""
    cmd_clean = command.strip().lower()
    if "git status" in cmd_clean:
        return "On branch main\nnothing to commit, working tree clean"
    elif "git diff" in cmd_clean:
        return "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,4 @@\n+print('hello world')"
    elif "git log" in cmd_clean:
        return "commit abc123def456\nAuthor: dev <dev@enki.internal>\nDate:   Fri Oct 10 10:00:00 2026\n\n    feat: add oauth2 login"
    elif "python --version" in cmd_clean:
        return "Python 3.14.0"
    elif "python -m unittest" in cmd_clean:
        return "Ran 25 tests in 0.450s\n\nOK"
    return f"[Shell Output] Command executed: {command}"

def run_single_benchmark_turn(
    prompt_item: Dict[str, Any],
    coordinator: ReActCoordinator,
    session_id: str,
    model: str = "qwen2.5:3b"
) -> Dict[str, Any]:
    """Menjalankan 1 prompt dalam ReAct loop multi-turn dan mencatat metrik komprehensif."""
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
            return mock_shell_output(cmd)
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
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
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

class TestReActBenchmarkSet2(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_root = Path(self.temp_dir)
        self.db_path = self.workspace_root / "bench_sessions.db"
        self.db = MemoryDatabase(db_path=self.db_path)
        self.session_id = self.db.create_session()
        os.environ["ENKI_DATA_DIR"] = str(self.workspace_root / "data")
        os.environ["ENKI_LOG_DIR"] = str(self.workspace_root / "logs")
        setup_benchmark_fixtures(self.workspace_root, self.db)
        self.client = OllamaClient()
        self.assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile="")
        self.coordinator = ReActCoordinator(
            client=self.client,
            assembler=self.assembler,
            workspace_root=self.workspace_root,
            db=self.db,
            confirm_func=lambda _: True,  # auto-confirm for benchmark
            audit_log_path=self.workspace_root / "data" / "tool_audit.log"
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if "ENKI_DATA_DIR" in os.environ:
            del os.environ["ENKI_DATA_DIR"]
        if "ENKI_LOG_DIR" in os.environ:
            del os.environ["ENKI_LOG_DIR"]

    @unittest.skip("Benchmark suite - run explicitly via python tests/test_react_benchmark_set2.py")
    def test_run_3_iterations_benchmark(self):
        """Menjalankan 20 prompt multi-turn DEV SET sebanyak 3 iterasi dan menyimpan raw JSONL."""
        if not self.client.is_ready():
            self.skipTest("Ollama server tidak aktif")

        results_dir = ROOT_DIR / "tests" / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = results_dir / "benchmark_set2_raw.jsonl"
        # Reset file JSONL
        if jsonl_path.exists():
            jsonl_path.unlink()

        print("\n" + "=" * 70)
        print("MEMULAI BENCHMARK SET KEDUA (DEV SET: 20 PROMPT x 3 RUNS - MULTI-TURN REACT)")
        print("=" * 70)

        runs_results = []
        num_runs = 3

        for run_idx in range(1, num_runs + 1):
            t_run_start = time.time()
            print(f"\n--- MENJALANKAN RUN {run_idx}/{num_runs} ---")
            run_items = []
            for item in BENCHMARK_PROMPTS_SET2:
                res = run_single_benchmark_turn(
                    prompt_item=item,
                    coordinator=self.coordinator,
                    session_id=self.session_id,
                    model="qwen2.5:3b"
                )
                run_items.append(res)

                # Simpan rekaman raw per iterasi ke JSONL
                jsonl_record = {
                    "benchmark_set": "set_2_dev_set",
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

        # Analisis metrik per prompt dan total agregat
        total_tests = len(BENCHMARK_PROMPTS_SET2) * num_runs
        total_success = 0
        total_false_calls = 0
        total_wrong_tool = 0
        total_invalid_arg = 0
        total_fidelity_pass = 0

        print("\n" + "=" * 70)
        print("HASIL MENTAH PER PROMPT DEV SET (3 RUNS):")
        print(f"{'ID':<10} | {'Expected Tool':<18} | {'Run 1':<6} | {'Run 2':<6} | {'Run 3':<6} | {'Status Agregat'}")
        print("-" * 70)

        for p_idx, prompt_item in enumerate(BENCHMARK_PROMPTS_SET2):
            p_id = prompt_item["id"]
            exp_tool = str(prompt_item["expected_tool"])
            r1 = "PASS" if runs_results[0][p_idx]["is_success"] else "FAIL"
            r2 = "PASS" if runs_results[1][p_idx]["is_success"] else "FAIL"
            r3 = "PASS" if runs_results[2][p_idx]["is_success"] else "FAIL"

            passes = [runs_results[r][p_idx]["is_success"] for r in range(num_runs)]
            agg = f"{sum(passes)}/{num_runs}"
            print(f"{p_id:<10} | {exp_tool:<18} | {r1:<6} | {r2:<6} | {r3:<6} | {agg}")

            for r in range(num_runs):
                item_res = runs_results[r][p_idx]
                if item_res["is_success"]:
                    total_success += 1
                if item_res["is_false_call"]:
                    total_false_calls += 1
                if item_res["is_wrong_tool"]:
                    total_wrong_tool += 1
                if item_res["is_invalid_arg"]:
                    total_invalid_arg += 1
                if item_res["is_fidelity_ok"]:
                    total_fidelity_pass += 1

        acc_pct = (total_success / total_tests) * 100
        print("=" * 70)
        print("RINGKASAN METRIK AKHIR DEV SET KEDUA:")
        print(f"Total Uji (20x3)        : {total_tests}")
        print(f"Sukses Ujung-ke-Ujung   : {total_success}/{total_tests} ({acc_pct:.1f}%)")
        print(f"Salah-Panggil Tool (Neg): {total_false_calls}/{total_tests}")
        print(f"Salah Pilih Tool (Wrong): {total_wrong_tool}/{total_tests}")
        print(f"Argumen Salah (Arg Fail): {total_invalid_arg}/{total_tests}")
        print(f"Fidelity Jawaban        : {total_fidelity_pass}/{total_tests}")
        print(f"Raw JSONL tersimpan di  : {jsonl_path}")
        print("=" * 70)

if __name__ == "__main__":
    unittest.main()
