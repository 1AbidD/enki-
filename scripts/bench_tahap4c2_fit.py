"""Langkah 1 Tahap 4C-2: Pengukuran Model Waktu Sintesis & Durasi Audio Supertonic M4 (Idle).
Menghasilkan laporan lengkap ke docs/evidence/tahap4c2_fit.txt.
"""

import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ["HF_HUB_OFFLINE"] = "1"

from enki.audio.supertonic import SupertonicEngine  # noqa: E402


# 6 ukuran kata (3, 6, 10, 16, 25, 40), masing-masing 2 teks bahasa Indonesia berbeda
SAMPLES = [
    # 3 kata
    (3, "3_kata_A", "Halo, saya Enki."),
    (3, "3_kata_B", "Sistem sudah siap."),
    # 6 kata
    (6, "6_kata_A", "Asisten lokal ini berjalan tanpa jaringan."),
    (6, "6_kata_B", "Semua berkas proyek tersimpan dengan aman."),
    # 10 kata
    (10, "10_kata_A", "Versi Python di sistem ini adalah tiga koma empat belas."),
    (10, "10_kata_B", "Mesin suara bekerja di prosesor utama agar memori grafis hemat."),
    # 16 kata
    (
        16,
        "16_kata_A",
        "Sistem asisten kecerdasan buatan lokal Enki kini menggunakan mesin suara baru yang bekerja sepenuhnya secara luring.",
    ),
    (
        16,
        "16_kata_B",
        "Pengguna dapat memeriksa riwayat perubahan kode sumber dan menjalankan pengujian otomatis langsung dari terminal setiap hari.",
    ),
    # 25 kata
    (
        25,
        "25_kata_A",
        "Asisten kecerdasan buatan lokal ini dirancang untuk membantu pengguna mengelola catatan harian, membaca dokumen proyek, serta menjawab berbagai pertanyaan teknis dengan cepat tanpa memerlukan jaringan.",
    ),
    (
        25,
        "25_kata_B",
        "Pendekatan antrean berpipa memastikan kalimat berikutnya sedang disiapkan di latar belakang saat kalimat sebelumnya masih diputar, sehingga jeda antar bagian terdengar sangat mulus bagi pendengar.",
    ),
    # 40 kata
    (
        40,
        "40_kata_A",
        "Arsitektur perangkat lunak yang dirancang untuk perangkat keras terbatas harus selalu mengutamakan efisiensi penggunaan memori dan waktu tanggap yang konsisten, sehingga seluruh komponen pemrosesan bahasa alami maupun sintesis suara lokal dapat berjalan berdampingan secara stabil tanpa membebani kartu grafis utama.",
    ),
    (
        40,
        "40_kata_B",
        "Ketika pengguna memberikan instruksi panjang melalui antarmuka baris perintah, modul pemecah kalimat akan segera membagi balasan menjadi beberapa potongan yang terukur agar bagian pembuka langsung terdengar sementara bagian selanjutnya disintesis secara bertahap di latar belakang tanpa memotong kalimat secara mendadak.",
    ),
]


def verify_word_counts():
    for expected_words, label, text in SAMPLES:
        words = text.split()
        if len(words) != expected_words:
            raise ValueError(
                f"Sampel {label} memiliki {len(words)} kata (diharapkan {expected_words}): {text!r}"
            )


def linear_regression(xs, ys):
    """Menghitung regresi linear y = b0 + b1 * x dan koefisien determinasi R^2."""
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_yy = sum((y - mean_y) ** 2 for y in ys)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))

    b1 = ss_xy / ss_xx if ss_xx > 0 else 0.0
    b0 = mean_y - b1 * mean_x

    y_pred = [b0 + b1 * x for x in xs]
    ss_res = sum((y - yp) ** 2 for y, yp in zip(ys, y_pred))
    r2 = 1.0 - (ss_res / ss_yy) if ss_yy > 0 else 1.0
    return b0, b1, r2


