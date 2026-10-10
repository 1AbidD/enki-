import json
import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional
from enki.core.prompt_assembler import estimate_tokens_local

from enki.config import get_data_dir

def get_default_memory_dir() -> Path:
    return get_data_dir() / "memory"

MAX_PROFILE_TOKENS = 500

class ProfileManager:
    def __init__(self, memory_dir: Optional[Path] = None):
        self.memory_dir = Path(memory_dir) if memory_dir else get_default_memory_dir()
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.profile_file = self.memory_dir / "profile.json"
        self.audit_log_file = self.memory_dir / "memory_audit.log"
        self._ensure_files()

    def _ensure_files(self):
        if not self.profile_file.exists():
            self._save_raw({"facts": {}})
        if not self.audit_log_file.exists():
            self.audit_log_file.touch()

    def _load_raw(self) -> Dict[str, Any]:
        try:
            with open(self.profile_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"facts": {}}

    def _save_raw(self, data: Dict[str, Any]):
        with open(self.profile_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def _log_audit(self, action: str, key: str, detail: str):
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{now_str}] {action.upper()}: key='{key}' | {detail}\n"
        with open(self.audit_log_file, "a", encoding="utf-8") as f:
            f.write(log_entry)

    def get_all_facts(self) -> Dict[str, str]:
        """Mengambil seluruh dictionary fakta aktif."""
        data = self._load_raw()
        return data.get("facts", {})

    def get_profile_text(self) -> str:
        """Merender fakta profil menjadi teks terformat untuk injeksi ke prompt."""
        facts = self.get_all_facts()
        if not facts:
            return "[PROFIL PENGGUNA TERVERIFIKASI - ENKI]\n(Belum ada fakta profil yang tersimpan)"

        lines = ["[PROFIL PENGGUNA TERVERIFIKASI - ENKI]"]
        for k, v in facts.items():
            lines.append(f"- {k}: {v}")
        return "\n".join(lines)

    def count_tokens(self) -> int:
        """Menghitung perkiraan jumlah token dari teks profil aktif."""
        return estimate_tokens_local(self.get_profile_text())

    def add_fact(self, key: str, value: str) -> Tuple[bool, str]:
        """Menambahkan fakta baru ke profil dengan validasi batas 500 token."""
        data = self._load_raw()
        facts = data.get("facts", {})
        
        # Simulasikan jika ditambahkan
        simulated_facts = facts.copy()
        simulated_facts[key] = value

        simulated_text = "[PROFIL PENGGUNA TERVERIFIKASI - ENKI]\n" + "\n".join(f"- {k}: {v}" for k, v in simulated_facts.items())
        est_tokens = estimate_tokens_local(simulated_text)

        if est_tokens > MAX_PROFILE_TOKENS:
            return False, f"Ditolak: Penambahan fakta ini menyebabkan profil berukuran {est_tokens} token (Batas maksimal: {MAX_PROFILE_TOKENS} token)."

        is_update = key in facts
        old_val = facts.get(key, "")
        facts[key] = value
        data["facts"] = facts
        self._save_raw(data)

        action = "UPDATE" if is_update else "ADD"
        detail = f"old='{old_val}' -> new='{value}'" if is_update else f"val='{value}'"
        self._log_audit(action, key, detail)

        return True, f"Fakta '{key}' berhasil disimpan ({est_tokens}/{MAX_PROFILE_TOKENS} token)."

    def delete_fact(self, key: str) -> Tuple[bool, str]:
        """Menghapus fakta dari profil."""
        data = self._load_raw()
        facts = data.get("facts", {})

        if key not in facts:
            return False, f"Fakta dengan kunci '{key}' tidak ditemukan di profil."

        old_val = facts.pop(key)
        data["facts"] = facts
        self._save_raw(data)

        self._log_audit("DELETE", key, f"dihapus (sebelumnya: '{old_val}')")
        return True, f"Fakta '{key}' berhasil dihapus dari profil."

    def edit_fact(self, key: str, new_value: str) -> Tuple[bool, str]:
        """Mengubah isi fakta yang sudah ada."""
        return self.add_fact(key, new_value)

    def get_audit_log(self, limit: int = 20) -> List[str]:
        """Mengambil riwayat log perubahan profil."""
        if not self.audit_log_file.exists():
            return []
        with open(self.audit_log_file, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        return lines[-limit:]
