import re
import json
import datetime
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

from enki.core.ollama_client import OllamaClient
from enki.memory.database import MemoryDatabase

DEFAULT_PROPOSALS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "memory" / "pending_facts.json"

# Pola rahasia yang wajib ditolak otomatis
SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*["\']?[a-zA-Z0-9_\-]{6,}'),
    re.compile(r'sk-[a-zA-Z0-9]{20,}'),
    re.compile(r'ghp_[a-zA-Z0-9]{20,}'),
    re.compile(r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}'),
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')
]

def contains_secret(text: str) -> bool:
    """Mendeteksi apakah teks mengandung token rahasia, password, atau API key."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)

class FactProposal(BaseModel):
    key: str = Field(description="Nama kunci fakta terstruktur, misal: 'bahasa_pemrograman_favorit'")
    value: str = Field(description="Nilai fakta pengguna, misal: 'Python dan Rust'")
    source_quote: str = Field(description="Kutipan verbatim persis dari pesan user")

class ReflectionOutput(BaseModel):
    proposals: List[FactProposal] = Field(default_factory=list)

REFLECTION_SYSTEM_PROMPT = """Tugas Anda adalah mengekstrak fakta atau preferensi permanen mengenai PENGGUNA yang dinyatakan secara nyata dalam percakapan.

ATURAN KETAT EKSTRAKSI:
1. HANYA ekstrak fakta atau preferensi pribadi tentang DIRI PENGGUNA SENDIRI (misal: nama, pekerjaan/profesi, tech stack favorit, kebiasaan kerja).
2. JANGAN ekstrak kalimat hipotetis / pengandaian ("kalau aku jadi...", "seandainya...").
3. JANGAN ekstrak fakta tentang orang lain ("temanku suka...", "rekan kerjaku...").
4. JANGAN ekstrak candaan, sarkasme, atau obrolan situasional ("aku alien", "lagi laper nih", "hari ini hujan").
5. JANGAN ekstrak data rahasia (password, API key, token).
6. 'source_quote' WAJIB merupakan kutipan teks VERBATIM (persis sama karakter demi karakter) dari pesan pengguna.
7. Jika TIDAK ADA fakta pengguna yang valid atau Anda ragu-ragu, kembalikan daftar kosong: {"proposals": []}."""

class ReflectorDaemon:
    def __init__(self, proposals_file: Path = DEFAULT_PROPOSALS_FILE):
        self.proposals_file = Path(proposals_file)
        self.proposals_file.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_file()

    def _ensure_file(self):
        if not self.proposals_file.exists():
            with open(self.proposals_file, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)

    def load_proposals(self) -> List[Dict[str, Any]]:
        try:
            with open(self.proposals_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []

    def save_proposals(self, proposals: List[Dict[str, Any]]):
        with open(self.proposals_file, "w", encoding="utf-8") as f:
            json.dump(proposals, f, indent=2, ensure_ascii=False)

    def get_pending_proposals(self) -> List[Dict[str, Any]]:
        return [p for p in self.load_proposals() if p.get("status") == "PENDING"]

    def update_proposal_status(self, proposal_id: int, status: str, new_value: Optional[str] = None) -> Optional[Dict[str, Any]]:
        proposals = self.load_proposals()
        target = None
        for p in proposals:
            if p["id"] == proposal_id:
                p["status"] = status
                if new_value:
                    p["value"] = new_value
                p["resolved_at"] = datetime.datetime.now().isoformat()
                target = p.copy()
                break
        if target:
            self.save_proposals(proposals)
        return target

    def validate_and_record_proposal(
        self,
        proposal: FactProposal,
        user_turns: List[str],
        session_id: str
    ) -> Tuple[bool, str]:
        """Validasi kode deterministik atas proposal yang dihasilkan LLM."""
        # 1. Tolak pola rahasia
        if contains_secret(proposal.key) or contains_secret(proposal.value) or contains_secret(proposal.source_quote):
            return False, "Ditolak otomatis: Terdeteksi pola data rahasia (API key / password)."

        # 2. Verifikasi quote verbatim di pesan user
        quote = proposal.source_quote.strip()
        if not quote:
            return False, "Ditolak: source_quote kosong."

        verbatim_found = any(quote in turn for turn in user_turns)
        if not verbatim_found:
            return False, f"Ditolak: source_quote '{quote[:40]}...' tidak ditemukan secara verbatim di pesan user."

        # Simpan ke pending_proposals.json
        proposals = self.load_proposals()
        new_id = len(proposals) + 1
        record = {
            "id": new_id,
            "session_id": session_id,
            "key": proposal.key.strip(),
            "value": proposal.value.strip(),
            "source_quote": quote,
            "status": "PENDING",
            "created_at": datetime.datetime.now().isoformat()
        }
        proposals.append(record)
        self.save_proposals(proposals)
        return True, f"Proposal #{new_id} berhasil masuk antrean."

    def reflect_session(
        self,
        session_id: str,
        db: MemoryDatabase,
        ollama_client: OllamaClient,
        model: str = "qwen2.5:3b"
    ) -> List[Dict[str, Any]]:
        """Mengeksekusi proses refleksi untuk satu sesi tertentu menggunakan model chat."""
        user_turns = db.get_user_turns(session_id)
        if not user_turns:
            db.mark_session_reflected(session_id)
            return []

        # Gabungkan transkrip pesan user saja
        transcript_text = "\n".join([f"- User: {turn}" for turn in user_turns])

        prompt_content = (
            f"{REFLECTION_SYSTEM_PROMPT}\n\n"
            f"TRANSKRIP PESAN USER DARI SESI:\n"
            f"{transcript_text}\n\n"
            f"Ekstrak fakta baru ke dalam JSON skema yang ditentukan."
        )

        schema = ReflectionOutput.model_json_schema()

        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt_content}
            ],
            "format": schema,
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_ctx": 4096
            }
        }

        accepted_proposals = []
        output_obj = None
        for attempt in range(2):
            try:
                req = urllib.request.Request(
                    f"{ollama_client.base_url}/api/chat",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                raw_json_str = data.get("message", {}).get("content", "{}")
                output_obj = ReflectionOutput.model_validate_json(raw_json_str)
                break
            except Exception as e:
                if attempt == 0:
                    continue  # Retry 1x
                else:
                    print(f"[Reflector Error] Gagal memvalidasi skema refleksi sesi {session_id} setelah retry: {e}")
                    output_obj = None

        if output_obj:
            for prop in output_obj.proposals:
                ok, msg = self.validate_and_record_proposal(prop, user_turns, session_id)
                if ok:
                    accepted_proposals.append(prop.model_dump())

        db.mark_session_reflected(session_id)
        return accepted_proposals
