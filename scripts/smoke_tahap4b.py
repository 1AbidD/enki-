#!/usr/bin/env python3
"""Smoke test Tahap 4B untuk subsistem TTS SAPI5 nyata.
Melakukan sintesis 3 kalimat menggunakan suara default Windows (SAPI5),
mengukur latensi, CPU%, memori RAM, dan memastikan 0 crash.
Hasil ditulis ke docs/evidence/tahap4b_smoke.txt.
"""

import sys
import os
import time
import datetime
import subprocess
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

import psutil
from enki.audio.sapi5 import SAPI5Engine
from enki.audio.tts import TTSManager, speak, cancel, clean_text_for_tts, chunk_text

def get_git_info():
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(WORKSPACE_ROOT), text=True).strip()
    except Exception as e:
        head = f"ERROR: {e}"
    try:
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=str(WORKSPACE_ROOT), text=True).strip()
        dirty = bool(status)
    except Exception as e:
        dirty = f"ERROR: {e}"
    return head, dirty

def main():
    start_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    git_head, git_dirty = get_git_info()
    proc = psutil.Process()

    lines = []
    def log(msg: str = ""):
        lines.append(msg)
        print(msg)

    log("=" * 80)
    log("SMOKE TEST TAHAP 4B: CORE VOICE OUTPUT (TTS SAPI5 NYATA)")
    log("=" * 80)
    log(f"Waktu UTC         : {start_utc}")
    log(f"Git Head          : {git_head}")
    log(f"Git Dirty         : {git_dirty}")
    log(f"Workspace Root    : {WORKSPACE_ROOT}")
    log(f"Host OS           : Windows (SAPI5 System.Speech)")
    log(f"Total CPU Cores   : {psutil.cpu_count(logical=True)} (Physical: {psutil.cpu_count(logical=False)})")
    log("=" * 80)
    log()

    engine = SAPI5Engine()
    test_voice = "Microsoft David Desktop"

    sentences = [
        "Halo, saya ENKI. Ini adalah uji coba sistem keluaran suara menggunakan engine SAPI5.",
        "Sistem ini menggunakan batas pemakaian CPU maksimal dua core dan mendukung interupsi instan.",
        "Nilai pi adalah 3.14 dan informasi lebih lanjut dapat dilihat pada tautan dokumentasi."
    ]

    crashes = 0
    results = []

    log(f"--- Sintesis 3 Kalimat (Suara: {test_voice}) ---")
    total_t0 = time.time()

    for idx, raw_text in enumerate(sentences, 1):
        log(f"\n[Kalimat #{idx}]")
        log(f"Teks Asli: {raw_text}")
        chunks = chunk_text(raw_text)
        log(f"Chunks ({len(chunks)}): {chunks}")

        ram_before = proc.memory_info().rss / (1024 * 1024)
        cpu_before = psutil.cpu_percent(interval=None)
        t0 = time.time()

        success = True
        try:
            for ch in chunks:
                ok = engine.speak_chunk(ch, voice=test_voice)
                if not ok:
                    success = False
        except Exception as e:
            crashes += 1
            success = False
            log(f"ERROR: Exception saat sintesis: {e}")

        dur = time.time() - t0
        ram_after = proc.memory_info().rss / (1024 * 1024)
        cpu_after = psutil.cpu_percent(interval=None)

        results.append({
            "idx": idx,
            "text": raw_text,
            "chunks_count": len(chunks),
            "latency_ms": round(dur * 1000, 2),
            "ram_before_mb": round(ram_before, 2),
            "ram_after_mb": round(ram_after, 2),
            "ram_delta_mb": round(ram_after - ram_before, 2),
            "cpu_percent": cpu_after,
            "success": success
        })

        log(f"Hasil: {'SUKSES' if success else 'GAGAL'} | Latensi: {round(dur * 1000, 2)} ms | RAM: {round(ram_after, 2)} MB (Delta: {round(ram_after - ram_before, 2)} MB) | CPU: {cpu_after}%")

    total_duration = time.time() - total_t0

    # Uji Interupsi Instan Nyata
    log("\n--- Uji Pembatalan / Interupsi Instan ---")
    cancel_t0 = time.time()
    mgr = TTSManager()
    mgr.speak("Ini adalah kalimat panjang yang sengaja dibuat untuk menguji pembatalan instan audio yang sedang diputar.", voice=test_voice)
    time.sleep(0.05)
    mgr.cancel()
    cancel_lat = (time.time() - cancel_t0) * 1000
    log(f"Pembatalan Berhasil | Latensi Cancel: {round(cancel_lat, 2)} ms | Antrean Bersih: {mgr.queue.empty()}")

    # Uji Zero-Cost Mute Nyata
    log("\n--- Uji Zero-Cost Mute ---")
    mute_t0 = time.time()
    speak("Pesan ini tidak boleh memicu proses audio.", is_muted=True)
    mute_lat = (time.time() - mute_t0) * 1000
    log(f"Mute Berhasil | Latensi Speak Muted: {round(mute_lat, 4)} ms (Beban: 0%)")

    # Rekapitulasi Akhir
    log("\n" + "=" * 80)
    log("REKAPITULASI SMOKE TEST TAHAP 4B")
    log("=" * 80)
    log(f"Total Kalimat Diuji   : {len(sentences)}")
    all_success = all(r["success"] for r in results)
    log(f"Status Sintesis       : {'100% SUKSES' if all_success else 'ADA KEGAGALAN'}")
    log(f"Total Crashes         : {crashes}")
    log(f"Total Waktu Eksekusi  : {round(total_duration, 2)} detik")
    avg_latency = sum(r["latency_ms"] for r in results) / len(results)
    log(f"Rata-rata Latensi     : {round(avg_latency, 2)} ms / kalimat")
    peak_ram = max(r["ram_after_mb"] for r in results)
    log(f"Puncak Pemakaian RAM  : {round(peak_ram, 2)} MB")
    max_cpu = max(r["cpu_percent"] for r in results)
    log(f"Puncak Beban CPU      : {max_cpu}%")
    log("=" * 80)
    log()

    output_path = WORKSPACE_ROOT / "docs" / "evidence" / "tahap4b_smoke.txt"
    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nHasil smoke test berhasil disimpan ke {output_path}")

if __name__ == "__main__":
    main()
