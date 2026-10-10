import unittest
import tempfile
import shutil
import os
from pathlib import Path
from enki.tools.shell_tools import is_command_allowed, execute_shell_command

class TestShellToolsSandbox(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir).resolve()
        os.environ["ENKI_DATA_DIR"] = str(self.workspace / "data")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        if "ENKI_DATA_DIR" in os.environ:
            del os.environ["ENKI_DATA_DIR"]

    def test_block_chaining_characters(self):
        """Memastikan semua karakter chaining (& | ; < > ^ % $ ` newline) ditolak."""
        bypass_attempts = [
            "git status && del x",
            "git status | more",
            "git status; rm -rf /",
            "dir > output.txt",
            "python --version < input.txt",
            "git status `dir`",
            "echo %PATH%",
            "echo $HOME",
            "git status\ndel file.txt"
        ]

        for cmd in bypass_attempts:
            ok, msg, _ = is_command_allowed(cmd)
            self.assertFalse(ok, f"Perintah chaining harus ditolak: '{cmd}'")
            self.assertIn("karakter rantai berbahaya", msg)

    def test_block_git_c_option(self):
        """Memastikan 'git -c ...' ditolak karena opsi sebelum subcommand dilarang."""
        ok, msg, _ = is_command_allowed("git -c core.editor=calc.exe commit")
        self.assertFalse(ok)
        self.assertIn("tidak diizinkan", msg)

    def test_block_unauthorized_commands(self):
        """Memastikan perintah di luar allowlist ditolak (pip, curl, powershell, etc.)."""
        unauthorized = [
            "pip install requests",
            "curl http://example.com",
            "powershell -c ls",
            "cmd /c calc",
            "bash script.sh",
            "cat document.txt"
        ]

        for cmd in unauthorized:
            ok, msg, _ = is_command_allowed(cmd)
            self.assertFalse(ok, f"Perintah '{cmd}' harus ditolak oleh allowlist")
            self.assertIn("tidak terdaftar dalam allowlist", msg)

    def test_allowed_commands(self):
        """Memastikan perintah allowlist sah diizinkan."""
        valid_commands = [
            "git status",
            "git log -n 3",
            "git diff HEAD~1",
            "python --version",
            "python -m unittest discover tests",
            "dir"
        ]

        for cmd in valid_commands:
            ok, msg, tokens = is_command_allowed(cmd)
            self.assertTrue(ok, f"Perintah '{cmd}' harus diizinkan: {msg}")
            self.assertGreater(len(tokens), 0)

    def test_manual_confirmation_rejection(self):
        """Jika konfirmasi manual ditolak (False), perintah tidak dieksekusi."""
        # Suntikkan fungsi konfirmasi palsu yang mengembalikan False
        rejected_confirmer = lambda cmd: False

        res = execute_shell_command(
            "python --version",
            workspace_root=self.workspace,
            confirm_func=rejected_confirmer
        )
        self.assertIn("dibatalkan oleh pengguna", res)

    def test_manual_confirmation_approval(self):
        """Jika konfirmasi manual disetujui (True), perintah dieksekusi dan dibungkus untrusted tag."""
        approved_confirmer = lambda cmd: True

        res = execute_shell_command(
            "python --version",
            workspace_root=self.workspace,
            confirm_func=approved_confirmer
        )
        self.assertIn("Python", res)
        self.assertTrue(res.startswith('<untrusted_external_content'))
        self.assertTrue(res.endswith('</untrusted_external_content>'))

if __name__ == "__main__":
    unittest.main()
