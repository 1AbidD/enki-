"""Skrip Pengukuran & Bukti Tahap 4C-3:
1. Langkah 2 (M8): Pengukuran independen tail clipping pada 6 WAV baru (2-12 detik) tanpa mengubah padding.
2. Langkah 3 & 4 (M1, M2, M4, M5, M6): Pengukuran tampilan teks sinkron, mulai tertunda (D),
   jeda antar-chunk Bentuk A-E (semua run dilaporkan), latensi token pertama -> teks pertama tampil,
   dan interupsi nyata saat Ollama sedang generasi nyata (qwen2.5:3b).
Menyimpan bukti ke:
- docs/evidence/tahap4c3_tail_clipping.txt
- docs/evidence/tahap4c3_bench.txt
"""

import io
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
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ["HF_HUB_OFFLINE"] = "1"

from enki.audio.supertonic import DEFAULT_WINSOUND_TAIL_PADDING_SEC, SupertonicEngine  # noqa: E402
from enki.audio.tts import (  # noqa: E402
    TTSManager,
    compute_gapless_start_delay,
    prepare_tts_chunks,
    run_synced_speech_stream,
)
from enki.config import AppConfig  # noqa: E402


# 6 teks BARU independen untuk Langkah 2 (M8) - berbeda dari sonda_1..5 dan 12 sampel regresi 4C-2
INDEPENDENT_TAIL_TEXTS = [
    (
        "indep_1.wav",
        "Pagi ini cuaca sangat cerah.",
    ),
    (
        "indep_2.wav",
        "Pembaruan kode sumber telah berhasil diuji pada ruang kerja lokal.",
    ),
    (
        "indep_3.wav",
        "Setiap fungsi utama dalam modul antarmuka konsol memiliki cakupan pengujian otomatis yang ketat.",
    ),
    (
        "indep_4.wav",
        "Pengelolaan basis data riwayat percakapan menggunakan indeks pencarian teks penuh agar pengambilan catatan masa lalu berlangsung cepat.",
    ),
    (
        "indep_5.wav",
        "Ketika perintah pembatalan dikirimkan oleh pengguna di tengah pembacaan kalimat, antrean pemutaran segera dikosongkan dan sisa jawaban langsung ditampilkan.",
    ),
    (
        "indep_6.wav",
        "Pengujian ketahanan sistem dilakukan secara menyeluruh dengan memadukan beban generasi model bahasa alami di kartu grafis serta pemrosesan gelombang suara di prosesor utama secara bersamaan.",
    ),
]


# Kelima bentuk uji M5 (Bentuk A, B, C, D persis 4C-2 + Bentuk E <= 250 karakter)
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

BENTUK_C_S1 = "Halo, saya ENKI."  # 3 kata (16 chars setelah pelafalan)
BENTUK_C_S2 = (
    "Pemeriksaan menyeluruh terhadap konfigurasi aplikasi asisten kecerdasan buatan lokal ini "
    "menunjukkan bahwa seluruh komponen pemrosesan bahasa alami dan manajemen memori percakapan "
    "berjalan sangat stabil sesuai rancangan arsitektur sistem."
)  # 28 kata (238 chars; gabungan 16 + 1 + 238 = 255 > 250 chars -> 2 chunk)
BENTUK_C = f"{BENTUK_C_S1} {BENTUK_C_S2}"

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

# Bentuk E: 1 kalimat 3 kata + 1 kalimat 28 kata, sama dengan C tetapi total <= 250 karakter
BENTUK_E_S1 = "Halo, saya ENKI."  # 3 kata (16 chars setelah pelafalan)
BENTUK_E_S2 = (
    "Pada pengujian hari ini seluruh modul asisten lokal berjalan sangat lancar di prosesor utama "
    "sehingga memori kartu grafis tetap longgar untuk melayani permintaan model bahasa alami dengan cepat."
)  # 28 kata (200 chars; gabungan 16 + 1 + 200 = 217 <= 250 chars -> 1 chunk)
BENTUK_E = f"{BENTUK_E_S1} {BENTUK_E_S2}"