def main():
    verify_word_counts()
    engine = SupertonicEngine(default_voice="M4")
    t_load0 = time.perf_counter()
    loaded = engine._ensure_loaded()
    load_sec = time.perf_counter() - t_load0
    if not loaded or engine.is_fallback:
        raise RuntimeError("Gagal memuat model Supertonic 3 M4 untuk Langkah 1!")

    # Pemanasan awal model
    warm_item = engine.synthesize_chunk("Halo, saya Enki.", voice="M4")
    engine.discard_synthesized(warm_item, count_as_cancelled=False)

    rows = []
    print("Menjalankan pengukuran Langkah 1 (12 sampel x [1 warmup + 3 ulangan])...")

    for word_count, label, text in SAMPLES:
        char_count = len(text)

        # 1x pemanasan dibuang
        t_w0 = time.perf_counter()
        w_item = engine.synthesize_chunk(text, voice="M4")
        warmup_sec = time.perf_counter() - t_w0
        engine.discard_synthesized(w_item, count_as_cancelled=False)

        synth_runs = []
        dur_runs = []
        for _ in range(3):
            t0 = time.perf_counter()
            item = engine.synthesize_chunk(text, voice="M4")
            elapsed = time.perf_counter() - t0
            dur = float(item["duration_sec"])
            engine.discard_synthesized(item, count_as_cancelled=False)
            synth_runs.append(elapsed)
            dur_runs.append(dur)

        med_synth = statistics.median(synth_runs)
        med_dur = statistics.median(dur_runs)

        row = {
            "word_count": word_count,
            "label": label,
            "text": text,
            "char_count": char_count,
            "warmup_sec": warmup_sec,
            "synth_runs": synth_runs,
            "med_synth_sec": med_synth,
            "med_dur_sec": med_dur,
            "rtf": med_synth / med_dur if med_dur > 0 else 0.0,
        }
        rows.append(row)
        print(
            f"  [{label:10s}] words={word_count:2d} chars={char_count:3d} "
            f"dur={med_dur:.4f}s synth_med={med_synth:.4f}s runs={[round(x, 4) for x in synth_runs]}"
        )

    # Regresi 1: waktu_sintesis = c0 + c1 * durasi_audio
    durs = [r["med_dur_sec"] for r in rows]
    synths = [r["med_synth_sec"] for r in rows]
    c0, c1, r2_synth = linear_regression(durs, synths)

    # Regresi 2: durasi_audio = d0 + d1 * jumlah_karakter
    chars = [float(r["char_count"]) for r in rows]
    d0, d1, r2_audio = linear_regression(chars, durs)
    chars_per_sec = (1.0 / d1) if d1 > 0 else 0.0

    out_lines = [
        "========================================================================================",
        "LAPORAN LANGKAH 1 TAHAP 4C-2: MODEL WAKTU SINTESIS & DURASI AUDIO SUPERTONIC M4 (IDLE)",
        "========================================================================================",
        f"Timestamp (UTC)        : {datetime.now(timezone.utc).isoformat()}",
        f"Python Executable      : {sys.executable} (v{sys.version.split()[0]})",
        f"Model Directory        : {engine.model_dir}",
        f"Voice Supertonic       : {engine.default_voice} (lang='id')",
        f"Waktu Muat Model (CPU) : {load_sec:.4f} s ({load_sec * 1000.0:.2f} ms)",
        "Metode                 : Kondisi Idle, 6 ukuran kata (3, 6, 10, 16, 25, 40) x 2 teks = 12 sampel.",
        "                         Tiap sampel: 1x pemanasan dibuang + median dari 3 ulangan.",
        "========================================================================================",
        "",
        "--- TABEL PENGUKURAN PER SAMPEL (12 SAMPEL) ---",
    ]

    for idx, r in enumerate(rows, 1):
        runs_str = ", ".join(f"{x:.4f}s" for x in r["synth_runs"])
        out_lines.extend(
            [
                f"  [{idx:02d}] {r['label']} ({r['word_count']} kata, {r['char_count']} karakter):",
                f"       Teks             : {r['text']!r}",
                f"       Durasi Audio     : {r['med_dur_sec']:.4f} s",
                f"       Warmup (dibuang) : {r['warmup_sec']:.4f} s",
                f"       3 Ulangan Synth  : [{runs_str}]",
                f"       Median Synth     : {r['med_synth_sec']:.4f} s (RTF: {r['rtf']:.4f}x)",
            ]
        )

    out_lines.extend(
        [
            "",
            "--- HASIL REGRESI LINEAR (N = 12 SAMPEL) ---",
            "1. Model Waktu Sintesis terhadap Durasi Audio:",
            "   Rumus : waktu_sintesis = c0 + c1 * durasi_audio",
            f"   c0    : {c0:.6f} detik",
            f"   c1    : {c1:.6f} (detik sintesis per detik audio)",
            f"   R^2   : {r2_synth:.6f}",
            "",
            "2. Model Durasi Audio terhadap Jumlah Karakter:",
            "   Rumus : durasi_audio = d0 + d1 * jumlah_karakter",
            f"   d0    : {d0:.6f} detik",
            f"   d1    : {d1:.6f} (detik audio per karakter)",
            f"   R^2   : {r2_audio:.6f}",
            f"   tts_chars_per_sec (1 / d1) : {chars_per_sec:.6f} karakter/detik",
            "",
            "--- KONSTANTA YANG DISIMPAN DI CONFIG (enki/config.py) ---",
            f"   tts_synth_c0      = {c0:.6f}",
            f"   tts_synth_c1      = {c1:.6f}",
            f"   tts_audio_d0      = {d0:.6f}",
            f"   tts_audio_d1      = {d1:.6f}",
            f"   tts_chars_per_sec = {chars_per_sec:.6f}",
            "========================================================================================",
            "",
        ]
    )

    out_path = ROOT_DIR / "docs" / "evidence" / "tahap4c2_fit.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"\nSelesai. Bukti tertulis di: {out_path}")


if __name__ == "__main__":
    main()
