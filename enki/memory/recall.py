import re
from typing import Optional, List, Dict, Any
from enki.core.prompt_assembler import estimate_tokens_local

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
    "kenapa", "mengapa", "soal", "tentang", "nggak", "tidak", "kok",
    # Stopword Bahasa Inggris
    "what", "the", "is", "are", "you", "we", "i", "a", "an", "in", "on", "at", "to",
    "for", "of", "with", "about", "did", "do", "does", "have", "has", "had"
}

def is_recall_triggered(text: str) -> bool:
    """Mengecek apakah pesan user mengandung pemicu leksikal dengan word boundary."""
    return bool(TRIGGER_REGEX.search(text))

def build_fts_query(user_text: str) -> Optional[str]:
    """Membersihkan pesan mentah menjadi query FTS5 yang aman dan fokus pada kata kunci substantif."""
    # Bersihkan semua tanda baca dan karakter khusus yang dapat merusak sintaks SQLite FTS5
    cleaned_chars = re.sub(r'[^\w\s]', ' ', user_text)
    words = cleaned_chars.split()

    substantive_tokens = []
    for w in words:
        w_lower = w.lower().strip()
        if len(w_lower) >= 2 and w_lower not in STOPWORDS_AND_TRIGGERS:
            substantive_tokens.append(w_lower)

    if not substantive_tokens:
        return None

    # Format sebagai pencarian FTS5 (kata1 OR kata2 atau frasa)
    # Menggunakan token terpisah dengan format kutipan ganda agar aman
    return " OR ".join(f'"{t}"' for t in substantive_tokens)

def format_recall_block(snippets: List[Dict[str, Any]], max_tokens: int = 300) -> Optional[str]:
    """Membungkus hasil cuplikan FTS5 dalam tag untrusted dan membatasi ukuran maksimal ~300 token."""
    if not snippets:
        return None

    lines = [
        '<untrusted_memory_content source="sqlite_fts5_recall">',
        "[Riwayat Percakapan Masa Lalu yang Relevan]"
    ]

    for item in snippets:
        role = item.get("role", "user")
        content = item.get("content", "").replace("</untrusted_memory_content>", "")
        lines.append(f"- {role.capitalize()}: {content.strip()}")

    lines.append('</untrusted_memory_content>')
    formatted = "\n".join(lines)

    # Batasi agar tidak melebihi ~300 token
    if estimate_tokens_local(formatted) > max_tokens:
        char_limit = int(max_tokens * 2.8)
        truncated_body = formatted[:char_limit].rstrip()
        if not truncated_body.endswith("</untrusted_memory_content>"):
            truncated_body += "\n... [Riwayat dipotong]\n</untrusted_memory_content>"
        return truncated_body

    return formatted
