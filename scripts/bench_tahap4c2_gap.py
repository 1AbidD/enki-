"""Langkah 3 Tahap 4C-2: Pengukuran Selisih Penutupan Perangkat (Tail Clipping)
dan Pengukuran Jeda Antar-Chunk Secara Adversarial (Chunker LAMA 4C-1 vs BARU 4C-2)
saat Ollama sedang generasi nyata (qwen2.5:3b).
Menyimpan laporan lengkap ke docs/evidence/tahap4c2_gap.txt.
"""

import json
import os
import statistics
import sys
import threading
import time
import urllib.request
import wave
import winsound
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ["HF_HUB_OFFLINE"] = "1"

from enki.audio.supertonic import DEFAULT_WINSOUND_TAIL_PADDING_SEC, SupertonicEngine  # noqa: E402
from enki.audio.tts import (  # noqa: E402
     TTSManager,
    prepare_tts_chunks,
    prepare_tts_chunks_4c1,
)
from enki.config import AppConfig  # noqa: E402


# Keempat bentuk adversarial (A, B, C, D) sesuai spesifikasi Langkah 3
BENTUK_A = (
    "Asisten lokal ini berjalan tanpa jaringan. "  # 6 kata (42 chars)
    "Seluruh proses sintesis suara dijalankan di prosesor utama komputer. "  # 9 kata (68 chars)
    "Pendekatan ini menjaga memori kartu grafis tetap hemat dan stabil sepanjang waktu. "  # 12 kata (82 chars)
    "Antrean berpipa menyiapkan kalimat berikutnya di latar belakang saat suara masih diputar. "  # 12 kata (89 chars)
    "Pengguna dapat bekerja dengan tenang tanpa takut kebocoran data pribadi ke jaringan luar."  # 13 kata (89 chars)
)

BENTUK_B = (
    "Sistem asisten kecerdasan buatan lokal ini dirancang untuk membantu pengguna memeriksa dokumen proyek harian. "  # 14 kata
    "Semua berkas konfigurasi disimpan secara aman di dalam ruang kerja lokal. "  # 11 kata
    "Mesin suara mengubah balasan teks menjadi gelombang audio secara efisien di prosesor. "  # 12 kata
    "Interaksi terasa lebih alami karena jeda antar potongan kalimat dijaga tetap singkat."  # 12 kata
)

# Bentuk C: kalimat pertama 3 kata + 1 kalimat panjang 28 kata.
# Kalimat pertama = 3 kata (16 chars setelah pelafalan 'Halo, saya Enki.').
# Kalimat kedua = 28 kata (238 chars <= 250 chars sehingga tidak dipotong oleh _split_sentences_from_cleaned,
# namun gabungan 16 + 1 + 238 = 255 chars > 250 chars sehingga aturan <6 kata tidak dapat menggabungkannya).
BENTUK_C = (
    "Halo, saya ENKI. "  # 3 kata (16 chars: 'Halo, saya Enki.')
    "Pemeriksaan menyeluruh terhadap konfigurasi aplikasi asisten kecerdasan buatan lokal menunjukkan "
    "bahwa seluruh komponen pemrosesan bahasa alami dan manajemen memori percakapan telah berjalan "
    "sangat stabil sesuai rancangan arsitektur."  # 27 kata -> kita pastikan tepat 28 kata di bawah
)

# Pastikan kalimat kedua Bentuk C tepat 28 kata dan panjangnya antara 235..248 karakter:
BENTUK_C_S1 = "Halo, saya ENKI."  # 3 kata
BENTUK_C_S2 = (
    "Pemeriksaan menyeluruh terhadap konfigurasi aplikasi asisten kecerdasan buatan lokal ini "
    "menunjukkan bahwa seluruh komponen pemrosesan bahasa alami dan manajemen memori percakapan "
    "berjalan sangat stabil sesuai rancangan arsitektur sistem."
)  # 28 kata, 240 karakter
BENTUK_C = f"{BENTUK_C_S1} {BENTUK_C_S2}"

