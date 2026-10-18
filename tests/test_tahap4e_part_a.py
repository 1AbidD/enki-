"""Unit test untuk Tahap 4E Bagian A: Suara, Tampilan & Telemetri (Kriteria A1, A2, A3, A4, A5)."""

import io
import re
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

from enki.audio.base import BaseTTSEngine
from enki.audio.tts import (
    TTSManager,
    clean_text_for_tts,
    apply_pronunciation,
    prepare_tts_chunks,
    align_raw_segments_to_chunks,
    run_synced_speech_stream,
)
from enki.cli.command_registry import COMMAND_REGISTRY
from enki.cli.console import EnkiConsole
from enki.core.capability_router import format_list_commands_reply


FIXTURE_MONITOR = (
    "Untuk membantu Anda menemukan rekomendasi monitor baru, saya membutuhkan beberapa informasi lebih lanjut tentang kebutuhan Anda, seperti:\n\n"
    "1. **Ukuran Monitor**: Ukuran monitor yang Anda inginkan. Biasanya, ukuran monitor dinyatakan dalam inci (ex: 24 inci, 27 inci, dll).\n\n"
    "2. **Resolusi**: Resolusi yang Anda inginkan. Monitor modern biasanya memiliki resolusi 1080p (Full HD) atau 4K (Ultra HD).\n\n"
    "3. **Tipe Monitor**: Apakah Anda membutuhkan monitor standar, monitor gaming, atau monitor dengan fitur khusus lainnya?\n\n"
    "4. **Budget**: Berapa anggaran yang Anda miliki untuk beli monitor?\n\n"
    "Setelah Anda memberikan informasi ini, saya dapat memberikan rekomendasi yang lebih spesifik untuk Anda."
)

FIXTURE_THREE_ITEMS = (
    "Berikut adalah tiga langkah utama dalam proses instalasi sistem:\n\n"
    "1. **Unduh Paket**: Unduh paket instalasi dari repositori resmi proyek.\n\n"
    "2. **Ekstrak Berkas**: Ekstrak seluruh isi arsip ke direktori kerja yang ditentukan.\n\n"
    "3. **Jalankan Skrip**: Jalankan skrip setup untuk menyelesaikan konfigurasi otomatis."
)

FIXTURE_LIST_COMMANDS = format_list_commands_reply()

FIXTURE_ENDING_COLON = (
    "Untuk memulai proses diagnosa sistem secara lengkap, silakan persiapkan hal berikut:"
)


class UndulatingDelayMockEngine(BaseTTSEngine):
    """Mock engine TTS dengan variasi delay bergelombang untuk uji konkurensi dan interupsi."""

    def __init__(self, synth_delays=None, play_delays=None):
        self.synth_delays = synth_delays or [0.03, 0.08, 0.02, 0.06, 0.04]
        self.play_delays = play_delays or [0.05, 0.12, 0.04, 0.10, 0.06]
        self.synth_idx = 0
        self.play_idx = 0
        self.spoken_chunks = []
        self.is_fallback = False

    def _ensure_loaded(self):
        return True

    def synthesize_chunk(self, chunk, voice=None, cancel_check=None):
        d = self.synth_delays[self.synth_idx % len(self.synth_delays)]
        self.synth_idx += 1
        time.sleep(d)
        return {"text": chunk, "ready_at": time.perf_counter()}

    def play_synthesized(self, item, cancel_check=None, on_before_play=None, on_after_play=None):
        if on_before_play:
            on_before_play(item)
        d = self.play_delays[self.play_idx % len(self.play_delays)]
        self.play_idx += 1
        time.sleep(d)
        self.spoken_chunks.append(item.get("text", ""))
        if on_after_play:
            on_after_play(item, False)
        return True

    def discard_synthesized(self, item, count_as_cancelled=False):
        pass

    def speak_chunk(self, chunk, voice=None):
        self.spoken_chunks.append(chunk)
        return True

    def stop(self):
        pass


