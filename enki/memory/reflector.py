import re
import json
import datetime
import urllib.request
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

from enki.core.ollama_client import OllamaClient
from enki.config import get_data_dir

def get_default_proposals_file() -> Path:
    return get_data_dir() / "memory" / "pending_facts.json"

# Pola rahasia yang wajib ditolak otomatis
SECRET_PATTERNS = [
    re.compile(r'(?i)(api[_-]?key|secret|token|password|passwd|pwd)\s*[:=]\s*["\']?[a-zA-Z0-9_\-]{6,}'),
    re.compile(r'sk-[a-zA-Z0-9]{20,}'),
    re.compile(r'ghp_[a-zA-Z0-9]{20,}'),
    re.compile(r'eyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}'),
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')
]

# Pola pengandaian / hipotesis yang wajib dibuang otomatis oleh kode
HYPOTHESIS_PATTERNS = [
    re.compile(r'\b(seandainya|andaikan|andai|misalkan|seumpama|if\s+i\s+were)\b', re.IGNORECASE),
    re.compile(r'\b(kalau|kalo|klo|jika|sekiranya)\s+(aku|saya|gw|gue|ku)\s+j(adi|d)\b', re.IGNORECASE),
]

def contains_secret(text: str) -> bool:
    """Mendeteksi apakah teks mengandung token rahasia, password, atau API key."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)

def contains_hypothesis(text: str) -> bool:
    """Mendeteksi apakah teks mengandung penanda pengandaian / hipotetis."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in HYPOTHESIS_PATTERNS)

class FactProposal(BaseModel):
    key: str = Field(description="Nama kunci fakta terstruktur, misal: 'bahasa_pemrograman_favorit'")
    value: str = Field(description="Nilai fakta pengguna, misal: 'Python dan Rust'")
    source_quote: str = Field(description="Kutipan verbatim persis dari pesan user")

class ReflectionOutput(BaseModel):
    proposals: List[FactProposal] = Field(default_factory=list)

REFLECTION_SYSTEM_PROMPT = """Tugas Anda adalah mengekstrak fakta, preferensi, atau proyek pribadi mengenai PENGGUNA yang dinyatakan dalam percakapan.

PANDUAN KATEGORI YANG WAJIB DIEKSTRAK:
1. Preferensi Pengguna: Permintaan gaya jawaban, format kode, preferensi bahasa, atau nada bicara (misal: "sertakan type hint", "jangan kaku/santai aja", "prioritaskan hemat RAM", "jawab bahasa Indonesia").
2. Fakta Diri & Latar Belakang: Nama, domisili, profesi, umur, latar belakang pendidikan, spesifikasi laptop/hardware, dan kebiasaan kerja.
3. Proyek Aktif: Aplikasi atau tugas yang sedang dikerjakan pengguna (misal: microservice Go, skripsi citra medis, sistem kasir toko, aplikasi Flutter).

ATURAN KETAT:
1. Periksa SELURUH pesan pengguna dari awal sampai akhir sesi secara teliti, karena fakta atau preferensi pengguna bisa terselip di pesan mana saja.
2. HANYA ekstrak tentang DIRI PENGGUNA. JANGAN ekstrak tentang orang lain ("temanku...", "bosku...").
3. JANGAN ekstrak kalimat pengandaian/hipotetis ("kalau aku jadi...", "seandainya...").
4. JANGAN ekstrak keluhan situasional sementara ("hari ini panas", "wifi lemot", "tugas numpuk").
5. JANGAN ekstrak candaan/sarkasme atau data rahasia (password, token, API key).
6. 'source_quote' WAJIB disalin dari kalimat pengguna yang menjadi dasar fakta tersebut.
7. Jika transkrip HANYA berisi obrolan biasa, pertanyaan teknis umum, atau tidak ada fakta/preferensi pengguna, kembalikan daftar kosong: {"proposals": []}.

CONTOH POSITIF:
- User: "Tolong tiap kali kasih kode, selalu sertakan type hint ya."
  Proposal: {"key": "preferensi_kode", "value": "Selalu menyertakan type hint dalam kode", "source_quote": "Tolong tiap kali kasih kode, selalu sertakan type hint ya."}
- User: "jgn pake bhs kaku bgt ya, santai aja."
  Proposal: {"key": "preferensi_gaya_bahasa", "value": "Gaya bahasa santai, tidak kaku", "source_quote": "jgn pake bhs kaku bgt ya, santai aja."}
- User: "Aku berdomisili di Surabaya dan kerja sebagai data analyst."
  Proposal: {"key": "domisili_dan_profesi", "value": "Domisili: Surabaya, Profesi: data analyst", "source_quote": "Aku berdomisili di Surabaya dan kerja sebagai data analyst."}
- User: "lg ngerjain skripsi ttg klasifikasi citra medis nih."
  Proposal: {"key": "proyek_skripsi", "value": "Sedang mengerjakan skripsi klasifikasi citra medis", "source_quote": "lg ngerjain skripsi ttg klasifikasi citra medis nih."}
- User: "Spesifikasi laptopku RAM 16GB dengan GPU RTX 3050."
  Proposal: {"key": "spesifikasi_laptop", "value": "Laptop dengan RAM 16GB dan GPU RTX 3050", "source_quote": "Spesifikasi laptopku RAM 16GB dengan GPU RTX 3050."}
- User: "Aku punya kebiasaan kerja di malam hari."
  Proposal: {"key": "kebiasaan_kerja", "value": "Terbiasa bekerja di malam hari", "source_quote": "Aku punya kebiasaan kerja di malam hari."}
- Transkrip Multi-Pesan:
  - User: "Mau tanya dong."
  - User: "Oiya kalau jawab teknis prioritaskan yang hemat RAM ya."
  - User: "Bagaimana cara optimasi SQL?"
  Proposal: {"key": "preferensi_solusi", "value": "Prioritaskan solusi yang hemat RAM", "source_quote": "Oiya kalau jawab teknis prioritaskan yang hemat RAM ya."}

CONTOH NEGATIF (Kembalikan {"proposals": []}):
- User: "Halo, menurutmu AI bakal gantiin programmer gak?" -> {"proposals": []}
- User: "Bagaimana cara kerja shallow copy di JS?" -> {"proposals": []}
- User: "Temanku si Budi baru beli iPhone." -> {"proposals": []}
- User: "Kalau aku jadi presiden bakal gratisin internet." -> {"proposals": []}
- User: "Wifi kosan lemot banget sore ini." -> {"proposals": []}
- User: "Password databaseku admin123!" -> {"proposals": []}"""

