import os
import sys
import json
import time
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Dict, Any, List, Optional

from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.core.react_loop import ReActCoordinator
from enki.memory.database import MemoryDatabase

# Definisi 20 Prompt Uji Baru (Set Kedua)
BENCHMARK_PROMPTS_SET2 = [
    # --- 7 Prompts: read_file (Casual / Slang) ---
    {
        "id": "P01_READ",
        "prompt": "bisa tlg bacain isi file project_status.txt dong bro",
        "expected_tool": "read_file",
        "expected_arg": "project_status.txt",
        "unique_token": "STATUS_TOKEN_991"
    },
    {
        "id": "P02_READ",
        "prompt": "liat dlu file config_prod.json isinya apaan ya",
        "expected_tool": "read_file",
        "expected_arg": "config_prod.json",
        "unique_token": "PORT_TOKEN_8088"
    },
    {
        "id": "P03_READ",
        "prompt": "tolong cek isi file todo_list.md di workspace kita",
        "expected_tool": "read_file",
        "expected_arg": "todo_list.md",
        "unique_token": "TASK_TOKEN_DEPLOY"
    },
    {
        "id": "P04_READ",
        "prompt": "baca requirements.txt plis mau liat package apa aja",
        "expected_tool": "read_file",
        "expected_arg": "requirements.txt",
        "unique_token": "FASTAPI_TOKEN_0110"
    },
    {
        "id": "P05_READ",
        "prompt": "coba buka dan liat skrip deploy.sh ada konfigurasi apa di dalemnya",
        "expected_tool": "read_file",
        "expected_arg": "deploy.sh",
        "unique_token": "CLUSTER_TOKEN_PROD44"
    },
    {
        "id": "P06_READ",
        "prompt": "eh cekin file notes_arsitektur.txt bentar isinya apaan",
        "expected_tool": "read_file",
        "expected_arg": "notes_arsitektur.txt",
        "unique_token": "ARCH_TOKEN_MICRO"
    },
    {
        "id": "P07_READ",
        "prompt": "tolong periksa file changelog.txt versi terakhir dong",
        "expected_tool": "read_file",
        "expected_arg": "changelog.txt",
        "unique_token": "VERSION_TOKEN_V210"
    },

    # --- 5 Prompts: run_shell_command (Casual / Slang) ---
    {
        "id": "P08_SHELL",
        "prompt": "coba liat status git kita skrg lg di branch mana",
        "expected_tool": "run_shell_command",
        "expected_arg": "git status",
        "unique_token": "BRANCH_MAIN_CLEAN"
    },
    {
        "id": "P09_SHELL",
        "prompt": "cek git diff bentar dong ada perubahan apa yg blm di commit",
        "expected_tool": "run_shell_command",
        "expected_arg": "git diff",
        "unique_token": "DIFF_TOKEN_MODIFIED"
    },
    {
        "id": "P10_SHELL",
        "prompt": "tolong liat riwayat git log commit terakhir di repo ini",
        "expected_tool": "run_shell_command",
        "expected_arg": "git log",
        "unique_token": "COMMIT_TOKEN_FEAT99"
    },
    {
        "id": "P11_SHELL",
        "prompt": "coba cek versi python yg terpasang di sistem kita dong",
        "expected_tool": "run_shell_command",
        "expected_arg": "python --version",
        "unique_token": "PYTHON_TOKEN_314"
    },
    {
        "id": "P12_SHELL",
        "prompt": "tlg jalankan unit test python kita pake unittest sekarang",
        "expected_tool": "run_shell_command",
        "expected_arg": "python -m unittest",
        "unique_token": "TEST_TOKEN_ALL_PASS"
    },

    # --- 2 Prompts: search_memory (Casual / Past Context) ---
    {
        "id": "P13_MEM",
        "prompt": "kita sempat bahas apa ya soal optimasi database sqlite di sesi lampau?",
        "expected_tool": "search_memory",
        "expected_arg": "sqlite",
        "unique_token": "SQLITE_WAL_TOKEN_77"
    },
    {
        "id": "P14_MEM",
        "prompt": "pernah gak kita catat nama server staging di riwayat obrolan lama?",
        "expected_tool": "search_memory",
        "expected_arg": "server",
        "unique_token": "SERVER_STAGING_TOKEN_SG"
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
    """Menyiapkan file fixture dengan token unik dan riwayat memori."""
    # 1. File fixtures
    (workspace / "project_status.txt").write_text("Proyek dalam progres. Token status: STATUS_TOKEN_991.", encoding="utf-8")
    (workspace / "config_prod.json").write_text('{"env": "prod", "port": "PORT_TOKEN_8088"}', encoding="utf-8")
    (workspace / "todo_list.md").write_text("# TODO List\n- Deploy TASK_TOKEN_DEPLOY\n- Review PR", encoding="utf-8")
    (workspace / "requirements.txt").write_text("fastapi==FASTAPI_TOKEN_0110\nuvicorn>=0.20", encoding="utf-8")
    (workspace / "deploy.sh").write_text("#!/bin/bash\necho Deploying to CLUSTER_TOKEN_PROD44", encoding="utf-8")
    (workspace / "notes_arsitektur.txt").write_text("Arsitektur microservices: ARCH_TOKEN_MICRO.", encoding="utf-8")
    (workspace / "changelog.txt").write_text("Changelog v2.1.0: VERSION_TOKEN_V210 released.", encoding="utf-8")

    # 2. Database past session fixtures
    old_session_1 = db.create_session()
    db.add_turn(old_session_1, "user", "Bagaimana cara optimasi database sqlite kita?")
    db.add_turn(old_session_1, "assistant", "Gunakan mode WAL dan pragma synchronous=NORMAL, kuncinya SQLITE_WAL_TOKEN_77.")

    old_session_2 = db.create_session()
    db.add_turn(old_session_2, "user", "Apa nama server staging kita?")
    db.add_turn(old_session_2, "assistant", "Server staging kita ada di SERVER_STAGING_TOKEN_SG.")

def mock_shell_output(command: str) -> str:
    """Mengembalikan output terkontrol dengan unique token untuk shell allowlist."""
    cmd_clean = command.strip().lower()
    if "git status" in cmd_clean:
        return "On branch main\nWorking tree clean: BRANCH_MAIN_CLEAN"
    elif "git diff" in cmd_clean:
        return "diff --git a/app.py b/app.py\n+ modified line with DIFF_TOKEN_MODIFIED"
    elif "git log" in cmd_clean:
        return "commit abc123def456\nAuthor: dev\n    feat: COMMIT_TOKEN_FEAT99"
    elif "python --version" in cmd_clean:
        return "Python 3.14.0 (PYTHON_TOKEN_314)"
    elif "python -m unittest" in cmd_clean:
        return "Ran 25 tests in 0.5s\nOK (TEST_TOKEN_ALL_PASS)"
    return f"[Shell Output] Command executed: {command}"

def run_single_benchmark_turn(
    prompt_item: Dict[str, Any],
    coordinator: ReActCoordinator,
    session_id: str,
    model: str = "qwen2.5:3b"
) -> Dict[str, Any]:
    """Menjalankan 1 prompt dalam ReAct loop multi-turn dan mencatat metrik."""
    user_prompt = prompt_item["prompt"]
    expected_tool = prompt_item["expected_tool"]
    expected_arg = prompt_item["expected_arg"]
    unique_token = prompt_item["unique_token"]

    tool_calls_recorded = []
    def trace_cb(evt: str, msg: str):
        if evt == "tool_call":
            tool_calls_recorded.append(msg)

    # Intercept shell jika diperlukan untuk injeksi unique token
    orig_execute_tool = coordinator.execute_tool
    def instrumented_execute_tool(tool_name: str, args: Dict[str, Any], session_id: str = "") -> str:
        if tool_name == "run_shell_command":
            cmd = args.get("command", "")
            return mock_shell_output(cmd)
        return orig_execute_tool(tool_name, args, session_id=session_id)

    coordinator.execute_tool = instrumented_execute_tool

    chunks = []
    final_metrics = {}
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
    finally:
        coordinator.execute_tool = orig_execute_tool

    final_text = "".join(chunks).strip()

    # Analisis hasil
    first_tool_call = None
    first_tool_arg = None
    if tool_calls_recorded:
        # Format string trace: "tool_name({'arg': ...})"
        raw_call = tool_calls_recorded[0]
        paren_idx = raw_call.find("(")
        if paren_idx != -1:
            first_tool_call = raw_call[:paren_idx].strip()
            first_tool_arg = raw_call[paren_idx + 1 : -1].strip()

    # Evaluasi metrik
    is_false_call = False
    is_wrong_tool = False
    is_invalid_arg = False
    is_fidelity_ok = False
    is_success = False

    if expected_tool is None:
        # Prompt Negatif
        if first_tool_call is not None:
            is_false_call = True
        else:
            is_success = True
            is_fidelity_ok = True
    else:
        # Prompt Positif
        if first_tool_call is None:
            # Model tidak memanggil tool padahal harus
            is_wrong_tool = True
        elif first_tool_call != expected_tool:
            is_wrong_tool = True
        else:
            # Tool benar, periksa argumen
            if expected_arg:
                arg_matched = expected_arg.lower() in (first_tool_arg or "").lower()
                if not arg_matched:
                    is_invalid_arg = True
                else:
                    # Argumen benar, cek fidelity jawaban akhir
                    if unique_token:
                        if unique_token.lower() in final_text.lower():
                            is_fidelity_ok = True
                            is_success = True
                        else:
                            # Token unik belum ada di teks akhir
                            is_fidelity_ok = False
                    else:
                        is_fidelity_ok = True
                        is_success = True

    return {
        "id": prompt_item["id"],
        "prompt": user_prompt,
        "expected_tool": expected_tool,
        "called_tool": first_tool_call,
        "called_arg": first_tool_arg,
        "is_false_call": is_false_call,
        "is_wrong_tool": is_wrong_tool,
        "is_invalid_arg": is_invalid_arg,
        "is_fidelity_ok": is_fidelity_ok,
        "is_success": is_success,
        "final_text_preview": final_text[:120].replace("\n", " ")
    }

class TestReActBenchmarkSet2(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_root = Path(self.temp_dir)
        self.db_path = self.workspace_root / "bench_sessions.db"
        self.db = MemoryDatabase(db_path=self.db_path)
        self.session_id = self.db.create_session()
        setup_benchmark_fixtures(self.workspace_root, self.db)
        self.client = OllamaClient()
        self.assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile="")
        self.coordinator = ReActCoordinator(
            client=self.client,
            assembler=self.assembler,
            workspace_root=self.workspace_root,
            db=self.db,
            confirm_func=lambda _: True  # auto-confirm for benchmark
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_run_3_iterations_benchmark(self):
        """Menjalankan 20 prompt multi-turn sebanyak 3 iterasi (runs)."""
        if not self.client.is_ready():
            self.skipTest("Ollama server tidak aktif")

        print("\n" + "=" * 70)
        print("MEMULAI BENCHMARK SET KEDUA (20 PROMPT x 3 RUNS - MULTI-TURN REACT)")
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
        print("HASIL MENTAH PER PROMPT (3 RUNS):")
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
        print("RINGKASAN METRIK AKHIR BENCHMARK SET KEDUA:")
        print(f"Total Uji (20x3)        : {total_tests}")
        print(f"Sukses Ujung-ke-Ujung   : {total_success}/{total_tests} ({acc_pct:.1f}%)")
        print(f"Salah-Panggil Tool (Neg): {total_false_calls}/{total_tests}")
        print(f"Salah Pilih Tool (Wrong): {total_wrong_tool}/{total_tests}")
        print(f"Argumen Salah (Arg Fail): {total_invalid_arg}/{total_tests}")
        print(f"Fidelity Jawaban (Token): {total_fidelity_pass}/{total_tests}")
        print("=" * 70)

        # Assert threshold >= 80% sesuai arahan user
        self.assertGreaterEqual(
            acc_pct, 80.0,
            f"Akurasi benchmark multi-turn ReAct di bawah 80%: {acc_pct:.1f}%"
        )

if __name__ == "__main__":
    unittest.main()
