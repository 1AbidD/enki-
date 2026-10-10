import unittest
import tempfile
import shutil
import os
from pathlib import Path
from enki.tools.shell_tools import (
    execute_git_command,
    execute_python_version,
    execute_run_unittests,
    execute_list_dir,
    GIT_VALID_ACTIONS
)

class TestFixedArgumentTools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir).resolve()
        self.data_dir = self.workspace / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.environ["ENKI_DATA_DIR"] = str(self.data_dir)
        self.audit_log = self.data_dir / "tool_audit.log"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if "ENKI_DATA_DIR" in os.environ:
            del os.environ["ENKI_DATA_DIR"]

    def test_git_valid_actions_enum(self):
        """Memastikan hanya action di dalam enum ['status', 'log', 'diff'] yang diizinkan."""
        for act in ["status", "log", "diff"]:
            self.assertIn(act, GIT_VALID_ACTIONS)

        # Uji aksi ilegal yang harus ditolak kebijakan
        illegal_actions = ["checkout", "push", "pull", "clone", "reset", "commit", "rm", "branch", "exec", "status && del x"]
        for act in illegal_actions:
            ok, is_policy_rejection, msg = execute_git_command(
                act,
                workspace_root=self.workspace,
                confirm_func=lambda _: True,
                audit_log_path=self.audit_log
            )
            self.assertFalse(ok)
            self.assertTrue(is_policy_rejection, f"Aksi ilegal '{act}' harus memicu policy rejection")
            self.assertIn("tidak valid", msg)

    def test_git_confirmation_rejection(self):
        """Memastikan konfirmasi pengguna [y/N] yang menolak akan membatalkan git command."""
        recorded_cmds = []
        def mock_reject(cmd: str) -> bool:
            recorded_cmds.append(cmd)
            return False

        ok, is_policy_rejection, msg = execute_git_command(
            "status",
            workspace_root=self.workspace,
            confirm_func=mock_reject,
            audit_log_path=self.audit_log
        )
        self.assertFalse(ok)
        self.assertTrue(is_policy_rejection)
        self.assertIn("dibatalkan oleh pengguna", msg)
        self.assertEqual(recorded_cmds, ["git status"])

    def test_python_version_execution_and_confirmation(self):
        """Memastikan python_version mengeksekusi dengan aman dan meminta konfirmasi."""
        recorded_cmds = []
        def mock_approve(cmd: str) -> bool:
            recorded_cmds.append(cmd)
            return True

        ok, is_policy_rejection, msg = execute_python_version(
            workspace_root=self.workspace,
            confirm_func=mock_approve,
            audit_log_path=self.audit_log
        )
        self.assertTrue(ok)
        self.assertFalse(is_policy_rejection)
        self.assertIn("Python", msg)
        self.assertEqual(recorded_cmds, ["python --version"])

    def test_run_unittests_execution_and_confirmation(self):
        """Memastikan run_unittests mengeksekusi python -m unittest."""
        recorded_cmds = []
        def mock_reject(cmd: str) -> bool:
            recorded_cmds.append(cmd)
            return False

        ok, is_policy_rejection, msg = execute_run_unittests(
            workspace_root=self.workspace,
            confirm_func=mock_reject,
            audit_log_path=self.audit_log
        )
        self.assertFalse(ok)
        self.assertTrue(is_policy_rejection)
        self.assertEqual(recorded_cmds, ["python -m unittest"])

    def test_list_dir_safe_listing_and_traversal_blocking(self):
        """Memastikan list_dir membaca folder workspace dan memblokir path traversal."""
        # Buat beberapa file dummy
        (self.workspace / "file1.txt").write_text("hello", encoding="utf-8")
        sub_dir = self.workspace / "src"
        sub_dir.mkdir()
        (sub_dir / "app.py").write_text("print('hi')", encoding="utf-8")

        # 1. Listing root workspace
        ok, is_policy_rejection, msg = execute_list_dir(".", self.workspace, audit_log_path=self.audit_log)
        self.assertTrue(ok)
        self.assertFalse(is_policy_rejection)
        self.assertIn("[FILE] file1.txt", msg)
        self.assertIn("[DIR]  src/", msg)

        # 2. Listing sub-folder
        ok, is_policy_rejection, msg = execute_list_dir("src", self.workspace, audit_log_path=self.audit_log)
        self.assertTrue(ok)
        self.assertIn("[FILE] app.py", msg)

        # 3. Path traversal di luar workspace: WAJIB ditolak kebijakan
        traversal_attempts = ["../", "../../windows", "C:\\Windows", "/etc"]
        for t in traversal_attempts:
            ok, is_policy_rejection, msg = execute_list_dir(t, self.workspace, audit_log_path=self.audit_log)
            self.assertFalse(ok)
            self.assertTrue(is_policy_rejection, f"Path traversal '{t}' harus memicu policy rejection")
            self.assertIn("di luar direktori kerja", msg)

    def test_no_arbitrary_string_command_execution_pathway(self):
        """Memastikan tidak ada API atau parameter yang menerima string perintah bebas untuk dieksekusi."""
        # Seluruh tool shell menerima argumen tetap (enum/void/path), tidak ada string command execution
        import enki.tools.shell_tools as st
        self.assertFalse(has_wrap := hasattr(st, "execute_shell_command"), "execute_shell_command seharusnya sudah dihapus")
        self.assertTrue(hasattr(st, "execute_git_command"))
        self.assertTrue(hasattr(st, "execute_python_version"))
        self.assertTrue(hasattr(st, "execute_run_unittests"))
        self.assertTrue(hasattr(st, "execute_list_dir"))

if __name__ == "__main__":
    unittest.main()
