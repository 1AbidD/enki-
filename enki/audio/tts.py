"""Subsistem Text-to-Speech (TTS) ENKI dengan dukungan Zero-Cost Mute, kamus pelafalan,
chunking cepat-mulai, pipelining sintesis-pemutaran, dan interupsi instan.
"""

import os
import queue
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from enki.audio.base import BaseTTSEngine, FakeEngine

ABBREVIATIONS = [
    "dll", "dsb", "dst", "misal", "mis", "dr", "prof", "etc", "e.g", "i.e", "vs"
]

DEFAULT_PRONUNCIATION: Dict[str, str] = {
    "ENKI": "Enki",
}


def clean_text_for_tts(text: str) -> str:
    """Membersihkan format teks dari blok kode, URL panjang, dan markup sebelum sintesis."""
    if not text:
        return ""

    # 1. Lewati/buang blok kode (fenced code block ```...```)
    cleaned = re.sub(r"```[\s\S]*?```", "", text)

    # 2. Gantikan URL panjang dengan kata 'tautan' (jaga tanda baca kalimat di akhir)
    def _url_repl(m):
        url = m.group(0)
        trailing = ""
        while url and url[-1] in ".,;:!?)]}>":
            trailing = url[-1] + trailing
            url = url[:-1]
        return "tautan" + trailing

    cleaned = re.sub(r"(?:https?://|www\.)[^\s]+", _url_repl, cleaned)

    # 3. Buang backtick inline code
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)

    # 4. Buang formatting bold / italic markdown secara aman
    # 4a. Bold: **teks**
    cleaned = re.sub(r"\*\*(?!\s)([^*]+?)(?<!\s)\*\*", r"\1", cleaned)

    # 4b. Italic: *teks* (bukan operasi aritmetika seperti 2 * 3 * 4)
    cleaned = re.sub(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])", r"\1", cleaned)

    # 4c. Italic dengan underscore tunggal: _teks_
    # HANYA jika diawali dan diakhiri batas non-kata/non-underscore (CommonMark),
    # sehingga tidak merusak snake_case (read_file), dunder (__init__), atau private (_private).
    cleaned = re.sub(r"(?<![\w_])_(?![\s_])([^_\n]+?)(?<![\s_])_(?![\w_])", r"\1", cleaned)

    # 5. Bersihkan bullet points dan nomor list di awal baris
    lines = []
    for line in cleaned.splitlines():
        cl = re.sub(r"^\s*[-*+]\s+", "", line)
        cl = re.sub(r"^\s*\d+\.\s+", "", cl)
        cl = cl.strip()
        if cl:
            lines.append(cl)

    return "\n".join(lines)


def apply_pronunciation(
    text: str,
    pronunciation_map: Optional[Dict[str, str]] = None,
) -> str:
    """Menerapkan kamus pelafalan pada teks yang telah dibersihkan sebelum chunking.
    Pencocokan peka huruf besar-kecil persis (case-sensitive), batas kata (\\b),
    dan tidak mengubah komponen di dalam path berkas (yang berbatasan dengan / atau \\).
    """
    if not text:
        return ""
    pmap = DEFAULT_PRONUNCIATION if pronunciation_map is None else pronunciation_map
    if not pmap:
        return text

    result = text
    for src, dst in pmap.items():
        if not src:
            continue
        pattern = re.compile(rf"(?<![/\\])\b{re.escape(src)}\b(?![/\\])")
        result = pattern.sub(dst, result)
    return result


