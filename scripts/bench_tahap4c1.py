r"""Skrip Pengukuran Tahap 4C-1 (scripts/bench_tahap4c1.py).
Mengukur Kriteria K5, K6, K7 serta bagian A, B, C, D, E dengan Supertonic 3 (voice M4, CPU, HF_HUB_OFFLINE=1).
Menyimpan laporan lengkap ke docs/evidence/tahap4c1_bench.txt dan berkas WAV sonda ke D:\ENKI\tts_test2\out4c\.
"""

import datetime
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["OMP_NUM_THREADS"] = "2"

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

import psutil
from enki.config import AppConfig
from enki.core.ollama_client import OllamaClient
from enki.audio.supertonic import SupertonicEngine
from enki.audio.tts import (
    TTSManager,
    clean_text_for_tts,
    apply_pronunciation,
    prepare_tts_chunks,
)


CHUNKS_4A = {
    "pendek_3_kata": "Halo, saya ENKI.",
    "sedang_10_kata": "Versi Python di sistem ini adalah tiga koma empat belas.",
    "panjang_25_kata": (
        "Asisten kecerdasan buatan lokal ini dirancang untuk membantu pengguna mengelola "
        "catatan harian, membaca dokumen proyek, serta menjawab berbagai pertanyaan teknis "
        "dengan cepat tanpa memerlukan jaringan."
    ),
}

REPLY_3_CHUNKS = (
    "Sistem asisten kecerdasan buatan lokal ENKI kini menggunakan mesin suara baru yang bekerja sepenuhnya secara luring. "
    "Seluruh proses sintesis suara dijalankan di prosesor utama sehingga memori kartu grafis tetap hemat dan stabil. "
    "Pendekatan antrean berpipa memastikan kalimat berikutnya disiapkan saat kalimat sebelumnya masih diputar. "
    "Dengan arsitektur ini pengguna dapat berinteraksi secara nyaman, cepat, dan aman tanpa khawatir terhadap kebocoran data ke jaringan luar."
)

SONDA_4E_STRINGS = [
    "Python 3.14 sudah terpasang.",
    "Versi ENKI sekarang v3.0.",
    "Hasilnya 118 tes, 115 lulus.",
    "Commit 81e6b7a sudah masuk.",
    "Memakai 2,6 GB VRAM pada suhu 45,5 derajat.",
    "Buka file enki/audio/tts.py lalu jalankan /test.",
]

SONDA_OUT_DIR = Path(r"D:\ENKI\tts_test2\out4c")
REPORT_PATH = WORKSPACE_ROOT / "docs" / "evidence" / "tahap4c1_bench.txt"


class OllamaLoadGenerator:
    """Menjalankan loop generasi nyata qwen2.5:3b di latar belakang selama pengukuran."""

    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "qwen2.5:3b"):
        self.client = OllamaClient(base_url=base_url)
        self.model = model
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._thread: threading.Thread = None
        self.total_tokens: int = 0
        self.total_requests: int = 0
        self.started_at: float = 0.0
        self.ended_at: float = 0.0
        self.errors: List[str] = []

    def start(self) -> None:
        self._stop_event.clear()
        self._ready_event.clear()
        self.total_tokens = 0
        self.total_requests = 0
        self.errors.clear()
        self.started_at = time.perf_counter()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="OllamaGenLoop")
        self._thread.start()
        # Tunggu sampai token pertama mulai mengalir dari qwen2.5:3b
        self._ready_event.wait(timeout=30.0)

    def _run_loop(self) -> None:
        prompt = (
            "Jelaskan secara sangat mendalam, rinci, dan panjang lebar mengenai arsitektur "
            "sistem operasi modern, manajemen memori virtual paging, penjadwalan proses CPU, "
            "sistem berkas jurnal, konkurensi thread, serta protokol jaringan TCP/IP dalam "
            "sepuluh paragraf lengkap beserta contoh teknisnya."
        )
        idx = 0
        while not self._stop_event.is_set():
            idx += 1
            self.total_requests += 1
            try:
                for chunk_text, metrics in self.client.chat_stream(
                    model=self.model,
                    messages=[{"role": "user", "content": f"{prompt} (Bagian {idx})"}],
                    num_ctx=8192,
                    temperature=0.2,
                ):
                    if chunk_text:
                        self.total_tokens += 1
                        if not self._ready_event.is_set():
                            self._ready_event.set()
                    if self._stop_event.is_set():
                        break
                    if metrics:
                        break
            except Exception as e:
                self.errors.append(str(e))
                self._ready_event.set()
                time.sleep(0.2)

    def stop(self) -> Tuple[float, int, int]:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=10.0)
        self.ended_at = time.perf_counter()
        return (self.ended_at - self.started_at), self.total_tokens, self.total_requests


