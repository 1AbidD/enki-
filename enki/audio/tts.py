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

# Langkah 1 Tahap 4C-3: Regex hash heksadesimal huruf kecil (panjang 7-40, minimal 1 angka & 1 huruf a-f)
HEX_HASH_REGEX = re.compile(r"\b(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")


def replace_hex_hashes_for_tts(text: str) -> str:
    """Mengganti token hash heksadesimal (cocok HEX_HASH_REGEX, tidak berbatasan dengan / atau \\)
    menjadi 'kode hash' pada teks untuk suara sebelum chunking.
    """
    if not text:
        return ""
    return HEX_HASH_REGEX.sub("kode hash", text)


def clean_text_for_tts(text: str) -> str:
    """Membersihkan format teks dari blok kode, URL panjang, hash hex, dan markup sebelum sintesis."""
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

    # 3b. Langkah 1 Tahap 4C-3: Ganti hash heksadesimal 7-40 karakter dengan 'kode hash'
    cleaned = replace_hex_hashes_for_tts(cleaned)

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


# Konstanta regresi linear Langkah 1 Tahap 4C-2 (Supertonic M4, CPU Idle, N=12):
# waktu_sintesis = c0 + c1 * durasi_audio  (R^2 = 0.993615)
# durasi_audio   = d0 + d1 * jumlah_karakter (R^2 = 0.999524)
DEFAULT_SYNTH_C0: float = 1.599501
DEFAULT_SYNTH_C1: float = 0.329724
DEFAULT_AUDIO_D0: float = 0.596618
DEFAULT_AUDIO_D1: float = 0.061480
DEFAULT_CHARS_PER_SEC: float = 16.265423


def predict_audio_duration(
    text_or_len: Any,
    d0: float = DEFAULT_AUDIO_D0,
    d1: float = DEFAULT_AUDIO_D1,
) -> float:
    """Memprediksi durasi audio (detik) dari teks atau jumlah karakter berdasarkan regresi Langkah 1."""
    char_count = len(text_or_len) if isinstance(text_or_len, str) else int(text_or_len)
    if char_count <= 0:
        return 0.0
    return float(d0) + float(d1) * float(char_count)


def predict_synth_time(
    text_or_len: Any,
    c0: float = DEFAULT_SYNTH_C0,
    c1: float = DEFAULT_SYNTH_C1,
    d0: float = DEFAULT_AUDIO_D0,
    d1: float = DEFAULT_AUDIO_D1,
) -> float:
    """Memprediksi waktu sintesis (detik) dari teks atau jumlah karakter berdasarkan regresi Langkah 1."""
    dur = predict_audio_duration(text_or_len, d0=d0, d1=d1)
    if dur <= 0.0:
        return 0.0
    return float(c0) + float(c1) * dur


