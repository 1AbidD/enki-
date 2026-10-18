"""Paket audio dan Text-to-Speech (TTS) untuk ENKI."""

from enki.audio.base import BaseTTSEngine, FakeEngine
from enki.audio.tts import (
    speak,
    cancel,
    is_busy,
    chunk_text,
    clean_text_for_tts,
    HEX_HASH_REGEX,
    replace_hex_hashes_for_tts,
    apply_pronunciation,
    prepare_tts_chunks,
    prepare_tts_chunks_4c1,
    merge_sentences_for_tts,
    merge_sentences_for_tts_ramp,
    predict_audio_duration,
    predict_synth_time,
    compute_gapless_start_delay,
    align_raw_segments_to_chunks,
    run_synced_speech_stream,
)

__all__ = [
    "BaseTTSEngine",
    "FakeEngine",
    "speak",
    "cancel",
    "is_busy",
    "chunk_text",
    "clean_text_for_tts",
    "HEX_HASH_REGEX",
    "replace_hex_hashes_for_tts",
    "apply_pronunciation",
    "prepare_tts_chunks",
    "prepare_tts_chunks_4c1",
    "merge_sentences_for_tts",
    "merge_sentences_for_tts_ramp",
    "predict_audio_duration",
    "predict_synth_time",
    "compute_gapless_start_delay",
    "align_raw_segments_to_chunks",
    "run_synced_speech_stream",
]
