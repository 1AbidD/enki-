import unittest
from enki.core.react_loop import should_enable_memory_tool, get_active_tools_for_turn

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
            "coba cari di catatan masa lalu",
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
        """Memastikan tool search_memory benar-benar hilang dari daftar schema saat gating bernilai False."""
        neg_tools = get_active_tools_for_turn("buatkan resep nasi goreng enak")
        neg_tool_names = [t["function"]["name"] for t in neg_tools]
        self.assertNotIn("search_memory", neg_tool_names)
        self.assertIn("read_file", neg_tool_names)
        self.assertIn("git", neg_tool_names)
        self.assertIn("python_version", neg_tool_names)
        self.assertIn("run_unittests", neg_tool_names)
        self.assertIn("list_dir", neg_tool_names)
        self.assertEqual(len(neg_tools), 5)

        pos_tools = get_active_tools_for_turn("kemarin kita bahas apa ya?")
        pos_tool_names = [t["function"]["name"] for t in pos_tools]
        self.assertIn("search_memory", pos_tool_names)
        self.assertIn("read_file", pos_tool_names)
        self.assertIn("git", pos_tool_names)
        self.assertIn("python_version", pos_tool_names)
        self.assertIn("run_unittests", pos_tool_names)
        self.assertIn("list_dir", pos_tool_names)
        self.assertEqual(len(pos_tools), 6)

if __name__ == "__main__":
    unittest.main()