class ReflectorDaemon:
    def __init__(self, proposals_file: Optional[Path] = None, stats_file: Optional[Path] = None):
        self.proposals_file = Path(proposals_file) if proposals_file else get_default_proposals_file()
        self.proposals_file.parent.mkdir(parents=True, exist_ok=True)
        self.stats_file = Path(stats_file) if stats_file else self.proposals_file.parent / "memory_stats.json"
        self._ensure_file()

    def _ensure_file(self):
        if not self.proposals_file.exists():
            with open(self.proposals_file, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)

    def _load_stats(self) -> Dict[str, int]:
        default_stats = {
            "approved": 0,
            "rejected": 0,
            "edited": 0,
            "discarded_secret": 0,
            "discarded_hypothesis": 0,
            "discarded_invalid_quote": 0,
            "quote_recovery_count": 0
        }
        if not self.stats_file.exists():
            return default_stats
        try:
            with open(self.stats_file, "r", encoding="utf-8") as f:
                d = json.load(f)
                for k, v in default_stats.items():
                    d.setdefault(k, v)
                return d
        except Exception:
            return default_stats

    def _save_stats(self, stats: Dict[str, int]):
        with open(self.stats_file, "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)

    def get_stats(self) -> Dict[str, int]:
        stats = self._load_stats()
        stats["pending"] = len(self.get_pending_proposals())
        stats["discarded_total"] = (
            stats["discarded_secret"] +
            stats["discarded_hypothesis"] +
            stats["discarded_invalid_quote"]
        )
        return stats

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
            stats = self._load_stats()
            if status == "APPROVED":
                stats["approved"] += 1
            elif status == "REJECTED":
                stats["rejected"] += 1
            if new_value:
                stats["edited"] += 1
            self._save_stats(stats)
        return target

    def validate_and_record_proposal(
        self,
        proposal: FactProposal,
        user_turns: List[str],
        session_id: str
    ) -> Tuple[bool, str]:
        """Validasi kode deterministik atas proposal yang dihasilkan LLM."""
        # 1a. Tolak pola rahasia
        if contains_secret(proposal.key) or contains_secret(proposal.value) or contains_secret(proposal.source_quote):
            stats = self._load_stats()
            stats["discarded_secret"] += 1
            self._save_stats(stats)
            return False, "Ditolak otomatis: Terdeteksi pola data rahasia (API key / password)."

        # 1b. Tolak penanda kalimat pengandaian / hipotetis
        if contains_hypothesis(proposal.key) or contains_hypothesis(proposal.value) or contains_hypothesis(proposal.source_quote):
            stats = self._load_stats()
            stats["discarded_hypothesis"] += 1
            self._save_stats(stats)
            return False, "Ditolak otomatis: Terdeteksi penanda kalimat pengandaian / hipotetis."

        # 2. Verifikasi quote verbatim di pesan user (case-insensitive substring check & quote recovery)
        quote = proposal.source_quote.strip()
        if not quote:
            return False, "Ditolak: source_quote kosong."

        verbatim_found = False
        exact_quote = quote
        quote_lower = quote.lower()
        for turn in user_turns:
            idx = turn.lower().find(quote_lower)
            if idx != -1:
                # Quote recovery HANYA mengambil substring yang persis ada di pesan user
                exact_quote = turn[idx : idx + len(quote)]
                verbatim_found = True
                break

        if not verbatim_found:
            stats = self._load_stats()
            stats["discarded_invalid_quote"] += 1
            self._save_stats(stats)
            return False, f"Ditolak: source_quote '{quote[:40]}...' tidak ditemukan secara verbatim di pesan user."

        if exact_quote != quote:
            stats = self._load_stats()
            stats["quote_recovery_count"] += 1
            self._save_stats(stats)

        quote = exact_quote

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
