import re
from typing import Optional

# Regex case-insensitive yang membuang tag pembuka dan penutup untrusted_*,
# termasuk variasi spasi sembarang, huruf besar/kecil, dan atribut di dalamnya.
UNTRUSTED_TAG_REGEX = re.compile(
    r'<\s*/?\s*untrusted_[a-zA-Z0-9_\-]+(\s+[^>]*)?>',
    re.IGNORECASE
)

def sanitize_untrusted_text(text: str) -> str:
    """Membersihkan teks dari tag injeksi untrusted_* (pembuka maupun penutup)."""
    if not text:
        return ""
    return UNTRUSTED_TAG_REGEX.sub("", text)

strip_untrusted_tags = sanitize_untrusted_text

def wrap_untrusted_content(
    text: str,
    tag_name: str = "untrusted_external_content",
    source: Optional[str] = None
) -> str:
    """Membungkus teks hasil tool atau memori dengan tag untrusted setelah sanitasi."""
    cleaned = sanitize_untrusted_text(text).strip()
    source_attr = f' source="{source}"' if source else ""
    return f"<{tag_name}{source_attr}>\n{cleaned}\n</{tag_name}>"