def verify_shapes():
    assert len((BENTUK_A.split(". ")[0] + ".").split()) == 6
    assert len((BENTUK_B.split(". ")[0] + ".").split()) == 14
    assert len(BENTUK_C_S1.split()) == 3
    assert len(BENTUK_C_S2.split()) == 28
    assert len(BENTUK_C) > 250
    assert len(BENTUK_D_S1.split()) == 25 and "," in BENTUK_D_S1
    assert len(BENTUK_E_S1.split()) == 3
    assert len(BENTUK_E_S2.split()) == 28
    assert len(BENTUK_E) <= 250


class OllamaLoadGenerator:
    """Menjalankan generasi streaming nyata pada Ollama (qwen2.5:3b) terus-menerus selama pengukuran,
    serta menyediakan event sinyal token nyata untuk mem-pace stream balasan uji.
    """

    def __init__(self, ollama_url: str = "http://127.0.0.1:11434", model: str = "qwen2.5:3b"):
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self.stop_event = threading.Event()
        self.active_ready = threading.Event()
        self.token_tick = threading.Condition()
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
                            with self.token_tick:
                                self.total_tokens += 1
                                if self.total_tokens >= 3:
                                    self.active_ready.set()
                                self.token_tick.notify_all()
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

    def pace_text_with_live_tokens(self, target_text: str):
        """Generator yang mengeluarkan potongan kata/token dari `target_text` beriringan
        dengan tibanya token nyata dari stream Ollama yang sedang berjalan.
        """
        words = target_text.split(" ")
        n = len(words)
        last_seen = self.total_tokens
        for i, w in enumerate(words):
            with self.token_tick:
                if self.total_tokens == last_seen and not self.stop_event.is_set():
                    self.token_tick.wait(timeout=0.08)
                last_seen = self.total_tokens
            piece = w + (" " if i < n - 1 else "")
            met = {"eval_count": n, "prompt_eval_count": 32, "eval_tok_per_sec": 70.0} if i == n - 1 else None
            yield (piece, met)

    def stop(self):
        self.stop_event.set()
        with self.token_tick:
            self.token_tick.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=5.0)
        if self.end_time == 0.0:
            self.end_time = time.perf_counter()


def run_langkah2_independent_tail_clipping(engine: SupertonicEngine) -> Dict[str, Any]:
    """Langkah 2 (M8): Menghasilkan 6 WAV BARU (durasi bervariasi 2-12 detik) lewat SupertonicEngine,
    tanpa mengubah padding, lalu membandingkan lama blokir winsound.PlaySound sinkron dengan
    waktu tunggu engine yang sebenarnya dipakai.
    """
    out_dir = Path(r"D:\ENKI\tts_test2\out4c")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Pastikan padding TIDAK diubah dari konstanta engine
    assert engine.tail_padding_sec == DEFAULT_WINSOUND_TAIL_PADDING_SEC

    rows: List[Dict[str, Any]] = []
    for fname, text in INDEPENDENT_TAIL_TEXTS:
        item = engine.synthesize_chunk(
            text=text,
            voice="M4",
            save_wav_dir=out_dir,
            save_wav_name=fname,
        )
        if item is None or item.get("mode") != "supertonic_wav":
            raise RuntimeError(f"Gagal menyintesis WAV independen {fname}")

        wav_path = item["wav_path"]
        with wave.open(wav_path, "rb") as wf:
            wav_frames_sec = wf.getnframes() / float(wf.getframerate())

        duration_sec = float(item["duration_sec"])
        engine_wait_sec = duration_sec + engine.tail_padding_sec

        # 1. Putar dengan winsound.PlaySound sinkron
        t0 = time.perf_counter()
        winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        sync_blocked_sec = time.perf_counter() - t0

        # 2. Putar lewat engine.play_synthesized untuk mencatat waktu aktual tunggu engine
        t1 = time.perf_counter()
        engine.play_synthesized(item)
        actual_engine_play_sec = time.perf_counter() - t1

        # Residu = waktu tunggu engine yang sebenarnya dipakai - lama blokir sinkron
        # Nilai negatif berarti engine selesai menunggu sebelum perangkat selesai (arah terpotong)
        residual_wait_ms = (engine_wait_sec - sync_blocked_sec) * 1000.0
        residual_actual_ms = (actual_engine_play_sec - sync_blocked_sec) * 1000.0

        rows.append(
            {
                "name": fname,
                "text": text,
                "chars": len(text),
                "words": len(text.split()),
                "wav_frames_sec": wav_frames_sec,
                "duration_sec": duration_sec,
                "tail_padding_sec": engine.tail_padding_sec,
                "engine_wait_sec": engine_wait_sec,
                "actual_engine_play_sec": actual_engine_play_sec,
                "sync_blocked_sec": sync_blocked_sec,
                "raw_device_overhang_ms": (sync_blocked_sec - duration_sec) * 1000.0,
                "residual_ms": residual_wait_ms,
                "abs_residual_ms": abs(residual_wait_ms),
                "residual_actual_ms": residual_actual_ms,
                "abs_residual_actual_ms": abs(residual_actual_ms),
            }
        )

    residuals = [r["residual_ms"] for r in rows]
    abs_residuals = [r["abs_residual_ms"] for r in rows]
    median_res_ms = statistics.median(residuals)
    median_abs_res_ms = statistics.median(abs_residuals)
    count_lt_neg50 = sum(1 for x in residuals if x < -50.0)
    count_lt_neg100 = sum(1 for x in residuals if x < -100.0)
    min_res_ms = min(residuals)
    max_res_ms = max(residuals)

    m8_pass = (median_abs_res_ms <= 50.0) and (count_lt_neg100 == 0)

    return {
        "tail_padding_sec": engine.tail_padding_sec,
        "tail_padding_ms": engine.tail_padding_sec * 1000.0,
        "rows": rows,
        "median_residual_ms": median_res_ms,
        "median_abs_residual_ms": median_abs_res_ms,
        "min_residual_ms": min_res_ms,
        "max_residual_ms": max_res_ms,
        "count_lt_neg50": count_lt_neg50,
        "count_lt_neg100": count_lt_neg100,
        "m8_pass": m8_pass,
    }


