"""Engine TTS Supertonic 3 (ONNX Runtime CPU, offline, pipelining sintesis & pemutaran winsound)."""

import os
import tempfile
import threading
import time
import winsound
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from enki.audio.base import BaseTTSEngine
from enki.audio.sapi5 import SAPI5Engine


def default_supertonic_model_dir() -> str:
    """Mengembalikan path default direktori model Supertonic 3 tanpa hardcode username."""
    return str(Path.home() / ".cache" / "supertonic3")


# Selisih penutupan perangkat winsound terukur pada 5 WAV uji (median = 287.21 ms)
DEFAULT_WINSOUND_TAIL_PADDING_SEC: float = 0.287208

SUPERTONIC_VALID_VOICES = {
    "F1", "F2", "F3", "F4", "F5",
    "M1", "M2", "M3", "M4", "M5",
}


def validate_supertonic_voice(voice: Optional[str], default_voice: str = "M4") -> str:
    """Memvalidasi nama suara Supertonic. Jika tidak valid, cetak peringatan satu baris dan gunakan default M4."""
    raw = (voice or default_voice or "M4").strip()
    if raw in SUPERTONIC_VALID_VOICES:
        return raw
    print(
        f"[Peringatan TTS] Suara '{voice}' tidak valid untuk Supertonic (hanya F1-F5, M1-M5); menggunakan default M4.",
        flush=True,
    )
    return "M4"