def measure_ttfa_suite(
    mgr: TTSManager,
    engine: SupertonicEngine,
    pron_map: Dict[str, str],
) -> Dict[str, Dict[str, Any]]:
    """Mengukur TTFA (1 warmup dibuang + 3 ulangan) untuk 3 panjang chunk pertama."""
    results: Dict[str, Dict[str, Any]] = {}

    for label, text in CHUNKS_4A.items():
        warmup_ttfa_s = 0.0
        runs_ttfa_s: List[float] = []

        for run_idx in range(4):  # 0 = warmup, 1..3 = 3 ulangan
            first_play_ts: List[float] = []
            first_play_evt = threading.Event()

            def on_before(item, _ts_list=first_play_ts, _evt=first_play_evt):
                if item.get("chunk_idx", 0) == 0 and not _ts_list:
                    _ts_list.append(time.perf_counter())
                    _evt.set()

            mgr.on_before_play = on_before
            mgr.on_after_play = None

            mgr.speak(text, engine=engine, pronunciation_map=pron_map)
            first_play_evt.wait(timeout=30.0)
            mgr.wait_done(timeout=45.0)

            if first_play_ts:
                ttfa_val = first_play_ts[0] - mgr.last_speak_called_at
            else:
                ttfa_val = -1.0

            if run_idx == 0:
                warmup_ttfa_s = ttfa_val
            else:
                runs_ttfa_s.append(ttfa_val)

        mgr.on_before_play = None
        median_s = statistics.median(runs_ttfa_s)
        results[label] = {
            "text": text,
            "chunks": prepare_tts_chunks(text, pronunciation_map=pron_map),
            "warmup_s": warmup_ttfa_s,
            "runs_s": runs_ttfa_s,
            "median_s": median_s,
            "median_ms": median_s * 1000.0,
        }

    return results


def measure_gap_suite(
    mgr: TTSManager,
    engine: SupertonicEngine,
    pron_map: Dict[str, str],
) -> Dict[str, Any]:
    """Mengukur celah antar-chunk (gap) pada balasan 3 chunk (1 warmup dibuang + 3 ulangan)."""
    chunks_preview = prepare_tts_chunks(REPLY_3_CHUNKS, pronunciation_map=pron_map)
    assert len(chunks_preview) == 3, f"Harus 3 chunk, dapat {len(chunks_preview)}: {chunks_preview}"

    warmup_gaps_ms: List[float] = []
    runs_gaps_ms: List[List[float]] = []
    all_measured_gaps_ms: List[float] = []

    for run_idx in range(4):  # 0 = warmup, 1..3 = 3 ulangan
        before_ts: Dict[int, float] = {}
        after_ts: Dict[int, float] = {}

        def on_before(item, _b=before_ts):
            c_idx = item.get("chunk_idx", 0)
            _b[c_idx] = time.perf_counter()

        def on_after(item, stopped_early, _a=after_ts):
            c_idx = item.get("chunk_idx", 0)
            _a[c_idx] = time.perf_counter()

        mgr.on_before_play = on_before
        mgr.on_after_play = on_after

        mgr.speak(REPLY_3_CHUNKS, engine=engine, pronunciation_map=pron_map)
        mgr.wait_done(timeout=90.0)

        gap_1_2_ms = (before_ts[1] - after_ts[0]) * 1000.0 if (1 in before_ts and 0 in after_ts) else -1.0
        gap_2_3_ms = (before_ts[2] - after_ts[1]) * 1000.0 if (2 in before_ts and 1 in after_ts) else -1.0
        pair_ms = [gap_1_2_ms, gap_2_3_ms]

        if run_idx == 0:
            warmup_gaps_ms = pair_ms
        else:
            runs_gaps_ms.append(pair_ms)
            all_measured_gaps_ms.extend(pair_ms)

    mgr.on_before_play = None
    mgr.on_after_play = None

    gap12_list = [r[0] for r in runs_gaps_ms]
    gap23_list = [r[1] for r in runs_gaps_ms]

    return {
        "chunks": chunks_preview,
        "warmup_gaps_ms": warmup_gaps_ms,
        "runs_gaps_ms": runs_gaps_ms,
        "median_gap_1_to_2_ms": statistics.median(gap12_list),
        "median_gap_2_to_3_ms": statistics.median(gap23_list),
        "median_all_gaps_ms": statistics.median(all_measured_gaps_ms),
        "all_measured_gaps_ms": all_measured_gaps_ms,
    }


