#!/usr/bin/env python3
"""Probe in-process /buka (Tahap 6-FIX-2 O4, diperbaiki & diotomatiskan di Tahap 6-FIX-3).

Perbaikan dari versi 6-FIX-2:
- Root repo ditambahkan ke sys.path (sebelumnya gagal "No module named 'enki'" bila
  dijalankan sebagai `python scripts/probe_buka_inproc.py`).
- Impor TTS diperbaiki ke `enki.audio.tts` (sebelumnya `enki.core.tts` yang tidak ada).
- Hasil tidak lagi diamati manusia: memakai detektor jendela otomatis K1 dari
  scripts/probe_open_url.py, dengan reset kondisi A (taskkill msedge.exe) sebelum tiap percobaan.

Langkah (kumulatif dalam SATU proses), N=3 per langkah, metode os.startfile:
  (a) Python polos  (b) setelah import enki.cli.console  (c) setelah TTSManager + 1 kalimat suara
  (d) os.startfile dari thread latar (daemon, join timeout)  (e) setelah EnkiConsole() dibuat

PERINGATAN: mematikan Edge berulang kali. Pakai --yes hanya setelah izin eksplisit.
Hasil: docs/evidence/tahap6_buka_inproc.txt
"""

import argparse
import datetime
import os
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
for _p in (str(REPO_ROOT), str(SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import probe_open_url as pou  # noqa: E402

EVIDENCE = REPO_ROOT / "docs" / "evidence" / "tahap6_buka_inproc.txt"
URL_PROBE = pou.TEST_URL
N = 3


def trial_once(launch) -> dict:
    procs_after_kill = pou.kill_edge()
    before = pou.snapshot()
    err = None
    try:
        launch()
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
    ok, lat, title = pou.detect_new_marker(before["_keys"])
    after = pou.snapshot()
    return {
        "procs_after_kill": procs_after_kill,
        "success": ok,
        "latency_s": round(lat, 3) if lat is not None else None,
        "title": title,
        "err": err,
        "procs": f"{before['edge_procs']}->{after['edge_procs']}",
        "vis": f"{before['visible_windows']}->{after['visible_windows']}",
    }


def run_step(label: str, launch, results: dict) -> None:
    print(f"\nLANGKAH {label}")
    rs = []
    for i in range(1, N + 1):
        r = trial_once(launch)
        rs.append(r)
        print(f"  #{i} success={r['success']} latency={r['latency_s']} procs={r['procs']} vis={r['vis']} "
              f"kill_left={r['procs_after_kill']} err={r['err']} title={r['title']!r}")
        time.sleep(pou.GAP_S)
    s = sum(1 for r in rs if r["success"])
    results[label] = s
    print(f"  => {s}/{N}")


def startfile_main():
    os.startfile(URL_PROBE)


def startfile_thread():
    errs = []

    def _w():
        try:
            os.startfile(URL_PROBE)
        except Exception as e:  # pragma: no cover
            errs.append(e)

    t = threading.Thread(target=_w, name="ProbeBgThread", daemon=True)
    t.start()
    t.join(timeout=5.0)
    if errs:
        raise errs[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    if sys.platform != "win32":
        print("Hanya untuk Windows.")
        return
    pou.confirm_or_exit(args.yes)

    tee = pou.Tee(EVIDENCE)
    sys.stdout = tee
    head, dirty = pou.get_git_info()
    print("=" * 75)
    print("PROBE IN-PROCESS /buka (deteksi jendela otomatis, reset kondisi A tiap percobaan)")
    print("=" * 75)
    print(f"time                 : {datetime.datetime.now().isoformat()}")
    print(f"git_head             : {head}")
    print(f"git_dirty            : {dirty}")
    print(f"harness_session_id   : {pou.session_id(os.getpid())}")
    print(f"explorer_session_ids : {pou.explorer_sessions()}")
    print(f"default_https_progid : {pou.default_https_progid()}")
    print(f"N per langkah        : {N}, metode os.startfile, url {URL_PROBE}")

    results: dict = {}
    run_step("a: Python polos", startfile_main, results)

    try:
        import enki.cli.console  # noqa: F401
        print("\n[import enki.cli.console OK]")
    except Exception as e:
        print(f"\n[import enki.cli.console GAGAL: {e}]")
    run_step("b: setelah import enki.cli.console", startfile_main, results)

    try:
        from enki.audio.tts import get_tts_manager
        mgr = get_tts_manager(create=True)
        mgr.speak("Tes audio diagnosa kondisi proses.")
        done = mgr.wait_done(timeout=20.0)
        time.sleep(1.0)
        print(f"\n[TTSManager speak selesai={done}]")
    except Exception as e:
        print(f"\n[TTSManager GAGAL: {type(e).__name__}: {e}]")
    run_step("c: setelah TTSManager + 1 kalimat", startfile_main, results)

    run_step("d: os.startfile dari thread latar", startfile_thread, results)

    try:
        from enki.cli.console import EnkiConsole
        _app = EnkiConsole()
        print("\n[EnkiConsole() dibuat]")
    except Exception as e:
        print(f"\n[EnkiConsole() GAGAL: {type(e).__name__}: {e}]")
    run_step("e: setelah EnkiConsole() dibuat", startfile_main, results)

    pou.kill_edge()

    print("\n" + "=" * 75)
    print("RINGKASAN (sukses/N):")
    for k, v in results.items():
        print(f"  {k:40} {v}/{N}")
    a = results.get("a: Python polos", 0)
    later = [v for k, v in results.items() if not k.startswith("a:")]
    if a >= 2 and any(v == 0 for v in later):
        verdict = "DIDUKUNG (ada langkah ENKI 0/3 sementara Python polos >= 2/3)"
    elif all(v >= a - 1 for v in later):
        verdict = "DIGUGURKAN untuk kondisi A (semua langkah ENKI >= sukses(a) - 1)"
    else:
        verdict = "BELUM PASTI"
    print(f"Hipotesis 'kondisi proses ENKI': {verdict}")
    print("=" * 75)
    tee.close()
    os._exit(0)  # hindari menunggu thread non-daemon milik komponen ENKI (TTS/DB)


if __name__ == "__main__":
    main()
