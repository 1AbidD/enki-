import os
import hashlib
import unittest
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

def get_dir_fingerprint(root_dir: Path) -> dict:
    """Menghitung hash sha256 dan ukuran untuk setiap file dalam direktori."""
    fingerprint = {}
    if not root_dir.exists():
        return fingerprint
    for path in sorted(root_dir.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root_dir)).replace("\\", "/")
            h = hashlib.sha256(path.read_bytes()).hexdigest()
            fingerprint[rel] = (path.stat().st_size, h)
    return fingerprint

class TestDirectoryIsolationIntegrity(unittest.TestCase):
    def test_suites_isolation_runner(self):
        """Menjalankan seluruh 7 suite unit test dan memverifikasi data/ dan logs/ asli 100% tidak tersentuh."""
        before_data = get_dir_fingerprint(DATA_DIR)
        before_logs = get_dir_fingerprint(LOGS_DIR)

        # Jalankan test runner untuk 7 unit test suites
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for mod_name in [
            "tests.test_assembler",
            "tests.test_memory_unit",
            "tests.test_memory_cross_session",
            "tests.test_sanitizer",
            "tests.test_file_tools",
            "tests.test_shell_tools",
            "tests.test_prompt_injection",
            "tests.test_memory_gating",
            "tests.test_policy_rejection",
            "tests.test_passive_recall"
        ]:
            suite.addTests(loader.loadTestsFromName(mod_name))

        runner = unittest.TextTestRunner(verbosity=0)
        result = runner.run(suite)
        self.assertTrue(result.wasSuccessful(), f"Unit test suites mengalami kegagalan: {result.errors + result.failures}")

        after_data = get_dir_fingerprint(DATA_DIR)
        after_logs = get_dir_fingerprint(LOGS_DIR)

        diff_data_added = set(after_data.keys()) - set(before_data.keys())
        diff_data_removed = set(before_data.keys()) - set(after_data.keys())
        diff_data_modified = [k for k in before_data if k in after_data and before_data[k] != after_data[k]]

        self.assertEqual(diff_data_added, set(), f"Kebocoran isolasi terdeteksi di data/: {diff_data_added}")
        self.assertEqual(diff_data_removed, set(), f"File di data/ terhapus: {diff_data_removed}")
        self.assertEqual(diff_data_modified, [], f"File di data/ termodifikasi: {diff_data_modified}")

        diff_logs_added = set(after_logs.keys()) - set(before_logs.keys())
        diff_logs_removed = set(before_logs.keys()) - set(after_logs.keys())
        diff_logs_modified = [k for k in before_logs if k in after_logs and before_logs[k] != after_logs[k]]

        self.assertEqual(diff_logs_added, set(), f"Kebocoran isolasi terdeteksi di logs/: {diff_logs_added}")
        self.assertEqual(diff_logs_removed, set(), f"File di logs/ terhapus: {diff_logs_removed}")
        self.assertEqual(diff_logs_modified, [], f"File di logs/ termodifikasi: {diff_logs_modified}")

    def test_negative_proof_detects_deliberate_leak(self):
        """Uji negatif: Membuktikan bahwa verifikator hash PASTI mendeteksi file bocor jika ada file ditulis ke data/ asli."""
        baseline_data = get_dir_fingerprint(DATA_DIR)

        leak_file = DATA_DIR / "leak_proof_dummy.log"
        try:
            # Sengaja tulis file bocor ke data/ asli
            leak_file.write_text("kebocoran isolasi yang disengaja", encoding="utf-8")

            # Periksa apakah terdeteksi
            current_data = get_dir_fingerprint(DATA_DIR)
            diff_added = set(current_data.keys()) - set(baseline_data.keys())

            # Assert bahwa deteksi kebocoran berhasil menangkap file persis
            self.assertIn("leak_proof_dummy.log", diff_added, "Verifikator hash GAGAL mendeteksi file yang sengaja dibocorkan!")
        finally:
            # Bersihkan kembali seketika
            if leak_file.exists():
                leak_file.unlink()

        # Verifikasi data/ kembali bersih sempurna
        restored_data = get_dir_fingerprint(DATA_DIR)
        self.assertEqual(baseline_data, restored_data, "Direktori data/ gagal dipulihkan setelah uji negatif.")

if __name__ == "__main__":
    unittest.main()