def measure_interrupt_suite(
    mgr: TTSManager,
    engine: SupertonicEngine,
    pron_map: Dict[str, str],
) -> Dict[str, Any]:
    """Mengukur waktu interupsi cancel() (5 ulangan) dan membuktikan 0 audio diputar setelah cancel."""
    interrupt_ms_runs: List[float] = []
    post_cancel_played_counts: List[int] = []
    discarded_counts_per_run: List[int] = []

    for run_idx in range(5):
        started_evt = threading.Event()

        def on_before(item, _evt=started_evt):
            if item.get("chunk_idx", 0) == 0:
                _evt.set()

        mgr.on_before_play = on_before
        mgr.on_after_play = None

        mgr.speak(REPLY_3_CHUNKS, engine=engine, pronunciation_map=pron_map)
        started_evt.wait(timeout=15.0)
        # Biarkan chunk 0 berbunyi ~0.25s sementara chunk 1 sedang disintesis ONNX di background
        time.sleep(0.25)

        played_before_cancel = engine.played_chunks_count
        discarded_before_cancel = engine.discarded_after_cancel_count

        t_cancel0 = time.perf_counter()
        mgr.cancel()
        t_cancel_return = time.perf_counter()

        # Tunggu sesaat agar thread pemutar keluar dari wait()
        time.sleep(0.02)
        t_stop_effective = max(engine.last_purge_completed_at, engine.last_playback_exited_at, t_cancel_return)
        stop_latency_ms = (t_stop_effective - t_cancel0) * 1000.0
        interrupt_ms_runs.append(stop_latency_ms)

        # Tunggu 4.5 detik agar inferensi ONNX chunk 1 yang sedang berjalan selesai dan dibuang
        time.sleep(4.5)
        played_after_cancel = engine.played_chunks_count - played_before_cancel
        discarded_delta = engine.discarded_after_cancel_count - discarded_before_cancel

        post_cancel_played_counts.append(played_after_cancel)
        discarded_counts_per_run.append(discarded_delta)

    mgr.on_before_play = None
    return {
        "runs_ms": interrupt_ms_runs,
        "median_ms": statistics.median(interrupt_ms_runs),
        "max_ms": max(interrupt_ms_runs),
        "post_cancel_played_counts": post_cancel_played_counts,
        "total_post_cancel_played": sum(post_cancel_played_counts),
        "discarded_counts_per_run": discarded_counts_per_run,
    }


