"""Skrip Diagnosa Kondisi In-Process Pembukaan Peramban (Tahap 6-FIX-2, Kriteria O4).

Tujuan:
Mendiagnosis titik tahapan di dalam proses ENKI di mana pemanggilan pembukaan URL
ke sistem operasi (`os.startfile`) mulai gagal memunculkan jendela peramban fisik.

PETUNJUK UNTUK ABID:
Jalankan skrip ini secara mandiri di PowerShell (bukan via agen):
    .\\venv\\Scripts\\python scripts/probe_buka_inproc.py

Perhatikan layar monitor Anda pada setiap langkah (ada jeda 5 detik antar-langkah).
Catat langkah ke berapa jendela browser (Edge) PERTAMA KALI gagal muncul.
"""

import os
import sys
import time
import threading

URL_PROBE = "https://example.com"


def main():
    if sys.platform != "win32":
        print("[PERINGATAN] Skrip diagnosa ini dirancang khusus untuk lingkungan Windows.")
        return

    print("=" * 70)
    print("DIAGNOSA IN-PROCESS: Titik Kegagalan Pembukaan Peramban")
    print(f"Target URL: {URL_PROBE}")
    print("Petunjuk: Amati apakah jendela browser muncul pada tiap langkah (jeda 5 detik).")
    print("=" * 70)

    # (a) Langkah 1: Python polos (os.startfile langsung)
    print("\nLANGKAH 1: Python polos (os.startfile langsung)...")
    try:
        os.startfile(URL_PROBE)
        print("  -> Perintah os.startfile terkirim.")
    except Exception as e:
        print(f"  -> Exception pada Langkah 1: {e}")
    time.sleep(5)

    # (b) Langkah 2: Setelah import enki.cli.console
    print("\nLANGKAH 2: Setelah import enki.cli.console...")
    try:
        import enki.cli.console  # noqa: F401
        os.startfile(URL_PROBE)
        print("  -> Perintah os.startfile terkirim.")
    except Exception as e:
        print(f"  -> Exception pada Langkah 2: {e}")
    time.sleep(5)

    # (c) Langkah 3: Setelah membuat TTSManager dan memutar 1 kalimat pendek (unmuted)
    print("\nLANGKAH 3: Setelah membuat TTSManager dan memutar 1 kalimat pendek (unmuted)...")
    try:
        from enki.core.tts import TTSManager
        tts = TTSManager(voice="M4", mute=False)
        tts.speak("Tes audio diagnosa kondisi proses.")
        time.sleep(2)  # Beri waktu pemutaran singkat
        os.startfile(URL_PROBE)
        print("  -> Perintah os.startfile terkirim.")
    except Exception as e:
        print(f"  -> Exception pada Langkah 3: {e}")
    time.sleep(5)

    # (d) Langkah 4: Pemanggilan os.startfile dari background thread terpisah
    print("\nLANGKAH 4: Pemanggilan os.startfile dari background thread terpisah...")
    th_exc = []

    def _worker():
        try:
            os.startfile(URL_PROBE)
        except Exception as e:
            th_exc.append(e)

    t = threading.Thread(target=_worker, name="ProbeBgThread", daemon=True)
    t.start()
    t.join(timeout=5.0)
    if th_exc:
        print(f"  -> Exception pada background thread: {th_exc[0]}")
    else:
        print("  -> Perintah os.startfile dari background thread terkirim.")
    time.sleep(5)

    # (e) Langkah 5: Setelah EnkiConsole() diinisialisasi (tanpa run())
    print("\nLANGKAH 5: Setelah EnkiConsole() dibuat (tanpa run())...")
    try:
        from enki.cli.console import EnkiConsole
        _app = EnkiConsole()  # Inisialisasi controller, router, dsb tanpa run()
        os.startfile(URL_PROBE)
        print("  -> Perintah os.startfile terkirim.")
    except Exception as e:
        print(f"  -> Exception pada Langkah 5: {e}")
    time.sleep(5)

    print("\n" + "=" * 70)
    print("DIAGNOSA SELESAI.")
    print("Silakan laporkan ke chat ENKI: pada LANGKAH ke berapa jendela browser")
    print("PERTAMA KALI tidak muncul di layar Anda.")
    print("=" * 70)


if __name__ == "__main__":
    main()
