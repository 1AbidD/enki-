import os
import fnmatch
from pathlib import Path
from typing import Tuple, Optional

from enki.core.sanitizer import wrap_untrusted_content
from enki.core.prompt_assembler import truncate_tool_output

SENSITIVE_PATTERNS = [
    ".env*",
    "*.pem",
    "id_rsa*",
    "*.key",
    "*.pfx",
    "*.crt"
]

BLOCKED_DIR_PARTS = [
    ".git",
    "data/memory",
    "data\\memory",
    "logs"
]

MAX_FILE_BYTES = 500_000  # 500 KB

def is_path_safe_to_read(
    file_path: str,
    workspace_root: Path
) -> Tuple[bool, str, Optional[Path]]:
    """Memeriksa apakah path file aman dibaca sesuai aturan sandbox keamanan."""
    if not file_path or not str(file_path).strip():
        return False, "Argumen 'file_path' tidak boleh kosong.", None

    clean_str = str(file_path).strip()
    root_resolved = Path(workspace_root).resolve()

    # Cegah UNC path secara eksplisit (\\server\share)
    if clean_str.startswith(r"\\") or clean_str.startswith("//"):
        return False, f"[Akses Ditolak] UNC path tidak diizinkan: {clean_str}", None

    # Resolve target path
    p = Path(clean_str)
    if not p.is_absolute():
        target = (root_resolved / p).resolve()
    else:
        target = p.resolve()

    # Dapatkan realpath untuk memblokir junction / symlink keluar workspace
    try:
        real_target = Path(os.path.realpath(target))
    except Exception as e:
        return False, f"[Akses Ditolak] Gagal memverifikasi path riil: {e}", None

    # 1. Wajib is_relative_to workspace root (baik logical maupun realpath)
    try:
        if not target.is_relative_to(root_resolved):
            return False, f"[Akses Ditolak] Path berada di luar workspace root: {target}", None
        if not real_target.is_relative_to(root_resolved):
            return False, f"[Akses Ditolak] Junction/symlink mengarah ke luar workspace: {real_target}", None
    except AttributeError:
        # Fallback Python < 3.9 (jika ada)
        if not str(target).startswith(str(root_resolved)) or not str(real_target).startswith(str(root_resolved)):
            return False, f"[Akses Ditolak] Path berada di luar workspace root.", None

    # 2. Blokir file sensitif berdasarkan nama dan pola
    fname = target.name.lower()
    for pattern in SENSITIVE_PATTERNS:
        if fnmatch.fnmatch(fname, pattern.lower()):
            return False, f"[Akses Ditolak] File '{fname}' tergolong file sensitif yang dilarang dibaca.", None

    # 3. Blokir folder terlarang (.git, data/memory, logs)
    rel_path_str = str(target.relative_to(root_resolved)).replace("\\", "/")
    rel_parts = rel_path_str.split("/")
    if ".git" in rel_parts or any(b in rel_path_str for b in ["data/memory", "logs"]):
        return False, f"[Akses Ditolak] Mengakses direktori terlindungi (.git, data/memory, logs) dilarang: {rel_path_str}", None

    # 4. Keberadaan file
    if not target.exists():
        return False, f"[File Tidak Ditemukan] File tidak ada di workspace: {clean_str}", None

    if not target.is_file():
        return False, f"[Bukan File] Target adalah direktori, bukan file: {clean_str}", None

    # 5. Batas ukuran
    try:
        st_size = target.stat().st_size
        if st_size > MAX_FILE_BYTES:
            return False, f"[Batas Ukuran] Ukuran file ({st_size} bytes) melebihi batas maksimal ({MAX_FILE_BYTES} bytes).", None
    except Exception as e:
        return False, f"[Error Akses] Gagal memeriksa ukuran file: {e}", None

    return True, "Path valid dan aman.", target

def execute_read_file(file_path: str, workspace_root: Path) -> str:
    """Membaca isi file teks di workspace secara aman, membungkus dengan tag untrusted."""
    ok, msg, target = is_path_safe_to_read(file_path, workspace_root)
    if not ok:
        return f"[Tool Error: read_file] {msg}"

    try:
        raw_bytes = target.read_bytes()
    except Exception as e:
        return f"[Tool Error: read_file] Gagal membaca isi file: {e}"

    # Cek binary / non-teks
    if b"\x00" in raw_bytes:
        return f"[Tool Error: read_file] File terdeteksi sebagai binary / non-teks dan ditolak."

    try:
        content_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        try:
            content_text = raw_bytes.decode("latin-1")
        except Exception:
            return f"[Tool Error: read_file] File tidak dapat di-decode sebagai teks."

    # Potong output tool jika terlalu panjang (~1000 token)
    truncated = truncate_tool_output(content_text, max_tokens=1000)

    # Bungkus hasil ke tag untrusted universal
    return wrap_untrusted_content(
        truncated,
        tag_name="untrusted_external_content",
        source=f"read_file:{target.name}"
    )
