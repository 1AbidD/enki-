#!/usr/bin/env python3
"""Skrip Verifikasi dan Benchmark Tahap 6-FIX-2:
- O1: Launcher proses terpisah di Windows (explorer.exe -> cmd /c start -> os.startfile)
      Argumen list, no shell=True, keamanan injeksi (&, %20, tanda kutip, %-encoding)
- O2: Pesan jujur: instruksi terkirim ke sistem, tanpa klaim jendela fisik terbuka
- O3: Pencatatan audit log mencatat method dan hasil eksekusi nyata
- O4: Verifikasi keberadaan dan integritas skrip diagnosa probe_buka_inproc.py (REPORT-ONLY)
- O5: Nol regresi (test suite 237+, base prompt hash, router SHA, SSRF defenses)
"""

import datetime
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from enki.cli.console import EnkiConsole
from enki.core.prompt_assembler import PromptAssembler
from enki.tools.web import open_in_browser, validate_url

EVIDENCE_FILE = REPO_ROOT / "docs" / "evidence" / "tahap6_fix2.txt"
BASE_PROMPT_HASH = "6db1ac9a38b2045f7976cee71dfa60b4b9d7251489483faea49c6bfa7bfdb728"
ROUTER_SHA = "48a03331881d8b611a4dbf940d6f08bbf804dc4740b1d6dbb1f359156d79d4a9"


class TeeWriter:
    """Menduplikasi output ke terminal dan berkas log evidence."""
    def __init__(self, file_path: Path):
        self.terminal = sys.stdout
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(file_path, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def close(self):
        self.log.close()


def get_git_info() -> Tuple[str, bool]:
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True).strip()
    except Exception as e:
        head = f"ERROR: {e}"
    try:
        status = subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=str(REPO_ROOT), text=True).strip()
        lines = [l for l in status.splitlines() if not l.strip().endswith("tahap6_fix2.txt")]
        is_dirty = bool(lines)
    except Exception:
        is_dirty = True
    return head, is_dirty


def evaluate_o1_launcher() -> Dict[str, Any]:
    """Evaluasi kriteria O1: Urutan pemanggilan launcher Windows, non-Windows, argumen list, dan keamanan injeksi."""
    results = {}

    # 1. Windows: Jalur 1 (explorer.exe sukses)
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        mock_proc = MagicMock()
        with patch("sys.platform", "win32"), \
             patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            ok, err = open_in_browser("https://example.com/item1")
            call_args = mock_popen.call_args
            is_list = isinstance(call_args[0][0], list)
            cmd_list = call_args[0][0]
            no_shell = not call_args[1].get("shell", False)
            results["win_explorer_ok"] = ok and is_list and cmd_list == ["explorer.exe", "https://example.com/item1"] and no_shell

    # 2. Windows: Jalur 2 (explorer gagal -> cmd /c start)
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        def side_effect_cmd(args, **kwargs):
            if args[0] == "explorer.exe":
                raise OSError("Simulated explorer error")
            return MagicMock()

        with patch("sys.platform", "win32"), \
             patch("subprocess.Popen", side_effect=side_effect_cmd) as mock_popen, \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            ok, err = open_in_browser("https://example.com/item2")
            calls = mock_popen.call_args_list
            c2 = calls[1]
            c2_is_list = isinstance(c2[0][0], list)
            c2_cmd = c2[0][0]
            c2_flags = "creationflags" in c2[1]
            results["win_cmd_start_fallback"] = ok and c2_is_list and c2_cmd == ["cmd", "/c", "start", "", "https://example.com/item2"] and c2_flags

    # 3. Windows: Jalur 3 (explorer & cmd gagal -> os.startfile)
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with patch("sys.platform", "win32"), \
             patch("subprocess.Popen", side_effect=OSError("Popen unavailable")), \
             patch("os.startfile", MagicMock()) as mock_sf, \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            ok, err = open_in_browser("https://example.com/item3")
            sf_called = mock_sf.call_count == 1 and mock_sf.call_args[0][0] == "https://example.com/item3"
            results["win_startfile_fallback"] = ok and sf_called

    # 4. Windows: Jalur 4 (semua gagal -> error jujur + URL)
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with patch("sys.platform", "win32"), \
             patch("subprocess.Popen", side_effect=OSError("All popen fail")), \
             patch("os.startfile", side_effect=OSError("Startfile fail")), \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            ok, err = open_in_browser("https://example.com/item4")
            results["win_all_fail_honest"] = (not ok) and ("os.startfile gagal" in str(err))

    # 5. Non-Windows: webbrowser.open
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with patch("sys.platform", "linux"), \
             patch("webbrowser.open", return_value=True) as mock_wb, \
             patch("enki.tools.web.get_log_dir", return_value=log_dir):
            ok, err = open_in_browser("https://example.com/linux")
            results["non_win_webbrowser"] = ok and mock_wb.call_args[0][0] == "https://example.com/linux"

    # 6. Keamanan Injeksi Argumen / CLI
    injection_cases = [
        "https://example.com/search?q=cat&page=2&filter=recent",
        "https://example.com/wiki/Data%20Science%26AI",
        'https://example.com/query?term="machine+learning"',
        "https://example.com/path/'single_quoted'",
        "https://example.com/test?a=%22quoted%22&b=%26escaped%26",
    ]
    all_safe = True
    with patch("sys.platform", "win32"), patch("subprocess.Popen", return_value=MagicMock()) as mock_popen:
        for u in injection_cases:
            mock_popen.reset_mock()
            ok, _ = open_in_browser(u)
            if not ok:
                all_safe = False
                break
            call_arg = mock_popen.call_args[0][0]
            if not isinstance(call_arg, list) or len(call_arg) != 2 or call_arg[0] != "explorer.exe":
                all_safe = False
                break
    results["injection_safety"] = all_safe

    return results


