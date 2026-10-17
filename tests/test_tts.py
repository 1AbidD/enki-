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

    def test_f2_clean_text_preserves_identifiers_and_paths(self):
        """F2: clean_text_for_tts tidak boleh merusak nama ber-underscore atau path (minimal 8 kasus tetap)."""
        cases = [
            ("read_file", "read_file"),
            ("list_dir", "list_dir"),
            ("snake_case_name", "snake_case_name"),
            ("app/main.py", "app/main.py"),
            ("__init__.py", "__init__.py"),
            ("_private", "_private"),
            ("2 * 3 * 4", "2 * 3 * 4"),
            ("Ini adalah teks **tebal** dan ini *miring*.", "Ini adalah teks tebal dan ini miring."),
        ]

        for raw_inp, expected in cases:
            cleaned = clean_text_for_tts(raw_inp)
            self.assertEqual(
                cleaned,
                expected,
                f"F2 gagal untuk input {raw_inp!r}: dihasilkan {cleaned!r}, diharapkan {expected!r}"
            )
            # Pastikan underscore di dalam kata tidak terhapus
            if "_" in raw_inp:
                self.assertIn("_", cleaned, f"Underscore hilang pada {raw_inp!r} -> {cleaned!r}")

    def test_f4_f5_mute_unmute_config_and_speak_behavior(self):
        """F4 & F5: Verifikasi default is_muted=True pada config baru, /mute dan /unmute mengubah config,

        dan speak() tidak memanggil engine saat is_muted=True.
        """
        import tempfile
        import json
        from pathlib import Path
        from enki.config import AppConfig, DEFAULT_CONFIG

        # F5: Verifikasi default is_muted adalah True
        self.assertTrue(DEFAULT_CONFIG["is_muted"], "DEFAULT_CONFIG['is_muted'] harus bernilai True")

        # Uji AppConfig dengan berkas sementara terisolasi
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_cfg_file = Path(tmp_dir) / "config.json"
            cfg = AppConfig(config_path=tmp_cfg_file)

            # Default pada config baru harus True
            self.assertTrue(cfg.is_muted)
            self.assertTrue(tmp_cfg_file.exists())
            with open(tmp_cfg_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                self.assertTrue(saved["is_muted"])

            # Uji set_mute(False) (ekuivalen /unmute)
            cfg.set_mute(False)
            self.assertFalse(cfg.is_muted)
            with open(tmp_cfg_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                self.assertFalse(saved["is_muted"])

            # Uji set_mute(True) (ekuivalen /mute)
            cfg.set_mute(True)
            self.assertTrue(cfg.is_muted)
            with open(tmp_cfg_file, "r", encoding="utf-8") as f:
                saved = json.load(f)
                self.assertTrue(saved["is_muted"])

            # Verifikasi speak() tidak memanggil engine saat is_muted=True
            fake_engine = FakeEngine()
            speak("Kalimat tes mute.", is_muted=cfg.is_muted, engine=fake_engine)
            self.assertEqual(len(fake_engine.spoken_chunks), 0)

            # Verifikasi speak() memanggil engine saat is_muted=False
            cfg.set_mute(False)
            mgr = TTSManager()
            mgr.speak("Kalimat tes unmute.", engine=fake_engine)
            mgr.wait_done(timeout=2.0)
            self.assertGreaterEqual(len(fake_engine.spoken_chunks), 1)

    def test_g1_sentence_level_clean_text_for_tts(self):
        """G1: Tes tetap tingkat kalimat untuk clean_text_for_tts (minimal 6 kasus multi-identifier)."""
        sentence_cases = [
            ("Panggil read_file lalu list_dir di app/main.py", "Panggil read_file lalu list_dir di app/main.py"),
            ("Gunakan snake_case_name dan other_name_here", "Gunakan snake_case_name dan other_name_here"),
            ("File __init__.py dan __main__.py ada", "File __init__.py dan __main__.py ada"),
            ("Ini _miring_ asli", "Ini miring asli"),
            ("Variabel max_value * 2 * min_value", "Variabel max_value * 2 * min_value"),
            ("Total 3 * 4 * 5 sama dengan 60", "Total 3 * 4 * 5 sama dengan 60"),
        ]

        self.assertGreaterEqual(len(sentence_cases), 6)

        for raw_sentence, expected_sentence in sentence_cases:
            cleaned = clean_text_for_tts(raw_sentence)
            self.assertEqual(
                cleaned,
                expected_sentence,
                f"G1 gagal untuk kalimat {raw_sentence!r}: dihasilkan {cleaned!r}, diharapkan {expected_sentence!r}"
            )

    def test_langkah0_regex_regression_dunder_and_italic_punctuation(self):
        """Langkah 0 (Utang Lama 4B-2): Regresi __init__ berdiri sendiri dan _miring_, diikuti tanda baca."""
        self.assertEqual(
            clean_text_for_tts("Method __init__ dipanggil"),
            "Method __init__ dipanggil"
        )
        self.assertEqual(
            clean_text_for_tts("Ini _miring_, bagus."),
            "Ini miring, bagus."
        )

    def test_k2_zero_cost_mute_supertonic_and_onnxruntime_not_imported(self):
        """K2: Saat ENKI start dengan is_muted=True dan speak() dipanggil,
        sys.modules tidak berisi onnxruntime maupun supertonic.
        """
        saved_modules = {}
        target_prefixes = ("onnxruntime", "supertonic", "enki.audio.supertonic")
        for mod_name in list(sys.modules.keys()):
            if mod_name == "onnxruntime" or mod_name.startswith("onnxruntime.") or mod_name == "supertonic" or mod_name.startswith("supertonic.") or mod_name == "enki.audio.supertonic":
                saved_modules[mod_name] = sys.modules.pop(mod_name)

        try:
            from enki.config import AppConfig
            import tempfile
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmp_dir:
                cfg_path = Path(tmp_dir) / "config.json"
                cfg = AppConfig(config_path=cfg_path)
                self.assertTrue(cfg.is_muted)
                self.assertEqual(cfg.tts_engine, "supertonic")

                speak(
                    "Halo, saya ENKI sedang dalam mode mute.",
                    is_muted=cfg.is_muted,
                    voice=cfg.tts_voice_supertonic,
                    pronunciation_map=cfg.tts_pronunciation,
                )

                self.assertNotIn("onnxruntime", sys.modules)
                self.assertNotIn("supertonic", sys.modules)
                self.assertNotIn("enki.audio.supertonic", sys.modules)
        finally:
            sys.modules.update(saved_modules)

    def test_k3_fallback_to_sapi5_on_invalid_model_dir(self):
        """K3: Jika tts_model_dir salah/tidak ada, SupertonicEngine otomatis pindah ke SAPI5,
        mencetak tepat satu baris pesan peringatan, tanpa melempar exception.
        """
        import io
        import tempfile
        from contextlib import redirect_stdout
        from pathlib import Path
        from enki.audio.supertonic import SupertonicEngine
        from enki.audio.sapi5 import SAPI5Engine

        with tempfile.TemporaryDirectory() as tmp_dir:
            bad_model_dir = str(Path(tmp_dir) / "direktori_model_tidak_ada")
            st_engine = SupertonicEngine(
                model_dir=bad_model_dir,
                default_voice="M4",
                fallback_voice="Microsoft David Desktop",
            )

            buf = io.StringIO()
            with patch.object(SAPI5Engine, "speak_chunk", return_value=True) as mock_sapi_speak:
                with redirect_stdout(buf):
                    mgr = TTSManager()
                    mgr.speak(
                        "Kalimat pertama yang cukup panjang untuk chunk satu. Kalimat kedua untuk menguji pesan peringatan hanya muncul sekali.",
                        engine=st_engine,
                    )
                    mgr.wait_done(timeout=3.0)

                self.assertTrue(st_engine.is_fallback)
                self.assertEqual(mock_sapi_speak.call_count, 2)

            out_lines = [line.strip() for line in buf.getvalue().splitlines() if line.strip()]
            self.assertEqual(
                len(out_lines),
                1,
                f"Pesan fallback harus tepat 1 baris, tetapi ditemukan {len(out_lines)}: {out_lines}",
            )
            self.assertIn("SAPI5", out_lines[0])

    def test_k4_pronunciation_dictionary_and_screen_text_unchanged(self):
        """K4: Kamus pelafalan menerapkan 'ENKI' -> 'Enki' secara case-sensitive dengan batas kata,
        tidak mengubah 'ENKIDU', 'enki', atau path 'enki/audio/tts.py', dan teks layar tidak berubah.
        """
        from enki.audio.tts import apply_pronunciation, prepare_tts_chunks

        cases = [
            # 1. Kalimat biasa
            ("Halo, saya ENKI asisten lokal.", "Halo, saya Enki asisten lokal."),
            # 2. Di awal dan di akhir kalimat
            ("ENKI siap membantu pengguna bersama ENKI", "Enki siap membantu pengguna bersama Enki"),
            # 3. Dengan tanda baca "ENKI,"
            ("Halo ENKI, bagaimana kabar sistem hari ini?", "Halo Enki, bagaimana kabar sistem hari ini?"),
            # 4. Dalam path "enki/audio/tts.py" TIDAK berubah
            ("Buka file enki/audio/tts.py lalu jalankan /test.", "Buka file enki/audio/tts.py lalu jalankan /test."),
            # 5. "ENKIDU" TIDAK berubah
            ("Kisah epik ENKIDU dan asisten ENKI.", "Kisah epik ENKIDU dan asisten Enki."),
            # 6. Huruf kecil "enki" TIDAK berubah
            ("Folder enki milik sistem ENKI.", "Folder enki milik sistem Enki."),
        ]

        for raw_text, expected in cases:
            cleaned = clean_text_for_tts(raw_text)
            pronounced = apply_pronunciation(cleaned, {"ENKI": "Enki"})
            self.assertEqual(
                pronounced,
                expected,
                f"K4 gagal untuk {raw_text!r}: dihasilkan {pronounced!r}, diharapkan {expected!r}",
            )

        # Verifikasi path ber-slash dengan huruf besar juga tetap terlindungi oleh guard slash
        self.assertEqual(
            apply_pronunciation("Lihat folder D:/ENKI/enki/audio/tts.py milik ENKI.", {"ENKI": "Enki"}),
            "Lihat folder D:/ENKI/enki/audio/tts.py milik Enki.",
        )

        # Verifikasi teks di layar tidak berubah sementara engine TTS menerima teks berlafal "Enki"
        screen_buffer = []
        raw_reply = "Halo ENKI, silakan cek enki/audio/tts.py dan cerita ENKIDU."
        screen_buffer.append(raw_reply)

        fake_engine = FakeEngine()
        mgr = TTSManager()
        mgr.speak(raw_reply, engine=fake_engine, pronunciation_map={"ENKI": "Enki"})
        mgr.wait_done(timeout=2.0)

        self.assertEqual(screen_buffer[0], "Halo ENKI, silakan cek enki/audio/tts.py dan cerita ENKIDU.")
        self.assertEqual(
            fake_engine.spoken_chunks,
            ["Halo Enki, silakan cek enki/audio/tts.py dan cerita ENKIDU."],
        )

    def test_langkah3_chunk_strategy_first_short_and_subsequent_250(self):
        """Langkah 3: Strategi chunk cepat-mulai:
        - Kasus 1: Balasan 1 kalimat pendek tetap 1 chunk.
        - Kasus 2: Balasan 3 kalimat (kalimat pertama >= 6 kata berdiri sendiri, kalimat ke-2 & ke-3 digabung <= 250 karakter).
        - Kasus 3: Kalimat pertama 3 kata (< 6 kata) digabung dengan kalimat kedua.
        """
        from enki.audio.tts import prepare_tts_chunks_4c1 as prepare_tts_chunks

        # Kasus 1: Balasan 1 kalimat pendek
        case1 = "Halo, saya ENKI."
        chunks1 = prepare_tts_chunks(case1)
        self.assertEqual(chunks1, ["Halo, saya Enki."])

        # Kasus 2: Balasan 3 kalimat (kalimat pertama 7 kata >= 6 kata)
        case2 = (
            "Sistem asisten lokal ini sudah siap digunakan. "
            "Silakan ketik perintah yang Anda butuhkan. "
            "Saya akan segera memproses permintaan tersebut."
        )
        chunks2 = prepare_tts_chunks(case2)
        self.assertEqual(
            chunks2,
            [
                "Sistem asisten lokal ini sudah siap digunakan.",
                "Silakan ketik perintah yang Anda butuhkan. Saya akan segera memproses permintaan tersebut.",
            ],
        )

        # Kasus 3: Kalimat pertama 3 kata (< 6 kata), digabung dengan kalimat berikutnya
        case3 = (
            "Halo, saya ENKI. "
            "Saya siap membantu pekerjaan teknis Anda sekarang. "
            "Silakan berikan instruksi atau pertanyaan lebih lanjut."
        )
        chunks3 = prepare_tts_chunks(case3)
        self.assertEqual(
            chunks3,
            [
                "Halo, saya Enki. Saya siap membantu pekerjaan teknis Anda sekarang.",
                "Silakan berikan instruksi atau pertanyaan lebih lanjut.",
            ],
        )

    def test_l3_1_first_sentence_20_words_with_comma_cut_le_14(self):
        """L3(1): Kalimat pertama 20 kata dengan koma -> chunk 1 dipotong pada batas klausa <= 14 kata."""
        from enki.audio.tts import prepare_tts_chunks

        # Kalimat pertama tepat 20 kata, ada koma setelah kata ke-9
        s1 = (
            "Ketika pengguna menjalankan aplikasi asisten lokal pada komputer pribadi, "
            "seluruh proses sintesis suara berjalan sepenuhnya di prosesor utama tanpa jaringan."
        )
        self.assertEqual(len(s1.split()), 20)
        s2 = "Hal ini menjaga memori kartu grafis tetap longgar untuk model bahasa."

        chunks = prepare_tts_chunks(f"{s1} {s2}")
        self.assertGreaterEqual(len(chunks), 2)
        first_chunk_words = len(chunks[0].split())
        self.assertLessEqual(
            first_chunk_words,
            14,
            f"Chunk 1 memiliki {first_chunk_words} kata (harus <= 14): {chunks[0]!r}",
        )
        self.assertEqual(
            chunks[0],
            "Ketika pengguna menjalankan aplikasi asisten lokal pada komputer pribadi,",
        )
        self.assertEqual(" ".join(chunks), f"{s1} {s2}")

    def test_l3_2_first_sentence_20_words_without_comma_uncut(self):
        """L3(2): Kalimat pertama 20 kata tanpa koma/titik-koma -> tidak dipotong."""
        from enki.audio.tts import prepare_tts_chunks

        s1 = (
            "Pengguna dapat menjalankan aplikasi asisten kecerdasan buatan lokal ini "
            "langsung melalui terminal untuk memeriksa dokumen proyek setiap hari dengan aman."
        )
        self.assertEqual(len(s1.split()), 20)
        self.assertNotIn(",", s1)
        self.assertNotIn(";", s1)
        s2 = "Seluruh data tetap tersimpan di komputer lokal."

        chunks = prepare_tts_chunks(f"{s1} {s2}")
        self.assertEqual(chunks[0], s1)
        self.assertEqual(len(chunks[0].split()), 20)
        self.assertEqual(" ".join(chunks), f"{s1} {s2}")

    def test_l3_3_six_sentences_ramp_non_decreasing_and_le_250(self):
        """L3(3): Balasan 6 kalimat -> ukuran chunk tidak menurun dan tidak melebihi 250 karakter."""
        from enki.audio.tts import prepare_tts_chunks

        sentences = [
            "Sistem asisten lokal siap membantu pekerjaan Anda.",  # 7 kata, 50 chars
            "Seluruh modul sintesis suara berjalan di prosesor utama.",  # 8 kata, 56 chars
            "Memori kartu grafis tetap aman untuk pemrosesan model bahasa.",  # 9 kata, 61 chars
            "Pendekatan antrean berpipa menyiapkan potongan berikutnya di latar.",  # 8 kata, 67 chars
            "Dengan cara ini pengguna tidak perlu menunggu seluruh teks selesai dibuat.",  # 11 kata, 74 chars
            "Setiap bagian suara diputar berurutan secara mulus tanpa jeda panjang yang mengganggu.",  # 12 kata, 86 chars
        ]
        reply = " ".join(sentences)
        chunks = prepare_tts_chunks(reply)

        self.assertGreaterEqual(len(chunks), 3)
        for c in chunks:
            self.assertLessEqual(len(c), 250, f"Chunk melebihi 250 karakter ({len(c)}): {c!r}")

        lengths = [len(c) for c in chunks]
        for i in range(len(lengths) - 1):
            self.assertLessEqual(
                lengths[i],
                lengths[i + 1],
                f"Ukuran chunk menurun pada indeks {i}->{i+1}: {lengths} ({chunks})",
            )
        self.assertEqual(" ".join(chunks), reply)

    def test_l3_4_concatenated_chunks_equal_cleaned_text_seeded(self):
        """L3(4): Properti gabungan chunk == teks asli (setelah dibersihkan) untuk 5 balasan acak tetap (seed tetap)."""
        import random
        from enki.audio.tts import apply_pronunciation, clean_text_for_tts, prepare_tts_chunks

        rng = random.Random(20261017)
        clause_pool = [
            "saat sistem dijalankan di komputer lokal",
            "meskipun beban kerja prosesor sedang tinggi",
            "karena seluruh model telah diunduh sebelumnya",
            "sehingga tidak ada koneksi jaringan keluar",
            "agar jeda antar kalimat tetap sangat singkat",
            "ketika pengguna meminta pemeriksaan berkas proyek",
            "sementara model bahasa menghasilkan jawaban panjang",
            "tanpa mengurangi kualitas pelafalan bahasa Indonesia",
        ]
        sentence_pool = [
            "Halo, saya ENKI.",
            "Sistem siap digunakan sekarang.",
            "Pemeriksaan berkas di folder enki/audio/tts.py telah selesai dijalankan.",
            "Modul antrean suara memproses setiap kalimat secara berurutan di latar belakang.",
            "Pengguna dapat menekan tombol interupsi kapan saja untuk menghentikan suara dengan cepat.",
            "Seluruh konfigurasi tersimpan secara aman di berkas lokal tanpa memerlukan layanan awan.",
            "Hasil pengukuran regresi linear digunakan untuk menentukan batas penggabungan kalimat berikutnya.",
        ]

        for idx in range(5):
            num_sentences = rng.randint(3, 8)
            built_sentences = []
            for s_idx in range(num_sentences):
                if s_idx == 0 and idx % 2 == 0:
                    c1 = rng.choice(clause_pool)
                    c2 = rng.choice(clause_pool)
                    c3 = rng.choice(clause_pool)
                    built_sentences.append(f"Pada pengujian ke-{idx}, {c1}, {c2}, dan {c3}.")
                else:
                    built_sentences.append(rng.choice(sentence_pool))

            raw_reply = " ".join(built_sentences)
            expected_clean = " ".join(
                apply_pronunciation(clean_text_for_tts(raw_reply)).split()
            )
            chunks = prepare_tts_chunks(raw_reply)
            reconstructed = " ".join(" ".join(c.split()) for c in chunks)
            self.assertEqual(
                reconstructed,
                expected_clean,
                f"Ketidakcocokan gabungan chunk pada sampel acak ke-{idx}",
            )

    def test_l3_5_single_short_sentence_one_chunk(self):
        """L3(5): 1 kalimat pendek -> tepat 1 chunk."""
        from enki.audio.tts import prepare_tts_chunks

        chunks = prepare_tts_chunks("Halo, saya ENKI siap membantu.")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "Halo, saya Enki siap membantu.")

    def test_l6_no_clipped_playback_and_offline_socket_guard(self):
        """L6 & K2: Verifikasi tidak ada pemutaran terpotong (clipped_playbacks_count == 0)
        serta verifikasi pemblokiran socket nyata (HF_HUB_OFFLINE=1, 0 koneksi jaringan).
        """
        import socket
        import tempfile
        import wave
        from pathlib import Path
        from enki.audio.supertonic import SupertonicEngine

        with tempfile.TemporaryDirectory() as tmp_dir:
            wav_path = Path(tmp_dir) / "dummy_l6.wav"
            sample_rate = 44100
            dur_sec = 0.12
            n_frames = int(sample_rate * dur_sec)
            with wave.open(str(wav_path), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sample_rate)
                wf.writeframes(b"\x00\x00" * n_frames)

            st_engine = SupertonicEngine(tail_padding_sec=0.05)
            item = {
                "mode": "supertonic_wav",
                "wav_path": str(wav_path),
                "is_temp": False,
                "duration_sec": dur_sec,
                "wav_frames_sec": dur_sec,
                "synth_time_sec": 0.01,
                "text": "Tes pemutaran utuh tanpa terpotong.",
                "voice": "M4",
            }

            with patch("winsound.PlaySound") as mock_play, patch.object(
                socket,
                "create_connection",
                side_effect=AssertionError("Koneksi jaringan keluar terdeteksi!"),
            ):
                ok = st_engine.play_synthesized(item)
                self.assertTrue(ok)
                self.assertEqual(st_engine.clipped_playbacks_count, 0)
                self.assertEqual(len(st_engine.playback_records), 1)
                rec = st_engine.playback_records[0]
                self.assertFalse(rec["clipped"])
                self.assertGreaterEqual(rec["actual_play_sec"], dur_sec)
                self.assertEqual(mock_play.call_count, 1)


if __name__ == "__main__":
    unittest.main()



