from pathlib import Path
from typing import Optional, Callable

from enki.tools.file_tools import execute_read_file
from enki.tools.shell_tools import (
    execute_git_command,
    execute_python_version,
    execute_run_unittests,
    execute_list_dir,
    GIT_VALID_ACTIONS
)
from enki.memory.database import MemoryDatabase
from enki.memory.profile_manager import ProfileManager
from enki.memory.recall import build_fts_query, format_recall_block
from enki.memory.reflector import contains_secret

EXPLICIT_COMMANDS = {
    "/baca",
    "/dir",
    "/git",
    "/test",
    "/versi",
    "/ingat",
    "/simpan"
}

def execute_explicit_command(
    cmd: str,
    raw_args: str,
    workspace_root: Path,
    db: MemoryDatabase,
    session_id: str,
    profile_mgr: ProfileManager,
    confirm_func: Optional[Callable[[str], bool]] = None,
    audit_log_path: Optional[Path] = None
) -> str:
    """Mengeksekusi perintah eksplisit Tahap 3B.
    Mengembalikan teks output mentah string yang dihasilkan fungsi eksekutor tool.
    """
    clean_cmd = (cmd or "").strip().lower()
    args_str = (raw_args or "").strip()

    if clean_cmd == "/baca":
        if not args_str:
            return "[Error /baca] Format salah: path file wajib disediakan. Gunakan: /baca <path_file>"
        res = execute_read_file(args_str, workspace_root=workspace_root)
        return res.output

    elif clean_cmd == "/dir":
        rel_path = args_str if args_str else "."
        res = execute_list_dir(rel_path=rel_path, workspace_root=workspace_root, audit_log_path=audit_log_path)
        return res.output

    elif clean_cmd == "/git":
        action = args_str.lower()
        if not action or action not in GIT_VALID_ACTIONS:
            return "[Error /git] Format salah: aksi git tidak valid. Aksi yang diizinkan: status, log, diff."
        res = execute_git_command(
            action=action,
            workspace_root=workspace_root,
            confirm_func=confirm_func,
            audit_log_path=audit_log_path
        )
        return res.output

    elif clean_cmd == "/test":
        res = execute_run_unittests(
            workspace_root=workspace_root,
            confirm_func=confirm_func,
            audit_log_path=audit_log_path
        )
        return res.output

    elif clean_cmd == "/versi":
        res = execute_python_version(
            workspace_root=workspace_root,
            confirm_func=confirm_func,
            audit_log_path=audit_log_path
        )
        return res.output

    elif clean_cmd == "/ingat":
        if not args_str:
            return "[Error /ingat] Format salah: kata kunci pencarian wajib disediakan. Gunakan: /ingat <kata_kunci>"
        fts_query = build_fts_query(args_str) or args_str
        snippets = db.search_fts(fts_query, exclude_session_id=session_id, limit=3)
        if not snippets:
            return "[Tool Result: search_memory] Tidak ditemukan catatan yang cocok di database memori."
        block = format_recall_block(snippets, max_tokens=1000)
        return block or "[Tool Result: search_memory] Hasil kosong."

    elif clean_cmd == "/simpan":
        if "|" not in args_str:
            return "[Error /simpan] Format salah: gunakan /simpan <kunci> | <isi fakta>"
        kunci, sep, isi = args_str.partition("|")
        clean_key = kunci.strip()
        clean_val = isi.strip()
        if not clean_key or not clean_val:
            return "[Error /simpan] Format salah: kunci dan isi fakta tidak boleh kosong. Gunakan /simpan <kunci> | <isi fakta>"

        # Cek kunci duplikat
        existing_facts = profile_mgr.get_all_facts()
        if clean_key in existing_facts:
            return f"[Error /simpan] Kunci '{clean_key}' sudah ada di profil. Gunakan /memory delete {clean_key} terlebih dahulu jika ingin menggantinya."

        # Cek pola rahasia deterministik
        if contains_secret(clean_key) or contains_secret(clean_val):
            return "[Error /simpan] Gagal menyimpan: Terdeteksi pola data rahasia (API key / token / password)."

        ok, msg = profile_mgr.add_fact(clean_key, clean_val)
        if ok:
            return f"[Sukses /simpan] {msg}"
        return f"[Error /simpan] {msg}"

    return f"Perintah eksplisit tidak dikenal: {cmd}"

def handle_explicit_command(
    cmd: str,
    raw_args: str,
    workspace_root: Path,
    db: MemoryDatabase,
    session_id: str,
    profile_mgr: ProfileManager,
    confirm_func: Optional[Callable[[str], bool]] = None,
    audit_log_path: Optional[Path] = None,
    output_func: Callable[[str], None] = print
) -> bool:
    """Handler pembungkus untuk dipanggil dari CLI console loop."""
    clean_cmd = (cmd or "").strip().lower()
    if clean_cmd not in EXPLICIT_COMMANDS:
        return False

    out = execute_explicit_command(
        cmd=clean_cmd,
        raw_args=raw_args,
        workspace_root=workspace_root,
        db=db,
        session_id=session_id,
        profile_mgr=profile_mgr,
        confirm_func=confirm_func,
        audit_log_path=audit_log_path
    )
    output_func(out)
    return True