def evaluate_o2_honest_messages() -> Dict[str, Any]:
    """Evaluasi kriteria O2: Pesan konsol jujur tanpa klaim jendela fisik terbuka."""
    results = {}

    # 1. Sukses
    with patch("enki.tools.web.open_in_browser", return_value=(True, None)):
        app = EnkiConsole()
        app.config.set_mute(True)
        app.check_startup_reflection = lambda: None
        app.submit_mid_reply_input("/buka https://example.com/target")
        app.submit_mid_reply_input("y")
        app.submit_mid_reply_input("/exit")
        out = io.StringIO()
        with patch("sys.stdout", out):
            app.run()
        out_str = out.getvalue()
        results["sent_message_present"] = "[Peramban] Perintah membuka dikirim ke sistem: https://example.com/target" in out_str
        results["hint_manual_present"] = "(Jika jendela tidak muncul, buka manual: https://example.com/target)" in out_str
        results["no_physical_claim"] = "[Peramban] Membuka " not in out_str

    # 2. Gagal
    with patch("enki.tools.web.open_in_browser", return_value=(False, "Simulated open failure")):
        app = EnkiConsole()
        app.config.set_mute(True)
        app.check_startup_reflection = lambda: None
        app.submit_mid_reply_input("/buka https://example.com/fail")
        app.submit_mid_reply_input("y")
        app.submit_mid_reply_input("/exit")
        out = io.StringIO()
        with patch("sys.stdout", out):
            app.run()
        out_fail = out.getvalue()
        results["fail_error_present"] = "[Error /buka] Gagal membuka peramban: Simulated open failure" in out_fail
        results["fail_manual_present"] = "Silakan buka tautan secara manual: https://example.com/fail" in out_fail

    return results


