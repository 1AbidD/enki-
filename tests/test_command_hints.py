import unittest
from pathlib import Path
from typing import Set, Dict, Any, List

from enki.cli.command_hints import get_command_hints, COMMAND_HINT_RULES
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT, estimate_tokens_local
from enki.core.react_loop import ReActCoordinator
from enki.memory.database import MemoryDatabase
from unittest.mock import MagicMock

BASELINE_COMMIT_C4FAE3E_TOKENS = 196

class TestCommandHints(unittest.TestCase):
    """Pengujian Kriteria Tahap 3B-1 (H1, H2, H3).
    Semuanya deterministik, tanpa panggilan jaringan / model eksternal (LLM di-mock).
    """

    def setUp(self):
        self.fixtures_path = Path(__file__).parent / "fixtures" / "tahap3b1_hint_sentences.txt"

    def test_h1_fixtures_structure(self):
        """Memverifikasi struktur file fixture: minimal 12 CMD (min 2 per perintah) dan 12 NONE."""
        self.assertTrue(self.fixtures_path.is_file(), f"Fixture tidak ditemukan: {self.fixtures_path}")

        lines = [line.strip() for line in self.fixtures_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        cmd_entries: Dict[str, List[str]] = {}
        none_entries: List[str] = []

        for line in lines:
            parts = [p.strip() for p in line.split("|", 1)]
            self.assertEqual(len(parts), 2, f"Format baris harus 'TAG | kalimat': {line}")
            tag, sentence = parts[0], parts[1]
            if tag == "NONE":
                none_entries.append(sentence)
            else:
                cmd_entries.setdefault(tag, []).append(sentence)

        # Cek jumlah NONE >= 12
        self.assertGreaterEqual(len(none_entries), 12, "Jumlah kalimat NONE harus >= 12")

        # Cek prompt khusus ada di NONE
        joined_none = " ".join(none_entries).lower()
        self.assertIn("overfitting", joined_none)  # S4A_U13
        self.assertIn("ngantuk", joined_none)       # S4A_U14
        self.assertIn("dns", joined_none)           # S4A_U15
        self.assertIn("45 dikali 12", joined_none)  # S4A_P28
        self.assertIn("garbage collection", joined_none) # S4A_P29
        self.assertIn("tembaga murni", joined_none) # S4A_P30

        # Cek ketujuh / keenam perintah tool memiliki >= 2 prompt
        expected_cmds = ["/baca", "/dir", "/git", "/test", "/versi", "/ingat"]
        total_cmd = 0
        for cmd in expected_cmds:
            self.assertIn(cmd, cmd_entries, f"Perintah {cmd} harus ada di fixture CMD")
            count = len(cmd_entries[cmd])
            self.assertGreaterEqual(count, 2, f"Perintah {cmd} harus punya minimal 2 kalimat, ditemukan {count}")
            total_cmd += count

        self.assertGreaterEqual(total_cmd, 12, "Total kalimat CMD harus >= 12")

    def test_h1_command_hints_appearance_and_suppression(self):
        """Memverifikasi petunjuk muncul tepat pada kalimat CMD pertama, tidak muncul pada NONE,
        dan tidak muncul pada pemanggilan kedua dalam sesi yang sama.
        """
        lines = [line.strip() for line in self.fixtures_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        for line in lines:
            tag, sentence = [p.strip() for p in line.split("|", 1)]

            if tag == "NONE":
                # NONE -> 0 petunjuk
                hints = get_command_hints(sentence, seen_commands=set())
                self.assertEqual(len(hints), 0, f"Kalimat NONE menghasilkan petunjuk: {sentence} -> {hints}")
            else:
                # CMD -> tepat petunjuk perintah yang benar muncul pada pemakaian pertama
                fresh_seen: Set[str] = set()
                hints = get_command_hints(sentence, seen_commands=fresh_seen)
                self.assertGreaterEqual(len(hints), 1, f"Kalimat CMD harus menghasilkan petunjuk: {sentence}")
                matched_cmds = [c for c, _ in hints]
                self.assertIn(tag, matched_cmds, f"Petunjuk untuk {tag} tidak ditemukan pada {sentence}: {hints}")

                # Tandai sudah dilihat
                for c, _ in hints:
                    fresh_seen.add(c)

                # Kalimat CMD yang sama diulang di sesi yang sama -> 0 petunjuk kedua
                hints_second = get_command_hints(sentence, seen_commands=fresh_seen)
                matched_second = [c for c, _ in hints_second]
                self.assertNotIn(tag, matched_second, f"Petunjuk {tag} muncul lagi padahal sudah pernah dilihat: {sentence}")

    def test_h1_hints_do_not_execute_tools_and_tools_none(self):
        """Memverifikasi petunjuk tidak pernah mengeksekusi tool dan model tetap dipanggil dengan tools=None."""
        lines = [line.strip() for line in self.fixtures_path.read_text(encoding="utf-8").splitlines() if line.strip()]

        mock_client = MagicMock()
        mock_client.chat.return_value = {
            "content": "Ini jawaban chat biasa tanpa tool.",
            "tool_calls": [
                {
                    "function": {
                        "name": "read_file",
                        "arguments": '{"file_path": "README.md"}'
                    }
                }
            ],
            "metrics": {}
        }

        mock_db = MagicMock(spec=MemoryDatabase)
        assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile="")
        coordinator = ReActCoordinator(
            client=mock_client,
            assembler=assembler,
            workspace_root=Path.cwd(),
            db=mock_db
        )

        for line in lines:
            _, sentence = [p.strip() for p in line.split("|", 1)]

            # Dapatkan petunjuk (fungsi murni, tidak mengeksekusi apapun)
            hints = get_command_hints(sentence, seen_commands=set())

            # Jalankan turn obrolan biasa via ReAct coordinator dengan enable_tools=False
            trace_events = []
            def trace_cb(evt: str, msg: str):
                trace_events.append((evt, msg))

            mock_client.reset_mock()
            outputs = list(coordinator.run_react_turn(
                user_input=sentence,
                session_id="dummy_sess",
                history=[],
                model="qwen2.5:3b",
                trace_callback=trace_cb,
                enable_tools=False
            ))

            # 1. Pastikan model selalu dipanggil dengan tools=None
            self.assertEqual(mock_client.chat.call_count, 1)
            call_kwargs = mock_client.chat.call_args[1]
            self.assertIsNone(call_kwargs.get("tools"), f"Model tidak boleh menerima tools: {call_kwargs}")

            # 2. Pastikan tidak ada event eksekusi tool_call atau tool_result di trace
            executed_tools = [e for e in trace_events if e[0] in ("tool_call", "tool_result")]
            self.assertEqual(len(executed_tools), 0, f"Terjadi eksekusi tool pada kalimat: {sentence}")

            # 3. Pastikan teks balasan model digunakan apa adanya
            self.assertEqual(outputs[0][0], "Ini jawaban chat biasa tanpa tool.")

    def test_h2_system_prompt_contains_all_seven_commands(self):
        """H2: Memastikan system prompt dasar yang dirakit memuat ketujuh nama perintah eksplisit."""
        assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT)
        msgs, _ = assembler.assemble_messages("halo")
        sys_prompt_content = msgs[0]["content"]

        all_seven_commands = [
            "/baca",
            "/dir",
            "/git",
            "/test",
            "/versi",
            "/ingat",
            "/simpan"
        ]

        for cmd in all_seven_commands:
            self.assertIn(cmd, sys_prompt_content, f"Perintah {cmd} wajib ada di system prompt dasar")

    def test_h2_token_overhead_within_limit(self):
        """H2: Memastikan tambahan estimasi token pada base system prompt <= 100 token dibanding commit c4fae3e."""
        assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT)
        msgs, _ = assembler.assemble_messages("halo")
        sys_prompt_content = msgs[0]["content"]

        current_tokens = estimate_tokens_local(sys_prompt_content)
        token_overhead = current_tokens - BASELINE_COMMIT_C4FAE3E_TOKENS

        # Verifikasi batas <= 100
        self.assertLessEqual(token_overhead, 100, f"Token overhead {token_overhead} melebihi batas 100 token!")
        self.assertGreater(token_overhead, 0, "Harus ada penambahan token dari kalimat petunjuk perintah")


if __name__ == "__main__":
    unittest.main()