class SupertonicEngine(BaseTTSEngine):
    """Engine TTS lokal Supertonic 3 dengan lazy loading, mode offline ketat,
    fallback otomatis ke SAPI5, dan pemutaran sinkron di thread pemutar via winsound.
    """

    def __init__(
        self,
        model_dir: Optional[str] = None,
        default_voice: str = "M4",
        fallback_voice: Optional[str] = None,
        tail_padding_sec: float = DEFAULT_WINSOUND_TAIL_PADDING_SEC,
    ):
        self.model_dir: str = model_dir or default_supertonic_model_dir()
        self.default_voice: str = default_voice if default_voice in SUPERTONIC_VALID_VOICES else "M4"
        self.fallback_voice: Optional[str] = fallback_voice
        self.tail_padding_sec: float = max(0.0, float(tail_padding_sec))

        self._load_lock = threading.Lock()
        self._tts: Any = None
        self._voice_styles: Dict[str, Any] = {}
        self._fallback_engine: Optional[SAPI5Engine] = None
        self._fallback_warned: bool = False

        self._stop_event = threading.Event()
        self._play_lock = threading.Lock()
        self._is_playing: bool = False

        # Telemetri dan inspeksi pengujian
        self.spoken_chunks: List[str] = []
        self.played_chunks_count: int = 0
        self.discarded_after_cancel_count: int = 0
        self.stop_called_count: int = 0
        self.last_stop_called_at: float = 0.0
        self.last_purge_completed_at: float = 0.0
        self.last_playback_exited_at: float = 0.0
        self.play_timestamps_after_stop: List[float] = []
        self.clipped_playbacks_count: int = 0
        self.playback_records: List[Dict[str, Any]] = []

    @property
    def is_fallback(self) -> bool:
        return self._fallback_engine is not None

    def reset_for_new_utterance(self) -> None:
        """Mereset event penghentian sebelum memulai pemutaran kalimat/balasan baru."""
        self._stop_event.clear()

    def _activate_fallback(self, reason: str) -> SAPI5Engine:
        """Mengaktifkan fallback ke SAPI5Engine dan mencetak tepat satu baris pesan peringatan."""
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        if self._fallback_engine is None:
            self._fallback_engine = SAPI5Engine()
        if not self._fallback_warned:
            self._fallback_warned = True
            print(
                f"[Peringatan TTS] Model Supertonic gagal dimuat dari '{self.model_dir}' ({reason}); beralih otomatis ke SAPI5.",
                flush=True,
            )
        return self._fallback_engine

    def _restore_full_cpu_affinity_for_onnx(self) -> None:
        """Memastikan proses memiliki akses ke seluruh core CPU agar thread pool ONNX Runtime
        tidak mengalami spinlock thrashing pada 2 hyperthread.
        """
        try:
            import psutil

            p = psutil.Process()
            total_cpus = os.cpu_count() or 1
            if len(p.cpu_affinity()) < total_cpus:
                p.cpu_affinity(list(range(total_cpus)))
        except Exception:
            pass

    def _ensure_loaded(self) -> bool:
        """Memuat model Supertonic 3 secara malas (lazy) di thread latar dengan HF_HUB_OFFLINE=1."""
        with self._load_lock:
            if self._tts is not None:
                return True
            if self._fallback_engine is not None:
                return False

            # Runtime dilarang mengunduh model
            os.environ["HF_HUB_OFFLINE"] = "1"
            self._restore_full_cpu_affinity_for_onnx()

            model_path = Path(self.model_dir)
            required_cfg = model_path / "onnx" / "tts.json"
            if not model_path.exists() or not required_cfg.exists():
                self._activate_fallback("direktori atau berkas onnx/tts.json tidak ditemukan")
                return False

            try:
                from supertonic import TTS

                self._tts = TTS(
                    model="supertonic-3",
                    model_dir=model_path,
                    auto_download=False,
                )
                return True
            except Exception as e:
                self._tts = None
                self._activate_fallback(str(e))
                return False

    def _get_voice_style(self, voice_name: str) -> Any:
        v = validate_supertonic_voice(voice_name, self.default_voice)
        if v not in self._voice_styles:
            self._voice_styles[v] = self._tts.get_voice_style(v)
        return self._voice_styles[v]

    def synthesize_chunk(
        self,
        text: str,
        voice: Optional[str] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        save_wav_dir: Optional[Path] = None,
        save_wav_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Menjalankan sintesis satu chunk teks menjadi berkas WAV sementara (untuk pipelining).
        Jika cancel_check() bernilai True sebelum atau sesudah inferensi ONNX, hasil langsung dibuang.
        """
        if not text or not text.strip():
            return None

        if cancel_check is not None and cancel_check():
            self.discarded_after_cancel_count += 1
            return None

        loaded = self._ensure_loaded()
        if not loaded or self._fallback_engine is not None:
            return {
                "mode": "fallback_sapi5",
                "text": text,
                "voice": self.fallback_voice,
            }

        if cancel_check is not None and cancel_check():
            self.discarded_after_cancel_count += 1
            return None

        target_voice = validate_supertonic_voice(voice, self.default_voice)
        try:
            self._restore_full_cpu_affinity_for_onnx()
            style = self._get_voice_style(target_voice)
            t_synth0 = time.perf_counter()
            wav_arr, dur_arr = self._tts.synthesize(
                text,
                voice_style=style,
                lang="id",
            )
            synth_time_sec = time.perf_counter() - t_synth0
        except Exception as e:
            self._tts = None
            self._activate_fallback(f"sintesis gagal: {e}")
            return {
                "mode": "fallback_sapi5",
                "text": text,
                "voice": self.fallback_voice,
            }

        # Jika cancel() dipanggil saat ONNX sedang inferensi, buang hasil sintesis!
        if (cancel_check is not None and cancel_check()) or self._stop_event.is_set():
            self.discarded_after_cancel_count += 1
            return None

        try:
            dur_model = float(dur_arr.squeeze())
        except Exception:
            dur_model = 0.0
        try:
            dur_frames = float(len(wav_arr.squeeze())) / float(getattr(self._tts, "sample_rate", 44100))
        except Exception:
            dur_frames = dur_model
        duration_sec = max(dur_model, dur_frames)

        if save_wav_dir is not None and save_wav_name is not None:
            save_wav_dir.mkdir(parents=True, exist_ok=True)
            wav_path = str(save_wav_dir / save_wav_name)
            is_temp = False
        else:
            fd, wav_path = tempfile.mkstemp(prefix="enki_supertonic_", suffix=".wav")
            os.close(fd)
            is_temp = True

        self._tts.save_audio(wav_arr, wav_path)

        return {
            "mode": "supertonic_wav",
            "wav_path": wav_path,
            "is_temp": is_temp,
            "duration_sec": duration_sec,
            "wav_frames_sec": dur_frames,
            "synth_time_sec": synth_time_sec,
            "ready_at": time.perf_counter(),
            "text": text,
            "voice": target_voice,
        }

    def discard_synthesized(
        self,
        item: Optional[Dict[str, Any]],
        count_as_cancelled: bool = True,
    ) -> None:
        """Membuang hasil sintesis yang belum diputar dan menghapus berkas WAV sementara."""
        if item is None:
            return
        if count_as_cancelled:
            self.discarded_after_cancel_count += 1
        wav_path = item.get("wav_path")
        if wav_path and item.get("is_temp", True):
            try:
                if os.path.exists(wav_path):
                    os.remove(wav_path)
            except Exception:
                pass

    def play_synthesized(
        self,
        item: Optional[Dict[str, Any]],
        cancel_check: Optional[Callable[[], bool]] = None,
        on_before_play: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_after_play: Optional[Callable[[Dict[str, Any], bool], None]] = None,
    ) -> bool:
        """Memutar hasil sintesis secara sinkron di thread pemutar menggunakan winsound."""
        if item is None:
            return False

        if (cancel_check is not None and cancel_check()) or self._stop_event.is_set():
            self.discard_synthesized(item, count_as_cancelled=True)
            return False

        mode = item.get("mode")
        if mode == "fallback_sapi5":
            if self._fallback_engine is None:
                self._activate_fallback("fallback SAPI5 diminta")
            if on_before_play is not None:
                on_before_play(item)
            self.spoken_chunks.append(item.get("text", ""))
            self.played_chunks_count += 1
            ok = self._fallback_engine.speak_chunk(item.get("text", ""), voice=item.get("voice"))
            if on_after_play is not None:
                on_after_play(item, self._stop_event.is_set())
            return ok

        wav_path = item.get("wav_path")
        duration_sec = float(item.get("duration_sec", 0.0))
        if not wav_path or not os.path.exists(wav_path):
            return False

        stopped_early = False
        try:
            if (cancel_check is not None and cancel_check()) or self._stop_event.is_set():
                self.discard_synthesized(item, count_as_cancelled=True)
                return False

            if on_before_play is not None:
                on_before_play(item)

            t_play_start = time.perf_counter()
            with self._play_lock:
                if self._stop_event.is_set() or (cancel_check is not None and cancel_check()):
                    stopped_early = True
                else:
                    self._is_playing = True
                    if self.last_stop_called_at > 0 and self._stop_event.is_set():
                        self.play_timestamps_after_stop.append(time.perf_counter())
                    winsound.PlaySound(
                        wav_path,
                        winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                    )
                    self.spoken_chunks.append(item.get("text", ""))
                    self.played_chunks_count += 1

            if stopped_early:
                self.discard_synthesized(item, count_as_cancelled=True)
                return False

            # Tunggu pemutaran selesai (durasi WAV + selisih penutupan perangkat terukur) atau diinterupsi oleh stop()
            wait_timeout = max(0.0, duration_sec + self.tail_padding_sec)
            stopped_early = self._stop_event.wait(timeout=wait_timeout)
            t_play_end = time.perf_counter()
            actual_play_sec = t_play_end - t_play_start

            if stopped_early:
                try:
                    winsound.PlaySound(None, winsound.SND_PURGE)
                except Exception:
                    pass
                self.last_playback_exited_at = time.perf_counter()
            else:
                is_clipped = actual_play_sec < duration_sec
                if is_clipped:
                    self.clipped_playbacks_count += 1
                self.playback_records.append(
                    {
                        "text": item.get("text", ""),
                        "duration_sec": duration_sec,
                        "tail_padding_sec": self.tail_padding_sec,
                        "expected_device_sec": wait_timeout,
                        "actual_play_sec": actual_play_sec,
                        "clipped": is_clipped,
                    }
                )

            if on_after_play is not None:
                on_after_play(item, stopped_early)

            return not stopped_early
        finally:
            with self._play_lock:
                self._is_playing = False
            if item.get("is_temp", True):
                try:
                    if os.path.exists(wav_path):
                        os.remove(wav_path)
                except Exception:
                    pass

    def speak_chunk(self, text: str, voice: Optional[str] = None) -> bool:
        """Sintesis dan putar satu chunk kalimat secara berurutan (antarmuka BaseTTSEngine)."""
        if not text or not text.strip():
            return True
        item = self.synthesize_chunk(
            text=text,
            voice=voice,
            cancel_check=lambda: self._stop_event.is_set(),
        )
        if item is None:
            return False
        return self.play_synthesized(
            item=item,
            cancel_check=lambda: self._stop_event.is_set(),
        )

    def stop(self) -> None:
        """Menghentikan pemutaran audio yang sedang berjalan seketika via winsound.SND_PURGE."""
        t0 = time.perf_counter()
        self.last_stop_called_at = t0
        self.stop_called_count += 1
        self._stop_event.set()
        try:
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
        self.last_purge_completed_at = time.perf_counter()

        if self._fallback_engine is not None:
            try:
                self._fallback_engine.stop()
            except Exception:
                pass
