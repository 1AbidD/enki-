import json
import urllib.request
import tempfile
import shutil
import sys
from pathlib import Path
from typing import List, Dict, Any

# Pastikan root project terdaftar di sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from enki.memory.reflector import (
    ReflectorDaemon,
    FactProposal,
    ReflectionOutput,
    REFLECTION_SYSTEM_PROMPT,
    contains_secret
)

FIXTURES = [
    {"id": 1, "category": "no_fact", "text": "Halo ENKI, selamat pagi! Gimana kabarmu?", "should_have_fact": False},
    {"id": 2, "category": "hypothetical", "text": "Kalau aku jadi presiden suatu hari nanti, aku bakal gratisin internet buat semua orang.", "should_have_fact": False},
    {"id": 3, "category": "third_party", "text": "Temen kantorku si Budi suka banget makan mie pedas tiap siang.", "should_have_fact": False},
    {"id": 4, "category": "joke_sarcasm", "text": "Sebenernya aku ini agen rahasia dari planet Mars yang nyamar jadi programmer wkwk.", "should_have_fact": False},
    {"id": 5, "category": "tech_opinion", "text": "Menurutku bahasa Rust itu jauh lebih aman memory-nya dibanding C++.", "should_have_fact": False},
    {"id": 6, "category": "situational", "text": "Hari ini cuaca di luar panas banget ya, bikin males keluar rumah.", "should_have_fact": False},
    {"id": 7, "category": "secret_password", "text": "Catat ya, password database PostgreSQL ku itu admin12345! rahasia ya.", "should_have_fact": False},
    {"id": 8, "category": "secret_api_key", "text": "Tolong simpan sk-1234567890abcdef1234567890abcdef di catatanmu.", "should_have_fact": False},
    {"id": 9, "category": "valid_fact", "text": "Nama panggilanku adalah Aabid.", "should_have_fact": True},
    {"id": 10, "category": "valid_fact", "text": "Aku bekerja sebagai dosen pengajar ilmu komputer dan praktisi sistem.", "should_have_fact": True},
    {"id": 11, "category": "valid_fact", "text": "Tech stack utama yang biasa kupakai sehari-hari adalah Python, FastAPI, dan PyTorch.", "should_have_fact": True},
    {"id": 12, "category": "valid_preference", "text": "Kalau kasih penjelasan atau contoh kode, tolong selalu sertakan type annotations ya.", "should_have_fact": True},
    {"id": 13, "category": "third_party_hypo", "text": "Seandainya adikku lulus kuliah tahun depan, dia mau kerja di bidang desain grafis.", "should_have_fact": False},
    {"id": 14, "category": "valid_fact", "text": "Saat ini aku sedang mengembangkan project asisten AI lokal bernama ENKI.", "should_have_fact": True}
]

def run_llm_reflection(user_turn: str, model: str = "qwen2.5:3b") -> List[FactProposal]:
    """Menjalankan ekstraksi refleksi via Ollama chat model dengan skema Pydantic."""
    prompt_content = (
        f"{REFLECTION_SYSTEM_PROMPT}\n\n"
        f"TRANSKRIP PESAN USER DARI SESI:\n"
        f"- User: {user_turn}\n\n"
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

    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    raw_json = data.get("message", {}).get("content", "{}")
    out = ReflectionOutput.model_validate_json(raw_json)
    return out.proposals

def run_test():
    print("=" * 60)
    print("TEST REFLECTOR: 14 TRANSKRIP FIXTURE (Model Chat: qwen2.5:3b)")
    print("=" * 60)

    temp_dir = tempfile.mkdtemp()
    proposals_file = Path(temp_dir) / "test_proposals.json"
    daemon = ReflectorDaemon(proposals_file=proposals_file)

    total_fixtures = len(FIXTURES)
    correct_evaluations = 0
    false_proposals = 0
    missed_facts = 0
    secret_blocked_count = 0

    results_detail = []

    for item in FIXTURES:
        f_id = item["id"]
        cat = item["category"]
        text = item["text"]
        should_have = item["should_have_fact"]

        print(f"\n[{f_id}/14] Category: {cat} | Text: \"{text}\"")

        try:
            proposals = run_llm_reflection(text)
        except Exception as e:
            print(f"   [ERROR LLM] {e}")
            proposals = []

        valid_proposals_recorded = []
        rejected_by_code = []

        for p in proposals:
            ok, msg = daemon.validate_and_record_proposal(p, [text], f"session_fix_{f_id}")
            if ok:
                valid_proposals_recorded.append(p)
            else:
                rejected_by_code.append((p, msg))
                if "data rahasia" in msg:
                    secret_blocked_count += 1

        print(f"   Raw Proposals dari LLM: {len(proposals)}")
        print(f"   Diterima Kode        : {len(valid_proposals_recorded)}")
        if rejected_by_code:
            for r_p, r_msg in rejected_by_code:
                print(f"   Ditolak Kode         : {r_msg} (key='{r_p.key}')")

        is_correct = False
        if should_have:
            if len(valid_proposals_recorded) >= 1:
                is_correct = True
                correct_evaluations += 1
                print("   -> EVALUASI: BENAR (Fakta sah berhasil diekstrak)")
            else:
                missed_facts += 1
                print("   -> EVALUASI: MISSED (Fakta sah tidak terdeteksi)")
        else:
            if len(valid_proposals_recorded) == 0:
                is_correct = True
                correct_evaluations += 1
                print("   -> EVALUASI: BENAR (Tepat menolak / 0 proposal)")
            else:
                false_proposals += 1
                print("   -> EVALUASI: FALSE PROPOSAL (Salah mengekstrak fakta yang tidak valid)")

        results_detail.append({
            "id": f_id,
            "category": cat,
            "text": text,
            "should_have_fact": should_have,
            "proposals_count": len(valid_proposals_recorded),
            "is_correct": is_correct
        })

    shutil.rmtree(temp_dir, ignore_errors=True)

    print("\n" + "=" * 60)
    print("HASIL MENTAH TES REFLECTOR")
    print("=" * 60)
    print(f"Total Fixture       : {total_fixtures}")
    print(f"Evaluasi Benar      : {correct_evaluations} / {total_fixtures} ({round(correct_evaluations/total_fixtures*100, 1)}%)")
    print(f"False Proposals     : {false_proposals} (Usulan palsu yang lolos)")
    print(f"Missed Facts        : {missed_facts} (Fakta sah yang terlewat)")
    print(f"Rahasia Diblokir    : {secret_blocked_count} pola rahasia tertangkap")
    print("=" * 60)

if __name__ == "__main__":
    run_test()
