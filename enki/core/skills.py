"""Subsistem Pengelolaan Skill ENKI (Tahap 7).

Menyediakan:
1. Validasi nama skill (regex ^[a-z0-9][a-z0-9-]{0,39}$) dan isolasi path traversal / symlink.
2. Pemuatan berkas SKILL.md dengan batas ukuran (maksimal 8 KB) dan estimasi token (maksimal 600 token).
3. Ekstraksi deskripsi 1 baris dari frontmatter YAML atau baris non-kosong pertama.
4. Manajer skill in-memory non-persisten (default OFF saat startup, single active skill).
5. Pembungkus blok <active_skill name="..."> dengan sanitasi konten.
"""

import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from enki.core.prompt_assembler import estimate_tokens_local
from enki.core.sanitizer import sanitize_untrusted_text

SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
MAX_FILE_BYTES = 8192  # 8 KB
MAX_SKILL_TOKENS = 600


@dataclass
class SkillInfo:
    """Informasi metadata dan isi skill aktif."""
    name: str
    content: str
    tokens: int
    description: str


def get_default_skills_dir() -> Path:
    """Mengembalikan path direktori skills default di root proyek."""
    return Path(__file__).resolve().parent.parent.parent / "skills"


def validate_skill_name(name: str) -> Tuple[bool, Optional[str]]:
    """Memvalidasi nama skill terhadap pola regex dan bahaya traversal."""
    if not name or not isinstance(name, str):
        return False, "Nama skill tidak boleh kosong."
    if ".." in name or "/" in name or "\\" in name:
        return False, "Path traversal dilarang pada nama skill."
    if not SKILL_NAME_PATTERN.match(name):
        return (
            False,
            f"Nama skill '{name}' tidak valid. Nama wajib diawali huruf kecil atau angka, "
            "hanya memuat karakter [a-z0-9-], dan memiliki panjang 1-40 karakter.",
        )
    return True, None


def extract_skill_description(content: str) -> str:
    """Mengekstrak deskripsi 1 baris dari frontmatter YAML atau baris non-kosong pertama."""
    if not content:
        return "(tanpa deskripsi)"

    # 1. Cek frontmatter YAML: --- \n description: ... \n ---
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            fm = parts[1]
            match = re.search(r"^description:\s*[\"']?(.*?)[\"']?\s*$", fm, re.MULTILINE)
            if match and match.group(1).strip():
                return match.group(1).strip()
            # Bila tidak ada description di frontmatter, cari baris pertama di body
            body = parts[2]
            for line in body.splitlines():
                clean = line.strip().lstrip("#").strip()
                if clean:
                    return clean[:120]

    # 2. Ambil baris pertama non-kosong
    for line in content.splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean and not clean.startswith("---"):
            return clean[:120]

    return "(tanpa deskripsi)"


def load_skill_file(name: str, skills_dir: Optional[Path] = None) -> Tuple[bool, Optional[SkillInfo], Optional[str]]:
    """Memuat dan memvalidasi berkas SKILL.md dari folder skills."""
    valid, err_name = validate_skill_name(name)
    if not valid:
        return False, None, err_name

    root_skills = (skills_dir or get_default_skills_dir()).resolve()
    target_dir = root_skills / name
    target_file = target_dir / "SKILL.md"

    # Periksa traversal via resolve
    try:
        resolved_file = target_file.resolve()
        if not str(resolved_file).startswith(str(root_skills)):
            return False, None, "Akses di luar folder skills ditolak."
    except Exception as e:
        return False, None, f"Validasi path gagal: {e}"

    # Periksa symlink (folder maupun file)
    if target_dir.is_symlink() or target_file.is_symlink():
        return False, None, "Symlink tidak diizinkan untuk direktori atau berkas skill."

    if not target_file.is_file():
        return False, None, f"Skill '{name}' tidak ditemukan (berkas SKILL.md tidak ada di {target_dir.name})."

    # Periksa batas ukuran berkas <= 8 KB
    try:
        size = target_file.stat().st_size
        if size > MAX_FILE_BYTES:
            return False, None, f"Ukuran berkas SKILL.md ({size} byte) melebihi batas maksimal {MAX_FILE_BYTES} byte (8 KB)."
    except Exception as e:
        return False, None, f"Gagal membaca atribut berkas: {e}"

    # Baca isi teks UTF-8
    try:
        content = target_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False, None, "Berkas SKILL.md bukan teks UTF-8 yang valid."
    except Exception as e:
        return False, None, f"Gagal membaca isi berkas SKILL.md: {e}"

    # Estimasi token lokal
    tokens = estimate_tokens_local(content)
    if tokens > MAX_SKILL_TOKENS:
        return False, None, f"Ukuran skill ({tokens} token) melebihi batas maksimal {MAX_SKILL_TOKENS} token."

    desc = extract_skill_description(content)
    info = SkillInfo(name=name, content=content, tokens=tokens, description=desc)
    return True, info, None


