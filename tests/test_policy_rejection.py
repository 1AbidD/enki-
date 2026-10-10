import unittest
import tempfile
import shutil
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

from enki.core.react_loop import ReActCoordinator
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.memory.database import MemoryDatabase

class MockOllamaClient:
    """Mock client untuk menguji alur ReAct loop tanpa LLM."""
    def __init__(self, step_responses: List[Dict[str, Any]]):
        self.step_responses = step_responses
        self.call_history = []
        self.call_index = 0

    def chat(self, model: str, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        self.call_history.append({
            "model": model,
            "messages_count": len(messages),
            "tools": tools,
            "has_tools": tools is not None
        })
        if self.call_index < len(self.step_responses):
            resp = self.step_responses[self.call_index]
            self.call_index += 1
            return resp
        # Default response if out of steps
        return {"content": "Jawaban default selesai.", "tool_calls": None}

    def chat_stream(self, model: str, messages: List[Dict[str, Any]], **kwargs):
        yield "Jawaban fallback stream selesai.", {"prompt_eval_count": 10, "eval_count": 5}

class TestPolicyRejectionHandling(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir).resolve()
        self.db = MemoryDatabase(self.workspace / "test.db")
        self.session_id = self.db.create_session()
        self.assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile="")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_policy_rejection_sends_next_iteration_without_tools(self):
        """Memastikan penolakan kebijakan (path traversal di luar workspace) menyebabkan
        request berikutnya dikirim TANPA tools (tools=None) dan menghasilkan teks.
        """
        step1_response = {
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "read_file",
                    "arguments": json.dumps({"file_path": "../outside.txt"})
                }
            }]
        }
        step2_response = {
            "content": "Maaf, akses ke luar workspace ditolak.",
            "tool_calls": None
        }

        mock_client = MockOllamaClient([step1_response, step2_response])
        coordinator = ReActCoordinator(
            client=mock_client,
            assembler=self.assembler,
            workspace_root=self.workspace,
            db=self.db,
            max_iterations=5
        )

        chunks = []
        for chunk, _ in coordinator.run_react_turn("baca file di luar", self.session_id, history=[]):
            if chunk:
                chunks.append(chunk)

        ans = "".join(chunks)
        self.assertEqual(ans, "Maaf, akses ke luar workspace ditolak.")
        self.assertEqual(len(mock_client.call_history), 2)

        # Iterasi 1: Memiliki tools aktif
        self.assertTrue(mock_client.call_history[0]["has_tools"])
        # Iterasi 2: Dikirim TANPA tools (tools=None)
        self.assertFalse(mock_client.call_history[1]["has_tools"])
        self.assertIsNone(mock_client.call_history[1]["tools"])

    def test_unregistered_tool_triggers_policy_rejection_without_tools(self):
        """Memastikan tool yang tidak terdaftar memicu penolakan kebijakan dan iterasi berikutnya tanpa tools."""
        step1_response = {
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "run_shell_command",  # Tool yang sudah dihapus
                    "arguments": json.dumps({"command": "dir"})
                }
            }]
        }
        step2_response = {
            "content": "Tool run_shell_command tidak diizinkan lagi.",
            "tool_calls": None
        }

        mock_client = MockOllamaClient([step1_response, step2_response])
        coordinator = ReActCoordinator(
            client=mock_client,
            assembler=self.assembler,
            workspace_root=self.workspace,
            db=self.db
        )

        chunks = []
        for chunk, _ in coordinator.run_react_turn("jalankan dir", self.session_id, history=[]):
            if chunk:
                chunks.append(chunk)

        ans = "".join(chunks)
        self.assertEqual(ans, "Tool run_shell_command tidak diizinkan lagi.")
        self.assertEqual(len(mock_client.call_history), 2)
        self.assertFalse(mock_client.call_history[1]["has_tools"])

    def test_invalid_argument_allows_one_retry_then_disables_tools(self):
        """Memastikan invalid argumen boleh diulang 1 kali, tetapi kegagalan argumen kedua
        langsung mematikan tools (tools=None).
        """
        # Step 1: Argumen salah pertama (missing action)
        step1_response = {
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "git",
                    "arguments": "{}"
                }
            }]
        }
        # Step 2: Retry pertama (masih diperbolehkan tools), tapi LLM masih mengirim argumen salah
        step2_response = {
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "git",
                    "arguments": "{}"
                }
            }]
        }
        # Step 3: Karena retry habis, step 3 WAJIB dipanggil dengan tools=None
        step3_response = {
            "content": "Gagal menjalankan git karena aksi tidak ditentukan.",
            "tool_calls": None
        }

        mock_client = MockOllamaClient([step1_response, step2_response, step3_response])
        coordinator = ReActCoordinator(
            client=mock_client,
            assembler=self.assembler,
            workspace_root=self.workspace,
            db=self.db,
            max_iterations=5
        )

        chunks = []
        for chunk, _ in coordinator.run_react_turn("cek git", self.session_id, history=[]):
            if chunk:
                chunks.append(chunk)

        ans = "".join(chunks)
        self.assertEqual(ans, "Gagal menjalankan git karena aksi tidak ditentukan.")
        self.assertEqual(len(mock_client.call_history), 3)

        # Call 1: Ada tools
        self.assertTrue(mock_client.call_history[0]["has_tools"])
        # Call 2: Retry pertama diperbolehkan ada tools
        self.assertTrue(mock_client.call_history[1]["has_tools"])
        # Call 3: Retry habis -> WAJIB tanpa tools
        self.assertFalse(mock_client.call_history[2]["has_tools"])
        self.assertIsNone(mock_client.call_history[2]["tools"])

if __name__ == "__main__":
    import json
    unittest.main()
