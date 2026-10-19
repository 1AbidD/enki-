#!/usr/bin/env python3
"""Skrip Benchmark & Bukti Empiris Tahap 6-FIX: Verifikasi Jalur Pembuka Peramban (/buka).

Memverifikasi Kriteria F1–F5:
F1: Pesan sukses hanya jika pembuka sukses. Jika gagal -> error jujur + URL manual. Audit log tercatat.
F2: Windows: os.startfile(url) sebagai jalur utama, fallback webbrowser.open; non-Windows: webbrowser.open.
F3: validate_url & pertahanan SSRF tidak berubah.
F4: Tes unit & verifikasi perilaku.
F5: Batasan klaim: Agen dilarang mengklaim browser fisik terbuka, verifikasi hanya oleh Abid secara live.
"""

import datetime
import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Pastikan path import project
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from enki.tools.web import open_in_browser, validate_url
from enki.cli.console import EnkiConsole


def get_git_info():
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT_DIR, text=True
        ).strip()
    except Exception:
        head = "unknown"
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT_DIR, text=True
        ).strip()
        dirty = len(status) > 0
    except Exception:
        dirty = True
    return head, dirty


def main():
    evidence_dir = ROOT_DIR / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / "tahap6_buka.txt"

    head_commit, is_dirty = get_git_info()
    start_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()

    output_lines = []

    def p(line=""):
        output_lines.append(line)
        print(line)

    p("=" * 80)
    p("EVALUASI EMPIRIS TAHAP 6-FIX: PERBAIKAN PEMBUKA PERAMBAN (/buka)")
    p("=" * 80)
    p(f"Timestamp Mulai (UTC): {start_utc}")
    p(f"Git HEAD             : {head_commit}")
    p(f"Git Dirty            : {is_dirty}")
    p(f"Sistem Operasi Host  : {sys.platform}")
    p("=" * 80)
    p()

    p("--- [1/5] VERIFIKASI KEBIJAKAN SSRF PADA /buka (F3) ---")
    invalid_cases = [
        ("file:///etc/passwd", "Skema tidak diizinkan"),
        ("http://localhost/", "Hostname lokal/privat"),
        ("http://127.0.0.1/", "IP loopback"),
        ("http://10.0.0.1/", "IP privat"),
    ]
    ssrf_passed = True
    for test_url, desc in invalid_cases:
        ok, err = open_in_browser(test_url)
        if ok or not err:
            p(f"  [GAGAL] URL {test_url} lolos validasi!")
            ssrf_passed = False
        else:
            p(f"  [PASS] {test_url} ({desc}) -> Ditolak: {err}")
    p(f"Status SSRF Guard: {'LULUS' if ssrf_passed else 'GAGAL'}")
    p()

    p("--- [2/5] VERIFIKASI JALUR WINDOWS: os.startfile DENGAN FALLBACK webbrowser.open (F2) ---")
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        # Kasus A: Windows os.startfile berhasil
        with patch("sys.platform", "win32"), \
             patch("os.startfile", MagicMock()) as mock_startfile, \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            ok_a, err_a = open_in_browser("https://example.com")
            p(f"  Kasus A (os.startfile sukses): ok={ok_a}, err={err_a}")
            p(f"    -> os.startfile terpanggil: {mock_startfile.called}")
            assert ok_a and mock_startfile.called

        # Kasus B: Windows os.startfile gagal, fallback webbrowser.open berhasil
        with patch("sys.platform", "win32"), \
             patch("os.startfile", side_effect=OSError("Access denied")), \
             patch("webbrowser.open", return_value=True) as mock_web_open, \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            ok_b, err_b = open_in_browser("https://example.com")
            p(f"  Kasus B (os.startfile gagal, fallback sukses): ok={ok_b}, err={err_b}")
            p(f"    -> webbrowser.open terpanggil: {mock_web_open.called}")
            assert ok_b and mock_web_open.called

        # Kasus C: Windows kedua jalur gagal
        with patch("sys.platform", "win32"), \
             patch("os.startfile", side_effect=OSError("No association")), \
             patch("webbrowser.open", return_value=False), \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            ok_c, err_c = open_in_browser("https://example.com")
            p(f"  Kasus C (kedua jalur gagal): ok={ok_c}, err={err_c}")
            assert not ok_c and "os.startfile gagal" in err_c
    p("Status Jalur Windows & Fallback: LULUS")
    p()

    p("--- [3/5] VERIFIKASI PENCATATAN AUDIT LOG (F1) ---")
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with patch("sys.platform", "win32"), \
             patch("os.startfile", MagicMock()), \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            open_in_browser("https://example.com")

        audit_file = log_dir / "tool_audit.log"
        if audit_file.exists():
            log_content = audit_file.read_text(encoding="utf-8").strip()
            p(f"  Isi Log Audit Terverifikasi:\n    {log_content}")
            assert "tool=open_browser" in log_content
            assert "status=OK" in log_content
            p("Status Audit Log: LULUS")
        else:
            p("  [GAGAL] File tool_audit.log tidak dibuat!")
            assert False
    p()

    p("--- [4/5] VERIFIKASI KONSOL CLI: OUTPUT SUKSES & GAGAL JUJUR (F1 & F4) ---")
    # Uji Sukses
    with patch("enki.tools.web.open_in_browser", return_value=(True, None)):
        app_s = EnkiConsole()
        app_s.config.set_mute(True)
        app_s.check_startup_reflection = lambda: None
        app_s.submit_mid_reply_input("/buka https://example.com")
        app_s.submit_mid_reply_input("y")
        app_s.submit_mid_reply_input("/exit")
        out_s = io.StringIO()
        with patch("sys.stdout", out_s):
            app_s.run()
        txt_s = out_s.getvalue()
        p("  Output Konsol Saat Sukses:")
        for line in txt_s.splitlines():
            if "[Peramban]" in line or "Internet" in line or "/buka" in line:
                p(f"    {line}")
        assert "[Peramban] Membuka https://example.com/ di peramban web sistem." in txt_s

    # Uji Gagal
    with patch("enki.tools.web.open_in_browser", return_value=(False, "Tidak ada browser default terdaftar")):
        app_f = EnkiConsole()
        app_f.config.set_mute(True)
        app_f.check_startup_reflection = lambda: None
        app_f.submit_mid_reply_input("/buka https://example.com")
        app_f.submit_mid_reply_input("y")
        app_f.submit_mid_reply_input("/exit")
        out_f = io.StringIO()
        with patch("sys.stdout", out_f):
            app_f.run()
        txt_f = out_f.getvalue()
        p("  Output Konsol Saat Gagal (Pesan Jujur + URL Manual):")
        for line in txt_f.splitlines():
            if "[Error /buka]" in line or "manual" in line or "[Peramban]" in line:
                p(f"    {line}")
        assert "[Peramban] Membuka" not in txt_f
        assert "[Error /buka] Gagal membuka peramban: Tidak ada browser default terdaftar" in txt_f
        assert "Silakan buka tautan secara manual: https://example.com/" in txt_f
    p("Status Tampilan Konsol CLI: LULUS")
    p()

    p("--- [5/5] STATUS BATASAN KLAIM & VERIFIKASI FISIK (F5) ---")
    p("PERNYATAAN RESMI (KRITERIA F5):")
    p("Verifikasi bahwa jendela peramban fisik benar-benar terbuka di layar sistem")
    p("TIDAK DAPAT dibuktikan atau diklaim secara otomatis oleh agen AI.")
    p("Status Verifikasi Layar Fisik: MENUNGGU VERIFIKASI LIVE ABID.")
    p()

    end_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    p("=" * 80)
    p(f"Timestamp Selesai (UTC): {end_utc}")
    p("STATUS AKHIR PROBE BUKA : SELESAI (MENUNGGU VERIFIKASI LIVE ABID)")
    p("=" * 80)

    evidence_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    print(f"\n[Selesai] Bukti berhasil ditulis ke: {evidence_path}")


if __name__ == "__main__":
    main()