def list_available_skills(skills_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Memindai daftar skill yang valid di folder skills."""
    root_skills = (skills_dir or get_default_skills_dir()).resolve()
    if not root_skills.is_dir():
        return []

    results: List[Dict[str, Any]] = []
    try:
        entries = sorted(root_skills.iterdir(), key=lambda p: p.name)
    except Exception:
        return []

    for entry in entries:
        if not entry.is_dir():
            continue
        if entry.is_symlink():
            continue
        valid, _ = validate_skill_name(entry.name)
        if not valid:
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file() or skill_file.is_symlink():
            continue

        try:
            if skill_file.stat().st_size > MAX_FILE_BYTES:
                continue
            text = skill_file.read_text(encoding="utf-8")
            tokens = estimate_tokens_local(text)
            if tokens > MAX_SKILL_TOKENS:
                continue
            desc = extract_skill_description(text)
            results.append({
                "name": entry.name,
                "description": desc,
                "tokens": tokens,
            })
        except Exception:
            continue

    return results


def format_active_skill_block(skill: Optional[SkillInfo]) -> str:
    """Membungkus isi skill aktif ke dalam tag isolasi sistem <active_skill name="...">."""
    if skill is None or not skill.content:
        return ""
    sanitized = sanitize_untrusted_text(skill.content)
    # Netralkan tag penutup palsu
    sanitized = sanitized.replace("</active_skill>", "&lt;/active_skill&gt;").replace("<active_skill", "&lt;active_skill")
    return f'<active_skill name="{skill.name}">\n{sanitized}\n</active_skill>'


class SkillManager:
    """Pengelola siklus hidup skill in-memory non-persisten per sesi ENKI."""

    def __init__(self, skills_dir: Optional[Path] = None):
        self.skills_dir = skills_dir or get_default_skills_dir()
        self.active_skill: Optional[SkillInfo] = None

    def get_active_skill(self) -> Optional[SkillInfo]:
        """Mengembalikan objek SkillInfo aktif saat ini atau None (zero-cost in-memory)."""
        return self.active_skill

    def get_active_skill_block(self) -> str:
        """Mengembalikan blok system prompt terformat untuk skill aktif atau string kosong."""
        return format_active_skill_block(self.active_skill)

    def activate(self, name: str) -> Tuple[bool, str, int]:
        """Mengaktifkan skill berdasarkan nama. Mengembalikan (success, message, token_count)."""
        ok, info, err = load_skill_file(name, skills_dir=self.skills_dir)
        if not ok:
            return False, f"[Error /skill] Gagal mengaktifkan skill: {err}", 0

        self.active_skill = info
        return True, f"[Skill] '{info.name}' aktif ({info.tokens} token).", info.tokens

    def deactivate(self) -> str:
        """Menonaktifkan skill aktif."""
        old_name = self.active_skill.name if self.active_skill else None
        self.active_skill = None
        if old_name:
            return f"[Skill] Skill '{old_name}' dinonaktifkan."
        return "[Skill] Tidak ada skill aktif yang perlu dinonaktifkan."

    def get_status_message(self) -> str:
        """Mengembalikan deskripsi status skill saat ini."""
        if self.active_skill:
            return f"[Skill] Aktif: '{self.active_skill.name}' ({self.active_skill.tokens} token) - {self.active_skill.description}"
        return "[Skill] Tidak ada skill aktif (mode normal)."

    def list_skills_message(self) -> str:
        """Mengembalikan daftar skill terformat untuk perintah /skill list."""
        available = list_available_skills(self.skills_dir)
        if not available:
            return "[Skill] Belum ada skill yang tersedia di folder skills/."

        lines = ["[Skill] Daftar skill yang tersedia:"]
        for s in available:
            active_marker = " * (AKTIF)" if self.active_skill and self.active_skill.name == s["name"] else ""
            lines.append(f"  - {s['name']}: {s['description']} ({s['tokens']} tok){active_marker}")
        lines.append("\nGunakan '/skill <nama>' untuk mengaktifkan, atau '/skill off' untuk mematikan.")
        return "\n".join(lines)