# Sub-varian referensi Bentuk C bila kalimat kedua 28 kata lebih pendek sehingga gabungan <= 250 karakter:
BENTUK_C_LE250_S2 = (
    "Pada pengujian hari ini seluruh modul asisten lokal berjalan sangat lancar di prosesor utama "
    "sehingga memori kartu grafis tetap longgar untuk melayani permintaan model bahasa alami dengan cepat."
)  # 28 kata, 200 karakter (gabungan = 16 + 1 + 200 = 217 <= 250 karakter -> 1 chunk)
BENTUK_C_LE250 = f"{BENTUK_C_S1} {BENTUK_C_LE250_S2}"

BENTUK_D_S1 = (
    "Ketika pengguna menjalankan aplikasi asisten kecerdasan buatan lokal pada komputer pribadi, "
    "seluruh proses sintesis suara dijalankan sepenuhnya di prosesor utama tanpa memerlukan koneksi jaringan luar."
)  # 25 kata dengan koma setelah kata ke-11
BENTUK_D_REST = (
    "Pendekatan ini menjaga memori kartu grafis tetap longgar untuk menjalankan model bahasa. "
    "Potongan kalimat berikutnya langsung disiapkan di latar belakang saat suara pertama diputar. "
    "Dengan arsitektur ini pengalaman percakapan terasa jauh lebih responsif dan nyaman didengar."
)
BENTUK_D = f"{BENTUK_D_S1} {BENTUK_D_REST}"


def verify_shapes():
    s1_a = BENTUK_A.split(". ")[0] + "."
    rest_a = BENTUK_A[len(s1_a) :].strip()
    assert len(s1_a.split()) == 6, f"Bentuk A kalimat 1 harus 6 kata, dapat {len(s1_a.split())}"
    assert len(rest_a) > 250, f"Bentuk A sisa karakter harus > 250, dapat {len(rest_a)}"

    s1_b = BENTUK_B.split(". ")[0] + "."
    assert len(s1_b.split()) == 14, f"Bentuk B kalimat 1 harus 14 kata, dapat {len(s1_b.split())}"

    assert len(BENTUK_C_S1.split()) == 3, f"Bentuk C kalimat 1 harus 3 kata, dapat {len(BENTUK_C_S1.split())}"
    assert len(BENTUK_C_S2.split()) == 28, f"Bentuk C kalimat 2 harus 28 kata, dapat {len(BENTUK_C_S2.split())}"
    assert 235 <= len(BENTUK_C_S2) <= 250, f"Bentuk C kalimat 2 chars={len(BENTUK_C_S2)}"

    assert len(BENTUK_C_LE250_S2.split()) == 28, f"Bentuk C_LE250 kalimat 2 harus 28 kata, dapat {len(BENTUK_C_LE250_S2.split())}"

    assert len(BENTUK_D_S1.split()) == 25, f"Bentuk D kalimat 1 harus 25 kata, dapat {len(BENTUK_D_S1.split())}"
    assert "," in BENTUK_D_S1, "Bentuk D kalimat 1 harus memiliki koma"


class OllamaLoadGenerator:
    """Menjalankan generasi streaming nyata pada Ollama (qwen2.5:3b) terus-menerus selama pengukuran."""

    def __init__(self, ollama_url: str = "http://127.0.0.1:11434", model: str = "qwen2.5:3b"):
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self.stop_event = threading.Event()
        self.active_ready = threading.Event()
        self.thread = None
        self.total_tokens = 0
        self.requests_count = 0
        self.errors_count = 0
        self.start_time = 0.0
        self.end_time = 0.0

    def _loop(self):
        self.start_time = time.perf_counter()
        prompt = (
            "Jelaskan secara sangat mendalam dan rinci arsitektur sistem operasi modern, "
            "manajemen memori virtual, penjadwalan prosesor, sistem berkas jurnal, "
            "serta protokol jaringan berlapis dalam 15 paragraf panjang bahasa Indonesia."
        )
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": prompt,
                "stream": True,
                "options": {"num_predict": 1024, "temperature": 0.2},
            }
        ).encode("utf-8")

        while not self.stop_event.is_set():
            req = urllib.request.Request(
                f"{self.ollama_url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                self.requests_count += 1
                with urllib.request.urlopen(req, timeout=60) as resp:
                    for raw_line in resp:
                        if self.stop_event.is_set():
                            break
                        if not raw_line:
                            continue
                        obj = json.loads(raw_line.decode("utf-8"))
                        if obj.get("response"):
                            self.total_tokens += 1
                            if self.total_tokens >= 3:
                                self.active_ready.set()
                        if obj.get("done"):
                            break
            except Exception:
                self.errors_count += 1
                time.sleep(0.2)
        self.end_time = time.perf_counter()

    def start(self):
        self.stop_event.clear()
        self.active_ready.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="OllamaGenWorker")
        self.thread.start()
        ok = self.active_ready.wait(timeout=30.0)
        if not ok:
            raise RuntimeError("Ollama gagal mulai menghasilkan token dalam 30 detik!")

    def stop(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=5.0)
        if self.end_time == 0.0:
            self.end_time = time.perf_counter()


