"""Unit test untuk Router Kemampuan Deterministik (Tahap 4D-v3, Kriteria Q5 & Q6)."""

import io
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from enki.cli.command_registry import COMMAND_REGISTRY, CommandDef
from enki.core.capability_router import (
    route_capability_inquiry,
    format_list_commands_reply,
    RouteResult
)

class TestCapabilityRouterIntents(unittest.TestCase):
    def setUp(self):
        # 42 kasus independen milik agen sendiri (3 positif dan 3 negatif per intent, BUKAN dari held-out)
        self.cases = [
            # 1. LIST_COMMANDS
            ("U_CMD_P1", "apa saja kemampuan yang kamu miliki?", "LIST_COMMANDS"),
            ("U_CMD_P2", "tampilkan list command yang ada", "LIST_COMMANDS"),
            ("U_CMD_P3", "bisa tolong sebutkan daftar perintah di sini?", "LIST_COMMANDS"),
            ("U_CMD_N1", "perintah git push origin main artinya apa?", None),
            ("U_CMD_N2", "bagaimana cara membuat command line tool di go?", None),
            ("U_CMD_N3", "apa bedanya command dan argument?", None),
            # 2. CAN_INTERNET
            ("U_NET_P1", "apakah kamu terhubung ke internet?", "CAN_INTERNET"),
            ("U_NET_P2", "enki punya akses web luar gak?", "CAN_INTERNET"),
            ("U_NET_P3", "bisa tolong cariin info dari internet?", "CAN_INTERNET"),
            ("U_NET_N1", "apa itu protokol TCP/IP di internet?", None),
            ("U_NET_N2", "bagaimana cara kerja kabel fiber optik internet?", None),
            ("U_NET_N3", "kenapa koneksi internet rumahku lambat?", None),
            # 3. CAN_SCREEN
            ("U_SCR_P1", "apakah kamu bisa melihat layarku?", "CAN_SCREEN"),
            ("U_SCR_P2", "enki sanggup memantau tampilan monitor saya?", "CAN_SCREEN"),
            ("U_SCR_P3", "bisa gak kamu screenshot isi layar?", "CAN_SCREEN"),
            ("U_SCR_N1", "layar monitor komputernya berkedip-kedip kenapa ya?", None),
            ("U_SCR_N2", "rekomendasi monitor layar 24 inch yang bagus", None),
            ("U_SCR_N3", "cara membersihkan debu di layar laptop", None),
            # 4. CAN_READ_FILE
            ("U_FIL_P1", "apakah kamu bisa membaca file lokal?", "CAN_READ_FILE"),
            ("U_FIL_P2", "gimana cara menyuruh kamu membuka dokumen proyek?", "CAN_READ_FILE"),
            ("U_FIL_P3", "enki bisa tolong baca berkas source code?", "CAN_READ_FILE"),
            ("U_FIL_N1", "bagaimana cara membaca file csv di pandas python?", None),
            ("U_FIL_N2", "apa perbedaan berkas biner dan teks?", None),
            ("U_FIL_N3", "aku ingin belajar membaca file pdf dengan library pdfplumber", None),
            # 5. CAN_TEST
            ("U_TST_P1", "apakah kamu bisa menjalankan unit test proyek?", "CAN_TEST"),
            ("U_TST_P2", "gimana cara eksekusi tes otomatis lewat kamu?", "CAN_TEST"),
            ("U_TST_P3", "bisa tolong jalankan test suite di aplikasi ini?", "CAN_TEST"),
            ("U_TST_N1", "apa perbedaan unit test dan integration test dalam software engineering?", None),
            ("U_TST_N2", "bagaimana cara menulis test case yang baik?", None),
            ("U_TST_N3", "besok aku ada tes ujian matematika di kampus", None),
            # 6. CAN_SPEAK
            ("U_SPK_P1", "apakah kamu bisa berbicara dengan suara?", "CAN_SPEAK"),
            ("U_SPK_P2", "enki punya kemampuan suara audio gak?", "CAN_SPEAK"),
            ("U_SPK_P3", "kamu bisa bersuara secara langsung?", "CAN_SPEAK"),
            ("U_SPK_N1", "kenapa suara rekaman mic saya berdengung?", None),
            ("U_SPK_N2", "apa yang dimaksud dengan gelombang suara dalam fisika?", None),
            ("U_SPK_N3", "suara burung di luar jendela merdu sekali", None),
            # 7. CAN_REMEMBER
            ("U_MEM_P1", "apakah kamu bisa mengingat preferensi pengguna?", "CAN_REMEMBER"),
            ("U_MEM_P2", "gimana cara agar kamu menyimpan catatan tentang saya?", "CAN_REMEMBER"),
            ("U_MEM_P3", "enki bisa menyimpan fakta profil saya gak?", "CAN_REMEMBER"),
            ("U_MEM_N1", "aku ingat masa kecil dulu sering main layangan", None),
            ("U_MEM_N2", "apa perbedaan memori RAM dan ROM di komputer?", None),
            ("U_MEM_N3", "cara meningkatkan daya ingat otak saat belajar", None),
        ]

    def test_q6_three_pos_and_three_neg_per_intent(self):
        """Q6: Minimal 3 kasus positif dan 3 negatif per intent (42 kasus independen)."""
        self.assertEqual(len(self.cases), 42)
        for code, prompt, expected in self.cases:
            res = route_capability_inquiry(prompt)
            if expected is None:
                self.assertIsNone(
                    res,
                    f"[{code}] Prompt negatif '{prompt}' seharusnya TIDAK dirouting, tapi terdeteksi {res.intent if res else None}"
                )
            else:
                self.assertIsNotNone(
                    res,
                    f"[{code}] Prompt positif '{prompt}' gagal dirouting (mengembalikan None)!"
                )
                self.assertEqual(
                    res.intent,
                    expected,
                    f"[{code}] Intent mismatch untuk '{prompt}': exp {expected}, got {res.intent}"
                )
                
                # Verifikasi konten balasan sesuai spesifikasi intent
                rep = res.reply.lower()
                if expected == "LIST_COMMANDS":
                    self.assertIn("/baca", rep)
                    self.assertIn("/dir", rep)
                    self.assertIn("/git", rep)
                    self.assertIn("/test", rep)
                    self.assertIn("/versi", rep)
                elif expected in ["CAN_INTERNET", "CAN_SCREEN"]:
                    self.assertTrue(
                        "tidak" in rep or "belum" in rep,
                        f"[{code}] Penolakan tegas harus memuat 'tidak' atau 'belum': {res.reply}"
                    )
                elif expected == "CAN_READ_FILE":
                    self.assertIn("/baca", rep)
                elif expected == "CAN_TEST":
                    self.assertIn("/test", rep)
                elif expected == "CAN_SPEAK":
                    self.assertIn("/unmute", rep)
                    self.assertIn("/mute", rep)
                elif expected == "CAN_REMEMBER":
                    self.assertTrue("/simpan" in rep or "/ingat" in rep)

    def test_q6_all_registered_commands_appear_in_list_commands_reply(self):
        """Q6: Jawaban LIST_COMMANDS wajib memuat setiap perintah di registri (penjaga melenceng)."""
        reply = format_list_commands_reply()
        self.assertGreaterEqual(len(COMMAND_REGISTRY), 17)
        for cmd in COMMAND_REGISTRY.keys():
            self.assertIn(
                cmd,
                reply,
                f"Perintah '{cmd}' ada di COMMAND_REGISTRY tapi tidak muncul di balasan LIST_COMMANDS!"
            )

        # Uji dinamis dengan custom registry
        custom_reg = {
            "/fitur_x": CommandDef("/fitur_x", "fungsi fitur x"),
            "/fitur_y": CommandDef("/fitur_y", "fungsi fitur y"),
        }
        custom_reply = format_list_commands_reply(registry=custom_reg)
        self.assertIn("/fitur_x", custom_reply)
        self.assertIn("/fitur_y", custom_reply)
        self.assertNotIn("/baca", custom_reply)

    def test_q6_overfit_guard_no_dev_or_heldout_literal_in_router_source(self):
        """Q6: Pencegah overfit: tidak boleh ada string prompt DEV atau HELD-OUT-V3 sebagai literal di sumber router."""
        router_path = Path("enki/core/capability_router.py")
        self.assertTrue(router_path.exists())
        src = router_path.read_text(encoding="utf-8").lower()

        # 1. Prompt DEV (K1-K8)
        dev_prompts = [
            "kamu bisa apa aja?",
            "ada perintah apa saja?",
            "bisa akses internet?",
            "bisa lihat layar monitor saya?",
            "bisa baca file di komputer saya?",
            "bisa jalanin unit test?",
            "kamu bisa bicara atau bersuara?",
            "bisa simpan fakta tentang saya ke profil?",
        ]
        for dp in dev_prompts:
            clean_dp = dp.strip().lower()
            self.assertNotIn(
                clean_dp,
                src,
                f"Overfit guard: Prompt DEV '{dp}' ditemukan sebagai literal di {router_path}!"
            )

        # 2. Prompt HELD-OUT-V3 (Positif, Negatif, Regresi)
        heldout_path = Path("docs/heldout_4dv3.json")
        self.assertTrue(heldout_path.exists())
        data = json.loads(heldout_path.read_text(encoding="utf-8"))

        for group in ["positif", "negatif", "regresi_percakapan"]:
            for item in data.get(group, []):
                p = item["prompt"].strip().lower()
                self.assertNotIn(
                    p,
                    src,
                    f"Overfit guard: Prompt HELD-OUT '{p}' ditemukan sebagai literal di {router_path}!"
                )

