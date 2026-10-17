"""Subsistem Text-to-Speech (TTS) ENKI dengan dukungan Zero-Cost Mute, chunking cerdas, dan interupsi instan."""

import os
import re
import queue
import threading
from typing import Optional, List, Tuple, Any

from enki.audio.base import BaseTTSEngine, FakeEngine

ABBREVIATIONS = [
    "dll", "dsb", "dst", "misal", "mis", "dr", "prof", "etc", "e.g", "i.e", "vs"
]

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

    # 4c. Bold / Italic dengan underscore: HANYA jika dipisahkan spasi/tanda baca,
    # BUKAN bagian dari identifier snake_case (read_file, snake_case_name), dunder (__init__.py), atau private (_private).
    cleaned = re.sub(r"(?<!\S)__([^_]+?)__(?!\S)", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\S)_([^_]+?)_(?!\S)", r"\1", cleaned)

    # 5. Bersihkan bullet points dan nomor list di awal baris
    lines = []
    for line in cleaned.splitlines():
        cl = re.sub(r"^\s*[-*+]\s+", "", line)
        cl = re.sub(r"^\s*\d+\.\s+", "", cl)
        cl = cl.strip()
        if cl:
            lines.append(cl)

    return "\n".join(lines)


def chunk_text(text: str, max_chunk_chars: int = 250) -> List[str]:
    """Memotong teks yang telah dibersihkan menjadi daftar chunk kalimat yang nyaman diucapkan.
    Menjaga angka desimal, angka ribuan, dan singkatan agar tidak terpotong sembarangan.
    """
    cleaned = clean_text_for_tts(text)
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
    """Pengelola antrean TTS satu worker thread dengan pembatalan instan."""

    def __init__(self):
        self.queue: queue.Queue = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker_thread: Optional[threading.Thread] = None
        self.current_engine: Optional[BaseTTSEngine] = None
        self._lock = threading.Lock()
        self._cpu_affinity_applied = False

    def _ensure_worker(self):
        with self._lock:
            if self.worker_thread is None or not self.worker_thread.is_alive():
                self.worker_thread = threading.Thread(
                    target=self._worker_loop,
                    daemon=True,
                    name="ENKI_TTS_Worker"
                )
                self.worker_thread.start()

    def _worker_loop(self):
        os.environ["OMP_NUM_THREADS"] = "2"
        self._cpu_affinity_applied = apply_cpu_affinity(2)
        while True:
            try:
                item = self.queue.get()
                if item is None:
                    self.queue.task_done()
                    break

                text_chunk, voice, engine = item
                if self.cancel_event.is_set():
                    self.queue.task_done()
                    continue

                with self._lock:
                    self.current_engine = engine

                try:
                    if not self.cancel_event.is_set():
                        engine.speak_chunk(text_chunk, voice=voice)
                finally:
                    with self._lock:
                        self.current_engine = None
                    self.queue.task_done()
            except Exception:
                pass

    def speak(
        self,
        text: str,
        voice: Optional[str] = None,
        engine: Optional[BaseTTSEngine] = None,
        max_chunk_chars: int = 250
    ) -> None:
        chunks = chunk_text(text, max_chunk_chars=max_chunk_chars)
        if not chunks:
            return

        if engine is None:
            # Lazy import SAPI5Engine hanya saat diperlukan (bukan mute)
            from enki.audio.sapi5 import SAPI5Engine
            engine = SAPI5Engine()

        self.cancel_event.clear()
        self._ensure_worker()

        for chunk in chunks:
            self.queue.put((chunk, voice, engine))

    def cancel(self) -> None:
        """Mengosongkan antrean seketika dan menghentikan pemutaran audio aktif."""
        self.cancel_event.set()
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break
            except ValueError:
                break
        with self._lock:
            if self.current_engine is not None:
                try:
                    self.current_engine.stop()
                except Exception:
                    pass
                self.current_engine = None

    def is_busy(self) -> bool:
        with self._lock:
            return not self.queue.empty() or self.current_engine is not None

    def wait_done(self, timeout: Optional[float] = None) -> bool:
        try:
            self.queue.join()
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
    max_chunk_chars: int = 250
) -> None:
    """Fungsi utama sintesis suara TTS.
    ZERO-COST MUTE: Jika is_muted == True, fungsi kembali langsung
    tanpa thread baru, tanpa subprocess, dan tanpa mengimpor modul engine audio.
    """
    if is_muted:
        return

    mgr = get_tts_manager(create=True)
    mgr.speak(text=text, voice=voice, engine=engine, max_chunk_chars=max_chunk_chars)


def cancel() -> None:
    """Membatalkan seluruh antrean sintesis TTS seketika."""
    mgr = get_tts_manager(create=False)
    if mgr is not None:
        mgr.cancel()


def is_busy() -> bool:
    """Memeriksa apakah worker TTS sedang memproses antrean audio."""
    mgr = get_tts_manager(create=False)
    if mgr is None:
        return False
    return mgr.is_busy()
