"""Registri perintah CLI tunggal (Single Source of Truth) untuk ENKI (Tahap 4D).

Menyediakan satu sumber kebenaran untuk seluruh perintah CLI ENKI agar:
1. Blok kemampuan pada system prompt dibangun secara dinamis tanpa duplikasi manual.
2. Tampilan bantuan (/help di console) konsisten dengan registri.
3. Penjaga melenceng (drift guard) mendeteksi setiap penambahan perintah baru secara otomatis.
"""

from typing import Dict, NamedTuple

class CommandDef(NamedTuple):
    syntax: str
    description: str
    help_text: str = ""

COMMAND_REGISTRY: Dict[str, CommandDef] = {
    "/baca": CommandDef("/baca <path>", "baca", "membaca isi berkas di workspace"),
    "/dir": CommandDef("/dir [path]", "folder", "melihat daftar berkas dan folder"),
    "/git": CommandDef("/git <aksi>", "git", "menjalankan aksi git aman status/log/diff"),
    "/test": CommandDef("/test", "tes", "menjalankan unit test sistem Python"),
    "/versi": CommandDef("/versi", "versi", "memeriksa versi sistem Python"),
    "/ingat": CommandDef("/ingat <kunci>", "cari", "mencari catatan masa lalu di memori"),
    "/simpan": CommandDef("/simpan <k> | <v>", "simpan", "menyimpan fakta diri ke profil pengguna"),
    "/memory": CommandDef("/memory <subcmd>", "memori", "mengelola entri memori sistem"),
    "/reflect": CommandDef("/reflect", "refleksi", "melakukan refleksi sesi percakapan"),
    "/coder": CommandDef("/coder", "coder", "beralih ke model khusus coding"),
    "/chat": CommandDef("/chat", "chat", "beralih ke model percakapan utama"),
    "/mute": CommandDef("/mute", "senyap", "menonaktifkan suara audio mode senyap"),
    "/unmute": CommandDef("/unmute", "suara", "mengaktifkan kemampuan berbicara dengan suara TTS"),
    "/status": CommandDef("/status", "status", "melihat status sistem dan model"),
    "/clear": CommandDef("/clear", "bersih", "membersihkan riwayat obrolan sesi"),
    "/web": CommandDef("/web <url> [tanya]", "web", "mengambil teks web dengan konfirmasi"),
    "/wiki": CommandDef("/wiki <topik>", "wiki", "mencari ringkasan ensiklopedia Wikipedia"),
    "/buka": CommandDef("/buka <url>", "buka", "membuka URL di peramban web"),
    "/exit": CommandDef("/exit", "keluar", "keluar dari aplikasi"),
    "/help": CommandDef("/help", "bantuan", "menampilkan panduan bantuan perintah"),
}