def merge_sentences_for_tts(
    sentences: List[str],
    max_chunk_chars: int = 250,
    min_first_words: int = 6,
) -> List[str]:
    """Strategi chunk Tahap 4C-1 (LAMA):
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


def merge_sentences_for_tts_ramp(
    sentences: List[str],
    max_chunk_chars: int = 250,
    min_first_words: int = 6,
    max_first_words: int = 14,
    synth_c0: float = DEFAULT_SYNTH_C0,
    synth_c1: float = DEFAULT_SYNTH_C1,
    audio_d0: float = DEFAULT_AUDIO_D0,
    audio_d1: float = DEFAULT_AUDIO_D1,
    safety_margin_sec: float = 0.3,
) -> List[str]:
    """Strategi chunk bertahap (ramp) Tahap 4C-2 (BARU):
    - Chunk pertama: kalimat pertama. Jika lebih dari 14 kata, potong di batas klausa
      (koma atau titik koma) terdekat yang menghasilkan <= 14 kata; jika tidak ada
      tanda baca yang cocok, jangan dipotong. Jika kurang dari 6 kata, gabung dengan
      kalimat berikutnya (aturan 4C-1 tetap).
    - Chunk ke-N+1: gabungkan kalimat berurutan selama
      waktu_sintesis_prediksi(chunk N+1) <= durasi_audio_prediksi(chunk N) - 0.3 detik,
      dengan batas atas 250 karakter dan minimal satu kalimat.
    """
    work = [s.strip() for s in sentences if s and s.strip()]
    if not work:
        return []

    first = work.pop(0)
    first_words = first.split()

    if len(first_words) > max_first_words:
        clauses = [c.strip() for c in re.split(r"(?<=[,;])\s+", first) if c.strip()]
        if len(clauses) > 1:
            best_k = None
            for k in range(1, len(clauses)):
                prefix_candidate = " ".join(clauses[:k]).strip()
                if len(prefix_candidate.split()) <= max_first_words:
                    best_k = k
                else:
                    break
            if best_k is not None:
                first_cut = " ".join(clauses[:best_k]).strip()
                remainder = " ".join(clauses[best_k:]).strip()
                first = first_cut
                if remainder:
                    work.insert(0, remainder)
    elif len(first_words) < min_first_words and len(work) > 0:
        combined = f"{first} {work[0]}".strip()
        if len(combined) <= max_chunk_chars:
            first = combined
            work.pop(0)

    chunks: List[str] = [first]

    while work:
        prev_chunk = chunks[-1]
        prev_audio_dur = predict_audio_duration(prev_chunk, d0=audio_d0, d1=audio_d1)
        max_synth_allowed = prev_audio_dur - safety_margin_sec

        current = work.pop(0)
        while work:
            candidate = f"{current} {work[0]}".strip()
            cand_synth = predict_synth_time(
                candidate,
                c0=synth_c0,
                c1=synth_c1,
                d0=audio_d0,
                d1=audio_d1,
            )
            if len(candidate) <= max_chunk_chars and cand_synth <= max_synth_allowed:
                current = candidate
                work.pop(0)
            else:
                break
        chunks.append(current)

    return chunks


def chunk_text(text: str, max_chunk_chars: int = 250) -> List[str]:
    """Memotong teks yang telah dibersihkan menjadi daftar chunk kalimat.
    Menjaga angka desimal, angka ribuan, dan singkatan agar tidak terpotong sembarangan.
    """
    cleaned = clean_text_for_tts(text)
    return _split_sentences_from_cleaned(cleaned, max_chunk_chars=max_chunk_chars)


def prepare_tts_chunks_4c1(
    text: str,
    max_chunk_chars: int = 250,
    pronunciation_map: Optional[Dict[str, str]] = None,
    min_first_words: int = 6,
) -> List[str]:
    """Pipeline teks ke daftar chunk TTS versi Tahap 4C-1 (LAMA, untuk perbandingan benchmark)."""
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


def prepare_tts_chunks(
    text: str,
    max_chunk_chars: int = 250,
    pronunciation_map: Optional[Dict[str, str]] = None,
    min_first_words: int = 6,
    max_first_words: int = 14,
    synth_c0: float = DEFAULT_SYNTH_C0,
    synth_c1: float = DEFAULT_SYNTH_C1,
    audio_d0: float = DEFAULT_AUDIO_D0,
    audio_d1: float = DEFAULT_AUDIO_D1,
) -> List[str]:
    """Pipeline lengkap teks ke daftar chunk TTS bertahap (Tahap 4C-2 BARU):
    1. clean_text_for_tts(text)
    2. apply_pronunciation(cleaned, pronunciation_map)
    3. Pemotongan per kalimat (kalimat > 250 karakter dipotong di batas klausa)
    4. Chunker bertahap (ramp):
       - Chunk 1: kalimat pertama (dipotong di batas klausa <= 14 kata bila > 14 kata;
         digabung dengan kalimat berikutnya bila < 6 kata).
       - Chunk N+1: gabungkan kalimat berurutan selama
         waktu_sintesis_prediksi(chunk N+1) <= durasi_audio_prediksi(chunk N) - 0.3 detik,
         batas atas 250 karakter, minimal 1 kalimat.
    """
    cleaned = clean_text_for_tts(text)
    if not cleaned.strip():
        return []
    pronounced = apply_pronunciation(cleaned, pronunciation_map=pronunciation_map)
    sentences = _split_sentences_from_cleaned(pronounced, max_chunk_chars=max_chunk_chars)
    return merge_sentences_for_tts_ramp(
        sentences,
        max_chunk_chars=max_chunk_chars,
        min_first_words=min_first_words,
        max_first_words=max_first_words,
        synth_c0=synth_c0,
        synth_c1=synth_c1,
        audio_d0=audio_d0,
        audio_d1=audio_d1,
    )


DEFAULT_GAPLESS_START: bool = True
DEFAULT_MAX_START_DELAY_SEC: float = 6.0


def compute_gapless_start_delay(
    chunks: List[str],
    gapless_start: bool = DEFAULT_GAPLESS_START,
    max_start_delay: float = DEFAULT_MAX_START_DELAY_SEC,
    synth_c0: float = DEFAULT_SYNTH_C0,
    synth_c1: float = DEFAULT_SYNTH_C1,
    audio_d0: float = DEFAULT_AUDIO_D0,
    audio_d1: float = DEFAULT_AUDIO_D1,
) -> Dict[str, Any]:
    """Langkah 3 Tahap 4C-3: Menyimulasikan pipeline berurutan dengan konstanta Langkah 1 4C-2
    (c0, c1, d0, d1) sebelum memutar chunk 1:
    D = max(0, max_k( waktu_siap(chunk k+1) - (waktu_mulai_putar_chunk1_tanpa_tunda + jumlah durasi audio chunk 1..k) ))
    Menunda mulai putar chunk 1 selama min(D, tts_max_start_delay) bila gapless_start=True.
    """
    pred_durs = [predict_audio_duration(c, d0=audio_d0, d1=audio_d1) for c in (chunks or [])]
    pred_synths = [
        predict_synth_time(c, c0=synth_c0, c1=synth_c1, d0=audio_d0, d1=audio_d1)
        for c in (chunks or [])
    ]

    if not chunks or len(chunks) <= 1:
        return {
            "raw_delay_sec": 0.0,
            "applied_delay_sec": 0.0,
            "exceeded_cap": False,
            "residual_deficit_sec": 0.0,
            "pred_audio_durs": pred_durs,
            "pred_synth_times": pred_synths,
        }

    # Sintesis berjalan berurutan sejak t = 0:
    # waktu_siap(chunk m) = sum_{i=1..m} waktu_sintesis(chunk i)
    # waktu_mulai_putar_chunk1_tanpa_tunda = waktu_siap(chunk 1) = pred_synths[0]
    t_play_start_chunk1_no_delay = pred_synths[0]
    cum_synth = pred_synths[0]
    cum_play_dur = 0.0
    max_deficit = 0.0

    for k in range(len(chunks) - 1):
        cum_synth += pred_synths[k + 1]
        cum_play_dur += pred_durs[k]
        deficit_k = cum_synth - (t_play_start_chunk1_no_delay + cum_play_dur)
        if deficit_k > max_deficit:
            max_deficit = deficit_k

    raw_d = max(0.0, float(max_deficit))
    cap = max(0.0, float(max_start_delay))
    if not gapless_start:
        applied_d = 0.0
    else:
        applied_d = min(raw_d, cap)

    exceeded = raw_d > cap
    residual = max(0.0, raw_d - applied_d)
    return {
        "raw_delay_sec": raw_d,
        "applied_delay_sec": applied_d,
        "exceeded_cap": exceeded,
        "residual_deficit_sec": residual,
        "pred_audio_durs": pred_durs,
        "pred_synth_times": pred_synths,
    }


def _sub_with_offsets(
    pattern: re.Pattern,
    repl_fn: Callable[[re.Match], Tuple[str, List[int]]],
    text: str,
    offsets: List[int],
) -> Tuple[str, List[int]]:
    """Menerapkan substitusi regex sembari mempertahankan pemetaan indeks karakter ke teks asli."""
    out_chars: List[str] = []
    out_offsets: List[int] = []
    last_end = 0
    for m in pattern.finditer(text):
        s, e = m.start(), m.end()
        if s > last_end:
            out_chars.append(text[last_end:s])
            out_offsets.extend(offsets[last_end:e if e < last_end else s])
        rep_str, rep_offs = repl_fn(m)
        if rep_str:
            out_chars.append(rep_str)
            out_offsets.extend(rep_offs)
        last_end = e
    if last_end < len(text):
        out_chars.append(text[last_end:])
        out_offsets.extend(offsets[last_end:])
    return "".join(out_chars), out_offsets


def _clean_and_pronounce_with_offsets(
    raw_text: str,
    pronunciation_map: Optional[Dict[str, str]] = None,
) -> Tuple[str, List[int]]:
    """Menjalankan pipeline clean_text_for_tts + apply_pronunciation sembari melacak indeks karakter asli."""
    if not raw_text:
        return "", []

    text = raw_text
    offsets = list(range(len(raw_text)))

    # 1. Buang fenced code block ```...```
    text, offsets = _sub_with_offsets(
        re.compile(r"```[\s\S]*?```"),
        lambda m: ("", []),
        text,
        offsets,
    )

    # 2. Gantikan URL panjang dengan 'tautan' (+ tanda baca akhir)
    def _url_repl_off(m: re.Match) -> Tuple[str, List[int]]:
        url = m.group(0)
        s = m.start()
        base_off = offsets[s]
        trailing = ""
        trailing_offs: List[int] = []
        idx_end = m.end() - 1
        while url and url[-1] in ".,;:!?)]}>":
            trailing = url[-1] + trailing
            trailing_offs.insert(0, offsets[idx_end])
            url = url[:-1]
            idx_end -= 1
        rep = "tautan" + trailing
        rep_offs = ([base_off] * len("tautan")) + trailing_offs
        return rep, rep_offs

    text, offsets = _sub_with_offsets(
        re.compile(r"(?:https?://|www\.)[^\s]+"),
        _url_repl_off,
        text,
        offsets,
    )

    # 3. Buang backtick inline code `...`
    def _group1_off(m: re.Match) -> Tuple[str, List[int]]:
        g1 = m.group(1)
        s1, e1 = m.start(1), m.end(1)
        return g1, offsets[s1:e1]

    text, offsets = _sub_with_offsets(
        re.compile(r"`([^`]+)`"),
        _group1_off,
        text,
        offsets,
    )

    # 3b. Ganti hash hex 7-40 karakter dengan 'kode hash'
    def _hash_off(m: re.Match) -> Tuple[str, List[int]]:
        base_off = offsets[m.start()]
        rep = "kode hash"
        return rep, [base_off] * len(rep)

    text, offsets = _sub_with_offsets(HEX_HASH_REGEX, _hash_off, text, offsets)

    # 4. Buang formatting bold / italic
    text, offsets = _sub_with_offsets(
        re.compile(r"\*\*(?!\s)([^*]+?)(?<!\s)\*\*"),
        _group1_off,
        text,
        offsets,
    )
    text, offsets = _sub_with_offsets(
        re.compile(r"(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])"),
        _group1_off,
        text,
        offsets,
    )
    text, offsets = _sub_with_offsets(
        re.compile(r"(?<![\w_])_(?![\s_])([^_\n]+?)(?<![\s_])_(?![\w_])"),
        _group1_off,
        text,
        offsets,
    )

    # 5. Bersihkan bullet points dan nomor list di awal baris
    line_Texts: List[str] = []
    line_Offsets: List[List[int]] = []
    pos = 0
    for raw_line in text.splitlines(keepends=True):
        line_len = len(raw_line)
        l_str = raw_line.rstrip("\r\n")
        l_off = offsets[pos : pos + len(l_str)]
        pos += line_len

        m_bul = re.match(r"^\s*[-*+]\s+", l_str)
        if m_bul:
            cut = m_bul.end()
            l_str = l_str[cut:]
            l_off = l_off[cut:]
        m_num = re.match(r"^\s*\d+\.\s+", l_str)
        if m_num:
            cut = m_num.end()
            l_str = l_str[cut:]
            l_off = l_off[cut:]

        # strip whitespace kiri & kanan
        l_strip_l = len(l_str) - len(l_str.lstrip())
        l_str = l_str.lstrip()
        l_off = l_off[l_strip_l:]
        l_strip_r = len(l_str.rstrip())
        l_str = l_str[:l_strip_r]
        l_off = l_off[:l_strip_r]

        if l_str:
            line_Texts.append(l_str)
            line_Offsets.append(l_off)

    if not line_Texts:
        return "", []

    joined_chars: List[str] = []
    joined_offs: List[int] = []
    for idx_l, (lt, lo) in enumerate(zip(line_Texts, line_Offsets)):
        if idx_l > 0:
            joined_chars.append("\n")
            joined_offs.append(lo[0])
        joined_chars.append(lt)
        joined_offs.extend(lo)

    text = "".join(joined_chars)
    offsets = joined_offs

    # 6. Terapkan kamus pelafalan
    pmap = DEFAULT_PRONUNCIATION if pronunciation_map is None else pronunciation_map
    if pmap:
        for src, dst in pmap.items():
            if not src:
                continue
            pat = re.compile(rf"(?<![/\\])\b{re.escape(src)}\b(?![/\\])")

            def _pron_off(m: re.Match, d: str = dst) -> Tuple[str, List[int]]:
                base_off = offsets[m.start()]
                return d, [base_off] * len(d)

            text, offsets = _sub_with_offsets(pat, _pron_off, text, offsets)

    return text, offsets


def align_raw_segments_to_chunks(
    raw_text: str,
    chunks: List[str],
    pronunciation_map: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Memetakan setiap chunk TTS ke segmen teks aslinya (raw_text) sedemikian sehingga:
    1. "".join(raw_segments) == raw_text persis (byte per byte, M1).
    2. len(raw_segments) == len(chunks) (bila chunks tidak kosong).
    3. Segmen tidak terbaca (blok kode ```...```, simbol/baris kosong, URL, dll.)
       digabungkan bersama segmen terbaca di depannya agar tidak menahan layar.
    """
    if not raw_text:
        return ["" for _ in chunks] if chunks else []
    if not chunks:
        return [raw_text]
    if len(chunks) == 1:
        return [raw_text]

    pronounced, clean_to_raw = _clean_and_pronounce_with_offsets(
        raw_text,
        pronunciation_map=pronunciation_map,
    )
    if not pronounced or not clean_to_raw:
        # Fallback partisi aman bila teks bersih kosong
        return [raw_text] + ["" for _ in range(len(chunks) - 1)]

    boundaries: List[int] = [0]
    cursor = 0

    for c_idx, chunk in enumerate(chunks):
        tokens = chunk.split()
        first_pron_pos: Optional[int] = None
        for t_idx, tok in enumerate(tokens):
            found = pronounced.find(tok, cursor)
            if found == -1:
                # Coba cari tanpa sensitivitas spasi bila ada perbedaan kecil
                found = cursor if cursor < len(pronounced) else len(pronounced) - 1
            if t_idx == 0:
                first_pron_pos = found
            cursor = min(len(pronounced), found + len(tok))

        if c_idx > 0:
            if first_pron_pos is not None and 0 <= first_pron_pos < len(clean_to_raw):
                raw_pos = clean_to_raw[first_pron_pos]
            else:
                raw_pos = boundaries[-1]

            # Mundurkan raw_pos ke awal marker list / tag pembuka markdown pada baris yang sama
            line_start = raw_text.rfind("\n", 0, raw_pos)
            line_start = 0 if line_start == -1 else line_start + 1
            prefix_on_line = raw_text[line_start:raw_pos]
            if prefix_on_line and re.match(r"^\s*(?:[-*+]|\d+\.)?\s*[*_`]*$", prefix_on_line):
                raw_pos = line_start
            else:
                # Mundurkan bila tepat di depan kata ada tag pembuka inline (** / * / _ / `)
                while raw_pos > boundaries[-1] and raw_text[raw_pos - 1] in "*_`":
                    raw_pos -= 1

            raw_pos = max(boundaries[-1], min(len(raw_text), raw_pos))
            boundaries.append(raw_pos)

    boundaries.append(len(raw_text))
    segments = [raw_text[boundaries[i] : boundaries[i + 1]] for i in range(len(chunks))]
    return segments


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
    """Pengelola antrean TTS dengan pipelining sintesis & pemutaran, mulai tertunda tanpa jeda,
    serta pembatalan instan.
    """

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

        # State mulai tertunda (Langkah 3 Tahap 4C-3) & streaming sinkron (Langkah 4)
        self._utterance_start_delays: Dict[int, float] = {}
        self._utterance_plan_events: Dict[int, threading.Event] = {}
        self._cancel_callbacks: List[Callable[[], None]] = []
        self.last_computed_delay_info: Dict[str, Any] = {}
        self.last_applied_start_delay_sec: float = 0.0

        # Hook opsional untuk telemetri benchmark & sinkronisasi layar
        self.on_chunk_synthesized: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_before_play: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_after_play: Optional[Callable[[Dict[str, Any], bool], None]] = None

    def register_cancel_callback(self, cb: Callable[[], None]) -> None:
        with self._lock:
            if cb not in self._cancel_callbacks:
                self._cancel_callbacks.append(cb)

    def unregister_cancel_callback(self, cb: Callable[[], None]) -> None:
        with self._lock:
            if cb in self._cancel_callbacks:
                self._cancel_callbacks.remove(cb)

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
                            if "ready_at" not in synth_item:
                                synth_item["ready_at"] = time.perf_counter()
                            synth_item["chunk_idx"] = chunk_idx
                            synth_item["speak_called_at"] = self.last_speak_called_at
                            with self._lock:
                                cancelled_now = self.cancel_event.is_set() or epoch != self._epoch
                                cb_synth = self.on_chunk_synthesized
                            if cancelled_now:
                                if hasattr(engine, "discard_synthesized"):
                                    engine.discard_synthesized(synth_item, count_as_cancelled=True)
                            else:
                                if cb_synth is not None:
                                    try:
                                        cb_synth(synth_item)
                                    except Exception:
                                        pass
                                self.play_queue.put((epoch, chunk_idx, synth_item, engine))
                    finally:
                        with self._lock:
                            self._in_flight_synth = max(0, self._in_flight_synth - 1)
                            if self._in_flight_synth == 0:
                                self.current_engine = None
                        self.queue.task_done()
                else:
                    if not self._cpu_affinity_applied:
                        self._cpu_affinity_applied = apply_cpu_affinity(2)
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
                    plan_evt = self._utterance_plan_events.get(epoch)

                try:
                    # Untuk chunk 1 (chunk_idx == 0), pastikan rencana & D sudah siap
                    # (maksimum 3.0 detik setelah chunk 1 siap pada mode streaming)
                    if chunk_idx == 0 and plan_evt is not None and not plan_evt.is_set():
                        ready_t0 = float(synth_item.get("ready_at", time.perf_counter()))
                        deadline_plan = ready_t0 + 3.0
                        while not plan_evt.is_set():
                            if self.cancel_event.is_set() or epoch != self._epoch:
                                break
                            rem_plan = deadline_plan - time.perf_counter()
                            if rem_plan <= 0:
                                break
                            plan_evt.wait(timeout=min(0.01, rem_plan))

                    # Langkah 3 Tahap 4C-3: Tunda mulai putar chunk 1 selama min(D, tts_max_start_delay)
                    if chunk_idx == 0 and not self.cancel_event.is_set() and epoch == self._epoch:
                        with self._lock:
                            start_delay = float(self._utterance_start_delays.get(epoch, 0.0))
                        if start_delay > 0.0:
                            ready_t0 = float(synth_item.get("ready_at", time.perf_counter()))
                            target_play_t = ready_t0 + start_delay
                            while True:
                                if self.cancel_event.is_set() or epoch != self._epoch:
                                    break
                                rem_delay = target_play_t - time.perf_counter()
                                if rem_delay <= 0:
                                    break
                                self.cancel_event.wait(timeout=min(0.02, rem_delay))

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

    def begin_streaming_utterance(
        self,
        engine: BaseTTSEngine,
        plan_ready_event: Optional[threading.Event] = None,
    ) -> int:
        """Memulai epoch ucapan baru untuk mode streaming sinkron (Langkah 4)."""
        if hasattr(engine, "reset_for_new_utterance"):
            engine.reset_for_new_utterance()
        with self._lock:
            self._epoch += 1
            utterance_epoch = self._epoch
            self.last_speak_called_at = time.perf_counter()
            self._utterance_start_delays[utterance_epoch] = 0.0
            if plan_ready_event is not None:
                self._utterance_plan_events[utterance_epoch] = plan_ready_event
        self.cancel_event.clear()
        self._ensure_worker()
        return utterance_epoch

    def set_utterance_start_delay(self, epoch: int, delay_sec: float) -> None:
        with self._lock:
            self._utterance_start_delays[epoch] = max(0.0, float(delay_sec))
            self.last_applied_start_delay_sec = max(0.0, float(delay_sec))

    def enqueue_streaming_chunk(
        self,
        epoch: int,
        chunk_idx: int,
        text_chunk: str,
        voice: Optional[str],
        engine: BaseTTSEngine,
    ) -> None:
        with self._lock:
            if self.cancel_event.is_set() or epoch != self._epoch:
                return
        self.queue.put((epoch, chunk_idx, text_chunk, voice, engine))

    def speak(
        self,
        text: str,
        voice: Optional[str] = None,
        engine: Optional[BaseTTSEngine] = None,
        max_chunk_chars: int = 250,
        pronunciation_map: Optional[Dict[str, str]] = None,
        chunker_fn: Optional[Callable[..., List[str]]] = None,
        gapless_start: Optional[bool] = None,
        max_start_delay: Optional[float] = None,
    ) -> None:
        from enki.config import AppConfig

        cfg = AppConfig()
        if engine is None:
            engine, resolved_voice, cfg_pron = self._resolve_default_engine_and_voice(voice=voice)
            if pronunciation_map is None:
                pronunciation_map = cfg_pron
        else:
            resolved_voice = voice

        active_chunker = chunker_fn if chunker_fn is not None else prepare_tts_chunks
        chunks = active_chunker(
            text,
            max_chunk_chars=max_chunk_chars,
            pronunciation_map=pronunciation_map,
        )
        if not chunks:
            return

        if hasattr(engine, "reset_for_new_utterance"):
            engine.reset_for_new_utterance()

        resolved_gapless = cfg.tts_gapless_start if gapless_start is None else bool(gapless_start)
        resolved_max_delay = cfg.tts_max_start_delay if max_start_delay is None else float(max_start_delay)

        is_pipelined = hasattr(engine, "synthesize_chunk") and hasattr(engine, "play_synthesized")
        if is_pipelined and active_chunker is prepare_tts_chunks:
            delay_info = compute_gapless_start_delay(
                chunks,
                gapless_start=resolved_gapless,
                max_start_delay=resolved_max_delay,
                synth_c0=cfg.tts_synth_c0,
                synth_c1=cfg.tts_synth_c1,
                audio_d0=cfg.tts_audio_d0,
                audio_d1=cfg.tts_audio_d1,
            )
            applied_delay = delay_info["applied_delay_sec"]
        else:
            delay_info = compute_gapless_start_delay(
                chunks,
                gapless_start=False,
                max_start_delay=resolved_max_delay,
            )
            applied_delay = 0.0

        with self._lock:
            self._epoch += 1
            utterance_epoch = self._epoch
            self.last_speak_called_at = time.perf_counter()
            self.last_computed_delay_info = delay_info
            self.last_applied_start_delay_sec = applied_delay
            self._utterance_start_delays[utterance_epoch] = applied_delay

        self.cancel_event.clear()
        self._ensure_worker()

        for idx, chunk in enumerate(chunks):
            self.queue.put((utterance_epoch, idx, chunk, resolved_voice, engine))

    def cancel(self) -> None:
        """Mengosongkan antrean sintesis & pemutaran seketika, menghentikan pemutaran aktif,
        dan langsung mem-flush sisa teks sinkron yang belum tampil.
        """
        self.cancel_event.set()
        with self._lock:
            self._epoch += 1
            eng_synth = self.current_engine
            eng_play = self._current_play_engine
            eng_def = self._default_engine
            plan_evts = list(self._utterance_plan_events.values())
            cancel_cbs = list(self._cancel_callbacks)

        for pe in plan_evts:
            try:
                pe.set()
            except Exception:
                pass

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

        # Hentikan engine yang sedang aktif seketika
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

        # Panggil callback flush teks sinkron seketika (<= 200 ms untuk M4)
        for cb in cancel_cbs:
            try:
                cb()
            except Exception:
                pass

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


