"""Paket audio dan Text-to-Speech (TTS) untuk ENKI."""

from enki.audio.base import BaseTTSEngine, FakeEngine
from enki.audio.tts import (
    speak,
    cancel,
    is_busy,
    chunk_text,
    clean_text_for_tts,
    apply_pronunciation,
    prepare_tts_chunks,
    prepare_tts_chunks_4c1,
    merge_sentences_for_tts,
    merge_sentences_for_tts_ramp,
    predict_audio_duration,
    predict_synth_time,
)

__all__ = [
    "BaseTTSEngine",
    "FakeEngine",
    "speak",
    "cancel",
    "is_busy",
    "chunk_text",
    "clean_text_for_tts",
    "apply_pronunciation",
    "prepare_tts_chunks",
    "prepare_tts_chunks_4c1",
    "merge_sentences_for_tts",
    "merge_sentences_for_tts_ramp",
    "predict_audio_duration",
    "predict_synth_time",
]
