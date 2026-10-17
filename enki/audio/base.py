"""Antarmuka dasar engine Text-to-Speech (TTS) ENKI."""

from abc import ABC, abstractmethod
from typing import Optional, List

class BaseTTSEngine(ABC):
    """Antarmuka abstrak engine TTS."""

    @abstractmethod
    def speak_chunk(self, text: str, voice: Optional[str] = None) -> bool:
        """Sintesis dan putar satu chunk kalimat. Mengembalikan True jika berhasil."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Menghentikan pemutaran audio yang sedang berjalan seketika."""
        pass


class FakeEngine(BaseTTSEngine):
    """Engine tiruan (fake) untuk pengujian deterministik tanpa memanggil audio sistem."""

    def __init__(self, delay_per_chunk: float = 0.0):
        self.delay_per_chunk = delay_per_chunk
        self.spoken_chunks: List[str] = []
        self.voices_used: List[Optional[str]] = []
        self.stop_called_count: int = 0
        self.is_stopped: bool = False

    def speak_chunk(self, text: str, voice: Optional[str] = None) -> bool:
        if self.is_stopped:
            return False
        self.spoken_chunks.append(text)
        self.voices_used.append(voice)
        if self.delay_per_chunk > 0:
            import time
            time.sleep(self.delay_per_chunk)
        return True

    def stop(self) -> None:
        self.is_stopped = True
        self.stop_called_count += 1
