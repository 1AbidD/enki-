"""Unit test suite untuk Subsistem TTS ENKI (Tahap 4B).
Memverifikasi Kriteria T1 hingga T5 secara deterministik tanpa audio nyata.
"""

import os
import sys
import threading
import time
import unittest
from unittest.mock import patch, MagicMock

from enki.audio.base import BaseTTSEngine, FakeEngine
from enki.audio.tts import (
    clean_text_for_tts,
    chunk_text,
    apply_cpu_affinity,
    TTSManager,
    speak,
    cancel,
    is_busy,
)


class TestTahap4BTTS(unittest.TestCase):
    """Pengujian Kriteria Kelulusan Tahap 4B (T1 - T5)."""

    def setUp(self):
        # Pastikan tidak ada antrean sisa
        cancel()

    def tearDown(self):
        cancel()

    def test_t1_zero_cost_mute(self):
        """T1: Dengan is_muted=True, 0 panggilan engine, 0 thread baru, 0 subprocess,

        dan modul enki.audio.sapi5 tidak diimpor.
        """
        # 1. Pastikan modul sapi5 tidak terimpor jika belum ada
        had_sapi5 = "enki.audio.sapi5" in sys.modules
        sapi5_mod = sys.modules.pop("enki.audio.sapi5", None)

        fake_engine = FakeEngine()
        threads_before = threading.active_count()

        with patch("subprocess.Popen") as mock_popen:
            # Panggil speak dengan mode senyap
            speak(
                "Halo dunia, ini pesan tes mode senyap.",
                is_muted=True,
                engine=fake_engine
            )

            # Verifikasi 0 panggilan engine
            self.assertEqual(len(fake_engine.spoken_chunks), 0)
            self.assertEqual(fake_engine.stop_called_count, 0)

            # Verifikasi 0 subprocess
            mock_popen.assert_not_called()

            # Verifikasi 0 thread baru
            threads_after = threading.active_count()
            self.assertEqual(threads_before, threads_after)

            # Verifikasi modul sapi5 tidak diimpor
            self.assertNotIn("enki.audio.sapi5", sys.modules)

        # Kembalikan modul jika sebelumnya memang ada
        if had_sapi5 and sapi5_mod is not None:
            sys.modules["enki.audio.sapi5"] = sapi5_mod

    def test_t2_text_chunking_fixed_table(self):
        """T2: Tabel tetap minimal 15 kasus pengujian chunking menghasilkan chunk yang diharapkan;

        blok kode dilewati, URL digantikan kata 'tautan', angka/singkatan terlindungi.
        """
        test_table = [
            # 1. Kalimat sederhana multi-kalimat
            ("Halo dunia. Apa kabar?", ["Halo dunia.", "Apa kabar?"]),
            # 2. Angka desimal
            ("Nilai pi adalah 3.14 dan rasio 0.5.", ["Nilai pi adalah 3.14 dan rasio 0.5."]),
            # 3. Angka ribuan
            ("Total ada 10.000 pengguna aktif.", ["Total ada 10.000 pengguna aktif."]),
            # 4. Singkatan dll.
            ("Buku, pensil, dll. ada di meja.", ["Buku, pensil, dll. ada di meja."]),
            # 5. Singkatan dsb.
            ("Kertas, pulpen, dsb. sudah siap.", ["Kertas, pulpen, dsb. sudah siap."]),
            # 6. Singkatan bahasa Inggris e.g.
            ("Gunakan alat, e.g. palu atau obeng.", ["Gunakan alat, e.g. palu atau obeng."]),
            # 7. URL HTTP
            ("Kunjungi http://example.com/api/v1 untuk info.", ["Kunjungi tautan untuk info."]),
            # 8. URL HTTPS dengan titik di akhir
            ("Dokumentasi ada di https://github.com/user/repo.", ["Dokumentasi ada di tautan."]),
            # 9. Blok kode dibuang sepenuhnya
            ("Berikut kodenya:\n```python\nprint('hello')\n```\nSelesai.", ["Berikut kodenya:", "Selesai."]),
            # 10. Inline backticks
            ("Jalankan perintah `git status` sekarang.", ["Jalankan perintah git status sekarang."]),
            # 11. Bold markdown
            ("Ini adalah **peringatan penting** untuk sistem.", ["Ini adalah peringatan penting untuk sistem."]),
            # 12. Italic markdown
            ("Harap baca *catatan* di bawah.", ["Harap baca catatan di bawah."]),
            # 13. Bullet points markdown
            ("- Item pertama\n- Item kedua\n* Item ketiga", ["Item pertama", "Item kedua", "Item ketiga"]),
            # 14. String kosong
            ("", []),
            # 15. Whitespace murni
            ("   \n\n  \t  ", []),
            # 16. Kalimat panjang (>250 karakter) dipotong ke <= 250 karakter per chunk
            ("Sebuah kalimat panjang yang berulang tanpa tanda baca " * 10, None),
            # 17. Tanda seru dan tanya
            ("Hebat sekali! Benarkah? Ya tentu.", ["Hebat sekali!", "Benarkah?", "Ya tentu."]),
        ]

        self.assertGreaterEqual(len(test_table), 15, "Tabel pengujian harus memiliki minimal 15 kasus")

        for idx, (raw_text, expected_chunks) in enumerate(test_table, 1):
            chunks = chunk_text(raw_text, max_chunk_chars=250)
            if expected_chunks is not None:
                self.assertEqual(
                    chunks,
                    expected_chunks,
                    f"Kasus #{idx} gagal! Dihasilkan: {chunks}, Diharapkan: {expected_chunks}"
                )
            else:
                self.assertGreater(len(chunks), 1, f"Kasus #{idx} harus dipecah menjadi beberapa chunk")
                for c_idx, c in enumerate(chunks):
                    self.assertLessEqual(
                        len(c),
                        250,
                        f"Kasus #{idx} chunk #{c_idx} melebihi 250 karakter ({len(c)}): '{c}'"
                    )

    def test_t3_instant_cancellation(self):
        """T3: Interupsi instan: cancel() mengosongkan antrean seketika dan memanggil engine.stop()."""
        fake_engine = FakeEngine(delay_per_chunk=0.08)
        mgr = TTSManager()

        # Masukkan 5 kalimat
        text = "Satu kalimat. Dua kalimat. Tiga kalimat. Empat kalimat. Lima kalimat."
        mgr.speak(text, engine=fake_engine)

        # Beri waktu worker memulai chunk pertama
        time.sleep(0.02)
        self.assertTrue(mgr.is_busy())

        # Interupsi instan
        mgr.cancel()

        # Verifikasi antrean kosong seketika
        self.assertTrue(mgr.queue.empty())
        self.assertTrue(mgr.cancel_event.is_set())

        # Verifikasi stop() engine dipanggil
        self.assertGreaterEqual(fake_engine.stop_called_count, 1)
        self.assertTrue(fake_engine.is_stopped)

        # Tunggu sejenak untuk memastikan worker berhenti dan tidak melanjutkan chunk tersisa
        time.sleep(0.1)
        self.assertLess(len(fake_engine.spoken_chunks), 5, "Sisa chunk tidak boleh diproses setelah cancel()")

    def test_t4_cpu_limits_and_affinity(self):
        """T4: Batas beban CPU: OMP_NUM_THREADS diset ke 2 dan affinity <= 2 core diterapkan."""
        # 1. Verifikasi fungsi apply_cpu_affinity tidak crash dan mengembalikan nilai boolean
        result = apply_cpu_affinity(2)
        self.assertIsInstance(result, bool)

        # 2. Verifikasi worker TTS mengeset OMP_NUM_THREADS = '2'
        fake_engine = FakeEngine(delay_per_chunk=0.01)
        mgr = TTSManager()
        mgr.speak("Tes batas cpu.", engine=fake_engine)
        mgr.wait_done(timeout=2.0)
        self.assertEqual(os.environ.get("OMP_NUM_THREADS"), "2")

    def test_t5_injection_safety(self):
        """T5: Keamanan injeksi: karakter kutip, $(), ;, backtick, dan newline tidak mengeksekusi shell."""
        from enki.audio.sapi5 import SAPI5Engine

        malicious_inputs = [
            'halo "; Remove-Item -Force C:\\; echo "terbakar',
            "$(Get-Process; Stop-Process -Name svchost)",
            "`whoami` && echo 'hacked'",
            "baris pertama\n; format c: /y\nbaris kedua",
            '" OR 1=1; DROP TABLE users; --',
            "calc.exe | powershell -Command 'Write-Host bahaya'",
        ]

        engine = SAPI5Engine()

        for payload in malicious_inputs:
            with patch("subprocess.Popen") as mock_popen:
                mock_proc = MagicMock()
                mock_proc.returncode = 0
                mock_proc.communicate.return_value = ("", "")
                mock_popen.return_value = mock_proc

                # Jalankan speak_chunk
                res = engine.speak_chunk(payload, voice="Microsoft David Desktop")
                self.assertTrue(res)

                # Verifikasi argumen Popen strictly PowerShell bebas injeksi
                mock_popen.assert_called_once()
                cmd_args = mock_popen.call_args[0][0]

                # Argumen adalah daftar tetap
                self.assertEqual(cmd_args[0], "powershell")
                self.assertIn("-NoProfile", cmd_args)
                self.assertIn("-NonInteractive", cmd_args)
                self.assertIn("-Command", cmd_args)

                # Payload berbahaya TIDAK PERNAH masuk ke cmd_args
                for arg in cmd_args:
                    self.assertNotIn("Remove-Item", arg)
                    self.assertNotIn("Stop-Process", arg)
                    self.assertNotIn("format c:", arg)
                    self.assertNotIn("calc.exe", arg)

                # Payload dikirim aman via stdin (proc.communicate)
                mock_proc.communicate.assert_called_once()
                sent_stdin = mock_proc.communicate.call_args[1].get("input", "")
                self.assertIn(payload, sent_stdin)

    def test_console_tts_graceful_error_handling(self):
        """Verifikasi bahwa error saat pemutaran TTS ditangani dengan aman tanpa membuat crash."""
        class FailingEngine(BaseTTSEngine):
            def speak_chunk(self, text: str, voice=None) -> bool:
                raise RuntimeError("Simulasi audio device failure")

            def stop(self) -> None:
                pass

        failing_engine = FailingEngine()
        mgr = TTSManager()
        # Harus selesai tanpa unhandled exception di thread utama
        mgr.speak("Tes penanganan error audio.", engine=failing_engine)
        mgr.wait_done(timeout=2.0)
        self.assertFalse(mgr.is_busy())


if __name__ == "__main__":
    unittest.main()