class TestCapabilityRouterIntegration(unittest.TestCase):
    def test_q5_router_integration_history_db_tts_and_telemetry(self):
        """Q5: Integrasi router: balasan tersimpan di DB & riwayat, melalui TTS mock, dan baris telemetri menyebut router."""
        from enki.cli.console import EnkiConsole

        app = EnkiConsole()
        app.config.set_mute(False)
        app.config._data["tts_engine"] = "sapi5"
        app.check_startup_reflection = MagicMock()

        test_msg = "apakah kamu bisa berbicara dengan suara?"
        app.submit_mid_reply_input(test_msg)
        app.submit_mid_reply_input("/exit")

        out_buf = io.StringIO()
        with patch("sys.stdout", out_buf), patch("enki.cli.console.speak") as mock_speak:
            app.run()

        captured = out_buf.getvalue()

        # 1. Baris telemetri menyebut router (tanpa LLM)
        self.assertIn("router (tanpa LLM)", captured)
        self.assertIn("Intent: CAN_SPEAK", captured)

        # 2. Balasan tersimpan di database sessions.db
        cursor = app.db.conn.cursor()
        cursor.execute("SELECT role, content FROM turns WHERE session_id = ?", (app.session_id,))
        turns = [{"role": row[0], "content": row[1]} for row in cursor.fetchall()]
        user_turns = [t for t in turns if t["role"] == "user"]
        asst_turns = [t for t in turns if t["role"] == "assistant"]

        self.assertGreaterEqual(len(user_turns), 1)
        self.assertGreaterEqual(len(asst_turns), 1)
        self.assertEqual(user_turns[0]["content"], test_msg)
        self.assertIn("/unmute", asst_turns[0]["content"])

        # 3. Balasan tersimpan di riwayat sesi aktif
        self.assertEqual(app.history[0]["role"], "user")
        self.assertEqual(app.history[0]["content"], test_msg)
        self.assertEqual(app.history[1]["role"], "assistant")
        self.assertIn("/unmute", app.history[1]["content"])

        # 4. Melewati jalur TTS (speak terpanggil dengan teks balasan)
        mock_speak.assert_called()
        call_kwargs = mock_speak.call_args[1]
        self.assertIn("/unmute", call_kwargs.get("text", ""))

    def test_q5_router_latency_under_20ms(self):
        """Q5: Latensi router harus <= 20 ms."""
        prompts = [
            "apa saja kemampuan yang kamu miliki?",
            "apakah kamu terhubung ke internet?",
            "apakah kamu bisa melihat layarku?",
            "apakah kamu bisa membaca file lokal?",
            "apakah kamu bisa menjalankan unit test proyek?",
            "apakah kamu bisa berbicara dengan suara?",
            "apakah kamu bisa mengingat preferensi pengguna?",
        ]
        latencies = []
        for _ in range(20):
            for p in prompts:
                t0 = time.perf_counter()
                res = route_capability_inquiry(p)
                dt = (time.perf_counter() - t0) * 1000
                latencies.append(dt)
                self.assertIsNotNone(res)

        latencies.sort()
        median_lat = latencies[len(latencies) // 2]
        self.assertLessEqual(
            median_lat,
            20.0,
            f"Median latensi router ({median_lat:.3f} ms) melampaui batas 20 ms!"
        )

if __name__ == "__main__":
    unittest.main()
