"""Fungsi murni untuk mendeteksi intensi pengguna dan memberikan petunjuk perintah eksplisit CLI (Tahap 3B-1).

Menggunakan predikat leksikal deterministik yang sudah ada di react_loop.py
tanpa mengubah logika gating, dan memetakan kalimat obrolan ke sintaks perintah yang relevan.
"""

from typing import List, Set, Optional, Tuple

from enki.core.react_loop import (
    should_enable_read_file,
    should_enable_list_dir,
    should_enable_git,
    should_enable_run_unittests,
    should_enable_python_version,
    should_enable_memory_tool
)

COMMAND_HINT_RULES = [
    ("/baca", should_enable_read_file, "/baca <path>", "Gunakan perintah /baca <path> untuk membaca file di workspace."),
    ("/dir", should_enable_list_dir, "/dir [path]", "Gunakan perintah /dir [path] untuk melihat daftar direktori atau file."),
    ("/git", should_enable_git, "/git status|log|diff", "Gunakan perintah /git status|log|diff untuk melihat informasi repositori git."),
    ("/test", should_enable_run_unittests, "/test", "Gunakan perintah /test untuk menjalankan unit test proyek."),
    ("/versi", should_enable_python_version, "/versi", "Gunakan perintah /versi untuk memeriksa versi runtime Python."),
    ("/ingat", should_enable_memory_tool, "/ingat <kata kunci>", "Gunakan perintah /ingat <kata kunci> untuk mencari catatan percakapan masa lalu.")
]

def get_command_hints(
    user_input: str,
    seen_commands: Optional[Set[str]] = None,
    max_hints: int = 2
) -> List[Tuple[str, str]]:
    """Memetakan input teks pengguna ke petunjuk perintah eksplisit CLI jika terdeteksi intensi leksikal.
    Hanya mengembalikan petunjuk untuk perintah yang BELUM pernah dicatat dalam `seen_commands`.
    Maksimal menghasilkan `max_hints` baris petunjuk (default: 2 baris).
    
    Returns:
        List[Tuple[str, str]]: Pasangan (cmd_key, teks_petunjuk)
    """
    if not user_input:
        return []

    seen = seen_commands if seen_commands is not None else set()
    hints: List[Tuple[str, str]] = []

    for cmd_key, predicate, syntax, explanation in COMMAND_HINT_RULES:
        if predicate(user_input):
            if cmd_key not in seen:
                hint_line = f"[Petunjuk] {explanation} (Sintaks: {syntax})"
                hints.append((cmd_key, hint_line))
                if len(hints) >= max_hints:
                    break

    return hints