def _split_sentences_from_cleaned(cleaned: str, max_chunk_chars: int = 250) -> List[str]:
    """Memecah teks bersih menjadi daftar kalimat yang masing-masing <= max_chunk_chars."""
    if not cleaned.strip():
        return []

    # Lindungi angka dengan tanda titik (desimal / ribuan): digit.digit
    protected = re.sub(r"(?<=\d)\.(?=\d)", "<DOT>", cleaned)

    # Lindungi singkatan umum (misal: dll., e.g.)
    for abbr in ABBREVIATIONS:
        pattern = re.compile(rf"\b({re.escape(abbr)})\.", re.IGNORECASE)
        protected = pattern.sub(r"\1<DOT>", protected)

    # Potong berdasarkan tanda baca akhir kalimat (.!? diikuti spasi atau baris baru)
    raw_sentences = re.split(r"(?<=[.!?])\s+|\n+", protected)
    chunks: List[str] = []

    for s in raw_sentences:
        s = s.replace("<DOT>", ".").strip()
        if not s:
            continue
        if len(s) <= max_chunk_chars:
            chunks.append(s)
        else:
            # Sub-split kalimat yang terlalu panjang berdasarkan koma/titik-koma/spasi
            parts = re.split(r"(?<=[,;])\s+", s)
            current = ""
            for p in parts:
                if len(current) + len(p) + 1 <= max_chunk_chars:
                    current = f"{current} {p}".strip()
                else:
                    if current:
                        chunks.append(current)
                    if len(p) > max_chunk_chars:
                        words = p.split()
                        w_curr = ""
                        for w in words:
                            if len(w_curr) + len(w) + 1 <= max_chunk_chars:
                                w_curr = f"{w_curr} {w}".strip()
                            else:
                                if w_curr:
                                    chunks.append(w_curr)
                                w_curr = w
                        if w_curr:
                            chunks.append(w_curr)
                        current = ""
                    else:
                        current = p
            if current:
                chunks.append(current)

    return chunks


def merge_sentences_for_tts(
    sentences: List[str],
    max_chunk_chars: int = 250,
    min_first_words: int = 6,
) -> List[str]:
    """Strategi chunk Tahap 4C-1:
    - Chunk PERTAMA dibuat pendek supaya suara cepat mulai: kalimat pertama saja,
      tetapi jika kurang dari `min_first_words` (6 kata), digabung dengan kalimat berikutnya
      (selama panjang gabungan <= max_chunk_chars).
    - Chunk berikutnya digabung berurutan sampai batas `max_chunk_chars` (250 karakter).
    """
    if not sentences:
        return []

    merged: List[str] = []
    idx = 0
    first = sentences[0]
    first_word_count = len(first.split())

    if first_word_count < min_first_words and len(sentences) > 1:
        combined = f"{first} {sentences[1]}".strip()
        if len(combined) <= max_chunk_chars:
            merged.append(combined)
            idx = 2
        else:
            merged.append(first)
            idx = 1
    else:
        merged.append(first)
        idx = 1

    current = ""
    while idx < len(sentences):
        s = sentences[idx]
        if not current:
            current = s
        elif len(current) + 1 + len(s) <= max_chunk_chars:
            current = f"{current} {s}".strip()
        else:
            merged.append(current)
            current = s
        idx += 1

    if current:
        merged.append(current)

    return merged


def chunk_text(text: str, max_chunk_chars: int = 250) -> List[str]:
    """Memotong teks yang telah dibersihkan menjadi daftar chunk kalimat.
    Menjaga angka desimal, angka ribuan, dan singkatan agar tidak terpotong sembarangan.
    """
    cleaned = clean_text_for_tts(text)
    return _split_sentences_from_cleaned(cleaned, max_chunk_chars=max_chunk_chars)


def prepare_tts_chunks(
    text: str,
    max_chunk_chars: int = 250,
    pronunciation_map: Optional[Dict[str, str]] = None,
    min_first_words: int = 6,
) -> List[str]:
    """Pipeline lengkap teks ke daftar chunk TTS (Tahap 4C-1):
    1. clean_text_for_tts(text)
    2. apply_pronunciation(cleaned, pronunciation_map)
    3. Pemotongan per kalimat
    4. Strategi chunk cepat-mulai (chunk pertama pendek / gabung jika < 6 kata; chunk berikutnya <= 250 chars)
    """
    cleaned = clean_text_for_tts(text)
    if not cleaned.strip():
        return []
    pronounced = apply_pronunciation(cleaned, pronunciation_map=pronunciation_map)
    sentences = _split_sentences_from_cleaned(pronounced, max_chunk_chars=max_chunk_chars)
    return merge_sentences_for_tts(
        sentences,
        max_chunk_chars=max_chunk_chars,
        min_first_words=min_first_words,
    )