def measure_ram_and_cpu(engine: SupertonicEngine) -> Dict[str, Any]:
    """Mengukur RAM (RSS) dan CPU% proses utama ENKI saat model termuat (idle vs sintesis chunk panjang)."""
    engine.reset_for_new_utterance()
    proc = psutil.Process(os.getpid())
    logical_cpus = psutil.cpu_count(logical=True) or 1
    children_count = len(proc.children(recursive=True))

    # 1. Kondisi Idle (2.0 detik, sampel tiap 100 ms)
    proc.cpu_percent(interval=None)
    idle_rss_mb: List[float] = []
    idle_cpu_raw: List[float] = []
    for _ in range(20):
        time.sleep(0.1)
        idle_rss_mb.append(proc.memory_info().rss / (1024 * 1024))
        idle_cpu_raw.append(proc.cpu_percent(interval=None))

    # 2. Kondisi Sintesis Chunk Panjang (25 kata, 2x berturut-turut agar sampel CPU memadai)
    synth_rss_mb: List[float] = []
    synth_cpu_raw: List[float] = []
    stop_mon = threading.Event()

    def mon_loop():
        proc.cpu_percent(interval=None)
        while not stop_mon.is_set():
            time.sleep(0.05)
            synth_rss_mb.append(proc.memory_info().rss / (1024 * 1024))
            synth_cpu_raw.append(proc.cpu_percent(interval=None))

    mon_th = threading.Thread(target=mon_loop, daemon=True)
    mon_th.start()

    long_text = CHUNKS_4A["panjang_25_kata"]
    for _ in range(2):
        item = engine.synthesize_chunk(long_text, voice="M4")
        engine.discard_synthesized(item, count_as_cancelled=False)

    stop_mon.set()
    mon_th.join(timeout=2.0)

    avg_idle_cpu_raw = statistics.mean(idle_cpu_raw) if idle_cpu_raw else 0.0
    avg_synth_cpu_raw = statistics.mean(synth_cpu_raw) if synth_cpu_raw else 0.0
    peak_synth_cpu_raw = max(synth_cpu_raw) if synth_cpu_raw else 0.0

    return {
        "pid": proc.pid,
        "logical_cpus": logical_cpus,
        "children_count": children_count,
        "idle_rss_avg_mb": statistics.mean(idle_rss_mb),
        "idle_rss_peak_mb": max(idle_rss_mb),
        "idle_cpu_avg_raw_pct": avg_idle_cpu_raw,
        "idle_cpu_avg_norm_pct": avg_idle_cpu_raw / logical_cpus,
        "synth_rss_peak_mb": max(synth_rss_mb) if synth_rss_mb else 0.0,
        "synth_cpu_avg_raw_pct": avg_synth_cpu_raw,
        "synth_cpu_avg_norm_pct": avg_synth_cpu_raw / logical_cpus,
        "synth_cpu_peak_raw_pct": peak_synth_cpu_raw,
        "synth_cpu_peak_norm_pct": peak_synth_cpu_raw / logical_cpus,
    }


def run_sonda_4e(engine: SupertonicEngine, pron_map: Dict[str, str]) -> List[Dict[str, Any]]:
    """Menjalankan sonda 6 string angka/simbol lewat pipeline pembersihan asli ENKI."""
    engine.reset_for_new_utterance()
    SONDA_OUT_DIR.mkdir(parents=True, exist_ok=True)
    results: List[Dict[str, Any]] = []

    for idx, raw_str in enumerate(SONDA_4E_STRINGS, 1):
        cleaned = clean_text_for_tts(raw_str)
        pronounced = apply_pronunciation(cleaned, pronunciation_map=pron_map)
        chunks = prepare_tts_chunks(raw_str, pronunciation_map=pron_map)
        exact_engine_input = " ".join(chunks)

        wav_name = f"sonda_{idx}.wav"
        item = engine.synthesize_chunk(
            exact_engine_input,
            voice="M4",
            save_wav_dir=SONDA_OUT_DIR,
            save_wav_name=wav_name,
        )
        if item is None:
            raise RuntimeError(f"Sonda #{idx} gagal disintesis!")
        wav_path = Path(item["wav_path"])
        size_bytes = wav_path.stat().st_size if wav_path.exists() else 0

        results.append({
            "idx": idx,
            "raw": raw_str,
            "cleaned": cleaned,
            "pronounced": pronounced,
            "chunks": chunks,
            "exact_engine_input": exact_engine_input,
            "wav_path": str(wav_path),
            "size_bytes": size_bytes,
            "duration_sec": item.get("duration_sec", 0.0),
            "synth_time_sec": item.get("synth_time_sec", 0.0),
        })

    return results