def evaluate_o3_audit_log() -> Dict[str, Any]:
    """Evaluasi kriteria O3: Pencatatan method spesifik dan status ke logs/tool_audit.log."""
    results = {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        audit_file = log_dir / "tool_audit.log"

        # Explorer
        with patch("sys.platform", "win32"), patch("subprocess.Popen", return_value=MagicMock()), patch("enki.tools.web.get_log_dir", return_value=log_dir):
            open_in_browser("https://example.com/aud1")

        # cmd_start
        def side_effect_cmd(args, **kwargs):
            if args[0] == "explorer.exe":
                raise OSError("Fail exp")
            return MagicMock()
        with patch("sys.platform", "win32"), patch("subprocess.Popen", side_effect=side_effect_cmd), patch("enki.tools.web.get_log_dir", return_value=log_dir):
            open_in_browser("https://example.com/aud2")

        # startfile
        with patch("sys.platform", "win32"), patch("subprocess.Popen", side_effect=OSError("fail")), patch("os.startfile", MagicMock()), patch("enki.tools.web.get_log_dir", return_value=log_dir):
            open_in_browser("https://example.com/aud3")

        # all_failed
        with patch("sys.platform", "win32"), patch("subprocess.Popen", side_effect=OSError("fail")), patch("os.startfile", side_effect=OSError("fail")), patch("enki.tools.web.get_log_dir", return_value=log_dir):
            open_in_browser("https://example.com/aud4")

        # webbrowser
        with patch("sys.platform", "linux"), patch("webbrowser.open", return_value=True), patch("enki.tools.web.get_log_dir", return_value=log_dir):
            open_in_browser("https://example.com/aud5")

        content = audit_file.read_text(encoding="utf-8")
        results["method_explorer"] = "url=https://example.com/aud1 | method=explorer | status=OK" in content
        results["method_cmd_start"] = "url=https://example.com/aud2 | method=cmd_start | status=OK" in content
        results["method_startfile"] = "url=https://example.com/aud3 | method=startfile | status=OK" in content
        results["method_all_failed"] = "url=https://example.com/aud4 | method=all_failed | status=ERROR" in content
        results["method_webbrowser"] = "url=https://example.com/aud5 | method=webbrowser | status=OK" in content

    return results


def evaluate_o4_probe_script() -> Dict[str, Any]:
    """Evaluasi kriteria O4: Keberadaan dan struktur bertahap probe_buka_inproc.py."""
    probe_path = REPO_ROOT / "scripts" / "probe_buka_inproc.py"
    exists = probe_path.exists()
    if not exists:
        return {"exists": False}

    code = probe_path.read_text(encoding="utf-8")
    has_l1 = "LANGKAH 1:" in code and "os.startfile" in code
    has_l2 = "LANGKAH 2:" in code and "import enki.cli.console" in code
    has_l3 = "LANGKAH 3:" in code and "TTSManager" in code
    has_l4 = "LANGKAH 4:" in code and "threading.Thread" in code
    has_l5 = "LANGKAH 5:" in code and "EnkiConsole" in code
    has_sleep_5 = "time.sleep(5)" in code

    return {
        "exists": True,
        "path": str(probe_path.relative_to(REPO_ROOT)),
        "steps_intact": has_l1 and has_l2 and has_l3 and has_l4 and has_l5 and has_sleep_5,
        "report_only_note": "Skrip khusus dijalankan oleh Abid secara manual; tidak dieksekusi otomatis oleh harness.",
    }


def evaluate_o5_regression_and_integrity() -> Dict[str, Any]:
    """Evaluasi kriteria O5: Test suite penuh, hash prompt, router SHA, SSRF intact."""
    # 1. Base Prompt Hash
    assembler = PromptAssembler()
    msgs, _ = assembler.assemble_messages("halo", [])
    sys_content = msgs[0]["content"]
    prompt_hash = hashlib.sha256(sys_content.encode("utf-8")).hexdigest()
    prompt_match = (prompt_hash == BASE_PROMPT_HASH)

    # 2. Router SHA
    router_file = REPO_ROOT / "enki" / "core" / "capability_router.py"
    router_sha = hashlib.sha256(router_file.read_bytes()).hexdigest()
    router_match = (router_sha == ROUTER_SHA)

    # 3. SSRF & URL Validation
    v_ok, _, _ = validate_url("https://example.com")
    v_priv, _, _ = validate_url("http://127.0.0.1")
    v_file, _, _ = validate_url("file:///etc/passwd")
    ssrf_ok = v_ok and (not v_priv) and (not v_file)

    # 4. Run Test Suite
    suite = unittest.defaultTestLoader.discover(str(REPO_ROOT / "tests"))
    runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0)
    suite_res = runner.run(suite)
    test_suite_passed = (len(suite_res.failures) == 0 and len(suite_res.errors) == 0)

    return {
        "prompt_hash": prompt_hash,
        "prompt_match": prompt_match,
        "router_sha": router_sha,
        "router_match": router_match,
        "ssrf_ok": ssrf_ok,
        "total_tests": suite_res.testsRun,
        "failures": len(suite_res.failures),
        "errors": len(suite_res.errors),
        "skipped": len(suite_res.skipped),
        "suite_passed": test_suite_passed,
    }


