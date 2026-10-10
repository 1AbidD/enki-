import unittest
import tempfile
import shutil
from pathlib import Path
from enki.tools.file_tools import execute_read_file, is_path_safe_to_read

class TestFileToolsSandbox(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir).resolve()

        # Buat beberapa file legal
        self.valid_file = self.workspace / "document.txt"
        self.valid_file.write_text("Ini adalah isi dokumen valid.", encoding="utf-8")

        self.nested_file = self.workspace / "src" / "code.py"
        self.nested_file.parent.mkdir(parents=True, exist_ok=True)
        self.nested_file.write_text("print('hello world')", encoding="utf-8")

        # Buat file sensitif di workspace
        self.env_file = self.workspace / ".env"
        self.env_file.write_text("SECRET_KEY=12345", encoding="utf-8")

        self.pem_file = self.workspace / "cert.pem"
        self.pem_file.write_text("CERT", encoding="utf-8")

        self.rsa_file = self.workspace / "id_rsa"
        self.rsa_file.write_text("KEY", encoding="utf-8")

        # Buat folder terlarang
        self.git_dir = self.workspace / ".git"
        self.git_dir.mkdir(parents=True, exist_ok=True)
        (self.git_dir / "config").write_text("git config", encoding="utf-8")

        self.mem_dir = self.workspace / "data" / "memory"
        self.mem_dir.mkdir(parents=True, exist_ok=True)
        (self.mem_dir / "profile.json").write_text("{}", encoding="utf-8")

        # Buat binary file
        self.bin_file = self.workspace / "binary.bin"
        self.bin_file.write_bytes(b"\x00\x01\x02\x03\x04")

        # Buat file raksasa (> 500 KB)
        self.large_file = self.workspace / "large.txt"
        self.large_file.write_bytes(b"A" * 600_000)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_read_valid_file(self):
        """Membaca file valid di dalam workspace berhasil dan dibungkus tag untrusted."""
        res = execute_read_file("document.txt", self.workspace)
        self.assertIn("Ini adalah isi dokumen valid.", res)
        self.assertTrue(res.startswith('<untrusted_external_content'))
        self.assertTrue(res.endswith('</untrusted_external_content>'))

    def test_read_nested_valid_file(self):
        """Membaca file di subdirektori workspace berhasil."""
        res = execute_read_file("src/code.py", self.workspace)
        self.assertIn("print('hello world')", res)

    def test_block_path_traversal_dot_dot(self):
        """Mencoba path traversal '..' keluar workspace ditolak."""
        ok, msg, _ = is_path_safe_to_read("../other_file.txt", self.workspace)
        self.assertFalse(ok)
        self.assertIn("di luar workspace root", msg)

    def test_block_absolute_path_outside_workspace(self):
        """Mencoba membaca file di luar drive / C:\\Windows ditolak."""
        windows_path = r"C:\Windows\System32\drivers\etc\hosts"
        ok, msg, _ = is_path_safe_to_read(windows_path, self.workspace)
        self.assertFalse(ok)
        self.assertIn("di luar workspace root", msg)

    def test_block_unc_path(self):
        """Mencoba membaca UNC path ditolak keras."""
        unc_path = r"\\192.168.1.1\share\secret.txt"
        ok, msg, _ = is_path_safe_to_read(unc_path, self.workspace)
        self.assertFalse(ok)
        self.assertIn("UNC path", msg)

    def test_block_sensitive_files(self):
        """File .env, *.pem, id_rsa* diblokir meskipun berada di dalam workspace."""
        for f in [".env", "cert.pem", "id_rsa"]:
            ok, msg, _ = is_path_safe_to_read(f, self.workspace)
            self.assertFalse(ok)
            self.assertIn("file sensitif", msg)

    def test_block_protected_directories(self):
        """Folder .git dan data/memory diblokir."""
        ok_git, msg_git, _ = is_path_safe_to_read(".git/config", self.workspace)
        self.assertFalse(ok_git)
        self.assertIn("direktori terlindungi", msg_git)

        ok_mem, msg_mem, _ = is_path_safe_to_read("data/memory/profile.json", self.workspace)
        self.assertFalse(ok_mem)
        self.assertIn("direktori terlindungi", msg_mem)

    def test_block_binary_file(self):
        """File binary dengan null bytes ditolak."""
        res = execute_read_file("binary.bin", self.workspace)
        self.assertIn("binary / non-teks", res)

    def test_block_large_file(self):
        """File melebihi batas 500 KB ditolak."""
        ok, msg, _ = is_path_safe_to_read("large.txt", self.workspace)
        self.assertFalse(ok)
        self.assertIn("melebihi batas maksimal", msg)

if __name__ == "__main__":
    unittest.main()
