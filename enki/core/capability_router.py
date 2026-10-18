"""Router kemampuan deterministik (Tahap 4D-v3).

Mendeteksi pertanyaan seputar kemampuan dan batasan ENKI sebelum LLM dipanggil,
dan memberikan jawaban langsung dari kode deterministik tanpa memanggil model.
"""

import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class RouteResult:
    intent: str
    reply: str

def format_list_commands_reply(registry: Optional[Dict[str, Any]] = None) -> str:
    """Membangun teks daftar perintah dinamis dari COMMAND_REGISTRY."""
    if registry is None:
        from enki.cli.command_registry import COMMAND_REGISTRY
        target_reg = COMMAND_REGISTRY
    else:
        target_reg = registry

    lines = ["Saya memiliki beberapa perintah yang dapat Anda gunakan di ENKI:"]
    for cmd, cdef in target_reg.items():
        desc = getattr(cdef, "help_text", None) or getattr(cdef, "description", None) or str(cdef)
        lines.append(f"- {cmd}: {desc}")
    lines.append("\nKetik perintah dengan awalan '/' untuk menjalankannya, atau ketik /help untuk panduan bantuan lengkap.")
    return "\n".join(lines)

def route_capability_inquiry(text: str, registry: Optional[Dict[str, Any]] = None) -> Optional[RouteResult]:
    """Mengevaluasi apakah pesan pengguna merupakan pertanyaan kemampuan ENKI.
    
    Hanya dijalankan pada mode chat untuk pesan non-slash.
    Mengembalikan RouteResult jika cocok, atau None jika bukan pertanyaan kemampuan.
    """
    if not text:
        return None

    raw = text.strip()
    if raw.startswith("/"):
        return None

    t_clean = raw.lower()

    # 1. Negative Filter: Topik eksternal, tutorial, kerusakan hardware, atau konsep pemrograman
    neg_pats = [
        r"\b(?:jelaskan|apa\s+itu|pengertian|definisi|maksudnya|artinya|fungsinya\s+apa)\b",
        r"\b(?:di\s+\w+\s+python|di\s+linux|di\s+python|di\s+javascript|di\s+golang|di\s+c\+\+|di\s+java|pandas|numpy|csv)\b",
        r"\b(?:retak|pecah|rusak)\b",
        r"\b(?:belajar|library|framework|tutorial)\b",
    ]
    for p in neg_pats:
        if re.search(p, t_clean):
            return None

    has_ref = bool(re.search(r"\b(?:kamu|anda|enki|dirimu|sistem|bot|asisten|lewat kamu|di sini)\b", t_clean))
    has_modal = bool(re.search(r"\b(?:bisa|dapat|sanggup|mampu|boleh|tolong|ada|gimana cara|bagaimana cara|cara nyuruh|daftar|list)\b", t_clean))

    # 2. Specific Capability Intents

    # CAN_INTERNET
    if re.search(r"\b(?:internet|browsing|web)\b", t_clean) and (has_ref or has_modal):
        if re.search(r"\b(?:bisa|dapat|browsing|akses|konek|terhubung)\b", t_clean):
            reply = (
                "Saya beroperasi secara lokal dan offline, sehingga saat ini saya tidak bisa mengakses "
                "internet atau browsing web. Semua fungsi dan data dijalankan langsung di komputer Anda "
                "tanpa koneksi ke luar."
            )
            return RouteResult(intent="CAN_INTERNET", reply=reply)

    # CAN_SCREEN
    if re.search(r"\b(?:layar\w*|monitor\w*|screen\w*)\b", t_clean) and (has_ref or has_modal):
        if re.search(r"\b(?:lihat|ngelihat|melihat|tengok|menengok|pantau|memantau|baca|screenshot)\b", t_clean):
            reply = (
                "Saya tidak bisa melihat atau memeriksa isi layar monitor Anda. "
                "Saya hanya dapat berinteraksi melalui pesan teks di terminal obrolan ini."
            )
            return RouteResult(intent="CAN_SCREEN", reply=reply)

    # CAN_READ_FILE
    if re.search(r"\b(?:file|berkas|dokumen)\b", t_clean) and (has_ref or has_modal):
        if re.search(r"\b(?:baca|membaca|buka|membuka)\b", t_clean):
            reply = (
                "Saya bisa membaca isi berkas di dalam direktori workspace. "
                "Anda dapat menggunakan perintah `/baca <path_berkas>` untuk membuka dan membaca berkas yang Anda butuhkan."
            )
            return RouteResult(intent="CAN_READ_FILE", reply=reply)

    # CAN_TEST
    if re.search(r"\b(?:unit\s+test|test\s+suite|test|tes)\b", t_clean) and (has_ref or has_modal):
        if re.search(r"\b(?:jalanin|jalankan|menjalankan|running|eksekusi|run)\b", t_clean):
            reply = (
                "Saya bisa menjalankan unit test sistem Python di workspace Anda. "
                "Anda dapat menggunakan perintah `/test` untuk menjalankan seluruh pengujian otomatis."
            )
            return RouteResult(intent="CAN_TEST", reply=reply)

    # CAN_SPEAK
    if (re.search(r"\b(?:ngomong|bicara|berbicara|bersuara)\b", t_clean) or re.search(r"\b(?:suara|audio)\b", t_clean)) and (has_ref or has_modal):
        if re.search(r"\b(?:ngomong|bicara|berbicara|bersuara)\b", t_clean) or re.search(r"\b(?:bisa|punya|pakai|keluarkan).*?\bsuara\b", t_clean):
            reply = (
                "Saya bisa berbicara menggunakan suara sintesis lokal (TTS). "
                "Anda dapat menggunakan perintah `/unmute` untuk mengaktifkan suara, atau `/mute` untuk mematikan suara dan kembali ke mode senyap."
            )
            return RouteResult(intent="CAN_SPEAK", reply=reply)

    # CAN_REMEMBER
    if re.search(r"\b(?:inget|ingat|mengingat)\b", t_clean) and (has_ref or has_modal):
        if re.search(r"\b(?:sesuatu|fakta|profil|preferensi|catatan|nama)\b", t_clean) or re.search(r"\b(?:nyuruh\s+kamu|agar\s+kamu|kamu\s+bisa)\b", t_clean):
            reply = (
                "Saya bisa mengingat dan menyimpan fakta tentang Anda. "
                "Gunakan perintah `/simpan <kunci> | <isi_fakta>` untuk menyimpan fakta permanen ke profil Anda, "
                "dan `/ingat <kata_kunci>` untuk mencari catatan masa lalu di memori."
            )
            return RouteResult(intent="CAN_REMEMBER", reply=reply)
    if re.search(r"\b(?:simpan|menyimpan)\b", t_clean) and (has_ref or has_modal):
        if re.search(r"\b(?:fakta|profil|preferensi|catatan|informasi)\b", t_clean):
            reply = (
                "Saya bisa mengingat dan menyimpan fakta tentang Anda. "
                "Gunakan perintah `/simpan <kunci> | <isi_fakta>` untuk menyimpan fakta permanen ke profil Anda, "
                "dan `/ingat <kata_kunci>` untuk mencari catatan masa lalu di memori."
            )
            return RouteResult(intent="CAN_REMEMBER", reply=reply)

    # LIST_COMMANDS
    if re.search(r"\b(?:daftar|list)\s+(?:fitur|perintah|command)\b", t_clean):
        return RouteResult(intent="LIST_COMMANDS", reply=format_list_commands_reply(registry))
    if re.search(r"\b(?:ada|daftar|sebutkan)\s+(?:perintah|command|fitur)\b", t_clean) and (re.search(r"\b(?:apa\s*(?:saja|aja)|yang\s+bisa)\b", t_clean) or "di sini" in t_clean):
        return RouteResult(intent="LIST_COMMANDS", reply=format_list_commands_reply(registry))
    if has_ref and re.search(r"\b(?:bisa|sanggup|mampu)\b", t_clean) and re.search(r"\b(?:ngapain|apa\s*(?:saja|aja)|bantu\s+apa)\b", t_clean):
        return RouteResult(intent="LIST_COMMANDS", reply=format_list_commands_reply(registry))
    if (has_ref or has_modal) and re.search(r"\b(?:apa\s*(?:saja|aja)|daftar|list|tampilkan)\b", t_clean) and re.search(r"\b(?:kemampuan|fitur|command)\b", t_clean):
        return RouteResult(intent="LIST_COMMANDS", reply=format_list_commands_reply(registry))
    if re.search(r"\b(?:fitur|kemampuan)\s+(?:kamu|anda|enki)\b", t_clean):
        return RouteResult(intent="LIST_COMMANDS", reply=format_list_commands_reply(registry))

    return None