def _try_extract_early_first_chunk(
    raw_so_far: str,
    max_chunk_chars: int = 250,
    pronunciation_map: Optional[Dict[str, str]] = None,
    min_first_words: int = 6,
    max_first_words: int = 14,
) -> Optional[str]:
    """Mengecek apakah kalimat pertama dalam stream token sudah lengkap dan siap disintesis sebagai Chunk 1."""
    if not raw_so_far or raw_so_far.count("```") % 2 != 0:
        return None
    cleaned = clean_text_for_tts(raw_so_far)
    if not cleaned.strip():
        return None
    pronounced = apply_pronunciation(cleaned, pronunciation_map=pronunciation_map)
    sents = _split_sentences_from_cleaned(pronounced, max_chunk_chars=max_chunk_chars)
    if len(sents) < 2:
        return None

    s0 = sents[0].strip()
    s0_words = s0.split()
    if len(s0_words) >= min_first_words:
        ramp = merge_sentences_for_tts_ramp(
            sents[:2],
            max_chunk_chars=max_chunk_chars,
            min_first_words=min_first_words,
            max_first_words=max_first_words,
        )
        return ramp[0] if ramp else None

    # Kalimat pertama < 6 kata: hanya pasti tidak bergabung jika gabungan dengan kalimat ke-2 > max_chunk_chars,
    # atau pasti bergabung/final jika kalimat ke-2 juga sudah lengkap (len(sents) >= 3)
    if len(sents) >= 3:
        ramp = merge_sentences_for_tts_ramp(
            sents[:2],
            max_chunk_chars=max_chunk_chars,
            min_first_words=min_first_words,
            max_first_words=max_first_words,
        )
        return ramp[0] if ramp else None

    if len(f"{s0} {sents[1].strip()}".strip()) > max_chunk_chars:
        return s0

    return None