def main():
    print("Memulai benchmark Tahap 4C-1...")
    cfg = AppConfig()
    pron_map = cfg.tts_pronunciation

    proc = psutil.Process(os.getpid())
    ram_before_load_mb = proc.memory_info().rss / (1024 * 1024)

    engine = SupertonicEngine(
        model_dir=cfg.tts_model_dir,
        default_voice="M4",
        fallback_voice=cfg.tts_voice,
    )
    mgr = TTSManager()

    # Pemanasan awal muat model
    t_load0 = time.perf_counter()
    loaded_ok = engine._ensure_loaded()
    model_load_sec = time.perf_counter() - t_load0
    ram_after_load_mb = proc.memory_info().rss / (1024 * 1024)

    if not loaded_ok or engine.is_fallback:
        raise RuntimeError(f"Model Supertonic gagal dimuat dari {cfg.tts_model_dir}!")

    print(f"Model Supertonic M4 termuat dalam {model_load_sec:.4f}s (RAM: {ram_after_load_mb:.2f} MB)")

    # --- 4A & 4B KONDISI 1: IDLE ---
    print("Mengukur 4A (TTFA) Kondisi 1: Idle...")
    ttfa_idle = measure_ttfa_suite(mgr, engine, pron_map)

    print("Mengukur 4B (Gap Antar-Chunk) Kondisi 1: Idle...")
    gap_idle = measure_gap_suite(mgr, engine, pron_map)

    # --- 4A & 4B KONDISI 2: SAAT OLLAMA GENERASI NYATA (qwen2.5:3b) ---
    print("Memulai loop generasi Ollama (qwen2.5:3b) untuk Kondisi 2...")
    ollama_gen = OllamaLoadGenerator(base_url=cfg.ollama_url, model="qwen2.5:3b")
    ollama_gen.start()

    print("Mengukur 4A (TTFA) Kondisi 2: Saat Ollama Generasi...")
    ttfa_ollama = measure_ttfa_suite(mgr, engine, pron_map)

    print("Mengukur 4B (Gap Antar-Chunk) Kondisi 2: Saat Ollama Generasi...")
    gap_ollama = measure_gap_suite(mgr, engine, pron_map)

    ollama_dur_s, ollama_tokens, ollama_reqs = ollama_gen.stop()
    print(f"Loop Ollama selesai: durasi={ollama_dur_s:.2f}s, token={ollama_tokens}, reqs={ollama_reqs}")

    # --- 4C: WAKTU INTERUPSI ---
    print("Mengukur 4C (Waktu Interupsi cancel() 5 ulangan)...")
    interrupt_res = measure_interrupt_suite(mgr, engine, pron_map)

    # --- 4D: RAM & CPU PROSES UTAMA ---
    print("Mengukur 4D (RAM & CPU Proses Utama ENKI)...")
    ram_cpu_res = measure_ram_and_cpu(engine)

    # --- 4E: SONDA 6 STRING ANGKA/SIMBOL ---
    print("Menjalankan 4E (Sonda 6 String Angka/Simbol)...")
    sonda_res = run_sonda_4e(engine, pron_map)

    # Susun laporan docs/evidence/tahap4c1_bench.txt
    now_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
    lines: List[str] = []
    lines.append("=" * 88)
    lines.append("LAPORAN PENGUKURAN TAHAP 4C-1: SUPERTONIC 3 (VOICE M4, CPU, HF_HUB_OFFLINE=1)")
    lines.append("=" * 88)
    lines.append(f"Timestamp (UTC)        : {now_utc}")
    lines.append(f"Python Executable      : {sys.executable} (v{sys.version.split()[0]})")
    lines.append(f"Model Directory        : {cfg.tts_model_dir}")
    lines.append(f"Voice Supertonic       : M4 (lang='id')")
    lines.append(f"HF_HUB_OFFLINE         : {os.environ.get('HF_HUB_OFFLINE')}")
    lines.append(f"Waktu Muat Model (CPU) : {model_load_sec:.4f} s ({model_load_sec * 1000:.2f} ms)")
    lines.append(f"RAM Sebelum Muat Model : {ram_before_load_mb:.2f} MB")
    lines.append(f"RAM Setelah Muat Model : {ram_after_load_mb:.2f} MB")
    lines.append("=" * 88)
    lines.append("")
    lines.append("--- BAGIAN A: TIME-TO-FIRST-AUDIO (TTFA) ---")
    lines.append("Catatan Metodologi: TTFA diukur sebagai selisih waktu perangkat lunak dari panggilan")
    lines.append("speak() (mgr.last_speak_called_at) sampai tepat sebelum pemanggilan winsound.PlaySound()")
    lines.append("pada chunk pertama (on_before_play chunk_idx=0). Ini adalah event perangkat lunak,")
    lines.append("BUKAN pengukuran gelombang akustik/suara fisik yang keluar dari speaker.")
    lines.append("Metode: 1x pemanasan dibuang + median dari 3 ulangan.")
    lines.append("")
    lines.append("[Kondisi 1: IDLE]")
    for k, v in ttfa_idle.items():
        runs_str = ", ".join(f"{r:.4f}s ({r*1000:.2f}ms)" for r in v["runs_s"])
        lines.append(f"  * {k}:")
        lines.append(f"    - Teks Masuk Engine   : {v['chunks'][0]!r}")
        lines.append(f"    - Warmup (dibuang)    : {v['warmup_s']:.4f} s ({v['warmup_s']*1000:.2f} ms)")
        lines.append(f"    - 3 Ulangan           : [{runs_str}]")
        lines.append(f"    - Median TTFA (Idle)  : {v['median_s']:.4f} s ({v['median_ms']:.2f} ms)")
    lines.append("")
    lines.append("[Kondisi 2: SAAT OLLAMA SEDANG GENERASI NYATA (qwen2.5:3b)]")
    lines.append(f"  - Durasi Loop Generasi Ollama : {ollama_dur_s:.2f} detik")
    lines.append(f"  - Total Token Dihasilkan LLM  : {ollama_tokens} token ({ollama_tokens / max(0.001, ollama_dur_s):.2f} tok/s)")
    lines.append(f"  - Jumlah Request Stream LLM   : {ollama_reqs} request (Errors: {len(ollama_gen.errors)})")
    for k, v in ttfa_ollama.items():
        runs_str = ", ".join(f"{r:.4f}s ({r*1000:.2f}ms)" for r in v["runs_s"])
        lines.append(f"  * {k}:")
        lines.append(f"    - Teks Masuk Engine   : {v['chunks'][0]!r}")
        lines.append(f"    - Warmup (dibuang)    : {v['warmup_s']:.4f} s ({v['warmup_s']*1000:.2f} ms)")
        lines.append(f"    - 3 Ulangan           : [{runs_str}]")
        lines.append(f"    - Median TTFA (Ollama): {v['median_s']:.4f} s ({v['median_ms']:.2f} ms)")
    lines.append("")
    lines.append("--- BAGIAN B: CELAH ANTAR-CHUNK (GAP) PADA BALASAN 3 CHUNK ---")
    lines.append("Definisi: Jeda waktu dari akhir pemutaran chunk N sampai awal pemutaran chunk N+1.")
    lines.append("Teks 3 Chunk setelah pipeline:")
    for idx_c, c_text in enumerate(gap_idle["chunks"], 1):
        lines.append(f"  Chunk {idx_c} ({len(c_text)} chars, {len(c_text.split())} kata): {c_text!r}")
    lines.append("")
    lines.append("[Kondisi 1: IDLE]")
    lines.append(f"  - Warmup (dibuang) [Gap 1->2, Gap 2->3] : [{gap_idle['warmup_gaps_ms'][0]:.3f} ms, {gap_idle['warmup_gaps_ms'][1]:.3f} ms]")
    for r_i, pair in enumerate(gap_idle["runs_gaps_ms"], 1):
        lines.append(f"  - Ulangan {r_i}        [Gap 1->2, Gap 2->3] : [{pair[0]:.3f} ms, {pair[1]:.3f} ms]")
    lines.append(f"  - Median Gap 1->2 (Idle)                : {gap_idle['median_gap_1_to_2_ms']:.3f} ms")
    lines.append(f"  - Median Gap 2->3 (Idle)                : {gap_idle['median_gap_2_to_3_ms']:.3f} ms")
    lines.append(f"  - Median Keseluruhan Gap (Idle)         : {gap_idle['median_all_gaps_ms']:.3f} ms")
    lines.append("")
    lines.append("[Kondisi 2: SAAT OLLAMA SEDANG GENERASI NYATA (qwen2.5:3b)]")
    lines.append(f"  - Warmup (dibuang) [Gap 1->2, Gap 2->3] : [{gap_ollama['warmup_gaps_ms'][0]:.3f} ms, {gap_ollama['warmup_gaps_ms'][1]:.3f} ms]")
    for r_i, pair in enumerate(gap_ollama["runs_gaps_ms"], 1):
        lines.append(f"  - Ulangan {r_i}        [Gap 1->2, Gap 2->3] : [{pair[0]:.3f} ms, {pair[1]:.3f} ms]")
    lines.append(f"  - Median Gap 1->2 (Ollama)              : {gap_ollama['median_gap_1_to_2_ms']:.3f} ms")
    lines.append(f"  - Median Gap 2->3 (Ollama)              : {gap_ollama['median_gap_2_to_3_ms']:.3f} ms")
    lines.append(f"  - Median Keseluruhan Gap (Ollama)       : {gap_ollama['median_all_gaps_ms']:.3f} ms")
    lines.append("")
    lines.append("--- BAGIAN C: WAKTU INTERUPSI (cancel()) & BUKTI PEMBUANGAN CHUNK BATAL ---")
    lines.append("Definisi: Selisih dari panggilan cancel() sampai pemutaran berhenti (SND_PURGE selesai")
    lines.append("dan thread pemutar keluar dari wait), diikuti jeda 4.5s untuk memastikan chunk yang")
    lines.append("sedang disintesis ONNX di latar belakang dibuang dan TIDAK diputar setelah cancel.")
    for r_i in range(5):
        lines.append(
            f"  - Ulangan {r_i+1}: Waktu berhenti = {interrupt_res['runs_ms'][r_i]:.3f} ms | "
            f"Audio diputar setelah cancel = {interrupt_res['post_cancel_played_counts'][r_i]} | "
            f"Chunk batal dibuang = {interrupt_res['discarded_counts_per_run'][r_i]}"
        )
    lines.append(f"  - Median Waktu Interupsi (5 ulangan)    : {interrupt_res['median_ms']:.3f} ms")
    lines.append(f"  - Maksimum Waktu Interupsi              : {interrupt_res['max_ms']:.3f} ms")
    lines.append(f"  - Total Audio Terdengar Setelah Cancel  : {interrupt_res['total_post_cancel_played']} (Wajib 0)")
    lines.append("")
    lines.append("--- BAGIAN D: RAM DAN CPU PROSES UTAMA ENKI SAAT MODEL TERMUAT ---")
    lines.append("Catatan Arsitektur: Engine Supertonic 3 berjalan in-process di proses utama Python")
    lines.append("(ONNX Runtime + stdlib winsound) dan TIDAK memiliki proses anak (jumlah proses anak = 0).")
    lines.append(f"  - PID Proses Utama                      : {ram_cpu_res['pid']}")
    lines.append(f"  - Jumlah Proses Anak (Child Processes)  : {ram_cpu_res['children_count']}")
    lines.append(f"  - Jumlah Logical CPU Cores              : {ram_cpu_res['logical_cpus']}")
    lines.append(f"  - RAM RSS Saat Idle (Model Termuat)     : Rata-rata {ram_cpu_res['idle_rss_avg_mb']:.2f} MB | Puncak {ram_cpu_res['idle_rss_peak_mb']:.2f} MB")
    lines.append(f"  - CPU% Saat Idle (Model Termuat)        : Rata-rata {ram_cpu_res['idle_cpu_avg_raw_pct']:.2f}% psutil ({ram_cpu_res['idle_cpu_avg_norm_pct']:.2f}% total sistem)")
    lines.append(f"  - RAM RSS Puncak Saat Sintesis Panjang  : {ram_cpu_res['synth_rss_peak_mb']:.2f} MB")
    lines.append(f"  - CPU% Rata-rata Saat Sintesis Panjang  : {ram_cpu_res['synth_cpu_avg_raw_pct']:.2f}% psutil ({ram_cpu_res['synth_cpu_avg_norm_pct']:.2f}% total sistem)")
    lines.append(f"  - CPU% Puncak Saat Sintesis Panjang     : {ram_cpu_res['synth_cpu_peak_raw_pct']:.2f}% psutil ({ram_cpu_res['synth_cpu_peak_norm_pct']:.2f}% total sistem)")
    lines.append("")
    lines.append("--- BAGIAN E: SONDA 6 STRING ANGKA/SIMBOL (PIPELINE PEMBERSIHAN ASLI ENKI) ---")
    for s_item in sonda_res:
        lines.append(f"  [{s_item['idx']}] String Asli          : {s_item['raw']!r}")
        lines.append(f"      Setelah clean_text   : {s_item['cleaned']!r}")
        lines.append(f"      Teks Masuk Engine    : {s_item['exact_engine_input']!r}")
        lines.append(f"      File WAV             : {s_item['wav_path']} ({s_item['size_bytes']:,} bytes, durasi {s_item['duration_sec']:.2f}s, synth {s_item['synth_time_sec']:.4f}s)")
    lines.append("")
    lines.append("--- RINGKASAN EVALUASI KRITERIA TERUKUR (K5, K6, K7) ---")
    k5_pass = interrupt_res["median_ms"] <= 300.0 and interrupt_res["total_post_cancel_played"] == 0
    k6_val_s = ttfa_ollama["pendek_3_kata"]["median_s"]
    k6_pass = k6_val_s <= 4.0
    k7_val_ms = gap_ollama["median_all_gaps_ms"]
    k7_pass = k7_val_ms <= 500.0
    lines.append(f"  - K5 (Interupsi <= 300 ms & 0 bocor)        : Median {interrupt_res['median_ms']:.3f} ms, bocor={interrupt_res['total_post_cancel_played']} -> {'LULUS' if k5_pass else 'GAGAL'}")
    lines.append(f"  - K6 (TTFA 3 kata <= 4,0 s saat Ollama gen) : Median {k6_val_s:.4f} s ({k6_val_s*1000:.2f} ms) -> {'LULUS' if k6_pass else 'GAGAL'}")
    lines.append(f"  - K7 (Gap <= 500 ms saat Ollama gen)        : Median {k7_val_ms:.3f} ms (Gap 1->2: {gap_ollama['median_gap_1_to_2_ms']:.3f} ms, Gap 2->3: {gap_ollama['median_gap_2_to_3_ms']:.3f} ms) -> {'LULUS' if k7_pass else 'GAGAL'}")
    lines.append("=" * 88)
    lines.append("")

    report_text = "\n".join(lines)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report_text, encoding="utf-8")
    print(f"\nLaporan disimpan ke: {REPORT_PATH}")
    print(report_text)


if __name__ == "__main__":
    main()
