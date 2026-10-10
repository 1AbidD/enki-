import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from enki.config import AppConfig
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.core.ollama_client import OllamaClient
from enki.memory.database import MemoryDatabase
from enki.memory.profile_manager import ProfileManager
from enki.memory.reflector import ReflectorDaemon, FactProposal

def run_cross_session_test():
    print("=" * 60)
    print("INTEGRATION TEST: MEMORY CROSS-SESSION UNIQUE TOKEN")
    print("=" * 60)

    config = AppConfig()
    client = OllamaClient(base_url=config.ollama_url)
    if not client.is_ready():
        print(f"[ERROR] Ollama tidak siap di {config.ollama_url}")
        sys.exit(1)

    db = MemoryDatabase()
    profile_mgr = ProfileManager()
    reflector = ReflectorDaemon()

    # 1. Buat token acak unik yang mustahil ada di pretraining LLM
    random_suffix = uuid.uuid4().hex[:8].upper()
    unique_token = f"TOKEN_KODEX_{random_suffix}_OMEGA"
    fact_key = "kode_rahasia_proyek_unik"
    fact_val = f"Kode rahasia proyek saya adalah {unique_token}"
    user_quote = f"Ingat ya, kode rahasia proyek saya adalah {unique_token}"

    print(f"\n[Langkah 1] Membuat sesi pertama (Sesi 1)...")
    s1_id = f"test_s1_{random_suffix}"
    session_1 = db.create_session(s1_id)
    db.add_turn(session_1, "user", user_quote)
    db.add_turn(session_1, "assistant", "Baik, saya catat kode rahasia Anda.")

    print(f"[Langkah 2] Menyimulasikan usulan fakta masuk ke antrean pending...")
    prop = FactProposal(key=fact_key, value=fact_val, source_quote=user_quote)
    ok, msg = reflector.validate_and_record_proposal(prop, [user_quote], session_1)
    if not ok:
        print(f"[ERROR Validasi Proposal] {msg}")
        sys.exit(1)

    pending_list = reflector.get_pending_proposals()
    target_p = [p for p in pending_list if p["key"] == fact_key][-1]
    proposal_id = target_p["id"]
    print(f"  -> Usulan masuk pending dengan ID #{proposal_id}")

    print(f"[Langkah 3] Menyetujui usulan fakta via approve (/memory approve {proposal_id})...")
    reflector.update_proposal_status(proposal_id, "APPROVED")
    saved_ok, saved_msg = profile_mgr.add_fact(fact_key, fact_val)
    if not saved_ok:
        print(f"[ERROR Simpan Profil] {saved_msg}")
        sys.exit(1)
    print(f"  -> {saved_msg}")

    # Verifikasi fakta ada di profile.json
    all_facts = profile_mgr.get_all_facts()
    assert fact_key in all_facts, f"Kunci {fact_key} harus ada di profile.json"
    assert unique_token in all_facts[fact_key], f"Token unik {unique_token} harus ada di nilai fakta"
    print(f"  -> Terverifikasi di profile.json: '{fact_key}' = '{all_facts[fact_key]}'")

    # 2. Mulai Sesi Baru (Sesi 2) tanpa history sesi 1
    print(f"\n[Langkah 4] Memulai Sesi Baru (Sesi 2 - Bersih)...")
    s2_id = f"test_s2_{random_suffix}"
    session_2 = db.create_session(s2_id)
    assembler = PromptAssembler(
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        profile=profile_mgr.get_profile_text()
    )

    query_user = "Sebutkan dengan persis apa kode rahasia proyek unik saya yang tersimpan di profilmu!"
    runs_output = []
    success_runs = 0

    print(f"\n[Langkah 5] Menguji pengambilan fakta di Sesi 2 sebanyak 3 kali (temperature 0)...")
    for i in range(1, 4):
        print(f"\n--- Run #{i} ---")
        messages, meta = assembler.assemble_messages(
            user_input=query_user,
            history=[],
            num_ctx=config.num_ctx
        )

        response_chunks = []
        for token, _ in client.chat_stream(
            model=config.model,
            messages=messages,
            num_ctx=config.num_ctx,
            temperature=0.0
        ):
            if token:
                response_chunks.append(token)

        full_response = "".join(response_chunks).strip()
        print(f"Jawaban Mentah LLM (Run #{i}):\n\"{full_response}\"")

        # Cek apakah token unik muncul di respons
        if unique_token in full_response:
            print(f"-> HASIL: SUKSES (Token unik '{unique_token}' ditemukan di jawaban)")
            success_runs += 1
        else:
            print(f"-> HASIL: GAGAL (Token unik '{unique_token}' TIDAK ditemukan)")

        runs_output.append({"run": i, "response": full_response, "passed": unique_token in full_response})

    # 3. Bersihkan fakta uji dari profil
    print(f"\n[Langkah 6] Membersihkan fakta uji dari profil...")
    del_ok, del_msg = profile_mgr.delete_fact(fact_key)
    print(f"  -> {del_msg}")

    # Bersihkan turn sesi uji dari database
    db.close()

    print("\n" + "=" * 60)
    print("HASIL INTEGRATION TEST CROSS-SESSION")
    print("=" * 60)
    print(f"Token Unik Diuji    : {unique_token}")
    print(f"Total Percobaan     : 3")
    print(f"Percobaan Berhasil  : {success_runs} / 3")
    for r in runs_output:
        status_txt = "PASSED" if r["passed"] else "FAILED"
        print(f"  Run #{r['run']} [{status_txt}]: {r['response'][:100]}...")
    print("=" * 60)

    if success_runs == 3:
        print("[KESIMPULAN] TEST CROSS-SESSION SUKSES 100% (3/3). Fakta berhasil ditransfer lintas sesi via profil terinjeksi.")
    else:
        print(f"[KESIMPULAN] TEST GAGAL: Hanya {success_runs}/3 berhasil.")
        sys.exit(1)

if __name__ == "__main__":
    run_cross_session_test()