def apply_cpu_affinity(max_cores: int = 2) -> bool:
    """Membatasi thread/process affinity ke <= 2 core bila didukung oleh sistem operasi."""
    try:
        import psutil
        p = psutil.Process()
        current = p.cpu_affinity()
        if len(current) > max_cores:
            p.cpu_affinity(current[:max_cores])
        return True
    except Exception:
        try:
            import ctypes
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            mask = (1 << max_cores) - 1
            res = ctypes.windll.kernel32.SetProcessAffinityMask(handle, mask)
            return bool(res)
        except Exception:
            return False


class TTSManager:
    """Pengelola antrean TTS dengan pipelining sintesis & pemutaran serta pembatalan instan."""

    def __init__(self):
        self.queue: queue.Queue = queue.Queue()
        self.play_queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.play_worker_thread: Optional[threading.Thread] = None
        self.current_engine: Optional[BaseTTSEngine] = None
        self._current_play_engine: Optional[BaseTTSEngine] = None
        self._default_engine: Optional[BaseTTSEngine] = None
        self._default_engine_key: Optional[Tuple[str, str, str, str]] = None
        self._lock = threading.Lock()
        self._cpu_affinity_applied = False
        self._epoch: int = 0
        self._in_flight_synth: int = 0
        self._in_flight_play: int = 0
        self.last_speak_called_at: float = 0.0

        # Hook opsional untuk telemetri benchmark (TTFA & gap antar-chunk)
        self.on_before_play: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_after_play: Optional[Callable[[Dict[str, Any], bool], None]] = None

    def _ensure_worker(self):
        with self._lock:
            if self.worker_thread is None or not self.worker_thread.is_alive():
                self.worker_thread = threading.Thread(
                    target=self._worker_loop,
                    daemon=True,
                    name="ENKI_TTS_Worker",
                )
                self.worker_thread.start()
            if self.play_worker_thread is None or not self.play_worker_thread.is_alive():
                self.play_worker_thread = threading.Thread(
                    target=self._play_worker_loop,
                    daemon=True,
                    name="ENKI_TTS_PlayWorker",
                )
                self.play_worker_thread.start()

    def _resolve_default_engine_and_voice(
        self,
        voice: Optional[str] = None,
    ) -> Tuple[BaseTTSEngine, Optional[str], Dict[str, str]]:
        from enki.config import AppConfig

        cfg = AppConfig()
        engine_type = cfg.tts_engine.lower().strip()
        model_dir = cfg.tts_model_dir
        st_voice = cfg.tts_voice_supertonic
        sapi_voice = cfg.tts_voice
        pron_map = cfg.tts_pronunciation

        key = (engine_type, model_dir, st_voice, sapi_voice)
        with self._lock:
            if self._default_engine is None or self._default_engine_key != key:
                if engine_type == "sapi5":
                    from enki.audio.sapi5 import SAPI5Engine

                    self._default_engine = SAPI5Engine()
                else:
                    from enki.audio.supertonic import SupertonicEngine

                    self._default_engine = SupertonicEngine(
                        model_dir=model_dir,
                        default_voice=st_voice,
                        fallback_voice=sapi_voice,
                    )
                self._default_engine_key = key
            engine = self._default_engine

        if engine_type == "sapi5":
            resolved_voice = voice if voice is not None else sapi_voice
        else:
            resolved_voice = voice if voice is not None else st_voice

        return engine, resolved_voice, pron_map

    def _worker_loop(self):
        os.environ["OMP_NUM_THREADS"] = "2"
        self._cpu_affinity_applied = apply_cpu_affinity(2)
        while True:
            try:
                item = self.queue.get()
                if item is None:
                    self.queue.task_done()
                    break

                epoch, chunk_idx, text_chunk, voice, engine = item
                if self.cancel_event.is_set() or epoch != self._epoch:
                    self.queue.task_done()
                    continue

                is_pipelined = hasattr(engine, "synthesize_chunk") and hasattr(engine, "play_synthesized")

                if is_pipelined:
                    with self._lock:
                        self.current_engine = engine
                        self._in_flight_synth += 1
                    try:
                        synth_item = engine.synthesize_chunk(
                            text_chunk,
                            voice=voice,
                            cancel_check=lambda ep=epoch: self.cancel_event.is_set() or ep != self._epoch,
                        )
                        if synth_item is not None:
                            with self._lock:
                                cancelled_now = self.cancel_event.is_set() or epoch != self._epoch
                            if cancelled_now:
                                if hasattr(engine, "discard_synthesized"):
                                    engine.discard_synthesized(synth_item, count_as_cancelled=True)
                            else:
                                synth_item["chunk_idx"] = chunk_idx
                                synth_item["speak_called_at"] = self.last_speak_called_at
                                self.play_queue.put((epoch, chunk_idx, synth_item, engine))
                    finally:
                        with self._lock:
                            self._in_flight_synth = max(0, self._in_flight_synth - 1)
                            if self._in_flight_synth == 0:
                                self.current_engine = None
                        self.queue.task_done()
                else:
                    with self._lock:
                        self.current_engine = engine
                    try:
                        if not self.cancel_event.is_set() and epoch == self._epoch:
                            engine.speak_chunk(text_chunk, voice=voice)
                    finally:
                        with self._lock:
                            self.current_engine = None
                        self.queue.task_done()
            except Exception:
                pass

    def _play_worker_loop(self):
        while True:
            try:
                play_item = self.play_queue.get()
                if play_item is None:
                    self.play_queue.task_done()
                    break

                epoch, chunk_idx, synth_item, engine = play_item
                if self.cancel_event.is_set() or epoch != self._epoch:
                    if hasattr(engine, "discard_synthesized"):
                        engine.discard_synthesized(synth_item, count_as_cancelled=True)
                    self.play_queue.task_done()
                    continue

                with self._lock:
                    self._current_play_engine = engine
                    self._in_flight_play += 1

                try:
                    if not self.cancel_event.is_set() and epoch == self._epoch:
                        engine.play_synthesized(
                            synth_item,
                            cancel_check=lambda ep=epoch: self.cancel_event.is_set() or ep != self._epoch,
                            on_before_play=self.on_before_play,
                            on_after_play=self.on_after_play,
                        )
                    else:
                        if hasattr(engine, "discard_synthesized"):
                            engine.discard_synthesized(synth_item, count_as_cancelled=True)
                finally:
                    with self._lock:
                        self._in_flight_play = max(0, self._in_flight_play - 1)
                        if self._in_flight_play == 0:
                            self._current_play_engine = None
                    self.play_queue.task_done()
            except Exception:
                pass

    def speak(
        self,
        text: str,
        voice: Optional[str] = None,
        engine: Optional[BaseTTSEngine] = None,
        max_chunk_chars: int = 250,
        pronunciation_map: Optional[Dict[str, str]] = None,
    ) -> None:
        if engine is None:
            engine, resolved_voice, cfg_pron = self._resolve_default_engine_and_voice(voice=voice)
            if pronunciation_map is None:
                pronunciation_map = cfg_pron
        else:
            resolved_voice = voice

        chunks = prepare_tts_chunks(
            text,
            max_chunk_chars=max_chunk_chars,
            pronunciation_map=pronunciation_map,
        )
        if not chunks:
            return

        if hasattr(engine, "reset_for_new_utterance"):
            engine.reset_for_new_utterance()

        with self._lock:
            self._epoch += 1
            utterance_epoch = self._epoch
            self.last_speak_called_at = time.perf_counter()

        self.cancel_event.clear()
        self._ensure_worker()

        for idx, chunk in enumerate(chunks):
            self.queue.put((utterance_epoch, idx, chunk, resolved_voice, engine))

    def cancel(self) -> None:
        """Mengosongkan antrean sintesis & pemutaran seketika dan menghentikan pemutaran aktif."""
        self.cancel_event.set()
        with self._lock:
            self._epoch += 1
            eng_synth = self.current_engine
            eng_play = self._current_play_engine
            eng_def = self._default_engine

        # Kosongkan antrean sintesis
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break
            except ValueError:
                break

        # Kosongkan antrean pemutaran dan buang berkas WAV sementara yang belum sempat diputar
        while True:
            try:
                item = self.play_queue.get_nowait()
                if item is not None:
                    _, _, synth_item, item_engine = item
                    if hasattr(item_engine, "discard_synthesized"):
                        try:
                            item_engine.discard_synthesized(synth_item, count_as_cancelled=True)
                        except Exception:
                            pass
                self.play_queue.task_done()
            except queue.Empty:
                break
            except ValueError:
                break

        # Hentikan engine yang sedang aktif
        seen = set()
        for eng in (eng_play, eng_synth, eng_def):
            if eng is not None and id(eng) not in seen:
                seen.add(id(eng))
                try:
                    eng.stop()
                except Exception:
                    pass

        with self._lock:
            self.current_engine = None
            self._current_play_engine = None

    def is_busy(self) -> bool:
        with self._lock:
            return (
                not self.queue.empty()
                or not self.play_queue.empty()
                or self.current_engine is not None
                or self._current_play_engine is not None
                or self._in_flight_synth > 0
                or self._in_flight_play > 0
            )

    def wait_done(self, timeout: Optional[float] = None) -> bool:
        try:
            if timeout is None:
                self.queue.join()
                self.play_queue.join()
                return True
            deadline = time.perf_counter() + timeout
            while self.is_busy():
                if time.perf_counter() >= deadline:
                    return False
                time.sleep(0.005)
            return True
        except Exception:
            return False


