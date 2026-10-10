import re
from typing import Optional, List, Dict, Any
from enki.core.prompt_assembler import estimate_tokens_local
from enki.core.sanitizer import sanitize_untrusted_text

# Regex pemicu word-boundary
TRIGGER_REGEX = re.compile(
    r"\b(kemarin|pernah|ingat|sebelumnya|tadi|waktu itu|remember|earlier)\b",
    re.IGNORECASE
)

# Daftar kata pemicu dan stopword percakapan yang harus dibuang dari query pencarian
STOPWORDS_AND_TRIGGERS = {
    # Pemicu
    "kemarin", "pernah", "ingat", "sebelumnya", "tadi", "waktu", "itu", "remember", "earlier",
    # Stopword Bahasa Indonesia
    "apa", "yang", "kita", "kamu", "aku", "saya", "dia", "mereka", "dong", "tolong",
    "ya", "sih", "kan", "lah", "kah", "dan", "di", "ke", "dari", "ini", "ada",
    "bisa", "mau", "pada", "untuk", "dengan", "apakah", "gimana", "bagaimana",
    "kenapa", "mengapa", "soal", "tentang", "nggak", "tidak", "kok", "itu", "ini",
    # Stopword Bahasa Inggris
    "what", "the", "is", "are", "you", "we", "i", "a", "an", "in", "on", "at", "to",
    "for", "of", "with", "about", "did", "do", "does", "have", "has", "had"
}

def is_recall_triggered(text: str) -> bool:
    """Mengecek apakah pesan user mengandung pemicu leksikal dengan word boundary."""
    return bool(TRIGGER_REGEX.search(text))

def extract_substantive_tokens(user_text: str) -> List[str]:
    """Mengekstrak kata-kata substantif unik dari teks pengguna."""
    cleaned_chars = re.sub(r'[^\w\s]', ' ', user_text)
    words = cleaned_chars.split()
    substantive = []
    seen = set()
    for w in words:
        w_lower = w.lower().strip()
        if len(w_lower) >= 2 and w_lower not in STOPWORDS_AND_TRIGGERS and w_lower not in seen:
            substantive.append(w_lower)
            seen.add(w_lower)
    return substantive

def build_fts_query(user_text: str) -> Optional[str]:
    """Membersihkan pesan mentah menjadi query FTS5 yang aman dan fokus pada kata kunci substantif."""
    tokens = extract_substantive_tokens(user_text)
    if not tokens:
        return None
    return " OR ".join(f'"{t}"' for t in tokens)

GROUNDING_INSTRUCTION = "\n\n[Instruksi Wajib]: Jawab berdasarkan hasil pencarian di atas. Jika hasil memuat jawabannya, sampaikan isinya. Jangan menambah saran umum."

def format_recall_block(snippets: List[Dict[str, Any]], max_tokens: int = 300) -> Optional[str]:
    """Membungkus hasil cuplikan FTS5 aktif dengan grounding tegas."""
    if not snippets:
        return None

    lines = [
        '<untrusted_memory_content source="sqlite_fts5_recall">',
        "[Riwayat Percakapan Masa Lalu yang Relevan]"
    ]

    for item in snippets:
        role = item.get("role", "user")
        content = sanitize_untrusted_text(item.get("content", ""))
        lines.append(f"- {role.capitalize()}: {content.strip()}")

    lines.append('</untrusted_memory_content>')
    formatted = "\n".join(lines)

    if estimate_tokens_local(formatted) > max_tokens:
        char_limit = int(max_tokens * 2.8)
        truncated_body = formatted[:char_limit].rstrip()
        if not truncated_body.endswith("</untrusted_memory_content>"):
            truncated_body += "\n... [Riwayat dipotong]\n</untrusted_memory_content>"
        return truncated_body + GROUNDING_INSTRUCTION

    return formatted + GROUNDING_INSTRUCTION

def format_passive_recall_block(snippets: List[Dict[str, Any]], max_tokens: int = 300) -> Optional[str]:
    """Membungkus cuplikan recall pasif tanpa instruksi grounding keras."""
    if not snippets:
        return None

    lines = [
        '<untrusted_memory_content source="sqlite_fts5_passive_recall">',
        "[Konteks Latar dari Percakapan Lama] (Gunakan HANYA jika relevan langsung dengan pertanyaan pengguna, abaikan jika tidak relevan):"
    ]

    for item in snippets:
        role = item.get("role", "user")
        content = sanitize_untrusted_text(item.get("content", ""))
        lines.append(f"- {role.capitalize()}: {content.strip()}")

    lines.append('</untrusted_memory_content>')
    formatted = "\n".join(lines)

    if estimate_tokens_local(formatted) > max_tokens:
        char_limit = int(max_tokens * 2.8)
        truncated_body = formatted[:char_limit].rstrip()
        if not truncated_body.endswith("</untrusted_memory_content>"):
            truncated_body += "\n... [Riwayat dipotong]\n</untrusted_memory_content>"
        return truncated_body

    return formatted

def execute_passive_recall(
    user_text: str,
    db: Any,
    current_session_id: str,
    max_tokens: int = 300,
    df_threshold_pct: float = 5.0
) -> Optional[str]:
    """Mengeksekusi recall pasif dengan ambang batas Document Frequency (DF) yang telah terkalibrasi dan dibekukan:
    - Kata dengan Document Frequency (DF) > df_threshold_pct % (default 5.0%) dianggap umum dan tidak boleh memicu recall sendirian.
    - Kata dengan DF <= df_threshold_pct % dianggap spesifik/domain token dan boleh memicu recall.
    - Turn kandidat lolos jika:
      * Minimal 2 kata substantif berbeda cocok dalam turn yang sama, ATAU
      * 1 kata cocok jika kata itu adalah kata spesifik (DF <= 5.0%).
    - Maksimal 3 cuplikan teratas, maksimal ~300 token.
    """
    tokens = extract_substantive_tokens(user_text)
    if not tokens:
        return None

    fts_query = " OR ".join(f'"{t}"' for t in tokens)
    candidates = db.search_fts(fts_query, exclude_session_id=current_session_id, limit=5)
    if not candidates:
        return None

    qualifying_snippets = []
    for snip in candidates:
        content_lower = snip.get("content", "").lower()
        matched = {t for t in tokens if re.search(r'\b' + re.escape(t), content_lower)}
        if not matched:
            continue

        if hasattr(db, "get_term_turn_frequency"):
            is_rare_word = any(db.get_term_turn_frequency(t) <= df_threshold_pct for t in matched)
        else:
            is_rare_word = any(len(t) >= 7 for t in matched)

        # Ambang batas evaluasi
        if len(matched) >= 2 or (len(matched) == 1 and is_rare_word):
            qualifying_snippets.append(snip)

    if not qualifying_snippets:
        return None

    return format_passive_recall_block(qualifying_snippets[:3], max_tokens=max_tokens)
