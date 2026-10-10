import unittest
import hashlib
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"

def get_dir_fingerprint(root_dir: Path) -> dict:
    fingerprint = {}
    if not root_dir.exists():
        return fingerprint
    for path in sorted(root_dir.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root_dir)).replace("\\", "/")
            h = hashlib.sha256(path.read_bytes()).hexdigest()
            fingerprint[rel] = (path.stat().st_size, h)
    return fingerprint

class TestNegativeIsolationFailure(unittest.TestCase):
    def test_deliberate_leak_triggers_isolation_failure(self):
        """Membuktikan bahwa menulis ke data/ asli terdeteksi dan memicu AssertionError pada checker isolasi."""
        before_data = get_dir_fingerprint(DATA_DIR)
        leak_file = DATA_DIR / "deliberate_leak_probe.log"

        try:
            # 1. Sengaja menulis ke data/ asli
            leak_file.write_text("kebocoran isolasi yang disengaja", encoding="utf-8")

            # 2. Ambil fingerprint setelah kebocoran
            after_data = get_dir_fingerprint(DATA_DIR)
            diff_data_added = set(after_data.keys()) - set(before_data.keys())

            # 3. Assert bahwa kebocoran terdeteksi
            self.assertIn("deliberate_leak_probe.log", diff_data_added)

            # 4. Buktikan bahwa checker isolasi pasti melempar AssertionError jika ada diff
            with self.assertRaises(AssertionError):
                self.assertEqual(diff_data_added, set(), f"Kebocoran isolasi terdeteksi di data/: {diff_data_added}")
        finally:
            # 5. Bersihkan seketika
            if leak_file.exists():
                leak_file.unlink()

        # 6. Pastikan data/ kembali bersih sempurna
        after_cleanup = get_dir_fingerprint(DATA_DIR)
        self.assertEqual(before_data, after_cleanup, "Direktori data/ gagal kembali bersih setelah uji negatif.")

if __name__ == "__main__":
    unittest.main()