def measure_synced_shape(
    mgr: TTSManager,
    engine: SupertonicEngine,
    ollama_gen: OllamaLoadGenerator,
    shape_name: str,
    text: str,
) -> Dict[str, Any]:
    """Menjalankan 1x pemanasan (dibuang dari statistik median/maks ulangan, tetapi tetap dilaporkan)
    + 3x ulangan untuk satu bentuk melalui `run_synced_speech_stream` saat Ollama generasi nyata.
    """
    chunks = prepare_tts_chunks(text)
    d_info = compute_gapless_start_delay(chunks, gapless_start=True, max_start_delay=6.0)
    chunk_meta = [{"idx": i + 1, "chars": len(c), "words": len(c.split()), "text": c} for i, c in enumerate(chunks)]

    print(
        f"  -> Mengukur {shape_name} ({len(chunks)} chunk, D={d_info['applied_delay_sec']:.4f}s)...",
        flush=True,
    )

    def _run_once(label: str) -> Dict[str, Any]:
        out_buf = io.StringIO()
        stream_gen = ollama_gen.pace_text_with_live_tokens(text)
        res = run_synced_speech_stream(
            token_stream=stream_gen,
            out_stream=out_buf,
            is_muted=False,
            tts_engine_name="supertonic",
            voice="M4",
            gapless_start=True,
            max_start_delay=6.0,
            mgr=mgr,
            engine=engine,
        )
        printed_joined = "".join(res["printed_segments"])
        fidelity_ok = (printed_joined == text)
        print(
            f"     [{label}] FirstTok->FirstText={res['first_token_to_first_text_sec']:.4f}s | "
            f"D={res['delay_info']['applied_delay_sec']:.4f}s | "
            f"Gaps(ms)={[round(g, 3) for g in res['gaps_ms']]} | "
            f"SyncDeltas(ms)={[round(d, 3) for d in res['sync_deltas_ms']]} | "
            f"Fidelity={fidelity_ok}",
            flush=True,
        )
        return {
            "label": label,
            "first_token_to_first_text_sec": res["first_token_to_first_text_sec"],
            "applied_delay_sec": res["delay_info"]["applied_delay_sec"],
            "raw_delay_sec": res["delay_info"]["raw_delay_sec"],
            "gaps_ms": res["gaps_ms"],
            "sync_deltas_ms": res["sync_deltas_ms"],
            "fidelity_ok": fidelity_ok,
        }

    warmup = _run_once("Warmup (dibuang)")
    runs = []
    for r_i in range(3):
        runs.append(_run_once(f"Ulangan {r_i + 1}"))

    med_lat_sec = statistics.median([r["first_token_to_first_text_sec"] for r in runs])
    max_lat_sec = max([r["first_token_to_first_text_sec"] for r in runs])

    all_run_gaps_ms: List[float] = []
    for r in runs:
        all_run_gaps_ms.extend(r["gaps_ms"])

    all_four_gaps_ms: List[float] = list(warmup["gaps_ms"]) + list(all_run_gaps_ms)

    med_gap_ms = statistics.median(all_run_gaps_ms) if all_run_gaps_ms else 0.0
    max_run_gap_ms = max(all_run_gaps_ms) if all_run_gaps_ms else 0.0
    max_including_warmup_gap_ms = max(all_four_gaps_ms) if all_four_gaps_ms else 0.0

    all_sync_deltas_ms: List[float] = list(warmup["sync_deltas_ms"])
    for r in runs:
        all_sync_deltas_ms.extend(r["sync_deltas_ms"])

    return {
        "shape_name": shape_name,
        "text": text,
        "num_chunks": len(chunks),
        "chunk_meta": chunk_meta,
        "raw_delay_sec": d_info["raw_delay_sec"],
        "applied_delay_sec": d_info["applied_delay_sec"],
        "warmup": warmup,
        "runs": runs,
        "med_first_token_to_first_text_sec": med_lat_sec,
        "max_first_token_to_first_text_sec": max_lat_sec,
        "all_run_gaps_ms": all_run_gaps_ms,
        "med_gap_ms": med_gap_ms,
        "max_run_gap_ms": max_run_gap_ms,
        "max_including_warmup_gap_ms": max_including_warmup_gap_ms,
        "all_sync_deltas_ms": all_sync_deltas_ms,
    }


