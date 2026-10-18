"""Router kemampuan deterministik v4 (Tahap 4E: Generalisasi Komposisional).

Mendeteksi pertanyaan kemampuan dan batasan ENKI sebelum LLM dipanggil menggunakan:
1. Bingkai pertanyaan-kemampuan (modalitas, referensi asisten, konstruksi 'cara agar kamu').
2. Leksikon topik per intent dengan variasi baku, slang, dan imbuhan bahasa Indonesia.
3. Filter negatif struktural (bukan kata topik) untuk membiarkan diskusi konsep, curhat,
   atau keluhan masalah pribadi/perangkat ke model.
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

    t = raw.lower()

    # 1. Structural Negative Filters (Bukan kata topik spesifik)
    # 1a. Permintaan penjelasan, cerita, perbedaan, cara kerja, tips, atau konsep
    if re.search(r"\b(?:jelas(?:kan|in)|cerita(?:kan|in)|rekomendasi(?:kan)?|tips|saran)\b", t):
        return None
    if re.search(r"\b(?:apa\s+itu|artinya|maksudnya|maknanya)\b", t):
        return None
    if re.search(r"\b(?:apa\s+beda(?:nya)?|apa\s+perbedaan(?:nya)?)\b", t):
        return None
    if re.search(r"\bcara\s+kerja\b", t):
        return None
    if re.search(r"\b(?:kenapa|mengapa)\b", t):
        return None

    # 1b. Kendala / kepemilikan orang pertama (keluhan kondisi pribadi atau perangkat pengguna)
    if re.search(r"\b(?:aku|gue|saya)\s+(?:ga|gak|nggak|tidak)\s+bisa\b", t):
        return None
    if re.search(r"\b(?:rusak|error|lemot|lambat|berkedip)\b", t):
        return None

    # 1c. Permintaan aksi langsung / bantuan pengerjaan tugas (bukan cek kemampuan)
    if re.search(r"^\s*tolong\s+(?:ingat(?:kan|in)?|catat|bikin|buat(?:kan)?|simpan)\b", t):
        return None
    if re.search(r"\bbantu\s+(?:aku|saya|gue)\s+bikin\b", t):
        return None

    # 1d. Pertanyaan entitas pihak ketiga / lingkungan eksternal
    if re.search(r"\bmanusia\b", t):
        return None
    if re.search(r"\b(?:jalanin|menjalankan|bikin|buat|ngetes)\s+.*?\bdi\s+(?:laptop|komputer|hp|postman|insomnia|terminal)\b", t):
        return None
    if re.search(r"\bperintah\s+\w+\s+di\s+(?:linux|windows|git)\b", t):
        return None
    if re.search(r"\b(?:di\s+(?:go|golang|python|java|javascript|js|c\+\+|rust|php|ruby|linux|windows))\b", t):
        return None
    if re.search(r"\bkamu\s+suka\b", t):
        return None

    # 2. Bingkai Pertanyaan Kemampuan (Ability Framing)
    # 2a. Komponen Subjek (Asisten)
    has_agent_ref = bool(re.search(r"\b(?:kamu|anda|lu|lo|loe|enki|dirimu|bot|asisten|sistem)\b", t))
    has_indirect_ref = bool(re.search(r"\b(?:lewat\s+kamu|di\s+sini|sama\s+kamu)\b", t))

    # 2b. Komponen Modalitas Kemampuan
    has_modal = bool(re.search(r"\b(?:bisa(?:kah)?|dapat(?:kah)?|mampu(?:kah)?|sanggup(?:kah)?)\b", t))
    has_leading_modal = bool(re.match(r"^\s*(?:apakah\s+)?(?:bisa(?:kah)?|dapat(?:kah)?|mampu(?:kah)?)\b", t))
    has_interrogative_tail = bool(re.search(r"\b(?:ga|gak|nggak|kah|sih|dong)\b\s*\??$", t))
    has_feature_noun = bool(re.search(r"\b(?:fitur\w*|kemampuan\w*|command\w*|perintah\w*)\b", t))

    # 2c. Konstruksi "cara agar/biar/nyuruh kamu"
    has_method_frame = bool(re.search(r"\bcara(?:nya)?\s+(?:biar|agar|nyuruh|menyuruh|supaya)\s+kamu\b", t))
    has_save_to_memory_frame = bool(re.search(r"\bcara\s+nyimpen\s+.*?\b(?:ke\s+ingatanmu|tentang\s+(?:aku|gue|saya))\b", t))
    has_method_via_frame = bool(re.search(r"\bgimana\s+cara(?:nya)?\s+.*?\blewat\s+kamu\b", t))
    has_how_open_file = bool(re.search(r"\bkalau\s+mau\s+kamu\s+buka\b", t))

    # 2d. Konstruksi kepemilikan fitur/akses
    has_possession_frame = bool(re.search(r"\b(?:kamu|lu|anda|enki)\s+punya\b", t))
    has_connect_frame = bool(re.search(r"\bterhubung\b", t))

    is_capability_frame = (
        has_leading_modal
        or (has_modal and (has_agent_ref or has_indirect_ref or has_interrogative_tail or "?" in t))
        or (has_agent_ref and has_modal)
        or has_feature_noun
        or has_method_frame
        or has_save_to_memory_frame
        or has_method_via_frame
        or has_how_open_file
        or (has_possession_frame and (has_feature_noun or re.search(r"\b(?:akses|suara)\b", t)))
        or has_connect_frame
    )

    if not is_capability_frame:
        return None

    # 3. Leksikon Topik Spesifik

    # CAN_INTERNET
    lex_net = bool(re.search(r"\b(?:internet|web|website|link|google|browsing)\b", t))
    act_net = bool(re.search(r"\b(?:buka(?:kan)?|membuka|akses|mengakses|nyari|mencari|browsing|terhubung|konek)\b", t))
    if lex_net and (act_net or has_possession_frame or has_connect_frame or has_modal):
        if not re.search(r"\b(?:belajar|bagus\s+buat)\b", t):
            reply = (
                "Saya beroperasi secara lokal dan offline, sehingga saat ini saya tidak bisa mengakses "
                "internet atau browsing web. Semua fungsi dan data dijalankan langsung di komputer Anda "
                "tanpa koneksi ke luar."
            )
            return RouteResult(intent="CAN_INTERNET", reply=reply)

    # CAN_SCREEN
    lex_scr = bool(re.search(r"\b(?:layar\w*|monitor\w*|screen\w*|tampilan\w*)\b", t))
    act_scr = bool(re.search(r"\b(?:liat|lihat|melihat|ngelihat|pantau|memantau|baca|screenshot)\b", t))
    if (lex_scr and (act_scr or has_modal)) or re.search(r"\bscreenshot\b", t):
        reply = (
            "Saya tidak bisa melihat atau memeriksa isi layar monitor Anda. "
            "Saya hanya dapat berinteraksi melalui pesan teks di terminal obrolan ini."
        )
        return RouteResult(intent="CAN_SCREEN", reply=reply)

    # CAN_READ_FILE
    lex_fil = bool(re.search(r"\b(?:file\w*|berkas\w*|dokumen\w*|folder\w*)\b", t))
    act_fil = bool(re.search(r"\b(?:baca|membaca|buka|membuka|lihat|liat)\b", t))
    if lex_fil and (act_fil or re.search(r"\bisi\s+(?:file|berkas|dokumen|folder)\b", t)):
        reply = (
            "Saya bisa membaca isi berkas di dalam direktori workspace. "
            "Anda dapat menggunakan perintah `/baca <path_berkas>` untuk membuka dan membaca berkas yang Anda butuhkan."
        )
        return RouteResult(intent="CAN_READ_FILE", reply=reply)

    # CAN_TEST
    lex_tst = bool(re.search(r"\b(?:unit\s+test|test\s+suite|test|tes|kode|program)\b", t))
    act_tst = bool(re.search(r"\b(?:ngetes|menguji|uji|jalanin|jalankan|menjalankan|eksekusi|running|run)\b", t))
    if re.search(r"\b(?:ngetes|menguji)\b", t) or (act_tst and lex_tst):
        reply = (
            "Saya bisa menjalankan unit test sistem Python di workspace Anda. "
            "Anda dapat menggunakan perintah `/test` untuk menjalankan seluruh pengujian otomatis."
        )
        return RouteResult(intent="CAN_TEST", reply=reply)

    # CAN_SPEAK
    lex_spk = bool(re.search(r"\b(?:ngomong|bersuara|bicara|berbicara|ngobrol|suara|audio)\b", t))
    if lex_spk:
        if re.search(r"\b(?:ngomong|bersuara|bicara|berbicara|ngobrol)\b", t) or (re.search(r"\bsuara\b", t) and (has_modal or has_possession_frame)):
            reply = (
                "Saya bisa berbicara menggunakan suara sintesis lokal (TTS). "
                "Anda dapat menggunakan perintah `/unmute` untuk mengaktifkan suara, atau `/mute` untuk mematikan suara dan kembali ke mode senyap."
            )
            return RouteResult(intent="CAN_SPEAK", reply=reply)

    # CAN_REMEMBER
    lex_rem = bool(re.search(r"\b(?:inget|nginget|ingat|mengingat|simpen|nyimpen|simpan|menyimpan|catat|mencatat)\b", t))
    if lex_rem:
        if has_method_frame or has_save_to_memory_frame or re.search(r"\b(?:nama\w*|fakta|percakapan|hal\s+tentang|sesuatu|profil|preferensi|catatan)\b", t):
            reply = (
                "Saya bisa mengingat dan menyimpan fakta tentang Anda. "
                "Gunakan perintah `/simpan <kunci> | <isi_fakta>` untuk menyimpan fakta permanen ke profil Anda, "
                "dan `/ingat <kata_kunci>` untuk mencari catatan masa lalu di memori."
            )
            return RouteResult(intent="CAN_REMEMBER", reply=reply)

    # 4. LIST_COMMANDS (General Capability Inquiry)
    # Jika ada pertanyaan kemampuan/fitur/command tanpa topik spesifik di atas
    is_list_cue = (
        re.search(r"\b(?:command|perintah|fitur|kemampuan)\b", t)
        or re.search(r"\b(?:bisa|sanggup|mampu)\s+(?:bantu\s+)?(?:apa\s*(?:aja|saja)?|ngapain)\b", t)
        or re.search(r"\bapa\s+yang\s+bisa\s+kamu\b", t)
        or re.search(r"\blist\s+perintah\b", t)
        or re.search(r"\bkemampuan\w*\s+apa\b", t)
    )
    if is_list_cue:
        return RouteResult(intent="LIST_COMMANDS", reply=format_list_commands_reply(registry))

    return None
