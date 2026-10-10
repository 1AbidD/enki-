"""
Unit test tanpa LLM untuk fungsi runner benchmark ReAct (Set 2, Set 3, Set 4).
Menggunakan MockCoordinator palsu untuk memvalidasi:
1. Respon teks langsung (tanpa tool)
2. Respon dengan satu tool call dan observasi
3. Penanganan timeout (timeout callback & HTTP exception) dengan invariant mutlak:
   is_timeout == True -> is_success == False, routing_ok == False, fidelity_ok == False.
"""

import unittest
import json
from typing import Dict, Any, Optional

from enki.memory.database import MemoryDatabase
from tests.test_react_benchmark_set2 import run_single_benchmark_turn as run_turn_set2
from tests.test_react_benchmark_set3 import run_single_benchmark_turn_set3 as run_turn_set3
from tests.test_react_benchmark_set4 import run_single_benchmark_turn as run_turn_set4


class MockCoordinator:
    def __init__(self, mode: str = "text", tool_call_data: Optional[Dict[str, Any]] = None):
        self.mode = mode
        self.tool_call_data = tool_call_data or {}
        self.execute_tool = self._default_execute_tool

    def _default_execute_tool(self, tool_name: str, args: Dict[str, Any], session_id: str = "") -> str:
        return f"[Tool Result: {tool_name}] dummy output for {json.dumps(args)}"

    def run_react_turn(
        self,
        user_input: str,
        session_id: str,
        history: Any,
        model: str,
        num_ctx: int = 8192,
        temperature: float = 0.2,
        recall_content: Optional[str] = None,
        trace_callback: Any = None
    ):
        if self.mode == "text":
            yield ("Halo, saya Enki siap membantu Anda.", {"eval_count": 8})

        elif self.mode == "tool":
            t_name = self.tool_call_data.get("name", "read_file")
            t_args = self.tool_call_data.get("args", {"file_path": "info.txt"})
            t_output = self.tool_call_data.get("output", "konten file rahasia info")
            ans = self.tool_call_data.get("final_ans", "Berikut isinya: konten file rahasia info")

            if trace_callback:
                trace_callback("tool_call", f"{t_name}({json.dumps(t_args)})")

            obs = self.execute_tool(t_name, t_args, session_id=session_id)
            if trace_callback:
                trace_callback("tool_result", str(obs) or t_output)

            yield (ans, {"eval_count": 15})

        elif self.mode == "timeout_cb":
            if trace_callback:
                trace_callback("timeout", "ReAct loop melebihi batas waktu (120s). Menghentikan iterasi.")
            return

        elif self.mode == "timeout_exception":
            raise TimeoutError("HTTP request timed out after 120s waiting for LLM")

        elif self.mode == "timeout_string_exception":
            raise RuntimeError("Connection timed out reading response")