def measure_real_interruption_m4(
    mgr: TTSManager,
    engine: SupertonicEngine,
    ollama_gen: OllamaLoadGenerator,
) -> List[Dict[str, Any]]:
    """Mengukur M4 secara nyata pada SupertonicEngine saat Ollama generasi:
    interupsi di tengah pemutaran chunk 1 melalui cancel(), /mute, dan input baru.
    """
    reply_text = BENTUK_A
    cases = ["cancel", "/mute", "input_baru"]
    results = []

    for mode in cases:
        out_buf = io.StringIO()
        engine.played_chunks_count = 0
        engine.play_timestamps_after_stop.clear()
        polled = {"done": False, "t_play0": 0.0}

        def _poll_fn(m=mode):
            if engine.played_chunks_count >= 1 and not polled["done"]:
                if polled["t_play0"] == 0.0:
                    polled["t_play0"] = time.perf_counter()
                if (time.perf_counter() - polled["t_play0"]) >= 0.35:
                    polled["done"] = True
                    return m
            return None

        stream_gen = ollama_gen.pace_text_with_live_tokens(reply_text)
        res = run_synced_speech_stream(
            token_stream=stream_gen,
            out_stream=out_buf,
            is_muted=False,
            tts_engine_name="supertonic",
            voice="M4",
            gapless_start=True,
            max_start_delay=6.0,
            mgr=mgr,
            engine=engine,
            interrupt_poll_fn=_poll_fn,
        )
        time.sleep(0.15)
        fidelity_ok = ("".join(res["printed_segments"]) == reply_text)
        leaked_after_cancel = len(engine.play_timestamps_after_stop)
        results.append(
            {
                "trigger": mode,
                "interrupted": res["interrupted"],
                "flush_ms": res["interrupt_flush_ms"],
                "played_chunks_before_cancel": engine.played_chunks_count,
                "leaked_audio_after_cancel": leaked_after_cancel,
                "fidelity_ok": fidelity_ok,
            }
        )
        print(
            f"  -> [M4 Nyata: {mode}] flush_ms={res['interrupt_flush_ms']:.3f} ms | "
            f"played={engine.played_chunks_count} | leaked_after_cancel={leaked_after_cancel} | "
            f"fidelity={fidelity_ok}",
            flush=True,
        )

    return results