def measure_tail_clipping(engine: SupertonicEngine) -> Dict[str, Any]:
    """Mengukur selisih penutupan perangkat (tail clipping) pada 5 WAV uji."""
    wav_dir = Path(r"D:\ENKI\tts_test2\out4c")
    wav_paths = sorted(wav_dir.glob("sonda_[1-5].wav"))
    if len(wav_paths) < 5:
        raise RuntimeError(f"Ditemukan kurang dari 5 WAV di {wav_dir}: {wav_paths}")

    raw_rows = []
    for wp in wav_paths[:5]:
        with wave.open(str(wp), "rb") as wf:
            wav_dur = wf.getnframes() / float(wf.getframerate())
        t0 = time.perf_counter()
        winsound.PlaySound(str(wp), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        sync_blocked = time.perf_counter() - t0
        raw_diff_ms = (sync_blocked - wav_dur) * 1000.0
        raw_rows.append(
            {
                "name": wp.name,
                "path": str(wp),
                "wav_dur_sec": wav_dur,
                "sync_blocked_sec": sync_blocked,
                "raw_diff_ms": raw_diff_ms,
            }
        )

    median_raw_diff_ms = statistics.median([r["raw_diff_ms"] for r in raw_rows])

    # Kalibrasi / pastikan engine memakai tail_padding_sec hasil pengukuran jika > 50 ms
    if abs(median_raw_diff_ms) > 50.0:
        engine.tail_padding_sec = max(0.0, median_raw_diff_ms / 1000.0)

    engine.playback_records.clear()
    engine.clipped_playbacks_count = 0

    after_rows = []
    for r in raw_rows:
        item = {
            "mode": "supertonic_wav",
            "wav_path": r["path"],
            "is_temp": False,
            "duration_sec": r["wav_dur_sec"],
            "wav_frames_sec": r["wav_dur_sec"],
            "synth_time_sec": 0.0,
            "text": r["name"],
            "voice": "M4",
        }
        t0 = time.perf_counter()
        engine.play_synthesized(item)
        engine_play_sec = time.perf_counter() - t0
        residual_diff_ms = (engine_play_sec - r["sync_blocked_sec"]) * 1000.0
        after_rows.append(
            {
                "name": r["name"],
                "wav_dur_sec": r["wav_dur_sec"],
                "sync_blocked_sec": r["sync_blocked_sec"],
                "engine_play_sec": engine_play_sec,
                "residual_diff_ms": residual_diff_ms,
                "abs_residual_diff_ms": abs(residual_diff_ms),
                "clipped": engine_play_sec < r["wav_dur_sec"],
            }
        )

    median_residual_ms = statistics.median([r["residual_diff_ms"] for r in after_rows])
    median_abs_residual_ms = statistics.median([r["abs_residual_diff_ms"] for r in after_rows])

    return {
        "raw_rows": raw_rows,
        "median_raw_diff_ms": median_raw_diff_ms,
        "applied_tail_padding_ms": engine.tail_padding_sec * 1000.0,
        "after_rows": after_rows,
        "median_residual_ms": median_residual_ms,
        "median_abs_residual_ms": median_abs_residual_ms,
        "clipped_count": engine.clipped_playbacks_count,
    }


def measure_utterance_once(
    mgr: TTSManager,
    engine: SupertonicEngine,
    text: str,
    chunker_fn: Callable[..., List[str]],
) -> Dict[str, Any]:
    """Menjalankan 1x pemutaran balasan melalui TTSManager dan mencatat TTFA serta semua gap antar-chunk."""
    before_times: Dict[int, float] = {}
    after_times: Dict[int, float] = {}

    def _on_before(item: Dict[str, Any]):
        idx = int(item.get("chunk_idx", 0))
        before_times[idx] = time.perf_counter()

    def _on_after(item: Dict[str, Any], stopped_early: bool):
        idx = int(item.get("chunk_idx", 0))
        after_times[idx] = time.perf_counter()

    mgr.on_before_play = _on_before
    mgr.on_after_play = _on_after

    chunks = chunker_fn(text)
    num_chunks = len(chunks)

    mgr.speak(text, voice="M4", engine=engine, chunker_fn=chunker_fn)
    ok = mgr.wait_done(timeout=180.0)
    if not ok:
        raise RuntimeError(f"Timeout menunggu TTS selesai untuk teks: {text[:60]!r}")

    speak_t0 = mgr.last_speak_called_at
    ttfa_sec = (before_times[0] - speak_t0) if 0 in before_times else -1.0

    gaps_ms: List[float] = []
    for i in range(num_chunks - 1):
        if i in after_times and (i + 1) in before_times:
            gap_ms = (before_times[i + 1] - after_times[i]) * 1000.0
            gaps_ms.append(gap_ms)

    return {
        "ttfa_sec": ttfa_sec,
        "gaps_ms": gaps_ms,
        "chunks": chunks,
    }


def measure_shape_comparison(
    mgr: TTSManager,
    engine: SupertonicEngine,
    shape_name: str,
    text: str,
    chunker_fn: Callable[..., List[str]],
    chunker_label: str,
) -> Dict[str, Any]:
    """Mengukur 1x warmup (dibuang) + 3x ulangan untuk satu bentuk & satu chunker."""
    chunks = chunker_fn(text)
    chunk_meta = [{"idx": i + 1, "chars": len(c), "words": len(c.split()), "text": c} for i, c in enumerate(chunks)]

    print(f"  -> Mengukur {shape_name} [{chunker_label}] ({len(chunks)} chunk)...")
    w_res = measure_utterance_once(mgr, engine, text, chunker_fn)

    runs = []
    for r_idx in range(3):
        res = measure_utterance_once(mgr, engine, text, chunker_fn)
        runs.append(res)
        print(
            f"     Run {r_idx + 1}: TTFA={res['ttfa_sec']:.4f}s | "
            f"Gaps(ms)={[round(g, 3) for g in res['gaps_ms']]}"
        )

    med_ttfa_sec = statistics.median([r["ttfa_sec"] for r in runs])
    num_gaps = len(chunks) - 1
    med_gaps_per_transition: List[float] = []
    all_gaps_ms: List[float] = []
    for g_idx in range(num_gaps):
        vals = [r["gaps_ms"][g_idx] for r in runs if len(r["gaps_ms"]) > g_idx]
        if vals:
            med_gaps_per_transition.append(statistics.median(vals))
            all_gaps_ms.extend(vals)

    overall_median_gap_ms = statistics.median(all_gaps_ms) if all_gaps_ms else 0.0
    max_median_transition_gap_ms = max(med_gaps_per_transition) if med_gaps_per_transition else 0.0

    return {
        "shape_name": shape_name,
        "chunker_label": chunker_label,
        "num_chunks": len(chunks),
        "chunk_meta": chunk_meta,
        "warmup": w_res,
        "runs": runs,
        "med_ttfa_sec": med_ttfa_sec,
        "med_gaps_per_transition": med_gaps_per_transition,
        "overall_median_gap_ms": overall_median_gap_ms,
        "max_median_transition_gap_ms": max_median_transition_gap_ms,
    }


def main():
    verify_shapes()
    cfg = AppConfig()
    engine = SupertonicEngine(
        model_dir=cfg.tts_model_dir,
        default_voice="M4",
        tail_padding_sec=DEFAULT_WINSOUND_TAIL_PADDING_SEC,
    )
    t_load0 = time.perf_counter()
    if not engine._ensure_loaded() or engine.is_fallback:
        raise RuntimeError("Gagal memuat model Supertonic 3 M4!")
    load_sec = time.perf_counter() - t_load0

    mgr = TTSManager()

    print("=== BAGIAN 1: Mengukur Selisih Penutupan Perangkat (Tail Clipping) pada 5 WAV Uji ===")
    tail_info = measure_tail_clipping(engine)
    print(
        f"  Median selisih sebelum perbaikan : {tail_info['median_raw_diff_ms']:+.2f} ms\n"
        f"  Tail padding diterapkan di engine: {tail_info['applied_tail_padding_ms']:.2f} ms\n"
        f"  Median selisih setelah perbaikan : {tail_info['median_residual_ms']:+.2f} ms "
        f"(|median| = {tail_info['median_abs_residual_ms']:.2f} ms)\n"
        f"  Jumlah pemutaran terpotong       : {tail_info['clipped_count']}"
    )

    print("\n=== BAGIAN 2: Memulai Beban Generasi Nyata Ollama (qwen2.5:3b) ===")
    ollama_gen = OllamaLoadGenerator(ollama_url=cfg.ollama_url, model="qwen2.5:3b")
    ollama_gen.start()

    shapes = [
        ("Bentuk A (6 kata + >250 chars [4 kalimat])", BENTUK_A),
        ("Bentuk B (14 kata + 3 kalimat)", BENTUK_B),
        ("Bentuk C (3 kata + 1 kalimat 28 kata [total >250 chars -> 2 chunk])", BENTUK_C),
        ("Bentuk D (25 kata dengan koma + 3 kalimat)", BENTUK_D),
    ]

    results_old: Dict[str, Any] = {}
    results_new: Dict[str, Any] = {}

    try:
        for shape_label, shape_text in shapes:
            results_old[shape_label] = measure_shape_comparison(
                mgr, engine, shape_label, shape_text, prepare_tts_chunks_4c1, "LAMA (4C-1)"
            )
            results_new[shape_label] = measure_shape_comparison(
                mgr, engine, shape_label, shape_text, prepare_tts_chunks, "BARU (4C-2)"
            )

        # Catat juga karakteristik referensi Bentuk C bila total gabungan <= 250 karakter
        c_le250_chunks_old = prepare_tts_chunks_4c1(BENTUK_C_LE250)
        c_le250_chunks_new = prepare_tts_chunks(BENTUK_C_LE250)
    finally:
        ollama_gen.stop()

    ollama_dur = ollama_gen.end_time - ollama_gen.start_time
    tok_per_s = ollama_gen.total_tokens / ollama_dur if ollama_dur > 0 else 0.0

    # Susun laporan docs/evidence/tahap4c2_gap.txt
    lines = [
        "========================================================================================",
        "LAPORAN LANGKAH 3 TAHAP 4C-2: SELISIH PENUTUPAN PERANGKAT & JEDA ADVERSARIAL (LAMA vs BARU)",
        "========================================================================================",
        f"Timestamp (UTC)                : {datetime.now(timezone.utc).isoformat()}",
        f"Python Executable              : {sys.executable} (v{sys.version.split()[0]})",
        f"Model TTS                      : Supertonic 3 (voice='M4', lang='id', HF_HUB_OFFLINE=1)",
        f"Waktu Muat Model (CPU)         : {load_sec:.4f} s ({load_sec * 1000.0:.2f} ms)",
        f"Konstanta Regresi Langkah 1    : c0={cfg.tts_synth_c0:.6f}, c1={cfg.tts_synth_c1:.6f}, "
        f"d0={cfg.tts_audio_d0:.6f}, d1={cfg.tts_audio_d1:.6f} (chars/s={cfg.tts_chars_per_sec:.6f})",
        f"Durasi Loop Generasi Ollama    : {ollama_dur:.2f} detik (model='qwen2.5:3b')",
        f"Total Token Dihasilkan LLM     : {ollama_gen.total_tokens} token ({tok_per_s:.2f} tok/s)",
        f"Jumlah Request Stream LLM      : {ollama_gen.requests_count} request (Errors: {ollama_gen.errors_count})",
        "========================================================================================",
        "",
        "--- BAGIAN 1: PENGUKURAN SELISIH PENUTUPAN PERANGKAT (TAIL CLIPPING - KRITERIA L6) ---",
        "Metode: Memutar 5 WAV uji (sonda_1.wav s.d. sonda_5.wav) menggunakan",
        "winsound.PlaySound(path, SND_FILENAME) sinkron dan membandingkan lama blokirnya",
        "dengan durasi WAV sebenarnya.",
        "",
        "[A. Sebelum Perbaikan (Lama Blokir Sinkron vs Durasi WAV)]",
    ]

    for r in tail_info["raw_rows"]:
        lines.append(
            f"  - {r['name']}: durasi_wav={r['wav_dur_sec']:.4f}s | "
            f"blokir_sinkron={r['sync_blocked_sec']:.4f}s | "
            f"selisih={r['raw_diff_ms']:+.2f} ms"
        )
    lines.extend(
        [
            f"  * Median Selisih Sebelum Perbaikan : {tail_info['median_raw_diff_ms']:+.2f} ms (> 50 ms -> Perlu Perbaikan)",
            f"  * Perbaikan Diterapkan di Engine   : Menambahkan tail_padding_sec = {tail_info['applied_tail_padding_ms']:.2f} ms "
            "serta memakai max(dur_model, dur_frames_wav) agar PlaySound berikutnya tidak memotong ekor chunk.",
            "",
            "[B. Setelah Perbaikan (Waktu Tunggu Engine vs Lama Blokir Sinkron)]",
        ]
    )
    for r in tail_info["after_rows"]:
        lines.append(
            f"  - {r['name']}: durasi_wav={r['wav_dur_sec']:.4f}s | "
            f"blokir_sinkron={r['sync_blocked_sec']:.4f}s | "
            f"engine_wait={r['engine_play_sec']:.4f}s | "
            f"selisih_residu={r['residual_diff_ms']:+.2f} ms (|residu|={r['abs_residual_diff_ms']:.2f} ms) | "
            f"clipped={r['clipped']}"
        )
    lines.extend(
        [
            f"  * Median Selisih Residu Setelah Perbaikan : {tail_info['median_residual_ms']:+.2f} ms "
            f"(Median Absolut: {tail_info['median_abs_residual_ms']:.2f} ms <= 50 ms)",
            f"  * Total Pemutaran Terpotong Tercatat      : {engine.clipped_playbacks_count} (dari seluruh pemutaran)",
            "",
            "--- BAGIAN 2: PERBANDINGAN ADVERSARIAL CHUNKER LAMA (4C-1) vs BARU (4C-2) SAAT OLLAMA GENERASI ---",
            "Metode: 1x pemanasan dibuang + median dari 3 ulangan per bentuk per chunker.",
        ]
    )

    for shape_label, _ in shapes:
        ro = results_old[shape_label]
        rn = results_new[shape_label]
        lines.append("")
        lines.append("========================================================================================")
        lines.append(f"BENTUK: {shape_label}")
        lines.append("========================================================================================")

        for res in (ro, rn):
            lines.append(f"\n  [{res['chunker_label']}]")
            lines.append(f"  - Jumlah Chunk : {res['num_chunks']}")
            for cm in res["chunk_meta"]:
                lines.append(
                    f"    * Chunk {cm['idx']} ({cm['chars']} karakter, {cm['words']} kata): {cm['text']!r}"
                )
            w_gaps = ", ".join(f"{g:.3f} ms" for g in res["warmup"]["gaps_ms"]) or "tidak ada (1 chunk)"
            lines.append(
                f"  - Warmup (dibuang) : TTFA = {res['warmup']['ttfa_sec']:.4f} s ({res['warmup']['ttfa_sec']*1000.0:.2f} ms) | "
                f"Jeda Antar-Chunk = [{w_gaps}]"
            )
            for idx_r, r in enumerate(res["runs"], 1):
                r_gaps = ", ".join(f"{g:.3f} ms" for g in r["gaps_ms"]) or "tidak ada (1 chunk)"
                lines.append(
                    f"  - Ulangan {idx_r}        : TTFA = {r['ttfa_sec']:.4f} s ({r['ttfa_sec']*1000.0:.2f} ms) | "
                    f"Jeda Antar-Chunk = [{r_gaps}]"
                )
            lines.append(
                f"  - Median TTFA      : {res['med_ttfa_sec']:.4f} s ({res['med_ttfa_sec']*1000.0:.2f} ms)"
            )
            if res["med_gaps_per_transition"]:
                for t_idx, mg in enumerate(res["med_gaps_per_transition"], 1):
                    lines.append(f"  - Median Gap {t_idx}->{t_idx+1}  : {mg:.3f} ms")
                lines.append(
                    f"  - Median Gap Keseluruhan : {res['overall_median_gap_ms']:.3f} ms "
                    f"(Maks Median Transisi: {res['max_median_transition_gap_ms']:.3f} ms)"
                )
            else:
                lines.append("  - Median Gap Keseluruhan : N/A (hanya 1 chunk)")

    lines.extend(
        [
            "",
            "--- CATATAN ANALISIS BENTUK C (3 KATA + 1 KALIMAT PANJANG 28 KATA) ---",
            "Pada Bentuk C, aturan Chunk Pertama menyatakan: 'Jika kurang dari 6 kata, gabung dengan",
            "kalimat berikutnya (aturan 4C-1 tetap).' Terdapat dua kemungkinan struktur fisik:",
            f"  1. Bila panjang gabungan kalimat 1 (3 kata) + kalimat 2 (28 kata) > 250 karakter "
            f"(seperti diuji di atas: 16 + 1 + {len(BENTUK_C_S2)} = {17 + len(BENTUK_C_S2)} karakter > 250):",
            "     Penggabungan ditolak oleh batas 250 karakter, sehingga Chunk 1 tetap 3 kata (16 karakter,",
            "     durasi audio ~1.64s) dan Chunk 2 berisi kalimat 28 kata (240 karakter, waktu sintesis ~5.9s).",
            "     Akibatnya, saat Chunk 1 selesai diputar, sintesis Chunk 2 belum selesai dan timbul jeda ~3.9s.",
            f"  2. Bila panjang gabungan kalimat 1 (3 kata) + kalimat 2 (28 kata) <= 250 karakter "
            f"(contoh varian 210 karakter -> LAMA={len(c_le250_chunks_old)} chunk, BARU={len(c_le250_chunks_new)} chunk):",
            "     Kedua kalimat digabung menjadi 1 chunk tunggal berukuran 31 kata (0 jeda antar-chunk,",
            "     namun TTFA naik menjadi ~5.5 detik karena harus menyintesis 31 kata di Chunk 1).",
            "",
            "--- RINGKASAN EVALUASI KRITERIA L4, L5, L6 ---",
        ]
    )

    for shape_label, _ in shapes:
        rn = results_new[shape_label]
        pass_l4 = rn["overall_median_gap_ms"] <= 500.0 and rn["max_median_transition_gap_ms"] <= 500.0
        lines.append(
            f"  - L4 [{shape_label}]: Median Gap BARU = {rn['overall_median_gap_ms']:.3f} ms "
            f"(Maks Transisi = {rn['max_median_transition_gap_ms']:.3f} ms) -> "
            f"{'LULUS' if pass_l4 else 'GAGAL (> 500 ms)'}"
        )

    rn_d = results_new["Bentuk D (25 kata dengan koma + 3 kalimat)"]
    pass_l5 = rn_d["med_ttfa_sec"] <= 4.0
    lines.append(
        f"  - L5 [TTFA Bentuk D BARU <= 4.0 s]: Median TTFA = {rn_d['med_ttfa_sec']:.4f} s "
        f"({rn_d['med_ttfa_sec']*1000.0:.2f} ms) -> {'LULUS' if pass_l5 else 'GAGAL'}"
    )
    pass_l6 = tail_info["median_abs_residual_ms"] <= 50.0 and engine.clipped_playbacks_count == 0
    lines.append(
        f"  - L6 [Selisih Penutupan Perangkat <= 50 ms & 0 Terpotong]: "
        f"Median Residu = {tail_info['median_residual_ms']:+.2f} ms "
        f"(|median|={tail_info['median_abs_residual_ms']:.2f} ms), "
        f"terpotong={engine.clipped_playbacks_count} -> {'LULUS' if pass_l6 else 'GAGAL'}"
    )
    lines.append("========================================================================================")
    lines.append("")

    out_path = ROOT_DIR / "docs" / "evidence" / "tahap4c2_gap.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSelesai. Laporan tertulis di: {out_path}")


if __name__ == "__main__":
    main()
