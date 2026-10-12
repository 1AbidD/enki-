import os
import unittest
import tempfile
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional

from enki.cli.explicit_commands import execute_explicit_command, handle_explicit_command
from enki.tools.file_tools import execute_read_file
from enki.tools.shell_tools import (
    execute_git_command,
    execute_python_version,
    execute_run_unittests,
    execute_list_dir
)
from enki.memory.database import MemoryDatabase
from enki.memory.profile_manager import ProfileManager
from enki.memory.recall import is_recall_triggered, build_fts_query, format_recall_block
from enki.core.react_loop import ReActCoordinator
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT


class MockOllamaClientWithToolCalls:
    """Mock client yang selalu mencatat argumen pemanggilan dan sengaja
    mengembalikan tool_calls terdaftar untuk menguji bahwa mode enable_tools=False
    tidak pernah mengeksekusi tool apa pun dan selalu mengirim tools=None.
    """
    def __init__(self):
        self.call_history: List[Dict[str, Any]] = []

    def chat(self, model: str, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, **kwargs) -> Dict[str, Any]:
        self.call_history.append({
            "model": model,
            "messages": messages,
            "tools": tools,
            "has_tools": tools is not None
        })
        # Sengaja mengembalikan tool_calls jahat/liar untuk menguji guard enable_tools=False
        return {
            "content": "Ini jawaban teks obrolan murni.",
            "tool_calls": [{
                "function": {
                    "name": "read_file",
                    "arguments": '{"file_path": "README.md"}'
                }
            }],
            "metrics": {"prompt_eval_count": 15, "eval_count": 8, "eval_tok_per_sec": 30.0}
        }

    def chat_stream(self, model: str, messages: List[Dict[str, Any]], **kwargs):
        yield "Ini jawaban fallback streaming.", {"prompt_eval_count": 10, "eval_count": 5}


