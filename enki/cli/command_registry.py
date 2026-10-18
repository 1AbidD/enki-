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
    "/baca": CommandDef("/baca <path>", "baca", "Baca isi file dokumen/kode di dalam workspace"),
    "/dir": CommandDef("/dir [path]", "folder", "Lihat daftar file dan folder di dalam workspace"),
    "/git": CommandDef("/git <aksi>", "git", "Jalankan aksi git aman ('status', 'log', 'diff')"),
    "/test": CommandDef("/test", "tes", "Jalankan unit test sistem Python (konfirmasi [y/N])"),
    "/versi": CommandDef("/versi", "versi", "Periksa versi Python sistem (konfirmasi [y/N])"),
    "/ingat": CommandDef("/ingat <kunci>", "cari", "Cari catatan/riwayat percakapan masa lalu di memori"),
    "/simpan": CommandDef("/simpan <k> | <v>", "simpan", "Simpan fakta diri permanen ke profil pengguna"),
    "/memory": CommandDef("/memory <subcmd>", "memori", "Manajemen memori (view, pending, approve, reject, edit, delete, log, stats)"),
    "/reflect": CommandDef("/reflect", "refleksi", "Jalankan refleksi atas sesi yang belum direfleksi"),
    "/coder": CommandDef("/coder", "coder", "Beralih ke model coding (qwen2.5-coder:3b, mode chat-only)"),
    "/chat": CommandDef("/chat", "chat", "Beralih ke model percakapan utama (qwen2.5:3b)"),
    "/mute": CommandDef("/mute", "senyap", "Aktifkan mode senyap (0% beban audio)"),
    "/unmute": CommandDef("/unmute", "suara", "Nonaktifkan mode senyap (TTS aktif)"),
    "/status": CommandDef("/status", "status", "Tampilkan status model, context limit, mute, dan VRAM"),
    "/clear": CommandDef("/clear", "bersih", "Bersihkan riwayat obrolan sesi aktif"),
    "/exit": CommandDef("/exit", "keluar", "Keluar dari aplikasi"),
    "/help": CommandDef("/help", "bantuan", "Tampilkan bantuan ini"),
}
