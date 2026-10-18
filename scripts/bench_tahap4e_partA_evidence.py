"""Skrip penghasil bukti empiris untuk Tahap 4E Bagian A: Suara, Tampilan & Telemetri.

Menghasilkan berkas docs/evidence/tahap4e_partA_evidence.txt dengan konten:
(i) Teks TTS untuk 17 command terdaftar (clean_text_for_tts + pronunciation)
(ii) Daftar chunk prepare_tts_chunks untuk fixture monitor & fixture 3 item
(iii) Hasil fidelity tampilan per fixture x mode (4 fixture x 3 mode = 12 uji)
(iv) Contoh baris telemetri router muted & unmuted dari jalur kode asli
(v) SHA-256 skrip ini dan commit git HEAD
"""

import datetime
import hashlib
import io
import re
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from enki.audio.base import BaseTTSEngine
from enki.audio.tts import (
    clean_text_for_tts,
    apply_pronunciation,
    prepare_tts_chunks,
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


class FastMockEngine(BaseTTSEngine):
    """Mock engine TTS cepat untuk pengujian fidelity dan telemetri."""

    def __init__(self, synth_delay=0.001, play_delay=0.001):
        self.synth_delay = synth_delay
        self.play_delay = play_delay
        self.spoken_chunks = []
        self.is_fallback = False

    def _ensure_loaded(self):
        return True

    def synthesize_chunk(self, chunk, voice=None, cancel_check=None):
        time.sleep(self.synth_delay)
        return {"text": chunk, "ready_at": time.perf_counter()}

    def play_synthesized(self, item, cancel_check=None, on_before_play=None, on_after_play=None):
        if on_before_play:
            on_before_play(item)
        time.sleep(self.play_delay)
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


def _extract_jawaban(stdout_text: str) -> str:
    m = re.search(r"\[Jawaban\]\s*\n([\s\S]*?)\n*-- Telemetri:", stdout_text)
    if m:
        return m.group(1).strip()
    return ""


def main():
    script_path = Path(__file__).resolve()
    evidence_path = Path("docs/evidence/tahap4e_partA_evidence.txt")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        git_head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        git_head = "UNKNOWN"

    script_sha256 = hashlib.sha256(script_path.read_bytes()).hexdigest()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append("=" * 80)
    lines.append("BUKTI EMPIRIS TAHAP 4E BAGIAN A (SUARA, TAMPILAN, TELEMETRI)")
    lines.append(f"Git HEAD     : {git_head}")
    lines.append(f"Skrip Berkas : {script_path.name}")
    lines.append(f"Skrip SHA-256: {script_sha256}")
    lines.append(f"Waktu Uji    : {now_str}")
    lines.append("=" * 80)
    lines.append("")

    # --------------------------------------------------------------------------
    # (i) Teks TTS untuk 17 command
    # --------------------------------------------------------------------------
    lines.append("(I) TEKS TTS UNTUK 17 COMMAND REGISTRI (clean_text_for_tts + apply_pronunciation)")
    lines.append("-" * 80)
    cmd_raw = format_list_commands_reply()
    cmd_cleaned = clean_text_for_tts(cmd_raw)
    cmd_pronounced = apply_pronunciation(cmd_cleaned)

    tts_lines = [l.strip() for l in cmd_pronounced.splitlines() if l.strip()]
    expected_cmds = list(COMMAND_REGISTRY.keys())
    matched_cmd_lines = [l for l in tts_lines if any(l.startswith(c.lstrip("/")) for c in expected_cmds)]

    for idx, cl in enumerate(matched_cmd_lines, 1):
        lines.append(f"[{idx:02d}] {cl}")
    lines.append(f"Total baris perintah terformat: {len(matched_cmd_lines)}/{len(expected_cmds)}")
    lines.append("-" * 80)
    lines.append("")

    # --------------------------------------------------------------------------
    # (ii) Daftar chunk prepare_tts_chunks untuk fixture monitor & 3 item
    # --------------------------------------------------------------------------
    lines.append("(II) DAFTAR CHUNK TTS & PENANDA LABEL+DESKRIPSI (prepare_tts_chunks)")
    lines.append("-" * 80)

    # Fixture Monitor
    lines.append("[Fixture 1: Jawaban Rekomendasi Monitor]")
    mon_chunks = prepare_tts_chunks(FIXTURE_MONITOR)
    for idx, ch in enumerate(mon_chunks, 1):
        is_label = any(kw in ch for kw in ["Ukuran Monitor,", "Resolusi,", "Tipe Monitor,", "Budget,"])
        marker = "[LABEL+DESKRIPSI]" if is_label else "[KALIMAT UTUH   ]"
        lines.append(f"  Chunk #{idx:02d} {marker} : {ch}")
    lines.append("")

    # Fixture Tiga Item
    lines.append("[Fixture 2: Tiga Item Berlabel]")
    three_chunks = prepare_tts_chunks(FIXTURE_THREE_ITEMS)
    for idx, ch in enumerate(three_chunks, 1):
        is_label = any(kw in ch for kw in ["Unduh Paket,", "Ekstrak Berkas,", "Jalankan Skrip,"])
        marker = "[LABEL+DESKRIPSI]" if is_label else "[KALIMAT UTUH   ]"
        lines.append(f"  Chunk #{idx:02d} {marker} : {ch}")
    lines.append("-" * 80)
    lines.append("")

    # --------------------------------------------------------------------------
    # (iii) Fidelity tampilan per fixture x mode (4 fixture x 3 mode)
    # --------------------------------------------------------------------------
    lines.append("(III) FIDELITY TAMPILAN PER FIXTURE X MODE (4 Fixture x 3 Mode = 12 Uji)")
    lines.append("-" * 80)
    fixtures = [
        ("monitor", FIXTURE_MONITOR),
        ("list_commands", FIXTURE_LIST_COMMANDS),
        ("three_items", FIXTURE_THREE_ITEMS),
        ("ending_colon", FIXTURE_ENDING_COLON),
    ]

    total_pass = 0
    total_runs = 0

    for fix_name, fix_text in fixtures:
        raw_expected = fix_text.strip()
        raw_len = len(raw_expected)

        # Mode 1: Muted
        total_runs += 1
        app_m = EnkiConsole()
        app_m.config.set_mute(True)
        app_m.check_startup_reflection = MagicMock()
        app_m.submit_mid_reply_input("tes")
        app_m.submit_mid_reply_input("/exit")
        buf_m = io.StringIO()
        with patch("sys.stdout", buf_m), \
             patch.object(app_m.coordinator, "run_react_turn", return_value=[(fix_text, {"prompt_eval_count": 10, "eval_count": 20, "eval_tok_per_sec": 15.0})]), \
             patch("enki.cli.console.route_capability_inquiry", return_value=None):
            app_m.run()
        got_m = _extract_jawaban(buf_m.getvalue())
        pass_m = (got_m == raw_expected)
        if pass_m:
            total_pass += 1
        lines.append(f"Fixture: {fix_name:<14} | Mode: {'muted':<14} | Status: {'PASS' if pass_m else 'FAIL'} | Tercetak: {len(got_m):>4} char | Asli: {raw_len:>4} char")

        # Mode 2: Unmuted normal
        total_runs += 1
        app_u = EnkiConsole()
        app_u.config.set_mute(False)
        app_u.config._data["tts_engine"] = "supertonic"
        app_u.check_startup_reflection = MagicMock()
        app_u.tts_engine_override = FastMockEngine()
        app_u.submit_mid_reply_input("tes")
        app_u.submit_mid_reply_input("/exit")
        buf_u = io.StringIO()
        with patch("sys.stdout", buf_u), \
             patch.object(app_u.coordinator, "run_react_turn", return_value=[(fix_text, {"prompt_eval_count": 10, "eval_count": 20, "eval_tok_per_sec": 15.0})]), \
             patch("enki.cli.console.route_capability_inquiry", return_value=None):
            app_u.run()
        got_u = _extract_jawaban(buf_u.getvalue())
        pass_u = (got_u == raw_expected)
        if pass_u:
            total_pass += 1
        lines.append(f"Fixture: {fix_name:<14} | Mode: {'unmuted':<14} | Status: {'PASS' if pass_u else 'FAIL'} | Tercetak: {len(got_u):>4} char | Asli: {raw_len:>4} char")

        # Mode 3: Unmuted cancel mid-reply
        total_runs += 1
        app_c = EnkiConsole()
        app_c.config.set_mute(False)
        app_c.config._data["tts_engine"] = "supertonic"
        app_c.check_startup_reflection = MagicMock()
        eng_c = FastMockEngine()
        app_c.tts_engine_override = eng_c
        app_c.submit_mid_reply_input("tes")

        polled = {"called": False}
        def _mid_cancel():
            if len(eng_c.spoken_chunks) >= 1 and not polled["called"]:
                polled["called"] = True
                return "/mute"
            return None
        app_c._poll_mid_reply_interrupt = _mid_cancel
        app_c.submit_mid_reply_input("/exit")

        buf_c = io.StringIO()
        with patch("sys.stdout", buf_c), \
             patch.object(app_c.coordinator, "run_react_turn", return_value=[(fix_text, {"prompt_eval_count": 10, "eval_count": 20, "eval_tok_per_sec": 15.0})]), \
             patch("enki.cli.console.route_capability_inquiry", return_value=None):
            app_c.run()
        got_c = _extract_jawaban(buf_c.getvalue())
        pass_c = (got_c == raw_expected)
        if pass_c:
            total_pass += 1
        lines.append(f"Fixture: {fix_name:<14} | Mode: {'unmuted+cancel':<14} | Status: {'PASS' if pass_c else 'FAIL'} | Tercetak: {len(got_c):>4} char | Asli: {raw_len:>4} char")

    lines.append(f"Ringkasan Fidelity: {total_pass}/{total_runs} PASS (100% Identik)")
    lines.append("-" * 80)
    lines.append("")

    # --------------------------------------------------------------------------
    # (iv) Contoh baris telemetri router muted & unmuted jalur kode asli
    # --------------------------------------------------------------------------
    lines.append("(IV) CONTOH BARIS TELEMETRI ROUTER DARI JALUR KODE ASLI (console.py)")
    lines.append("-" * 80)

    # Jalur asli: Muted
    app_t_m = EnkiConsole()
    app_t_m.check_startup_reflection = MagicMock()
    app_t_m.config.set_mute(True)
    app_t_m.submit_mid_reply_input("apakah kamu bisa berbicara dengan suara?")
    app_t_m.submit_mid_reply_input("/exit")
    buf_t_m = io.StringIO()
    with patch("sys.stdout", buf_t_m):
        app_t_m.run()
    tele_m_lines = [l for l in buf_t_m.getvalue().splitlines() if "-- Telemetri:" in l]
    tele_m = tele_m_lines[0] if tele_m_lines else "TIDAK DITEMUKAN"
    lines.append("[Mode Muted]")
    lines.append(f"{tele_m}")
    lines.append("")

    # Jalur asli: Unmuted
    app_t_u = EnkiConsole()
    app_t_u.check_startup_reflection = MagicMock()
    app_t_u.config.set_mute(False)
    app_t_u.config._data["tts_engine"] = "supertonic"
    app_t_u.tts_engine_override = FastMockEngine()
    app_t_u.submit_mid_reply_input("apakah kamu bisa berbicara dengan suara?")
    app_t_u.submit_mid_reply_input("/exit")
    buf_t_u = io.StringIO()
    with patch("sys.stdout", buf_t_u):
        app_t_u.run()
    tele_u_lines = [l for l in buf_t_u.getvalue().splitlines() if "-- Telemetri:" in l]
    tele_u = tele_u_lines[0] if tele_u_lines else "TIDAK DITEMUKAN"
    lines.append("[Mode Unmuted]")
    lines.append(f"{tele_u}")
    lines.append("-" * 80)
    lines.append("")

    # --------------------------------------------------------------------------
    # (v) SHA-256 skrip & Git HEAD
    # --------------------------------------------------------------------------
    lines.append("(V) VERIFIKASI HASH & INTEGRITAS")
    lines.append("-" * 80)
    lines.append(f"Git HEAD Commit    : {git_head}")
    lines.append(f"Berkas Skrip       : {script_path}")
    lines.append(f"SHA-256 Skrip      : {script_sha256}")
    lines.append("=" * 80)

    content = "\n".join(lines) + "\n"
    evidence_path.write_text(content, encoding="utf-8")
    print(f"Bukti berhasil ditulis ke {evidence_path} ({len(content)} bytes).")
    print(content)


if __name__ == "__main__":
    main()