def main():
    writer = TeeWriter(EVIDENCE_FILE)
    sys.stdout = writer

    now_iso = datetime.datetime.now().isoformat()
    head_sha, is_dirty = get_git_info()

    print("=" * 75)
    print("BUKTI EMPIRIS TAHAP 6-FIX-2: /buka VIA PROSES TERPISAH & DIAGNOSA IN-PROCESS")
    print("=" * 75)
    print(f"Waktu Pengujian : {now_iso}")
    print(f"Git Commit      : {head_sha}")
    print(f"Git Dirty       : {is_dirty}")
    print(f"Python Exec     : {sys.executable}")
    print(f"Platform OS     : {sys.platform}")
    print(f"Base Prompt Hash: {BASE_PROMPT_HASH}")
    print(f"Router SHA      : {ROUTER_SHA}")

    print("\n" + "=" * 75)
    print("BAGIAN 1: KONTEKS LIVE TEST & DATA 5 CARA MANDIRI ABID")
    print("=" * 75)
    print("Konteks Uji Mandiri Python di Mesin Abid (Windows, Edge Default, Non-Admin):")
    print("  1. os.startfile(url)                    : 3/3 BERHASIL")
    print("  2. webbrowser.open(url)                 : 3/3 BERHASIL")
    print("  3. Popen(['explorer.exe', url])         : 3/3 BERHASIL")
    print("  4. Popen(['cmd', '/c', 'start', '', url]): 3/3 BERHASIL")
    print("  5. Popen(['powershell', 'Start-Process']): 3/3 BERHASIL")
    print("Masalah Teramati:")
    print("  - Seluruh cara di atas 100% membuka peramban saat dipanggil dari Python mandiri.")
    print("  - Namun pemanggilan /buka di dalam proses interaktif ENKI tidak memunculkan")
    print("    jendela di beberapa sesi (example.com dan youtube.com), walau tool_audit.log")
    print("    mencatat status=OK.")
    print("  - Kesimpulan: Penyebab kegagalan berasal dari kondisi internal di dalam proses")
    print("    ENKI (thread, console, COM initialization, dsb).")
    print("  - Status Akar Masalah: BELUM PASTI (diperlukan diagnosa bertahap in-process O4).")

    print("\n" + "=" * 75)
    print("BAGIAN 2: EVALUASI KRITERIA O1 (LAUNCHER PROSES TERPISAH & KEAMANAN)")
    print("=" * 75)
    o1_res = evaluate_o1_launcher()
    for k, v in o1_res.items():
        status_txt = "LULUS" if v else "GAGAL"
        print(f"  - [{status_txt}] {k}: {v}")
    o1_all_passed = all(o1_res.values())
    print(f"Status Kriteria O1: {'LULUS' if o1_all_passed else 'GAGAL'}")

    print("\n" + "=" * 75)
    print("BAGIAN 3: EVALUASI KRITERIA O2 (PESAN JUJUR TANPA KLAIM JENDELA FISIK)")
    print("=" * 75)
    o2_res = evaluate_o2_honest_messages()
    for k, v in o2_res.items():
        status_txt = "LULUS" if v else "GAGAL"
        print(f"  - [{status_txt}] {k}: {v}")
    o2_all_passed = all(o2_res.values())
    print(f"Status Kriteria O2: {'LULUS' if o2_all_passed else 'GAGAL'}")

    print("\n" + "=" * 75)
    print("BAGIAN 4: EVALUASI KRITERIA O3 (PENCATATAN AUDIT LOG METODE & HASIL)")
    print("=" * 75)
    o3_res = evaluate_o3_audit_log()
    for k, v in o3_res.items():
        status_txt = "LULUS" if v else "GAGAL"
        print(f"  - [{status_txt}] {k}: {v}")
    o3_all_passed = all(o3_res.values())
    print(f"Status Kriteria O3: {'LULUS' if o3_all_passed else 'GAGAL'}")

    print("\n" + "=" * 75)
    print("BAGIAN 5: VERIFIKASI KRITERIA O4 (SKRIP DIAGNOSA IN-PROCESS REPORT-ONLY)")
    print("=" * 75)
    o4_res = evaluate_o4_probe_script()
    print(f"  - Berkas Ditemukan : {o4_res['exists']}")
    if o4_res['exists']:
        print(f"  - Jalur Skrip      : {o4_res['path']}")
        print(f"  - Langkah 1 s.d. 5 : {'LENGKAP' if o4_res['steps_intact'] else 'TIDAK LENGKAP'}")
        print(f"  - Catatan Eksekusi : {o4_res['report_only_note']}")
    o4_all_passed = o4_res['exists'] and o4_res.get('steps_intact', False)
    print(f"Status Kriteria O4: {'LULUS (DOKUMENTASI TERSEDIA)' if o4_all_passed else 'GAGAL'}")

    print("\n" + "=" * 75)
    print("BAGIAN 6: EVALUASI KRITERIA O5 (PRESERVASI KEAMANAN & NOL REGRESI)")
    print("=" * 75)
    o5_res = evaluate_o5_regression_and_integrity()
    print(f"  - Base Prompt Hash Match: {o5_res['prompt_match']} ({o5_res['prompt_hash']})")
    print(f"  - Router SHA Match      : {o5_res['router_match']} ({o5_res['router_sha']})")
    print(f"  - SSRF Defenses Intact  : {o5_res['ssrf_ok']}")
    print(f"  - Unit Tests Run        : {o5_res['total_tests']}")
    print(f"  - Failures              : {o5_res['failures']}")
    print(f"  - Errors                : {o5_res['errors']}")
    print(f"  - Skipped               : {o5_res['skipped']}")
    o5_all_passed = o5_res['prompt_match'] and o5_res['router_match'] and o5_res['ssrf_ok'] and o5_res['suite_passed']
    print(f"Status Kriteria O5: {'LULUS' if o5_all_passed else 'GAGAL'}")

    print("\n" + "=" * 75)
    print("RINGKASAN HASIL EVALUASI TAHAP 6-FIX-2:")
    print("=" * 75)
    print(f"  - O1 (Launcher Urutan Windows & Argumen List): {'LULUS' if o1_all_passed else 'GAGAL'}")
    print(f"  - O2 (Pesan Jujur Tanpa Klaim Jendela Fisik)  : {'LULUS' if o2_all_passed else 'GAGAL'}")
    print(f"  - O3 (Pencatatan Audit Log Metode & Hasil)    : {'LULUS' if o3_all_passed else 'GAGAL'}")
    print(f"  - O4 (Diagnosa In-Process REPORT-ONLY)        : {'LULUS (MENUNGGU ABID)' if o4_all_passed else 'GAGAL'}")
    print(f"  - O5 (Preservasi Keamanan & Nol Regresi)      : {'LULUS' if o5_all_passed else 'GAGAL'}")
    print("STATUS KELULUSAN TAHAP 6-FIX-2: MENUNGGU VERIFIKASI LIVE ABID")
    print("=" * 75)

    writer.close()


if __name__ == "__main__":
    main()