def _build_chunks_anchored_on_first(
    full_raw_text: str,
    anchored_chunk1: Optional[str],
    max_chunk_chars: int = 250,
    pronunciation_map: Optional[Dict[str, str]] = None,
    synth_c0: float = DEFAULT_SYNTH_C0,
    synth_c1: float = DEFAULT_SYNTH_C1,
    audio_d0: float = DEFAULT_AUDIO_D0,
    audio_d1: float = DEFAULT_AUDIO_D1,
) -> List[str]:
    """Menghasilkan daftar chunk bertahap 4C-2 dari teks, dengan menjaga chunk 1 tetap konsisten
    bila chunk 1 sudah mulai disintesis lebih dulu saat kalimat pertama selesai.
    """
    std_chunks = prepare_tts_chunks(
        full_raw_text,
        max_chunk_chars=max_chunk_chars,
        pronunciation_map=pronunciation_map,
        synth_c0=synth_c0,
        synth_c1=synth_c1,
        audio_d0=audio_d0,
        audio_d1=audio_d1,
    )
    if not anchored_chunk1 or not std_chunks:
        return std_chunks
    if std_chunks[0] == anchored_chunk1:
        return std_chunks

    cleaned = clean_text_for_tts(full_raw_text)
    pronounced = apply_pronunciation(cleaned, pronunciation_map=pronunciation_map)
    pos = pronounced.find(anchored_chunk1)
    if pos == -1:
        return std_chunks
    rem_pronounced = pronounced[pos + len(anchored_chunk1) :].strip()
    if not rem_pronounced:
        return [anchored_chunk1]

    rem_sents = _split_sentences_from_cleaned(rem_pronounced, max_chunk_chars=max_chunk_chars)
    chunks = [anchored_chunk1]
    work = [s.strip() for s in rem_sents if s and s.strip()]
    while work:
        prev_chunk = chunks[-1]
        prev_audio_dur = predict_audio_duration(prev_chunk, d0=audio_d0, d1=audio_d1)
        max_synth_allowed = prev_audio_dur - 0.3
        current = work.pop(0)
        while work:
            candidate = f"{current} {work[0]}".strip()
            cand_synth = predict_synth_time(
                candidate,
                c0=synth_c0,
                c1=synth_c1,
                d0=audio_d0,
                d1=audio_d1,
            )
            if len(candidate) <= max_chunk_chars and cand_synth <= max_synth_allowed:
                current = candidate
                work.pop(0)
            else:
                break
        chunks.append(current)
    return chunks


