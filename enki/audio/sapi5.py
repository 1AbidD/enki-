"""Engine TTS SAPI5 menggunakan subprocess PowerShell System.Speech bawaan Windows."""

import os
import subprocess
import threading
from typing import Optional

from enki.audio.base import BaseTTSEngine

class SAPI5Engine(BaseTTSEngine):
    """Engine SAPI5 bawaan Windows tanpa dependensi pihak ketiga.
    Teks dikirimkan melalui stdin agar bebas dari risiko injeksi shell.
    """

    def __init__(self):
        self._current_proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def speak_chunk(self, text: str, voice: Optional[str] = None) -> bool:
        if not text or not text.strip():
            return True

        ps_script = """
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
[Console]::InputEncoding = [System.Text.Encoding]::UTF8
$voice = [Console]::ReadLine()
if ($voice -and $voice.Trim() -ne '') {
    try { $synth.SelectVoice($voice.Trim()) } catch {}
}
$text = [Console]::In.ReadToEnd()
if ($text -and $text.Trim() -ne '') {
    $synth.Speak($text)
}
$synth.Dispose()
"""
        env = os.environ.copy()
        env["OMP_NUM_THREADS"] = "2"

        # Teks dikirim aman melalui stdin: baris 1 adalah nama voice (bisa kosong), baris berikutnya teks ucapan
        payload = f"{voice or ''}\n{text}"

        proc = None
        try:
            with self._lock:
                self._current_proc = subprocess.Popen(
                    ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    env=env
                )
                proc = self._current_proc

            proc.communicate(input=payload)
            return proc.returncode == 0
        except Exception:
            return False
        finally:
            with self._lock:
                if self._current_proc is proc:
                    self._current_proc = None

    def stop(self) -> None:
        """Menghentikan sintesis audio yang sedang berjalan dengan menterminasi subprocess PowerShell."""
        with self._lock:
            if self._current_proc is not None:
                try:
                    self._current_proc.terminate()
                except Exception:
                    pass
                self._current_proc = None