class TestTahap4EPartA(unittest.TestCase):
    """Test suite untuk Kriteria A1 sampai A4 Tahap 4E."""

    def test_a1_list_item_pause_and_colon_to_comma_all_17_commands(self):
        """A1: Jeda antar item list dan koma setelah command/label untuk seluruh 17 command."""
        cmd_text = format_list_commands_reply()
        cleaned = clean_text_for_tts(cmd_text)
        pronounced = apply_pronunciation(cleaned)

        # (a) Seluruh 17 command harus muncul dan urutannya tetap
        expected_cmds = list(COMMAND_REGISTRY.keys())
        self.assertEqual(len(expected_cmds), len(COMMAND_REGISTRY))

        positions = []
        for cmd in expected_cmds:
            name_without_slash = cmd.lstrip("/")
            m = re.search(rf"(?:^|\n){re.escape(name_without_slash)},", pronounced)
            self.assertIsNotNone(
                m, f"Command '{name_without_slash},' tidak ditemukan di awal baris pada teks TTS: {pronounced}"
            )
            positions.append((m.start(), name_without_slash))

        # Verifikasi urutan tetap
        for i in range(len(positions) - 1):
            self.assertLess(
                positions[i][0],
                positions[i + 1][0],
                f"Urutan item '{positions[i][1]}' dan '{positions[i+1][1]}' terbalik!"
            )

        # (c) Titik dua setelah nama command/label menjadi koma
        for _, name in positions:
            # Di awal baris harus ada "name," bukan "name:"
            pat_colon = re.compile(rf"(?:^|\n){re.escape(name)}:")
            self.assertFalse(
                bool(pat_colon.search(pronounced)),
                f"Ditemukan titik dua setelah nama command '{name}' pada teks TTS: {pronounced}"
            )
            pat_comma = re.compile(rf"(?:^|\n){re.escape(name)},")
            self.assertTrue(
                bool(pat_comma.search(pronounced)),
                f"Tidak ditemukan koma setelah nama command '{name}' pada teks TTS: {pronounced}"
            )

        # (b) Di antara akhir deskripsi item N dan nama item N+1 ada tanda jeda (, . ;)
        lines = [l.strip() for l in pronounced.splitlines() if l.strip()]
        cmd_lines = [l for l in lines if any(l.startswith(c.lstrip("/")) for c in expected_cmds)]
        self.assertEqual(len(cmd_lines), len(COMMAND_REGISTRY))
        for i in range(len(cmd_lines) - 1):
            curr_line = cmd_lines[i]
            self.assertIn(
                curr_line[-1],
                [".", ",", ";"],
                f"Item #{i+1} ('{curr_line}') tidak berakhir tanda baca jeda!",
            )

    def test_a2_label_cannot_be_separated_from_description(self):
        """A2: Label pendek (<= 4 kata, :) dan nomor list tidak boleh jadi chunk sendirian."""
        # Fixture 1: Jawaban Monitor
        chunks_mon = prepare_tts_chunks(FIXTURE_MONITOR)
        for idx, c in enumerate(chunks_mon):
            words = c.split()
            # Tidak boleh ada chunk berupa nomor list saja ("2.", "3.")
            self.assertFalse(
                bool(re.match(r"^\d+\.?$", c.strip())),
                f"Chunk #{idx} pada monitor hanya berupa nomor: '{c}'"
            )
            # Tidak boleh ada chunk berupa label pendek saja (<= 4 kata berakhir ':' atau ',')
            if len(words) <= 4 and (c.strip().endswith(":") or c.strip().endswith(",")):
                self.fail(f"Chunk #{idx} pada monitor berupa label sendirian: '{c}'")

        # Chunk yang memuat "Resolusi" harus memuat "Resolusi yang Anda inginkan"
        resolusi_chunks = [c for c in chunks_mon if "Resolusi" in c]
        self.assertGreater(len(resolusi_chunks), 0)
        for c in resolusi_chunks:
            if "Resolusi," in c or "Resolusi:" in c:
                self.assertIn(
                    "Resolusi yang Anda inginkan",
                    c,
                    f"Chunk memuat label Resolusi tetapi tidak memuat awal deskripsinya: '{c}'"
                )

        # Kalimat pembuka yang berakhir ":" (> 4 kata) boleh jadi chunk sendiri
        opening = chunks_mon[0]
        self.assertIn("Untuk membantu Anda", opening)
        self.assertGreater(len(opening.split()), 4)

        # Fixture 2: Tiga item berlabel tebal
        chunks_three = prepare_tts_chunks(FIXTURE_THREE_ITEMS)
        for idx, c in enumerate(chunks_three):
            words = c.split()
            self.assertFalse(
                bool(re.match(r"^\d+\.?$", c.strip())),
                f"Chunk #{idx} pada fixture 3 item hanya nomor: '{c}'"
            )
            if len(words) <= 4 and (c.strip().endswith(":") or c.strip().endswith(",")):
                self.fail(f"Chunk #{idx} pada fixture 3 item berupa label sendirian: '{c}'")

        # Sync display mengikuti batas chunk yang sama
        segs_mon = align_raw_segments_to_chunks(FIXTURE_MONITOR, chunks_mon)
        self.assertEqual(len(segs_mon), len(chunks_mon))
        self.assertEqual("".join(segs_mon), FIXTURE_MONITOR)
        for s in segs_mon:
            s_clean = s.strip()
            self.assertFalse(
                bool(re.match(r"^\d+\.\s*(\*\*[^*]+\*\*:\s*)?$", s_clean)),
                f"Sync display mencetak segmen berupa label sendirian: '{s}'"
            )

    def test_a3_display_fidelity_4_fixtures_3_modes(self):
        """A3: Teks antara [Jawaban] dan -- Telemetri persis sama dengan teks mentah (3 mode, 4 fixture)."""
        fixtures = [
            ("monitor", FIXTURE_MONITOR),
            ("list_commands", FIXTURE_LIST_COMMANDS),
            ("three_items", FIXTURE_THREE_ITEMS),
            ("ending_colon", FIXTURE_ENDING_COLON),
        ]

        def _clean_between_jawaban_and_telemetri(out_text: str) -> str:
            # Ambil bagian antara [Jawaban] dan -- Telemetri
            m = re.search(r"\[Jawaban\]\s*\n([\s\S]*?)\n*-- Telemetri:", out_text)
            self.assertIsNotNone(m, f"Format stdout tidak memuat [Jawaban] dan -- Telemetri:\n{out_text}")
            extracted = m.group(1).strip()
            return extracted

        for fix_name, fix_text in fixtures:
            # Mode 1: Muted
            app_muted = EnkiConsole()
            app_muted.config.set_mute(True)
            app_muted.check_startup_reflection = MagicMock()
            app_muted.submit_mid_reply_input("tes")
            app_muted.submit_mid_reply_input("/exit")

            out_muted = io.StringIO()
            with patch("sys.stdout", out_muted), \
                 patch.object(app_muted.coordinator, "run_react_turn", return_value=[(fix_text, {"prompt_eval_count": 10, "eval_count": 20, "eval_tok_per_sec": 15.0})]), \
                 patch("enki.cli.console.route_capability_inquiry", return_value=None):
                app_muted.run()

            extracted_muted = _clean_between_jawaban_and_telemetri(out_muted.getvalue())
            self.assertEqual(
                extracted_muted,
                fix_text.strip(),
                f"Fidelity gagal pada fixture {fix_name} (Mode Muted)!",
            )
            self.assertIn("-- Telemetri:", out_muted.getvalue())

            # Mode 2: Unmuted normal
            app_unmuted = EnkiConsole()
            app_unmuted.config.set_mute(False)
            app_unmuted.config._data["tts_engine"] = "supertonic"
            app_unmuted.check_startup_reflection = MagicMock()
            app_unmuted.tts_engine_override = UndulatingDelayMockEngine()
            app_unmuted.submit_mid_reply_input("tes")
            app_unmuted.submit_mid_reply_input("/exit")

            out_unmuted = io.StringIO()
            with patch("sys.stdout", out_unmuted), \
                 patch.object(app_unmuted.coordinator, "run_react_turn", return_value=[(fix_text, {"prompt_eval_count": 10, "eval_count": 20, "eval_tok_per_sec": 15.0})]), \
                 patch("enki.cli.console.route_capability_inquiry", return_value=None):
                app_unmuted.run()

            extracted_unmuted = _clean_between_jawaban_and_telemetri(out_unmuted.getvalue())
            self.assertEqual(
                extracted_unmuted,
                fix_text.strip(),
                f"Fidelity gagal pada fixture {fix_name} (Mode Unmuted Normal)!",
            )
            self.assertIn("-- Telemetri:", out_unmuted.getvalue())

            # Mode 3: Unmuted cancel di tengah jalan
            app_cancel = EnkiConsole()
            app_cancel.config.set_mute(False)
            app_cancel.config._data["tts_engine"] = "supertonic"
            app_cancel.check_startup_reflection = MagicMock()
            eng_cancel = UndulatingDelayMockEngine(play_delays=[0.1, 0.1, 0.1])
            app_cancel.tts_engine_override = eng_cancel
            app_cancel.submit_mid_reply_input("tes")

            polled = {"called": False}
            def _mid_cancel_poll():
                if len(eng_cancel.spoken_chunks) >= 1 and not polled["called"]:
                    polled["called"] = True
                    return "/mute"
                return None
            app_cancel._poll_mid_reply_interrupt = _mid_cancel_poll

            app_cancel.submit_mid_reply_input("/exit")

            out_cancel = io.StringIO()
            with patch("sys.stdout", out_cancel), \
                 patch.object(app_cancel.coordinator, "run_react_turn", return_value=[(fix_text, {"prompt_eval_count": 10, "eval_count": 20, "eval_tok_per_sec": 15.0})]), \
                 patch("enki.cli.console.route_capability_inquiry", return_value=None):
                app_cancel.run()

            extracted_cancel = _clean_between_jawaban_and_telemetri(out_cancel.getvalue())
            self.assertEqual(
                extracted_cancel,
                fix_text.strip(),
                f"Fidelity gagal pada fixture {fix_name} (Mode Unmuted Cancel)!",
            )
            self.assertIn("-- Telemetri:", out_cancel.getvalue())

    def test_a4_telemetry_label_format_locked(self):
        """A4: Router memisahkan 'Router: x ms' dan 'Suara: y s', tidak ada field 'Latensi'."""
        app = EnkiConsole()
        app.check_startup_reflection = MagicMock()

        # 1. Kasus Muted: Ada Router: x ms, TANPA Suara: y s, TANPA Latensi
        app.config.set_mute(True)
        app.submit_mid_reply_input("apakah kamu bisa berbicara dengan suara?")
        app.submit_mid_reply_input("/exit")

        out_muted = io.StringIO()
        with patch("sys.stdout", out_muted):
            app.run()

        muted_str = out_muted.getvalue()
        self.assertIn("router (tanpa LLM)", muted_str)
        self.assertIn("Router:", muted_str)
        self.assertNotIn("Suara:", muted_str)
        self.assertNotIn("Latensi:", muted_str)
        self.assertRegex(
            muted_str,
            r"-- Telemetri: router \(tanpa LLM\) \| Intent: \w+ \| Router: [\d\.]+ ms \| Sisa Context: \d+/\d+ tok --"
        )

        # 2. Kasus Unmuted: Ada Router: x ms DAN Suara: y s, TANPA Latensi
        app_unmuted = EnkiConsole()
        app_unmuted.config.set_mute(False)
        app_unmuted.config._data["tts_engine"] = "supertonic"
        app_unmuted.check_startup_reflection = MagicMock()
        app_unmuted.tts_engine_override = UndulatingDelayMockEngine()
        app_unmuted.submit_mid_reply_input("apakah kamu bisa berbicara dengan suara?")
        app_unmuted.submit_mid_reply_input("/exit")

        out_unmuted = io.StringIO()
        with patch("sys.stdout", out_unmuted):
            app_unmuted.run()

        unmuted_str = out_unmuted.getvalue()
        self.assertIn("router (tanpa LLM)", unmuted_str)
        self.assertIn("Router:", unmuted_str)
        self.assertIn("Suara:", unmuted_str)
        self.assertNotIn("Latensi:", unmuted_str)
        self.assertRegex(
            unmuted_str,
            r"-- Telemetri: router \(tanpa LLM\) \| Intent: \w+ \| Router: [\d\.]+ ms \| Suara: [\d\.]+ s \| Sisa Context: \d+/\d+ tok --"
        )


if __name__ == "__main__":
    unittest.main()