def run_synced_speech_stream(
    token_stream: Any,
    out_stream: Any = None,
    is_muted: bool = False,
    tts_engine_name: str = "supertonic",
    voice: Optional[str] = None,
    pronunciation_map: Optional[Dict[str, str]] = None,
    gapless_start: Optional[bool] = None,
    max_start_delay: Optional[float] = None,
    plan_wait_after_chunk1_sec: float = 3.0,
    max_chunk_chars: int = 250,
    mgr: Optional[TTSManager] = None,
    engine: Optional[BaseTTSEngine] = None,
    status_text: str = "[menyiapkan suara...]",
    interrupt_poll_fn: Optional[Callable[[], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Langkah 4 Tahap 4C-3: Menjalankan tampilan teks sinkron dengan suara.
    - Aktif HANYA jika not is_muted DAN engine aktif adalah Supertonic (bukan fallback SAPI5).
    - Jika mute, engine = SAPI5, fallback ke SAPI5, atau terjadi error TTS: jalur streaming
      lama dijalankan tanpa perubahan (M3).
    - Saat aktif:
      * Menampilkan baris status sementara `status_text` yang dihapus begitu teks pertama tampil.
      * Mengumpulkan token LLM; saat kalimat pertama lengkap, langsung memulai sintesis chunk 1.
      * Merencanakan chunk berikutnya dan menghitung D setelah stream LLM selesai atau
        maksimum `plan_wait_after_chunk1_sec` (3.0 detik) setelah chunk 1 siap, mana yang lebih dulu.
      * Mencetak setiap segmen teks asli tepat saat `on_before_play` chunk yang bersesuaian.
      * Saat interupsi (`cancel()`, `/mute`, atau input baru), suara berhenti dan seluruh sisa
        teks yang belum tampil langsung dicetak utuh (<= 200 ms, M4) sehingga gabungan teks
        yang tampil == balasan asli persis byte per byte (M1).
    """
    import sys as _sys

    if out_stream is None:
        out_stream = _sys.stdout

    from enki.config import AppConfig

    cfg = AppConfig()
    resolved_gapless = cfg.tts_gapless_start if gapless_start is None else bool(gapless_start)
    resolved_max_delay = cfg.tts_max_start_delay if max_start_delay is None else float(max_start_delay)
    resolved_pron = cfg.tts_pronunciation if pronunciation_map is None else pronunciation_map

    def _unpack_token(item: Any) -> Tuple[str, Optional[Dict[str, Any]]]:
        if isinstance(item, tuple) and len(item) == 2:
            return (str(item[0] or ""), item[1])
        return (str(item or ""), None)

    # 1. Cek kondisi jalur lama (M3: mute atau engine bukan supertonic)
    if is_muted or tts_engine_name.lower().strip() != "supertonic":
        content_chunks: List[str] = []
        final_metrics: Dict[str, Any] = {}
        first_token_at = 0.0
        first_text_at = 0.0
        for raw_item in token_stream:
            tok, met = _unpack_token(raw_item)
            if tok:
                if first_token_at == 0.0:
                    first_token_at = time.perf_counter()
                out_stream.write(tok)
                out_stream.flush()
                if first_text_at == 0.0:
                    first_text_at = time.perf_counter()
                content_chunks.append(tok)
            if met:
                final_metrics = met
        full_text = "".join(content_chunks)
        if not is_muted and full_text.strip():
            try:
                active_mgr = mgr if mgr is not None else get_tts_manager(create=True)
                active_mgr.speak(
                    text=full_text,
                    voice=voice or cfg.tts_voice,
                    engine=engine,
                    max_chunk_chars=max_chunk_chars,
                    pronunciation_map=resolved_pron,
                    gapless_start=False,
                )
            except Exception as e:
                out_stream.write(f"\n[Peringatan TTS] Gagal memutar audio: {e}\n")
                out_stream.flush()
        return {
            "mode": "legacy_mute" if is_muted else "legacy_sapi5",
            "full_text": full_text,
            "printed_segments": list(content_chunks),
            "metrics": final_metrics,
            "first_token_at": first_token_at,
            "first_text_at": first_text_at,
            "delay_info": {"raw_delay_sec": 0.0, "applied_delay_sec": 0.0},
            "sync_deltas_ms": [],
            "gaps_ms": [],
            "interrupted": False,
            "interrupt_reason": None,
            "interrupt_flush_ms": 0.0,
        }

    # 2. Resolusi engine Supertonic & cek fallback / error inisialisasi (M3)
    active_mgr = mgr if mgr is not None else get_tts_manager(create=True)
    try:
        if engine is None:
            active_engine, resolved_voice, _ = active_mgr._resolve_default_engine_and_voice(voice=voice)
        else:
            active_engine = engine
            resolved_voice = voice or cfg.tts_voice_supertonic

        is_pipelined = hasattr(active_engine, "synthesize_chunk") and hasattr(active_engine, "play_synthesized")
        loaded_ok = True
        if hasattr(active_engine, "_ensure_loaded"):
            loaded_ok = bool(active_engine._ensure_loaded())
        if not loaded_ok or getattr(active_engine, "is_fallback", False) or not is_pipelined:
            # Jalur lama saat fallback SAPI5 (M3)
            content_chunks = []
            final_metrics = {}
            first_token_at = 0.0
            first_text_at = 0.0
            for raw_item in token_stream:
                tok, met = _unpack_token(raw_item)
                if tok:
                    if first_token_at == 0.0:
                        first_token_at = time.perf_counter()
                    out_stream.write(tok)
                    out_stream.flush()
                    if first_text_at == 0.0:
                        first_text_at = time.perf_counter()
                    content_chunks.append(tok)
                if met:
                    final_metrics = met
            full_text = "".join(content_chunks)
            if full_text.strip():
                try:
                    active_mgr.speak(
                        text=full_text,
                        voice=resolved_voice,
                        engine=active_engine,
                        max_chunk_chars=max_chunk_chars,
                        pronunciation_map=resolved_pron,
                        gapless_start=False,
                    )
                except Exception as e:
                    out_stream.write(f"\n[Peringatan TTS] Gagal memutar audio: {e}\n")
                    out_stream.flush()
            return {
                "mode": "legacy_fallback_sapi5",
                "full_text": full_text,
                "printed_segments": list(content_chunks),
                "metrics": final_metrics,
                "first_token_at": first_token_at,
                "first_text_at": first_text_at,
                "delay_info": {"raw_delay_sec": 0.0, "applied_delay_sec": 0.0},
                "sync_deltas_ms": [],
                "gaps_ms": [],
                "interrupted": False,
                "interrupt_reason": None,
                "interrupt_flush_ms": 0.0,
            }
    except Exception as init_err:
        # Error TTS -> jalur streaming lama utuh (M3)
        content_chunks = []
        final_metrics = {}
        for raw_item in token_stream:
            tok, met = _unpack_token(raw_item)
            if tok:
                out_stream.write(tok)
                out_stream.flush()
                content_chunks.append(tok)
            if met:
                final_metrics = met
        out_stream.write(f"\n[Peringatan TTS] Gagal memutar audio: {init_err}\n")
        out_stream.flush()
        return {
            "mode": "legacy_tts_error",
            "full_text": "".join(content_chunks),
            "printed_segments": list(content_chunks),
            "metrics": final_metrics,
            "first_token_at": 0.0,
            "first_text_at": 0.0,
            "delay_info": {"raw_delay_sec": 0.0, "applied_delay_sec": 0.0},
            "sync_deltas_ms": [],
            "gaps_ms": [],
            "interrupted": False,
            "interrupt_reason": None,
            "interrupt_flush_ms": 0.0,
        }

    # 3. Mode Tampilan Teks Sinkron Supertonic (Langkah 4)
    seg_lock = threading.Lock()
    status_shown = False
    if status_text:
        out_stream.write(status_text)
        out_stream.flush()
        status_shown = True

    def _clear_status_locked():
        nonlocal status_shown
        if status_shown:
            blank = "\r" + (" " * len(status_text)) + "\r"
            out_stream.write(blank)
            out_stream.flush()
            status_shown = False

    raw_tokens: List[str] = []
    final_metrics: Dict[str, Any] = {}
    raw_segments: List[str] = []
    planned_chunks: List[str] = []
    printed_segments: List[str] = []
    printed_seg_count: int = 0
    printed_char_offset: int = 0

    first_token_at: float = 0.0
    first_text_at: float = 0.0
    chunk1_ready_at: float = 0.0
    chunk1_ready_evt = threading.Event()
    plan_ready_evt = threading.Event()

    before_play_times: Dict[int, float] = {}
    after_play_times: Dict[int, float] = {}
    segment_printed_times: Dict[int, float] = {}
    sync_deltas_ms: List[float] = []

    interrupted = False
    interrupt_reason: Optional[str] = None
    interrupt_triggered_at: float = 0.0
    interrupt_flushed_at: float = 0.0
    tts_error_fallback = False

    prev_on_synth = active_mgr.on_chunk_synthesized
    prev_on_before = active_mgr.on_before_play
    prev_on_after = active_mgr.on_after_play

    utterance_epoch = active_mgr.begin_streaming_utterance(
        engine=active_engine,
        plan_ready_event=plan_ready_evt,
    )

    def _flush_remaining_locked(full_raw_now: str):
        nonlocal printed_char_offset, printed_seg_count, first_text_at, interrupt_flushed_at
        _clear_status_locked()
        if printed_char_offset < len(full_raw_now):
            rem_text = full_raw_now[printed_char_offset:]
            out_stream.write(rem_text)
            out_stream.flush()
            now_t = time.perf_counter()
            if first_text_at == 0.0:
                first_text_at = now_t
            printed_segments.append(rem_text)
            printed_char_offset = len(full_raw_now)
            printed_seg_count = len(raw_segments)
            interrupt_flushed_at = now_t
        elif interrupt_flushed_at == 0.0:
            interrupt_flushed_at = time.perf_counter()

    def _on_cancel_flush():
        nonlocal interrupted, interrupt_triggered_at
        with seg_lock:
            if interrupt_triggered_at == 0.0:
                interrupt_triggered_at = time.perf_counter()
            interrupted = True
            current_full = "".join(raw_tokens)
            _flush_remaining_locked(current_full)

    def _on_chunk_synth(item: Dict[str, Any]):
        nonlocal chunk1_ready_at, tts_error_fallback
        idx = int(item.get("chunk_idx", 0))
        if item.get("mode") == "fallback_sapi5":
            with seg_lock:
                tts_error_fallback = True
                _flush_remaining_locked("".join(raw_tokens))
            plan_ready_evt.set()
            return
        if idx == 0 and chunk1_ready_at == 0.0:
            chunk1_ready_at = float(item.get("ready_at", time.perf_counter()))
            chunk1_ready_evt.set()
        if prev_on_synth is not None:
            try:
                prev_on_synth(item)
            except Exception:
                pass

    def _on_before_play(item: Dict[str, Any]):
        nonlocal printed_seg_count, printed_char_offset, first_text_at
        idx = int(item.get("chunk_idx", 0))
        t_before = time.perf_counter()
        before_play_times[idx] = t_before
        with seg_lock:
            if not interrupted and not tts_error_fallback:
                _clear_status_locked()
                while printed_seg_count <= idx and printed_seg_count < len(raw_segments):
                    seg = raw_segments[printed_seg_count]
                    out_stream.write(seg)
                    out_stream.flush()
                    t_printed = time.perf_counter()
                    if first_text_at == 0.0 and seg:
                        first_text_at = t_printed
                    if printed_seg_count == idx:
                        segment_printed_times[idx] = t_printed
                        sync_deltas_ms.append((t_printed - t_before) * 1000.0)
                    printed_segments.append(seg)
                    printed_char_offset += len(seg)
                    printed_seg_count += 1
        if prev_on_before is not None:
            try:
                prev_on_before(item)
            except Exception:
                pass

    def _on_after_play(item: Dict[str, Any], stopped_early: bool):
        idx = int(item.get("chunk_idx", 0))
        after_play_times[idx] = time.perf_counter()
        if prev_on_after is not None:
            try:
                prev_on_after(item, stopped_early)
            except Exception:
                pass

    active_mgr.on_chunk_synthesized = _on_chunk_synth
    active_mgr.on_before_play = _on_before_play
    active_mgr.on_after_play = _on_after_play
    active_mgr.register_cancel_callback(_on_cancel_flush)

    chunk1_launched = False
    launched_chunk1_text: Optional[str] = None
    plan_made = False
    planned_raw_len = 0
    enqueued_chunk_count = 0
    delay_info: Dict[str, Any] = {
        "raw_delay_sec": 0.0,
        "applied_delay_sec": 0.0,
        "exceeded_cap": False,
        "residual_deficit_sec": 0.0,
    }

    def _make_plan_from_available(avail_raw: str):
        nonlocal plan_made, planned_raw_len, planned_chunks, raw_segments, delay_info
        nonlocal chunk1_launched, launched_chunk1_text, enqueued_chunk_count
        chunks_now = _build_chunks_anchored_on_first(
            avail_raw,
            anchored_chunk1=launched_chunk1_text if chunk1_launched else None,
            max_chunk_chars=max_chunk_chars,
            pronunciation_map=resolved_pron,
            synth_c0=cfg.tts_synth_c0,
            synth_c1=cfg.tts_synth_c1,
            audio_d0=cfg.tts_audio_d0,
            audio_d1=cfg.tts_audio_d1,
        )
        if not chunks_now:
            with seg_lock:
                _flush_remaining_locked(avail_raw)
            plan_made = True
            planned_raw_len = len(avail_raw)
            plan_ready_evt.set()
            return

        segs_now = align_raw_segments_to_chunks(
            avail_raw,
            chunks_now,
            pronunciation_map=resolved_pron,
        )
        d_info = compute_gapless_start_delay(
            chunks_now,
            gapless_start=resolved_gapless,
            max_start_delay=resolved_max_delay,
            synth_c0=cfg.tts_synth_c0,
            synth_c1=cfg.tts_synth_c1,
            audio_d0=cfg.tts_audio_d0,
            audio_d1=cfg.tts_audio_d1,
        )
        with seg_lock:
            planned_chunks = list(chunks_now)
            raw_segments = list(segs_now)
            delay_info = d_info
            planned_raw_len = len(avail_raw)
            plan_made = True

        active_mgr.last_computed_delay_info = d_info
        active_mgr.set_utterance_start_delay(utterance_epoch, d_info["applied_delay_sec"])

        if not chunk1_launched:
            chunk1_launched = True
            launched_chunk1_text = chunks_now[0]
            active_mgr.enqueue_streaming_chunk(
                utterance_epoch, 0, chunks_now[0], resolved_voice, active_engine
            )
            enqueued_chunk_count = 1

        for c_idx in range(enqueued_chunk_count, len(chunks_now)):
            active_mgr.enqueue_streaming_chunk(
                utterance_epoch, c_idx, chunks_now[c_idx], resolved_voice, active_engine
            )
        enqueued_chunk_count = len(chunks_now)
        plan_ready_evt.set()

    try:
        for raw_item in token_stream:
            if interrupt_poll_fn is not None and not interrupted:
                intr = interrupt_poll_fn()
                if intr is not None:
                    interrupt_reason = intr
                    interrupt_triggered_at = time.perf_counter()
                    active_mgr.cancel()

            tok, met = _unpack_token(raw_item)
            if tok:
                if first_token_at == 0.0:
                    first_token_at = time.perf_counter()
                with seg_lock:
                    raw_tokens.append(tok)
                    if interrupted or tts_error_fallback or active_mgr.cancel_event.is_set():
                        # Jika sudah diinterupsi saat stream masih berjalan, langsung cetak token baru
                        _clear_status_locked()
                        out_stream.write(tok)
                        out_stream.flush()
                        printed_segments.append(tok)
                        printed_char_offset += len(tok)
            if met:
                final_metrics = met

            if interrupted or tts_error_fallback or active_mgr.cancel_event.is_set():
                continue

            current_raw = "".join(raw_tokens)

            # Cek apakah kalimat pertama sudah lengkap untuk langsung mulai sintesis chunk 1
            if not chunk1_launched:
                early_c1 = _try_extract_early_first_chunk(
                    current_raw,
                    max_chunk_chars=max_chunk_chars,
                    pronunciation_map=resolved_pron,
                )
                if early_c1:
                    chunk1_launched = True
                    launched_chunk1_text = early_c1
                    enqueued_chunk_count = 1
                    active_mgr.enqueue_streaming_chunk(
                        utterance_epoch, 0, early_c1, resolved_voice, active_engine
                    )

            # Cek batas 3.0 detik setelah chunk 1 siap bila stream LLM masih berjalan
            if (
                chunk1_launched
                and not plan_made
                and chunk1_ready_evt.is_set()
                and (time.perf_counter() - chunk1_ready_at) >= plan_wait_after_chunk1_sec
            ):
                # Gunakan teks sampai batas kalimat lengkap terakhir
                last_sent_match = list(re.finditer(r"[.!?](?:\s+|$)|\n+", current_raw))
                cut_idx = last_sent_match[-1].end() if last_sent_match else len(current_raw)
                _make_plan_from_available(current_raw[:cut_idx])

        full_raw_text = "".join(raw_tokens)

        if not interrupted and not tts_error_fallback and not active_mgr.cancel_event.is_set():
            if not plan_made:
                _make_plan_from_available(full_raw_text)
            elif len(full_raw_text) > planned_raw_len:
                # Stream selesai setelah batas 3.0 detik: tambahkan sisa teks dengan aturan ramp
                rem_raw = full_raw_text[planned_raw_len:]
                rem_cleaned = clean_text_for_tts(rem_raw)
                rem_pron = apply_pronunciation(rem_cleaned, pronunciation_map=resolved_pron)
                rem_sents = _split_sentences_from_cleaned(rem_pron, max_chunk_chars=max_chunk_chars)
                if rem_sents:
                    prev_last = planned_chunks[-1] if planned_chunks else ""
                    combined_sents = ([prev_last] if prev_last else []) + rem_sents
                    ramped = merge_sentences_for_tts_ramp(
                        combined_sents,
                        max_chunk_chars=max_chunk_chars,
                        min_first_words=1,
                        max_first_words=999,
                        synth_c0=cfg.tts_synth_c0,
                        synth_c1=cfg.tts_synth_c1,
                        audio_d0=cfg.tts_audio_d0,
                        audio_d1=cfg.tts_audio_d1,
                    )
                    new_tail_chunks = ramped[1:] if prev_last else ramped
                    if new_tail_chunks:
                        tail_segs = align_raw_segments_to_chunks(
                            rem_raw,
                            new_tail_chunks,
                            pronunciation_map=resolved_pron,
                        )
                        with seg_lock:
                            start_idx = len(planned_chunks)
                            planned_chunks.extend(new_tail_chunks)
                            raw_segments.extend(tail_segs)
                        for offset_i, tc in enumerate(new_tail_chunks):
                            active_mgr.enqueue_streaming_chunk(
                                utterance_epoch,
                                start_idx + offset_i,
                                tc,
                                resolved_voice,
                                active_engine,
                            )
                    else:
                        with seg_lock:
                            if raw_segments:
                                raw_segments[-1] += rem_raw
                            else:
                                raw_segments.append(rem_raw)
                else:
                    with seg_lock:
                        if raw_segments:
                            raw_segments[-1] += rem_raw
                        else:
                            raw_segments.append(rem_raw)

        # Tunggu pemutaran selesai atau interupsi
        while active_mgr.is_busy() and not active_mgr.cancel_event.is_set():
            if interrupt_poll_fn is not None and not interrupted:
                intr = interrupt_poll_fn()
                if intr is not None:
                    interrupt_reason = intr
                    interrupt_triggered_at = time.perf_counter()
                    active_mgr.cancel()
                    break
            time.sleep(0.005)

        # Pastikan seluruh sisa teks tercetak penuh (M1)
        with seg_lock:
            _flush_remaining_locked(full_raw_text)

    finally:
        active_mgr.unregister_cancel_callback(_on_cancel_flush)
        active_mgr.on_chunk_synthesized = prev_on_synth
        active_mgr.on_before_play = prev_on_before
        active_mgr.on_after_play = prev_on_after
        with active_mgr._lock:
            active_mgr._utterance_plan_events.pop(utterance_epoch, None)
            active_mgr._utterance_start_delays.pop(utterance_epoch, None)

    num_chunks = len(planned_chunks)
    gaps_ms: List[float] = []
    for i in range(num_chunks - 1):
        if i in after_play_times and (i + 1) in before_play_times:
            gaps_ms.append((before_play_times[i + 1] - after_play_times[i]) * 1000.0)

    interrupt_flush_ms = 0.0
    if interrupted and interrupt_triggered_at > 0.0 and interrupt_flushed_at > 0.0:
        interrupt_flush_ms = max(0.0, (interrupt_flushed_at - interrupt_triggered_at) * 1000.0)

    return {
        "mode": "synced_supertonic",
        "full_text": full_raw_text,
        "chunks": planned_chunks,
        "raw_segments": raw_segments,
        "printed_segments": printed_segments,
        "metrics": final_metrics,
        "first_token_at": first_token_at,
        "first_text_at": first_text_at,
        "first_token_to_first_text_sec": (
            (first_text_at - first_token_at)
            if (first_token_at > 0.0 and first_text_at > 0.0)
            else 0.0
        ),
        "chunk1_ready_at": chunk1_ready_at,
        "delay_info": delay_info,
        "before_play_times": before_play_times,
        "after_play_times": after_play_times,
        "segment_printed_times": segment_printed_times,
        "sync_deltas_ms": sync_deltas_ms,
        "gaps_ms": gaps_ms,
        "interrupted": interrupted,
        "interrupt_reason": interrupt_reason,
        "interrupt_flush_ms": interrupt_flush_ms,
    }


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
    gapless_start: Optional[bool] = None,
    max_start_delay: Optional[float] = None,
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
        gapless_start=gapless_start,
        max_start_delay=max_start_delay,
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

