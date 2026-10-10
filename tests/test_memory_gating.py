import unittest
from enki.core.react_loop import (
    should_enable_memory_tool,
    should_enable_read_file,
    should_enable_run_unittests,
    get_active_tools_for_turn
)

class TestMemoryGating(unittest.TestCase):
    def test_recall_trigger_words(self):
        """Memastikan kata-kata pemicu recall mengaktifkan tool search_memory."""
        triggers = [
            "kemarin kita bahas apa ya?",
            "pernah nggak kita bicarakan server staging?",
            "ingat apa yang aku katakan soal database?",
            "sebelumnya kita sempat diskusikan arsitektur?",
            "tadi kamu bilang apa?",
            "waktu itu kuncinya apa ya?",
            "remember what we discussed earlier?",
            "earlier you mentioned something about cache"
        ]
        for prompt in triggers:
            with self.subTest(prompt=prompt):
                self.assertTrue(
                    should_enable_memory_tool(prompt),
                    f"Prompt pemicu recall gagal mengaktifkan memory tool: {prompt}"
                )

    def test_explicit_memory_keywords(self):
        """Memastikan kata kunci eksplisit memori mengaktifkan tool search_memory."""
        explicit_prompts = [
            "coba cari di catatan lama kita",
            "tolong periksa riwayat obrolan kita",
            "buka log obrolan kemarin",
            "apa yang ada di sesi lampau?",
            "cari info di sesi sebelumnya dong",
            "percakapan lama kita bahas apa?",
            "cek memori kamu soal topik ini"
        ]
        for prompt in explicit_prompts:
            with self.subTest(prompt=prompt):
                self.assertTrue(
                    should_enable_memory_tool(prompt),
                    f"Prompt kata kunci eksplisit gagal mengaktifkan memory tool: {prompt}"
                )

    def test_narrowed_riwayat_and_catatan_gating(self):
        """Memastikan riwayat dan catatan dipersempit agar tidak salah terpicu pada istilah non-percakapan."""
        # 'riwayat perubahan di repo kita' -> tanpa search_memory
        self.assertFalse(should_enable_memory_tool("riwayat perubahan di repo kita"))
        tools1 = get_active_tools_for_turn("bagaimana riwayat perubahan terakhir di repo kita?")
        names1 = [t["function"]["name"] for t in tools1] if tools1 else []
        self.assertNotIn("search_memory", names1)

        # 'buka catatan di ./docs/catatan.txt' -> tanpa search_memory
        self.assertFalse(should_enable_memory_tool("buka catatan di ./docs/catatan.txt"))
        tools2 = get_active_tools_for_turn("buka catatan di ./docs/catatan.txt")
        names2 = [t["function"]["name"] for t in tools2] if tools2 else []
        self.assertNotIn("search_memory", names2)
        self.assertIn("read_file", names2)

        # 'buka riwayat percakapan lama' -> search_memory
        self.assertTrue(should_enable_memory_tool("buka riwayat percakapan lama"))
        tools3 = get_active_tools_for_turn("buka riwayat percakapan lama")
        names3 = [t["function"]["name"] for t in tools3] if tools3 else []
        self.assertIn("search_memory", names3)

    def test_negative_prompts_do_not_enable_memory_tool(self):
        """Memastikan prompt umum/coding/resep/sapaan TIDAK mengaktifkan memory tool."""
        negative_prompts = [
            "halo selamat pagi enki, gimana kabarmu hari ini?",
            "jelaskan dong apa itu konsep recursion dalam pemrograman secara ringkas",
            "siapa pencipta bahasa pemrograman python dan kapan pertama kali dirilis?",
            "buatkan resep singkat cara bikin nasi goreng kampung yang enak dan gurih",
            "menurutmu apa perbedaan mendasar antara framework dan library?",
            "tuliskan contoh fungsi fibonacci rekursif sederhana dalam bahasa python",
            "bagaimana cara menggunakan docker compose?",
            "bisa bantu bikin script web scraping?",
            "coba liat status git kita skrg lg di branch mana",
            "tolong baca file requirements.txt dong"
        ]
        for prompt in negative_prompts:
            with self.subTest(prompt=prompt):
                self.assertFalse(
                    should_enable_memory_tool(prompt),
                    f"Prompt negatif seharusnya TIDAK mengaktifkan memory tool: {prompt}"
                )

    def test_get_active_tools_for_turn_schemas(self):
        """Memastikan tool search_memory dan tools lainnya dikendalikan gating deterministik ketat."""
        # 1. Prompt tanpa kata kunci alat apa pun -> None (tanpa tools sama sekali)
        neg_tools = get_active_tools_for_turn("buatkan resep nasi goreng enak")
        self.assertIsNone(neg_tools)

        # 2. Prompt memori murni tanpa kata kunci workspace -> hanya search_memory
        pos_tools = get_active_tools_for_turn("kemarin kita bahas apa ya?")
        self.assertIsNotNone(pos_tools)
        pos_tool_names = [t["function"]["name"] for t in pos_tools]
        self.assertIn("search_memory", pos_tool_names)
        self.assertNotIn("read_file", pos_tool_names)
        self.assertNotIn("git", pos_tool_names)
        self.assertEqual(len(pos_tools), 1)

        # 3. Prompt gabungan memori + file -> search_memory dan read_file
        combo_tools = get_active_tools_for_turn("kemarin file README.md diubah apa ya?")
        self.assertIsNotNone(combo_tools)
        combo_names = [t["function"]["name"] for t in combo_tools]
        self.assertIn("search_memory", combo_names)
        self.assertIn("read_file", combo_names)

    def test_read_file_gating_positives_and_negatives(self):
        """Memastikan gating read_file tidak terpicu pada istilah umum bergaris-miring dan hanya terpicu path sah."""
        # Kasus Negatif (HARUS False)
        negatives = [
            "dan/atau",
            "input/output",
            "TCP/IP",
            "24/7",
            "client/server",
            "aplikasi ini berjalan 24/7 dengan model client/server serta input/output TCP/IP dan/atau UDP"
        ]
        for prompt in negatives:
            with self.subTest(neg=prompt):
                self.assertFalse(
                    should_enable_read_file(prompt),
                    f"read_file salah terpicu pada frasa negatif: {prompt}"
                )

        # Kasus Positif (HARUS True)
        positives = [
            "tolong baca file konfigurasi ini",
            "bisa periksa berkas catatan proyek?",
            "buka ./settings.py sekarang",
            "lihat ../tests/test_mock.py",
            "baca D:/workspace/project/README.md",
            "periksa isi enki/core/react_loop.py",
            "tampilkan docs/implementation_plan.md",
            "baca file config.json"
        ]
        for prompt in positives:
            with self.subTest(pos=prompt):
                self.assertTrue(
                    should_enable_read_file(prompt),
                    f"read_file gagal terpicu pada frasa positif yang sah: {prompt}"
                )

    def test_run_unittests_gating_positives_and_negatives(self):
        """Memastikan run_unittests tidak terpicu tes non-software dan hanya terpicu pengujian kode/proyek."""
        # Kasus Negatif (HARUS False)
        negatives = [
            "coba tes kamera",
            "cek tes suara",
            "test drive",
            "coba run test kamera",
            "cek tes mikrofon di laptop",
            "mari kita lakukan blood test"
        ]
        for prompt in negatives:
            with self.subTest(neg=prompt):
                self.assertFalse(
                    should_enable_run_unittests(prompt),
                    f"run_unittests salah terpicu pada frasa non-software: {prompt}"
                )

        # Kasus Positif (HARUS True)
        positives = [
            "jalankan unit test sekarang",
            "coba jalanin semua test di proyek ini",
            "tolong run pytest untuk validasi",
            "jalankan tes kode sebelum commit",
            "run seluruh test unit di direktori tests",
            "coba jalankan unittest modul ini"
        ]
        for prompt in positives:
            with self.subTest(pos=prompt):
                self.assertTrue(
                    should_enable_run_unittests(prompt),
                    f"run_unittests gagal terpicu pada frasa pengujian kode yang sah: {prompt}"
                )

if __name__ == "__main__":
    unittest.main()