_GLOBAL_TTS_MANAGER: Optional[TTSManager] = None
_GLOBAL_LOCK = threading.Lock()


def get_tts_manager(create: bool = True) -> Optional[TTSManager]:
    global _GLOBAL_TTS_MANAGER
    with _GLOBAL_LOCK:
        if _GLOBAL_TTS_MANAGER is None and create:
            _GLOBAL_TTS_MANAGER = TTSManager()
        return _GLOBAL_TTS_MANAGER


def speak(
    text: str,
    is_muted: bool = False,
    voice: Optional[str] = None,
    engine: Optional[BaseTTSEngine] = None,
    max_chunk_chars: int = 250,
    pronunciation_map: Optional[Dict[str, str]] = None,
) -> None:
    """Fungsi utama sintesis suara TTS.
    ZERO-COST MUTE: Jika is_muted == True, fungsi kembali langsung
    tanpa thread baru, tanpa subprocess, dan tanpa mengimpor modul engine audio.
    """
    if is_muted:
        return

    mgr = get_tts_manager(create=True)
    mgr.speak(
        text=text,
        voice=voice,
        engine=engine,
        max_chunk_chars=max_chunk_chars,
        pronunciation_map=pronunciation_map,
    )


def cancel() -> None:
    """Membatalkan seluruh antrean sintesis dan pemutaran TTS seketika."""
    mgr = get_tts_manager(create=False)
    if mgr is not None:
        mgr.cancel()


def is_busy() -> bool:
    """Memeriksa apakah worker TTS sedang memproses antrean audio."""
    mgr = get_tts_manager(create=False)
    if mgr is None:
        return False
    return mgr.is_busy()