def main():
    verify_shapes()
    cfg = AppConfig()
    engine = SupertonicEngine(
        model_dir=cfg.tts_model_dir,
        default_voice="M4",
        fallback_voice=cfg.tts_voice,
    )

    t_load0 = time.perf_counter()
    ok_load = engine._ensure_loaded()
    load_sec = time.perf_counter() - t_load0
    if not ok_load or engine.is_fallback:
        raise RuntimeError("SupertonicEngine gagal dimuat untuk benchmark Tahap 4C-3!")

    print(f"[1/3] Menjalankan Langkah 2 (M8): Pengukuran Tail Clipping Independen (6 WAV baru)...")
    tail_res = run_langkah2_independent_tail_clipping(engine)
    for r in tail_res["rows"]:
        print(
            f"  - {r['name']}: dur={r['duration_sec']:.4f}s | wait={r['engine_wait_sec']:.4f}s | "
            f"sync_block={r['sync_blocked_sec']:.4f}s | residu={r['residual_ms']:+.2f} ms"
        )
    print(
        f"  => Median Residu = {tail_res['median_residual_ms']:+.2f} ms | "
        f"Median Absolut = {tail_res['median_abs_residual_ms']:.2f} ms | "
        f"Count < -50ms = {tail_res['count_lt_neg50']} | "
        f"Count < -100ms = {tail_res['count_lt_neg100']} | "
        f"M8 LULUS = {tail_res['m8_pass']}"
    )

    # Tulis bukti Langkah 2 ke docs/evidence/tahap4c3_tail_clipping.txt
    ev_dir = ROOT_DIR / "docs" / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    tail_path = ev_dir / "tahap4c3_tail_clipping.txt"

    tail_lines = [
        "=" * 92,
        "LAPORAN LANGKAH 2 TAHAP 4C-3 (KRITERIA M8): UKUR ULANG TAIL CLIPPING SECARA INDEPENDEN",
        "=" * 92,
        f"Timestamp (UTC)             : {datetime.now(timezone.utc).isoformat()}",
        f"Python Executable           : {sys.executable} (v{sys.version.split()[0]})",
        f"Engine & Suara              : Supertonic 3 (voice='M4', lang='id', HF_HUB_OFFLINE=1)",
        f"Lokasi Berkas WAV Sementara : D:\\ENKI\\tts_test2\\out4c\\indep_1.wav .. indep_6.wav",
        f"Tail Padding Engine Tetap   : {tail_res['tail_padding_sec']:.6f} s ({tail_res['tail_padding_ms']:.2f} ms) [TIDAK DIUBAH]",
        "=" * 92,
        "",
        "--- TABEL PER FILE (6 WAV BARU INDEPENDEN, DURASI 2-12 DETIK) ---",
        "Definisi Residu = (waktu_tunggu_engine_dipakai - lama_blokir_winsound_sinkron) dalam ms.",
        "Residu negatif berarti engine selesai menunggu sebelum winsound sinkron selesai (arah terpotong).",
        "",
    ]
    for idx, r in enumerate(tail_res["rows"], 1):
        tail_lines.append(
            f"  [{idx}] {r['name']} ({r['words']} kata, {r['chars']} karakter):"
        )
        tail_lines.append(f"      Teks                 : {r['text']!r}")
        tail_lines.append(f"      Durasi Audio (WAV)   : {r['duration_sec']:.4f} s")
        tail_lines.append(f"      Waktu Tunggu Engine  : {r['engine_wait_sec']:.4f} s (durasi + {tail_res['tail_padding_ms']:.2f} ms)")
        tail_lines.append(f"      Blokir PlaySound Sync: {r['sync_blocked_sec']:.4f} s (overhang mentah: {r['raw_device_overhang_ms']:+.2f} ms)")
        tail_lines.append(f"      Residu               : {r['residual_ms']:+.2f} ms (|residu| = {r['abs_residual_ms']:.2f} ms)")
        tail_lines.append("")

    tail_lines.extend(
        [
            "--- RINGKASAN STATISTIK TAIL CLIPPING INDEPENDEN (M8) ---",
            f"  - Residu per File (ms)       : {[round(r['residual_ms'], 2) for r in tail_res['rows']]}",
            f"  - Median Residu              : {tail_res['median_residual_ms']:+.2f} ms",
            f"  - Median Absolut Residu      : {tail_res['median_abs_residual_ms']:.2f} ms (Batas M8: <= 50.00 ms)",
            f"  - Rentang Residu (Min..Maks) : {tail_res['min_residual_ms']:+.2f} ms s.d. {tail_res['max_residual_ms']:+.2f} ms",
            f"  - Jumlah Residu < -50 ms     : {tail_res['count_lt_neg50']} dari 6 file",
            f"  - Jumlah Residu < -100 ms    : {tail_res['count_lt_neg100']} dari 6 file (Batas M8: 0)",
            f"  - Status Kriteria M8         : {'LULUS' if tail_res['m8_pass'] else 'GAGAL'}",
            "=" * 92,
        ]
    )
    tail_path.write_text("\n".join(tail_lines) + "\n", encoding="utf-8")

    print("\n[2/3] Memulai loop generasi nyata Ollama (qwen2.5:3b) untuk pengujian M2, M4, M5, M6...")
    ollama_gen = OllamaLoadGenerator(ollama_url=cfg.ollama_url, model=cfg.model)
    ollama_gen.start()

    mgr = TTSManager()
    shape_list = [
        ("Bentuk A (6 kata + >250 chars [4 kalimat])", BENTUK_A),
        ("Bentuk B (14 kata + 3 kalimat)", BENTUK_B),
        ("Bentuk C (3 kata + 1 kalimat 28 kata [total >250 chars -> 2 chunk])", BENTUK_C),
        ("Bentuk D (25 kata dengan koma + 3 kalimat)", BENTUK_D),
        ("Bentuk E (3 kata + 1 kalimat 28 kata [total <=250 chars -> 1 chunk])", BENTUK_E),
    ]

    shape_results: List[Dict[str, Any]] = []
    try:
        for s_name, s_text in shape_list:
            res_s = measure_synced_shape(mgr, engine, ollama_gen, s_name, s_text)
            shape_results.append(res_s)

        print("\n[3/3] Mengukur Interupsi Nyata M4 (cancel, /mute, input_baru) saat Ollama generasi...")
        m4_real_results = measure_real_interruption_m4(mgr, engine, ollama_gen)
    finally:
        ollama_gen.stop()

    # Agregasi metrik M2, M4, M5, M6
    all_sync_deltas: List[float] = []
    for sr in shape_results:
        all_sync_deltas.extend(sr["all_sync_deltas_ms"])
    med_sync_delta_ms = statistics.median(all_sync_deltas) if all_sync_deltas else 0.0
    max_sync_delta_ms = max(all_sync_deltas) if all_sync_deltas else 0.0
    m2_pass = (med_sync_delta_ms <= 100.0) and (max_sync_delta_ms <= 300.0)

    m4_max_flush_ms = max(r["flush_ms"] for r in m4_real_results)
    m4_leaked_total = sum(r["leaked_audio_after_cancel"] for r in m4_real_results)
    m4_fidelity_all = all(r["fidelity_ok"] for r in m4_real_results)
    m4_pass = (m4_max_flush_ms <= 200.0) and (m4_leaked_total == 0) and m4_fidelity_all

    m5_pass_all = True
    for sr in shape_results:
        # Periksa median <= 500 ms dan maksimum run tunggal <= 1000 ms pada 3 ulangan
        if sr["med_gap_ms"] > 500.0 or sr["max_run_gap_ms"] > 1000.0:
            m5_pass_all = False

    bentuk_c_res = next(sr for sr in shape_results if sr["shape_name"].startswith("Bentuk C"))
    bentuk_d_res = next(sr for sr in shape_results if sr["shape_name"].startswith("Bentuk D"))
    m6_c_med = bentuk_c_res["med_first_token_to_first_text_sec"]
    m6_d_med = bentuk_d_res["med_first_token_to_first_text_sec"]
    m6_pass = (m6_c_med <= 8.0) and (m6_d_med <= 8.0)

    ollama_dur = max(0.001, ollama_gen.end_time - ollama_gen.start_time)
    ollama_tps = ollama_gen.total_tokens / ollama_dur

    bench_path = ev_dir / "tahap4c3_bench.txt"
    b_lines = [
        "=" * 92,
        "LAPORAN BENCHMARK TAHAP 4C-3: TAMPILAN SINKRON (M2), INTERUPSI (M4), TANPA JEDA (M5), LATENSI (M6)",
        "=" * 92,
        f"Timestamp (UTC)                : {datetime.now(timezone.utc).isoformat()}",
        f"Python Executable              : {sys.executable} (v{sys.version.split()[0]})",
        f"Model TTS                      : Supertonic 3 (voice='M4', lang='id', HF_HUB_OFFLINE=1)",
        f"Waktu Muat Model (CPU)         : {load_sec:.4f} s ({load_sec * 1000.0:.2f} ms)",
        f"Konstanta Regresi Langkah 1    : c0={cfg.tts_synth_c0}, c1={cfg.tts_synth_c1}, d0={cfg.tts_audio_d0}, d1={cfg.tts_audio_d1}",
        f"Konfigurasi Gapless-by-Delay   : tts_gapless_start={cfg.tts_gapless_start}, tts_max_start_delay={cfg.tts_max_start_delay} s",
        f"Durasi Loop Generasi Ollama    : {ollama_dur:.2f} detik (model='{cfg.model}')",
        f"Total Token Dihasilkan LLM     : {ollama_gen.total_tokens} token ({ollama_tps:.2f} tok/s)",
        f"Jumlah Request Stream LLM      : {ollama_gen.requests_count} request (Errors: {ollama_gen.errors_count})",
        "=" * 92,
        "",
        "--- BAGIAN 1: PENGUKURAN BENTUK A, B, C, D, E (KRITERIA M2, M5, M6) ---",
        "Metode: 1x pemanasan dibuang + 3x ulangan per bentuk saat Ollama sedang generasi nyata.",
        "SEMUA run (Warmup + Ulangan 1, 2, 3) dilaporkan secara lengkap di bawah ini.",
        "",
    ]

    for sr in shape_results:
        b_lines.append("=" * 92)
        b_lines.append(f"BENTUK: {sr['shape_name']}")
        b_lines.append("=" * 92)
        b_lines.append(f"  - Jumlah Chunk             : {sr['num_chunks']}")
        for cm in sr["chunk_meta"]:
            b_lines.append(
                f"    * Chunk {cm['idx']} ({cm['chars']} karakter, {cm['words']} kata): {cm['text']!r}"
            )
        b_lines.append(
            f"  - Tunda Mulai (D) Simulasi : D_raw = {sr['raw_delay_sec']:.4f} s | "
            f"D_applied = {sr['applied_delay_sec']:.4f} s (batas={cfg.tts_max_start_delay:.1f}s)"
        )
        w = sr["warmup"]
        b_lines.append(
            f"  - Warmup (dibuang)         : FirstTok->FirstText = {w['first_token_to_first_text_sec']:.4f} s | "
            f"Jeda Antar-Chunk = {[round(g, 3) for g in w['gaps_ms']]} ms | "
            f"Sync Delta = {[round(d, 3) for d in w['sync_deltas_ms']]} ms | Fidelity = {w['fidelity_ok']}"
        )
        for r in sr["runs"]:
            b_lines.append(
                f"  - {r['label']:<24} : FirstTok->FirstText = {r['first_token_to_first_text_sec']:.4f} s | "
                f"Jeda Antar-Chunk = {[round(g, 3) for g in r['gaps_ms']]} ms | "
                f"Sync Delta = {[round(d, 3) for d in r['sync_deltas_ms']]} ms | Fidelity = {r['fidelity_ok']}"
            )
        b_lines.append(
            f"  - Median FirstTok->FirstTxt: {sr['med_first_token_to_first_text_sec']:.4f} s "
            f"(Maks 3 Ulangan: {sr['max_first_token_to_first_text_sec']:.4f} s)"
        )
        b_lines.append(
            f"  - Median Jeda (3 Ulangan)  : {sr['med_gap_ms']:.3f} ms "
            f"(Maks Run Tunggal 3 Ulangan: {sr['max_run_gap_ms']:.3f} ms | "
            f"Maks Termasuk Warmup: {sr['max_including_warmup_gap_ms']:.3f} ms)"
        )
        status_m5_shape = (
            "LULUS"
            if (sr["med_gap_ms"] <= 500.0 and sr["max_run_gap_ms"] <= 1000.0)
            else "GAGAL"
        )
        b_lines.append(f"  - Evaluasi M5 Bentuk Ini   : {status_m5_shape}")
        b_lines.append("")

    b_lines.extend(
        [
            "--- BAGIAN 2: EVALUASI SINKRONISASI TEKS TERHADAP ON_BEFORE_PLAY (KRITERIA M2) ---",
            f"  - Total Segmen Diukur        : {len(all_sync_deltas)} segmen (seluruh chunk A-E saat Ollama generasi)",
            f"  - Median Selisih Tampil      : {med_sync_delta_ms:.4f} ms (Batas M2: <= 100.0 ms)",
            f"  - Maksimum Selisih Tampil    : {max_sync_delta_ms:.4f} ms (Batas M2: <= 300.0 ms)",
            f"  - Status Kriteria M2         : {'LULUS' if m2_pass else 'GAGAL'}",
            "",
            "--- BAGIAN 3: EVALUASI INTERUPSI NYATA DI TENGAH BALASAN (KRITERIA M4) ---",
        ]
    )
    for r in m4_real_results:
        b_lines.append(
            f"  - Trigger {r['trigger']:<12} : Flush Sisa Teks = {r['flush_ms']:.3f} ms (<= 200 ms) | "
            f"Chunk Terputar Sebelum Cancel = {r['played_chunks_before_cancel']} | "
            f"Audio Bocor Setelah Cancel = {r['leaked_audio_after_cancel']} | "
            f"M1 Fidelity = {r['fidelity_ok']}"
        )
    b_lines.extend(
        [
            f"  - Maksimum Waktu Flush M4    : {m4_max_flush_ms:.3f} ms (Batas M4: <= 200.0 ms)",
            f"  - Total Audio Bocor Cancel   : {m4_leaked_total} (Batas M4: 0)",
            f"  - Status Kriteria M4         : {'LULUS' if m4_pass else 'GAGAL'}",
            "",
            "--- BAGIAN 4: RINGKASAN LATENSI TOTAL TOKEN PERTAMA -> TEKS PERTAMA TAMPIL (KRITERIA M6) ---",
            f"  - Bentuk C: D = {bentuk_c_res['applied_delay_sec']:.4f} s | "
            f"Median Latensi = {m6_c_med:.4f} s (Ulangan: {[round(r['first_token_to_first_text_sec'], 4) for r in bentuk_c_res['runs']]} s) [Batas: <= 8.0 s]",
            f"  - Bentuk D: D = {bentuk_d_res['applied_delay_sec']:.4f} s | "
            f"Median Latensi = {m6_d_med:.4f} s (Ulangan: {[round(r['first_token_to_first_text_sec'], 4) for r in bentuk_d_res['runs']]} s) [Batas: <= 8.0 s]",
            f"  - Status Kriteria M6         : {'LULUS' if m6_pass else 'GAGAL'}",
            "",
            "--- BAGIAN 5: RINGKASAN AKHIR KRITERIA BENCHMARK (M2, M4, M5, M6, M8) ---",
            f"  - M2 (Sinkron <=100ms med, <=300ms max) : {'LULUS' if m2_pass else 'GAGAL'} (med={med_sync_delta_ms:.4f} ms, max={max_sync_delta_ms:.4f} ms)",
            f"  - M4 (Interupsi <=200ms, 0 bocor, M1)   : {'LULUS' if m4_pass else 'GAGAL'} (max_flush={m4_max_flush_ms:.3f} ms, bocor={m4_leaked_total})",
            f"  - M5 (Tanpa jeda A-E <=500ms med/1000ms): {'LULUS' if m5_pass_all else 'GAGAL'}",
            f"  - M6 (Latensi C & D <= 8.0 detik)       : {'LULUS' if m6_pass else 'GAGAL'} (C={m6_c_med:.4f}s, D={m6_d_med:.4f}s)",
            f"  - M8 (Tail clipping independen)         : {'LULUS' if tail_res['m8_pass'] else 'GAGAL'} (|med|={tail_res['median_abs_residual_ms']:.2f} ms, <-100ms={tail_res['count_lt_neg100']})",
            "=" * 92,
        ]
    )
    bench_path.write_text("\n".join(b_lines) + "\n", encoding="utf-8")
    print(f"\n[SELESAI] Bukti tersimpan di:\n  - {tail_path}\n  - {bench_path}")


if __name__ == "__main__":
    main()
