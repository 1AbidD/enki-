"""
Unit tests tanpa LLM untuk harness benchmark:
1. Aturan Timeout = Gagal pada evaluator sintetis (is_timeout=True -> is_success, routing_ok, fidelity_ok False).
2. Mekanisme BenchmarkLock (penolakan PID hidup & pengambilalihan PID mati/stale).
3. Klasifikasi kelompok prompt MEM (active trigger, marker, none).
"""

import os
import json
import tempfile
import shutil
import unittest
from pathlib import Path

from tests.benchmark_common import (
    evaluate_benchmark_turn,
    BenchmarkLock,
    classify_mem_prompt
)
from enki.core.react_loop import get_active_tools_for_turn
from tests.test_react_benchmark_set4 import (
    validate_user_prompts_file_integrity,
    TEMPLATE_EMPTY_SHA256
)

class TestBenchmarkHarnessUnit(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_synthetic_record_timeout_forces_failure(self):
        """Memastikan record sintetis dengan is_timeout=True menghasilkan False untuk
        is_success, routing_ok, dan fidelity_ok, baik pada prompt negatif maupun positif.
        """
        # Kasus 1: Prompt Negatif (tanpa tool, jawaban ada, tapi timeout)
        res_neg = evaluate_benchmark_turn(
            expected_tool=None,
            expected_arg=None,
            expected_key=None,
            tool_calls_recorded=[],
            tool_observations_recorded=[],
            final_text="Ini jawaban yang terpotong karena timeout",
            is_timeout=True,
            timeout_reason="http_request_timeout"
        )
        self.assertFalse(res_neg["is_success"], "Prompt negatif yang timeout harus is_success=False!")
        self.assertFalse(res_neg["routing_ok"], "Prompt negatif yang timeout harus routing_ok=False!")
        self.assertFalse(res_neg["fidelity_ok"], "Prompt negatif yang timeout harus fidelity_ok=False!")
        self.assertTrue(res_neg["is_timeout"])

        # Kasus 2: Prompt Positif (tool cocok sempurna, keyword ada, tapi timeout)
        res_pos = evaluate_benchmark_turn(
            expected_tool="read_file",
            expected_arg="README.md",
            expected_key="enki",
            tool_calls_recorded=['read_file({"file_path": "README.md"})'],
            tool_observations_recorded=["# ENKI Local Assistant"],
            final_text="Judul proyek adalah ENKI",
            is_timeout=True,
            timeout_reason="turn_timeout"
        )
        self.assertFalse(res_pos["is_success"], "Prompt positif yang timeout harus is_success=False!")
        self.assertFalse(res_pos["routing_ok"], "Prompt positif yang timeout harus routing_ok=False!")
        self.assertFalse(res_pos["fidelity_ok"], "Prompt positif yang timeout harus fidelity_ok=False!")
        self.assertTrue(res_pos["is_timeout"])

    def test_synthetic_record_normal_success_when_no_timeout(self):
        """Memastikan evaluasi normal menghasilkan True jika is_timeout=False."""
        # Negatif normal
        res_neg = evaluate_benchmark_turn(
            expected_tool=None,
            expected_arg=None,
            expected_key=None,
            tool_calls_recorded=[],
            tool_observations_recorded=[],
            final_text="Halo apa kabar!",
            is_timeout=False
        )
        self.assertTrue(res_neg["is_success"])
        self.assertTrue(res_neg["routing_ok"])
        self.assertTrue(res_neg["fidelity_ok"])

        # Positif normal
        res_pos = evaluate_benchmark_turn(
            expected_tool="read_file",
            expected_arg="README.md",
            expected_key="enki",
            tool_calls_recorded=['read_file({"file_path": "README.md"})'],
            tool_observations_recorded=["# ENKI Local Assistant"],
            final_text="Judul proyek adalah ENKI",
            is_timeout=False
        )
        self.assertTrue(res_pos["is_success"])
        self.assertTrue(res_pos["routing_ok"])
        self.assertTrue(res_pos["fidelity_ok"])

    def test_fidelity_failure_forces_is_success_false(self):
        """Memastikan jika fidelity_ok=False, maka is_success WAJIB False,
        meskipun routing_ok dan valid_args bernilai True (kasus SMOKE_P03 & MEM).
        """
        # Kasus A: Tool cocok tanpa expected_arg (misal run_unittests), tapi expected_key tidak muncul
        res_tool = evaluate_benchmark_turn(
            expected_tool="run_unittests",
            expected_arg=None,
            expected_key="Ran",
            tool_calls_recorded=['run_unittests({})'],
            tool_observations_recorded=["0 tests found"],
            final_text="Tidak ada test yang ditemukan.",
            is_timeout=False
        )
        self.assertTrue(res_tool["routing_ok"], "Routing tool harus True.")
        self.assertTrue(res_tool["valid_args"], "Valid args harus True.")
        self.assertFalse(res_tool["fidelity_ok"], "Fidelity harus False karena keyword 'Ran' tidak ada.")
        self.assertFalse(res_tool["is_success"], "is_success WAJIB False jika fidelity_ok=False!")

        # Kasus B: MEM / Negatif tanpa tool, tapi expected_key tidak muncul di final_text
        res_mem = evaluate_benchmark_turn(
            expected_tool=None,
            expected_arg=None,
            expected_key="debian",
            tool_calls_recorded=[],
            tool_observations_recorded=[],
            final_text="Saya tidak tahu sistem operasinya.",
            is_timeout=False
        )
        self.assertTrue(res_mem["routing_ok"])
        self.assertFalse(res_mem["fidelity_ok"])
        self.assertFalse(res_mem["is_success"], "is_success WAJIB False untuk MEM jika fidelity_ok=False!")

    def test_benchmark_lock_rejects_living_pid(self):
        """Memastikan BenchmarkLock menolak proses jika lock file dipegang PID yang masih hidup."""
        lock_file = self.temp_path / ".test_lock"
        
        # Simulasikan lock file dipegang PID saat ini (yang pasti masih hidup)
        my_pid = os.getpid()
        lock_file.write_text(json.dumps({
            "pid": my_pid,
            "time_str": "2026-10-10T12:00:00Z"
        }), encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            with BenchmarkLock(lock_file=lock_file):
                pass
        self.assertIn("Benchmark terkunci oleh proses PID=", str(ctx.exception))

    def test_benchmark_lock_reclaims_stale_dead_pid(self):
        """Memastikan BenchmarkLock mengambil alih lock jika PID di lock file sudah mati."""
        lock_file = self.temp_path / ".test_lock"

        # Gunakan PID buatan yang tidak mungkin hidup (misal 99999999)
        dead_pid = 99999999
        lock_file.write_text(json.dumps({
            "pid": dead_pid,
            "time_str": "2026-10-10T10:00:00Z"
        }), encoding="utf-8")

        # Mencoba acquire lock: harus berhasil mengambil alih tanpa raise error
        with BenchmarkLock(lock_file=lock_file) as lock:
            self.assertTrue(lock_file.exists())
            curr_data = json.loads(lock_file.read_text(encoding="utf-8"))
            self.assertEqual(curr_data["pid"], os.getpid())

        # Setelah keluar dari context manager, file lock harus otomatis terhapus
        self.assertFalse(lock_file.exists())

    def test_classify_mem_prompt_groups(self):
        """Memastikan klasifikasi prompt MEM akurat untuk 3 kelompok."""
        # 1. Active trigger
        p_act1 = "database apa kemarin kita sepakati?"
        p_act2 = "pernah gak kita catat nama port staging?"
        self.assertEqual(classify_mem_prompt(p_act1), "has_active_trigger")
        self.assertEqual(classify_mem_prompt(p_act2), "has_active_trigger")

        # 2. Context marker (tanpa pemicu aktif)
        p_mark1 = "feature flag service aplikasi web kita pakai engine apa?"
        p_mark2 = "backend distributed tracing transaksi di proyekku dikirim ke software apa?"
        self.assertEqual(classify_mem_prompt(p_mark1), "has_marker")
        self.assertEqual(classify_mem_prompt(p_mark2), "has_marker")

        # 3. None (tanpa penanda dan tanpa pemicu aktif)
        p_none1 = "tool pengujian load testing performa endpoint http apa?"
        p_none2 = "migration runner buat update skema database pakai apa?"
        self.assertEqual(classify_mem_prompt(p_none1), "none")
        self.assertEqual(classify_mem_prompt(p_none2), "none")

    def test_confirmed_commands_isolation_per_turn(self):
        """Memastikan confirmed_log direset per turn dan tidak menumpuk ke record turn berikutnya."""
        confirmed_log = []
        def confirm_func(cmd: str) -> bool:
            confirmed_log.append(cmd)
            return True

        records = []
        turn_simulations = [
            ("turn_1", ["python -m unittest discover tests"]),
            ("turn_2", []),  # Turn 2 tidak ada shell execution
            ("turn_3", ["git status"])
        ]

        for turn_id, executed_cmds in turn_simulations:
            confirmed_log.clear()  # Invarian: reset per turn
            for c in executed_cmds:
                confirm_func(c)
            records.append({
                "id": turn_id,
                "confirmed_commands": list(confirmed_log)
            })

        self.assertEqual(records[0]["confirmed_commands"], ["python -m unittest discover tests"])
        self.assertEqual(records[1]["confirmed_commands"], [], "Turn 2 harus kosong, tidak boleh bocor dari Turn 1!")
        self.assertEqual(records[2]["confirmed_commands"], ["git status"], "Turn 3 hanya berisi perintah miliknya!")

    def test_strict_deterministic_tool_gating(self):
        """Memverifikasi gating leksikal deterministik ketat untuk skema alat."""
        # 1. P04: pertanyaan fakta OS server staging -> TANPA tools (None)
        p04 = "apa sistem operasi yang kita pakai di server staging kita?"
        self.assertIsNone(get_active_tools_for_turn(p04), "P04 harus menghasilkan tools=None!")

        # 2. Status proyek tanpa kata 'git' -> TANPA git tool
        p_status = "apa status proyek kita?"
        tools_status = get_active_tools_for_turn(p_status)
        names_status = [t["function"]["name"] for t in (tools_status or [])]
        self.assertNotIn("git", names_status, "'status' tanpa 'git' tidak boleh memicu tool git!")

        # 3. 'isi dompetku berapa' (kata 'isi' tanpa file/ekstensi) -> TANPA read_file
        p_isi = "isi dompetku berapa"
        tools_isi = get_active_tools_for_turn(p_isi)
        names_isi = [t["function"]["name"] for t in (tools_isi or [])]
        self.assertNotIn("read_file", names_isi, "Kata 'isi' saja tidak boleh memicu read_file!")

        # 4. 'test drive mobil' (kata 'test' tanpa aksi run/jalan/coba/cek) -> TANPA unittests
        p_drive = "test drive mobil"
        tools_drive = get_active_tools_for_turn(p_drive)
        names_drive = [t["function"]["name"] for t in (tools_drive or [])]
        self.assertNotIn("run_unittests", names_drive, "'test drive' tidak boleh memicu run_unittests!")

        # 5. 'cek git status' -> memicu git
        p_git = "cek git status"
        tools_git = get_active_tools_for_turn(p_git)
        self.assertIsNotNone(tools_git)
        names_git = [t["function"]["name"] for t in tools_git]
        self.assertIn("git", names_git, "'cek git status' wajib memicu tool git!")

        # 6. 'baca README.md' -> memicu read_file
        p_read = "baca README.md"
        tools_read = get_active_tools_for_turn(p_read)
        self.assertIsNotNone(tools_read)
        names_read = [t["function"]["name"] for t in tools_read]
        self.assertIn("read_file", names_read, "'baca README.md' wajib memicu tool read_file!")

    def test_set4_integrity_validation_failures(self):
        """Memverifikasi runner Set 4 gagal keras pada hash template kosong dan hash tidak cocok."""
        dummy_prompt_file = self.temp_path / "dummy_prompts.txt"
        dummy_prompt_file.write_text("MEM|prompt|fakta|kunci|recall\n", encoding="utf-8")

        # Kasus 1: Kunci konfigurasi adalah template kosong -> GAGAL KERAS
        with self.assertRaises(RuntimeError) as ctx_empty:
            validate_user_prompts_file_integrity(
                dummy_prompt_file,
                expected_sha256=TEMPLATE_EMPTY_SHA256
            )
        self.assertIn("hash template kosong", str(ctx_empty.exception))

        # Kasus 2: Kunci konfigurasi tidak cocok dengan hash file riil -> GAGAL KERAS
        fake_expected_hash = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
        with self.assertRaises(RuntimeError) as ctx_mismatch:
            validate_user_prompts_file_integrity(
                dummy_prompt_file,
                expected_sha256=fake_expected_hash
            )
        self.assertIn("tidak cocok dengan kunci ujian", str(ctx_mismatch.exception))

if __name__ == "__main__":
    unittest.main()