class TestReactMockRunnersUnit(unittest.TestCase):
    def setUp(self):
        self.db = MemoryDatabase(":memory:")

    def tearDown(self):
        self.db.close()

    # =========================================================================
    # SET 2 TESTS
    # =========================================================================
    def test_set2_text_response(self):
        coord = MockCoordinator(mode="text")
        prompt_item = {
            "id": "S2_NEG_01",
            "prompt": "Halo selamat pagi",
            "expected_tool": None,
            "expected_arg": None,
            "unique_token": None,
            "category": "NEG"
        }
        res = run_turn_set2(prompt_item, coord, session_id="test_set2_txt")

        self.assertEqual(res["tool_calls"], [])
        self.assertEqual(res["tool_observations"], [])
        self.assertIn("Halo, saya Enki", res["final_response"])
        self.assertTrue(res["evaluation"]["is_success"])
        self.assertTrue(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["is_timeout"])
        self.assertIsNotNone(res["started_at"])
        self.assertIsNotNone(res["ended_at"])

    def test_set2_tool_call_response(self):
        coord = MockCoordinator(
            mode="tool",
            tool_call_data={
                "name": "read_file",
                "args": {"file_path": "info.txt"},
                "output": "konten info penting",
                "final_ans": "Hasil bacaan: konten info penting"
            }
        )
        prompt_item = {
            "id": "S2_TOOL_01",
            "prompt": "baca file info.txt dong",
            "expected_tool": "read_file",
            "expected_arg": "info.txt",
            "unique_token": "konten info",
            "category": "FILE"
        }
        res = run_turn_set2(prompt_item, coord, session_id="test_set2_tool")

        self.assertEqual(len(res["tool_calls"]), 1)
        self.assertIn("read_file", res["tool_calls"][0])
        self.assertEqual(len(res["tool_observations"]), 1)
        self.assertTrue(res["evaluation"]["routing_ok"])
        self.assertTrue(res["evaluation"]["fidelity_ok"])
        self.assertTrue(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["is_timeout"])

    def test_set2_timeout_callback_fails(self):
        coord = MockCoordinator(mode="timeout_cb")
        prompt_item = {
            "id": "S2_TO_01",
            "prompt": "prompt yang macet",
            "expected_tool": None,
            "expected_arg": None,
            "unique_token": None,
            "category": "NEG"
        }
        res = run_turn_set2(prompt_item, coord, session_id="test_set2_to1")

        self.assertTrue(res["evaluation"]["is_timeout"])
        self.assertFalse(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["fidelity_ok"])

    def test_set2_timeout_exception_fails(self):
        coord = MockCoordinator(mode="timeout_string_exception")
        prompt_item = {
            "id": "S2_TO_02",
            "prompt": "prompt timeout exception",
            "expected_tool": None,
            "expected_arg": None,
            "unique_token": None,
            "category": "NEG"
        }
        res = run_turn_set2(prompt_item, coord, session_id="test_set2_to2")

        self.assertTrue(res["evaluation"]["is_timeout"])
        self.assertFalse(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["fidelity_ok"])

    # =========================================================================
    # SET 3 TESTS
    # =========================================================================
    def test_set3_text_response(self):
        coord = MockCoordinator(mode="text")
        prompt_item = {
            "id": "S3_NEG_01",
            "prompt": "Halo apa kabar",
            "expected_tool": None,
            "expected_arg": None,
            "unique_token": None,
            "category": "NEG"
        }
        res = run_turn_set3(prompt_item, coord, session_id="test_set3_txt")

        self.assertEqual(res["tool_calls"], [])
        self.assertEqual(res["tool_observations"], [])
        self.assertIn("Halo, saya Enki", res["final_response"])
        self.assertTrue(res["evaluation"]["is_success"])
        self.assertTrue(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["is_timeout"])
        self.assertIsNotNone(res["started_at"])
        self.assertIsNotNone(res["ended_at"])

    def test_set3_tool_call_response(self):
        coord = MockCoordinator(
            mode="tool",
            tool_call_data={
                "name": "read_file",
                "args": {"file_path": "README.md"},
                "output": "dokumentasi proyek penting",
                "final_ans": "Isi dokumen: dokumentasi proyek penting"
            }
        )
        prompt_item = {
            "id": "S3_TOOL_01",
            "prompt": "baca README.md",
            "expected_tool": "read_file",
            "expected_arg": "README.md",
            "unique_token": "dokumentasi",
            "category": "FILE"
        }
        res = run_turn_set3(prompt_item, coord, session_id="test_set3_tool")

        self.assertEqual(len(res["tool_calls"]), 1)
        self.assertIn("read_file", res["tool_calls"][0])
        self.assertEqual(len(res["tool_observations"]), 1)
        self.assertTrue(res["evaluation"]["routing_ok"])
        self.assertTrue(res["evaluation"]["fidelity_ok"])
        self.assertTrue(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["is_timeout"])

    def test_set3_timeout_callback_fails(self):
        coord = MockCoordinator(mode="timeout_cb")
        prompt_item = {
            "id": "S3_TO_01",
            "prompt": "prompt macet set3",
            "expected_tool": None,
            "expected_arg": None,
            "unique_token": None,
            "category": "NEG"
        }
        res = run_turn_set3(prompt_item, coord, session_id="test_set3_to1")

        self.assertTrue(res["evaluation"]["is_timeout"])
        self.assertFalse(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["fidelity_ok"])

    def test_set3_timeout_exception_fails(self):
        coord = MockCoordinator(mode="timeout_string_exception")
        prompt_item = {
            "id": "S3_TO_02",
            "prompt": "prompt timeout exception set3",
            "expected_tool": None,
            "expected_arg": None,
            "unique_token": None,
            "category": "NEG"
        }
        res = run_turn_set3(prompt_item, coord, session_id="test_set3_to2")

        self.assertTrue(res["evaluation"]["is_timeout"])
        self.assertFalse(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["fidelity_ok"])

    # =========================================================================
    # SET 4 TESTS
    # =========================================================================
    def test_set4_text_response(self):
        coord = MockCoordinator(mode="text")
        prompt_item = {
            "id": "S4_NEG_01",
            "prompt": "Berapa hasil satu tambah satu?",
            "expected_tool": None,
            "expected_arg": None,
            "expected_key": None,
            "category": "NEG"
        }
        confirmed_log = []
        res = run_turn_set4(
            prompt_item=prompt_item,
            coordinator=coord,
            session_id="test_set4_txt",
            db=self.db,
            confirmed_commands_log=confirmed_log
        )

        self.assertEqual(res["tool_calls"], [])
        self.assertEqual(res["tool_observations"], [])
        self.assertIn("Halo, saya Enki", res["final_response"])
        self.assertTrue(res["evaluation"]["is_success"])
        self.assertTrue(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["is_timeout"])
        self.assertIsNotNone(res["started_at"])
        self.assertIsNotNone(res["ended_at"])

    def test_set4_tool_call_response(self):
        coord = MockCoordinator(
            mode="tool",
            tool_call_data={
                "name": "git",
                "args": {"action": "status"},
                "output": "branch master clean",
                "final_ans": "Status git: branch master clean"
            }
        )
        prompt_item = {
            "id": "S4_GIT_01",
            "prompt": "cek status git",
            "expected_tool": "git",
            "expected_arg": "status",
            "expected_key": "master",
            "category": "DEV"
        }
        confirmed_log = []
        res = run_turn_set4(
            prompt_item=prompt_item,
            coordinator=coord,
            session_id="test_set4_tool",
            db=self.db,
            confirmed_commands_log=confirmed_log
        )

        self.assertEqual(len(res["tool_calls"]), 1)
        self.assertIn("git", res["tool_calls"][0])
        self.assertEqual(len(res["tool_observations"]), 1)
        self.assertTrue(res["evaluation"]["routing_ok"])
        self.assertTrue(res["evaluation"]["fidelity_ok"])
        self.assertTrue(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["is_timeout"])

    def test_set4_timeout_callback_fails(self):
        coord = MockCoordinator(mode="timeout_cb")
        prompt_item = {
            "id": "S4_TO_01",
            "prompt": "prompt macet set4",
            "expected_tool": None,
            "expected_arg": None,
            "expected_key": None,
            "category": "NEG"
        }
        confirmed_log = []
        res = run_turn_set4(
            prompt_item=prompt_item,
            coordinator=coord,
            session_id="test_set4_to1",
            db=self.db,
            confirmed_commands_log=confirmed_log
        )

        self.assertTrue(res["evaluation"]["is_timeout"])
        self.assertFalse(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["fidelity_ok"])

    def test_set4_timeout_exception_fails(self):
        coord = MockCoordinator(mode="timeout_exception")
        prompt_item = {
            "id": "S4_TO_02",
            "prompt": "prompt timeout exception set4",
            "expected_tool": None,
            "expected_arg": None,
            "expected_key": None,
            "category": "NEG"
        }
        confirmed_log = []
        res = run_turn_set4(
            prompt_item=prompt_item,
            coordinator=coord,
            session_id="test_set4_to2",
            db=self.db,
            confirmed_commands_log=confirmed_log
        )

        self.assertTrue(res["evaluation"]["is_timeout"])
        self.assertFalse(res["evaluation"]["is_success"])
        self.assertFalse(res["evaluation"]["routing_ok"])
        self.assertFalse(res["evaluation"]["fidelity_ok"])


if __name__ == "__main__":
    unittest.main()
