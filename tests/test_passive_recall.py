import unittest
import tempfile
import shutil
from pathlib import Path
from enki.memory.database import MemoryDatabase
from enki.memory.recall import execute_passive_recall

class TestPassiveRecall(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = Path(self.temp_dir).resolve()
        self.db = MemoryDatabase(self.workspace / "test_passive.db")
        self.active_session_id = self.db.create_session()

        # Fixture sesi masa lalu di memori
        old_session = self.db.create_session()
        self.db.add_turn(
            old_session,
            "user",
            "apa database utama proyek kita ya?"
        )
        self.db.add_turn(
            old_session,
            "assistant",
            "Database utama proyek kita adalah SQLite dengan mode WAL dan indeks FTS5."
        )

        old_session_2 = self.db.create_session()
        self.db.add_turn(
            old_session_2,
            "user",
            "bahasa apa yg kita pake buat backend service pembayaran?"
        )
        self.db.add_turn(
            old_session_2,
            "assistant",
            "Untuk service pembayaran, backend kita menggunakan Python FastAPI."
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_a_project_database_prompt_triggers_passive_recall(self):
        """Uji (a): 'apa database utama proyek kita ya?' memunculkan blok recall pasif bila ada turn relevan."""
        prompt = "apa database utama proyek kita ya?"
        block = execute_passive_recall(prompt, self.db, self.active_session_id, max_tokens=300)

        print("\n=== RAW OUTPUT UJI (a) PASSIVE RECALL ===")
        print(block)
        print("=" * 50)

        self.assertIsNotNone(block, "Blok recall pasif seharusnya muncul untuk pertanyaan database proyek.")
        self.assertIn("sqlite", block.lower(), "Blok harus memuat cuplikan SQLite dari memori lampau.")
        self.assertIn("[Konteks Latar dari Percakapan Lama]", block, "Header harus bertuliskan konteks latar pasif.")
        self.assertNotIn("[Instruksi Wajib]: Jawab berdasarkan hasil pencarian di atas", block, "Recall pasif TIDAK boleh memiliki grounding keras.")

    def test_b_generic_prompts_do_not_trigger_passive_recall(self):
        """Uji (b): Pesan umum ('siapa pencipta python', 'resep nasi goreng') TIDAK memunculkan blok
        walaupun database memori menyimpan turn tentang backend/Python.
        """
        generic_prompts = [
            "siapa pencipta bahasa pemrograman python dan kapan pertama kali dirilis?",
            "buatkan resep singkat cara bikin nasi goreng kampung yang enak dan gurih",
            "halo selamat pagi enki, gimana kabarmu hari ini?",
            "apa beda framework dan library dalam pemrograman web?"
        ]

        print("\n=== RAW OUTPUT UJI (b) PASSIVE RECALL (HARUS NONE) ===")
        for p in generic_prompts:
            block = execute_passive_recall(p, self.db, self.active_session_id, max_tokens=300)
            print(f"Prompt: '{p}' -> Result: {block}")
            self.assertIsNone(
                block,
                f"Prompt umum '{p}' seharusnya TIDAK memicu recall pasif!"
            )
        print("=" * 50)

    def test_r3_strict_mechanisms(self):
        """Tes unit tanpa LLM untuk aturan resmi R3_strict:
        1. Negatif berpenanda yang berbagi >= 2 token umum (high DF) TIDAK MEMICU.
        2. Positif berpenanda dengan token ber-DF rendah MEMICU.
        3. Positif tanpa penanda TIDAK MEMICU (keterbatasan terdokumentasi).
        """
        db_path = self.workspace / "test_r3_strict_mechanisms.db"
        test_db = MemoryDatabase(db_path)
        active_sess = test_db.create_session()

        # Buat 25 turn background sehingga total turn >= 25 dan token umum memiliki DF > 5%
        for i in range(1, 11):
            s = test_db.create_session()
            test_db.add_turn(s, "user", f"bagaimana koding fungsi python dan struktur kode aplikasi {i}?")
            test_db.add_turn(s, "assistant", f"koding fungsi python aplikasi {i} harus terstruktur dan bersih.")

        # Buat 1 sesi memori unik (DF rendah)
        s_mem = test_db.create_session()
        test_db.add_turn(s_mem, "user", "analitik data gudang kita memutuskan pakai software apa?")
        test_db.add_turn(s_mem, "assistant", "Untuk analitik data gudang kita memutuskan memakai embedded DuckDB.")

        # 1. Negatif berpenanda yang berbagi >= 2 token umum (koding, fungsi, python - semua DF > 5%)
        neg_prompt = "gimana cara koding fungsi python yang rapi di aplikasi kita?"
        neg_res = execute_passive_recall(neg_prompt, test_db, active_sess, df_threshold_pct=5.0)
        self.assertIsNone(
            neg_res,
            "Negatif berpenanda dengan token ber-DF tinggi seharusnya TIDAK memicu recall pasif!"
        )

        # 2. Positif berpenanda (memiliki marker 'kita' dan token DF rendah 'gudang' / 'duckdb')
        pos_marker_prompt = "analitik data gudang kita kemarin pakai software apa ya?"
        pos_marker_res = execute_passive_recall(pos_marker_prompt, test_db, active_sess, df_threshold_pct=5.0)
        self.assertIsNotNone(
            pos_marker_res,
            "Positif berpenanda dengan token ber-DF rendah seharusnya memicu recall pasif!"
        )
        self.assertIn("gudang", pos_marker_res.lower())

        # 3. Positif tanpa penanda (tidak ada penanda konteks: keterbatasan terdokumentasi)
        pos_no_marker_prompt = "database analitik pemrosesan data gudang apa?"
        pos_no_marker_res = execute_passive_recall(pos_no_marker_prompt, test_db, active_sess, df_threshold_pct=5.0)
        self.assertIsNone(
            pos_no_marker_res,
            "Positif tanpa penanda seharusnya TIDAK memicu (keterbatasan tercatat dari aturan berbasis marker)."
        )

        test_db.close()

if __name__ == "__main__":
    unittest.main()