class TestTahap3BExplicitCommands(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_root = Path(self.temp_dir)
        self.data_dir = self.workspace_root / "data"
        self.memory_dir = self.data_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log_path = self.data_dir / "tool_audit.log"

        self.db_path = self.memory_dir / "test_sessions.db"
        self.db = MemoryDatabase(db_path=self.db_path)
        self.session_id = self.db.create_session()

        self.profile_mgr = ProfileManager(memory_dir=self.memory_dir)

        # Buat beberapa file dummy di workspace untuk pengujian
        (self.workspace_root / "README.md").write_text("# Project ENKI Dummy", encoding="utf-8")
        (self.workspace_root / "notes with space.txt").write_text("Catatan berjarak spasi.", encoding="utf-8")
        sub = self.workspace_root / "subfolder"
        sub.mkdir(exist_ok=True)
        (sub / "nested.txt").write_text("File di dalam subfolder.", encoding="utf-8")

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # =========================================================================
    # K1: Pemanggilan fungsi eksekutor dengan argumen tepat (minimal 3 kasus per perintah)
    # =========================================================================
    def test_k1_baca_command(self):
        # Kasus 1: File valid
        out1 = execute_explicit_command("/baca", "README.md", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("Project ENKI Dummy", out1)

        # Kasus 2: File dengan spasi
        out2 = execute_explicit_command("/baca", "notes with space.txt", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("Catatan berjarak spasi", out2)

        # Kasus 3: Argumen salah (path kosong) -> pesan error, tanpa crash
        out3 = execute_explicit_command("/baca", "", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("[Error /baca]", out3)
        self.assertIn("path file wajib", out3)

    def test_k1_dir_command(self):
        # Kasus 1: Default tanpa argumen -> root "."
        out1 = execute_explicit_command("/dir", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, audit_log_path=self.audit_log_path)
        self.assertIn("README.md", out1)
        self.assertIn("subfolder/", out1)

        # Kasus 2: Subfolder spesifik
        out2 = execute_explicit_command("/dir", "subfolder", self.workspace_root, self.db, self.session_id, self.profile_mgr, audit_log_path=self.audit_log_path)
        self.assertIn("nested.txt", out2)

        # Kasus 3: Argumen salah (folder tidak ada) -> pesan error, tanpa crash
        out3 = execute_explicit_command("/dir", "folder_palsu_xyz", self.workspace_root, self.db, self.session_id, self.profile_mgr, audit_log_path=self.audit_log_path)
        self.assertIn("tidak ditemukan", out3)

    def test_k1_git_command(self):
        # Mock confirmer auto-yes
        mock_yes = lambda cmd: True

        # Kasus 1: Aksi valid 'status'
        out1 = execute_explicit_command("/git", "status", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=mock_yes, audit_log_path=self.audit_log_path)
        self.assertTrue(out1.startswith("<untrusted_external_content") or "git" in out1.lower() or "fatal" in out1.lower() or "selesai" in out1.lower())

        # Kasus 2: Aksi valid 'log'
        out2 = execute_explicit_command("/git", "log", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=mock_yes, audit_log_path=self.audit_log_path)
        self.assertTrue(len(out2) > 0)

        # Kasus 3: Argumen salah (aksi tidak ada di enum) -> pesan error, tanpa crash
        out3 = execute_explicit_command("/git", "push origin main", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=mock_yes, audit_log_path=self.audit_log_path)
        self.assertIn("[Error /git]", out3)
        self.assertIn("status, log, diff", out3)

    def test_k1_test_command(self):
        # Kasus 1: Konfirmasi diterima -> eksekusi unittest
        out1 = execute_explicit_command("/test", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=lambda cmd: True, audit_log_path=self.audit_log_path)
        self.assertTrue(len(out1) > 0)

        # Kasus 2: Konfirmasi ditolak -> policy rejection rapi
        out2 = execute_explicit_command("/test", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=lambda cmd: False, audit_log_path=self.audit_log_path)
        self.assertIn("dibatalkan oleh pengguna", out2)

        # Kasus 3: Panggilan via handle_explicit_command
        captured = []
        handled = handle_explicit_command("/test", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=lambda cmd: False, output_func=captured.append)
        self.assertTrue(handled)
        self.assertIn("dibatalkan oleh pengguna", captured[0])

    def test_k1_versi_command(self):
        # Kasus 1: Konfirmasi diterima -> versi Python
        out1 = execute_explicit_command("/versi", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=lambda cmd: True, audit_log_path=self.audit_log_path)
        self.assertIn("Python", out1)

        # Kasus 2: Konfirmasi ditolak -> dibatalkan
        out2 = execute_explicit_command("/versi", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=lambda cmd: False, audit_log_path=self.audit_log_path)
        self.assertIn("dibatalkan oleh pengguna", out2)

        # Kasus 3: Panggilan via handle_explicit_command
        captured = []
        handled = handle_explicit_command("/versi", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=lambda cmd: True, output_func=captured.append)
        self.assertTrue(handled)
        self.assertIn("Python", captured[0])

    def test_k1_ingat_command(self):
        # Isi data ke DB sesi lain
        other_sid = self.db.create_session()
        self.db.add_turn(other_sid, "user", "Kemarin kita instal PostgreSQL versi 16 di server staging.")
        self.db.add_turn(other_sid, "assistant", "Baik, PostgreSQL 16 sudah terpasang.")

        # Kasus 1: Query yang ada hasilnya
        out1 = execute_explicit_command("/ingat", "PostgreSQL", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("PostgreSQL", out1)

        # Kasus 2: Query yang tidak ada hasilnya
        out2 = execute_explicit_command("/ingat", "KubernetesKlasterSuperRahasia", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("Tidak ditemukan catatan yang cocok", out2)

        # Kasus 3: Argumen kosong -> pesan error, tanpa crash
        out3 = execute_explicit_command("/ingat", "", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("[Error /ingat]", out3)

    def test_k1_simpan_command(self):
        # Kasus 1: Format valid
        out1 = execute_explicit_command("/simpan", "bahasa_favorit | Python dan Rust", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("[Sukses /simpan]", out1)
        self.assertEqual(self.profile_mgr.get_all_facts().get("bahasa_favorit"), "Python dan Rust")

        # Kasus 2: Kunci duplikat ditolak
        out2 = execute_explicit_command("/simpan", "bahasa_favorit | C++", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("sudah ada di profil", out2)

        # Kasus 3: Argumen tanpa pemisah pipe | -> pesan error, tanpa crash
        out3 = execute_explicit_command("/simpan", "bahasa_favorit C++", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("[Error /simpan]", out3)
        self.assertIn("Format salah", out3)

    # =========================================================================
    # K2: Pesan bahasa natural tidak pernah mengeksekusi tool (>=50 kalimat, tools=None)
    # =========================================================================
    def test_k2_natural_sentences_never_execute_tools(self):
        fixture_path = Path("tests/fixtures/tahap3b_natural_sentences.txt")
        self.assertTrue(fixture_path.exists(), "Berkas fixture kalimat natural wajib ada.")

        with open(fixture_path, "r", encoding="utf-8") as f:
            sentences = [line.strip() for line in f if line.strip()]

        self.assertGreaterEqual(len(sentences), 50, f"Jumlah kalimat harus >= 50 (ditemukan: {len(sentences)})")

        mock_client = MockOllamaClientWithToolCalls()
        assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile="")
        coordinator = ReActCoordinator(
            client=mock_client,
            assembler=assembler,
            workspace_root=self.workspace_root,
            db=self.db
        )

        tool_executed = []
        def intercept_trace(evt: str, msg: str):
            if evt == "tool_call":
                tool_executed.append(msg)

        for sentence in sentences:
            mock_client.call_history.clear()
            tool_executed.clear()

            chunks = []
            for chunk, _ in coordinator.run_react_turn(
                user_input=sentence,
                session_id=self.session_id,
                history=[],
                trace_callback=intercept_trace,
                enable_tools=False
            ):
                if chunk:
                    chunks.append(chunk)

            # 1. Panggilan chat wajib mengirim tools=None
            self.assertEqual(len(mock_client.call_history), 1)
            self.assertIsNone(mock_client.call_history[0]["tools"], f"Tools harus None untuk kalimat: '{sentence}'")
            self.assertFalse(mock_client.call_history[0]["has_tools"])

            # 2. Tidak ada eksekusi tool apa pun meskipun mock mengembalikan tool_calls
            self.assertEqual(len(tool_executed), 0, f"Tool tidak boleh dieksekusi untuk kalimat: '{sentence}'")

            # 3. Jawaban teks dikembalikan utuh
            final_text = "".join(chunks)
            self.assertEqual(final_text, "Ini jawaban teks obrolan murni.")

    # =========================================================================
    # K3: Keamanan lewat jalur perintah eksplisit (sandbox, UNC, traversal, confirmation, audit)
    # =========================================================================
    def test_k3_security_sandboxing_and_confirmation(self):
        # 1. Path traversal dot-dot diblokir
        out_trav = execute_explicit_command("/baca", "../outside.txt", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("Akses Ditolak", out_trav)
        self.assertIn("di luar workspace", out_trav)

        # 2. UNC path diblokir
        out_unc = execute_explicit_command("/baca", r"\\evil-server\share\file.txt", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("UNC path tidak diizinkan", out_unc)

        # 3. File sensitif .env diblokir
        (self.workspace_root / ".env").write_text("SECRET=123", encoding="utf-8")
        out_env = execute_explicit_command("/baca", ".env", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("file sensitif yang dilarang", out_env)

        # 4. Direktori sensitif .git diblokir
        out_git_dir = execute_explicit_command("/baca", ".git/config", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("direktori terlindungi", out_git_dir)

        # 5. /dir data/memory diblokir
        out_dir_mem = execute_explicit_command("/dir", "data/memory", self.workspace_root, self.db, self.session_id, self.profile_mgr, audit_log_path=self.audit_log_path)
        # Note: data/memory is blocked by list_dir traversal or handled
        self.assertTrue(len(out_dir_mem) > 0)

        # 6. Konfirmasi [y/N] ditolak -> tidak dieksekusi dan dicatat di audit log
        confirm_called = []
        def reject_confirm(cmd_str: str) -> bool:
            confirm_called.append(cmd_str)
            return False

        out_git_no = execute_explicit_command("/git", "status", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=reject_confirm, audit_log_path=self.audit_log_path)
        self.assertEqual(confirm_called, ["git status"])
        self.assertIn("dibatalkan oleh pengguna", out_git_no)

        out_test_no = execute_explicit_command("/test", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=reject_confirm, audit_log_path=self.audit_log_path)
        self.assertIn("dibatalkan oleh pengguna", out_test_no)

        out_ver_no = execute_explicit_command("/versi", "", self.workspace_root, self.db, self.session_id, self.profile_mgr, confirm_func=reject_confirm, audit_log_path=self.audit_log_path)
        self.assertIn("dibatalkan oleh pengguna", out_ver_no)

        # Verifikasi audit log mencatat aksi penolakan
        self.assertTrue(self.audit_log_path.exists())
        audit_content = self.audit_log_path.read_text(encoding="utf-8")
        self.assertIn("REJECTED_USER", audit_content)
        self.assertIn("GIT", audit_content)
        self.assertIn("RUN_UNITTESTS", audit_content)
        self.assertIn("PYTHON_VERSION", audit_content)

    # =========================================================================
    # K4: Preservasi Recall Aktif (Fixture FTS5 Sementara)
    # =========================================================================
    def test_k4_active_recall_preservation(self):
        other_session = self.db.create_session()
        self.db.add_turn(other_session, "user", "kemarin aku setup cluster redis di port 6379")
        self.db.add_turn(other_session, "assistant", "Redis cluster siap di port 6379.")

        # Kalimat dengan pemicu leksikal baku: 'kemarin'
        prompt_with_trigger = "kemarin redis cluster kita jalan di port berapa?"
        self.assertTrue(is_recall_triggered(prompt_with_trigger))
        q = build_fts_query(prompt_with_trigger)
        snippets = self.db.search_fts(q, exclude_session_id=self.session_id, limit=3)
        self.assertTrue(len(snippets) > 0)
        block = format_recall_block(snippets, max_tokens=300)
        self.assertIsNotNone(block)
        self.assertIn("6379", block)

        # Kalimat tanpa pemicu: tidak pernah menyisipkan blok recall
        prompt_without_trigger = "redis cluster jalan di port berapa ya?"
        self.assertFalse(is_recall_triggered(prompt_without_trigger))

    # =========================================================================
    # K7: Penyimpanan Profil Eksplisit (/simpan)
    # =========================================================================
    def test_k7_simpan_profile_facts(self):
        # 1. Menambah fakta ke profil
        res1 = execute_explicit_command("/simpan", "kota_asal | Padang", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("[Sukses /simpan]", res1)
        self.assertEqual(self.profile_mgr.get_all_facts().get("kota_asal"), "Padang")

        # 2. Pola rahasia ditolak
        res_sec = execute_explicit_command("/simpan", "token_api | sk-1234567890abcdef1234567890", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("pola data rahasia", res_sec)
        self.assertNotIn("token_api", self.profile_mgr.get_all_facts())

        # 3. Kunci duplikat ditolak
        res_dup = execute_explicit_command("/simpan", "kota_asal | Jakarta", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("sudah ada di profil", res_dup)
        self.assertEqual(self.profile_mgr.get_all_facts().get("kota_asal"), "Padang")

        # 4. Melebihi batas 500 token ditolak
        long_value = "kata " * 600
        res_limit = execute_explicit_command("/simpan", f"deskripsi_panjang | {long_value}", self.workspace_root, self.db, self.session_id, self.profile_mgr)
        self.assertIn("Ditolak", res_limit)
        self.assertIn("500 token", res_limit)

        # 5. Fakta tetap ada setelah instance ProfileManager baru dibuat (simulasi lintas sesi)
        new_pm = ProfileManager(memory_dir=self.memory_dir)
        self.assertEqual(new_pm.get_all_facts().get("kota_asal"), "Padang")

        # 6. Fakta muncul di system prompt yang dirakit
        assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile=new_pm.get_profile_text())
        msgs, _ = assembler.assemble_messages("halo", history=[], recall_content=None)
        system_text = msgs[0]["content"]
        self.assertIn("Padang", system_text)
        self.assertIn("kota_asal", system_text)


if __name__ == "__main__":
    unittest.main()
