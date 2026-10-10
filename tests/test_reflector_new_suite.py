import json
import urllib.request
import tempfile
import shutil
import sys
import time
from pathlib import Path
from typing import List, Dict, Any

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from enki.memory.reflector import (
    ReflectorDaemon,
    FactProposal,
    ReflectionOutput,
    REFLECTION_SYSTEM_PROMPT
)

# 26 Transkrip Uji Baru: 13 Positif & 13 Negatif
NEW_FIXTURES = [
    # 1-13: POSITIF
    {
        "id": 1,
        "name": "pos_pref_code_typing",
        "category": "preferensi_kode",
        "turns": ["Tolong tiap kali kasih kode Python, selalu sertakan type hint dan docstring lengkap ya."],
        "should_have_fact": True
    },
    {
        "id": 2,
        "name": "pos_pref_casual_typo",
        "category": "preferensi_santai_typo",
        "turns": ["jgn pake bhs kaku bgt ya, santai aja kek ngobrol sm tmen biasa."],
        "should_have_fact": True
    },
    {
        "id": 3,
        "name": "pos_fact_identity_job",
        "category": "fakta_identitas_profesi",
        "turns": ["Aku berdomisili di Surabaya dan sehari-hari bekerja sebagai data analyst."],
        "should_have_fact": True
    },
    {
        "id": 4,
        "name": "pos_fact_casual_background",
        "category": "fakta_santai_typo",
        "turns": ["fyi umurku 28 thn, basic ku sbnrnya teknik elektro bkn infomatika."],
        "should_have_fact": True
    },
    {
        "id": 5,
        "name": "pos_proj_active_backend",
        "category": "proyek_aktif_formal",
        "turns": ["Aku sedang mengembangkan microservice payment gateway menggunakan Go dan RabbitMQ."],
        "should_have_fact": True
    },
    {
        "id": 6,
        "name": "pos_proj_casual_skripsi",
        "category": "proyek_santai_typo",
        "turns": ["lg ngerjain skripsi ttg klasifikasi citra medis pke densenet nih skrg."],
        "should_have_fact": True
    },
    {
        "id": 7,
        "name": "pos_multi_os_hardware",
        "category": "fakta_multi_pesan_os",
        "turns": [
            "Halo, selamat sore.",
            "Sekadar info, aku biasa ngoding di laptop Linux Ubuntu 22.04.",
            "Bisa tolong bantu cek cara install Docker rootless?"
        ],
        "should_have_fact": True
    },
    {
        "id": 8,
        "name": "pos_multi_preference_ram",
        "category": "preferensi_multi_pesan_ram",
        "turns": [
            "Mau tanya dong.",
            "Soal optimasi query SQL tadi.",
            "Oiya, kalau jawab pertanyaan teknis, tolong prioritaskan solusi yang hemat RAM ya."
        ],
        "should_have_fact": True
    },
    {
        "id": 9,
        "name": "pos_multi_project_offline_pos",
        "category": "proyek_multi_pesan_pos",
        "turns": [
            "Pusing banget hari ini.",
            "Proyek sistem kasir toko offline punyaku lagi banyak bug di bagian sinkronisasi offline-online.",
            "Ada ide arsitektur sync yang bagus?"
        ],
        "should_have_fact": True
    },
    {
        "id": 10,
        "name": "pos_pref_language_id",
        "category": "preferensi_bahasa",
        "turns": ["Jawab selalu dalam Bahasa Indonesia kecuali istilah teknis khusus ya."],
        "should_have_fact": True
    },
    {
        "id": 11,
        "name": "pos_fact_laptop_specs",
        "category": "fakta_spesifikasi_laptop",
        "turns": ["Spesifikasi laptopku itu RAM 16GB dengan GPU RTX 3050 4GB VRAM."],
        "should_have_fact": True
    },
    {
        "id": 12,
        "name": "pos_proj_flutter_community",
        "category": "proyek_tim_mobile",
        "turns": ["Saat ini timku sedang membangun aplikasi mobile Flutter untuk komunitas pecinta hewan."],
        "should_have_fact": True
    },
    {
        "id": 13,
        "name": "pos_fact_night_owl_habit",
        "category": "fakta_kebiasaan_kerja",
        "turns": ["Aku punya kebiasaan kerja di malam hari dan lebih produktif setelah jam 10 malam."],
        "should_have_fact": True
    },

    # 14-26: NEGATIF
    {
        "id": 14,
        "name": "neg_chat_general_ai_opinion",
        "category": "tanya_opini_umum",
        "turns": ["Halo ENKI, menurutmu apakah AI bakal menggantikan programmer dalam 5 tahun ke depan?"],
        "should_have_fact": False
    },
    {
        "id": 15,
        "name": "neg_chat_js_shallow_deep_copy",
        "category": "tanya_teknis_murni",
        "turns": ["Bagaimana perbedaan utama antara shallow copy dan deep copy di JavaScript?"],
        "should_have_fact": False
    },
    {
        "id": 16,
        "name": "neg_hypo_biotech_degree",
        "category": "pengandaian_formal",
        "turns": ["Kalau seandainya aku punya kesempatan kuliah lagi, aku pengen ambil jurusan bioteknologi kelautan."],
        "should_have_fact": False
    },
    {
        "id": 17,
        "name": "neg_hypo_google_ceo_casual",
        "category": "pengandaian_santai",
        "turns": ["klo gw jd CEO Google mah bakal gw bikin semua layanan cloud gratis buat pelajar wkwk."],
        "should_have_fact": False
    },
    {
        "id": 18,
        "name": "neg_third_friend_iphone",
        "category": "orang_lain_teman",
        "turns": ["Temanku si Dimas baru aja beli iPhone 16 Pro Max kemarin lusa di mall."],
        "should_have_fact": False
    },
    {
        "id": 19,
        "name": "neg_third_boss_excel_policy",
        "category": "orang_lain_atasan",
        "turns": ["Atasanku di kantor mewajibkan semua laporan bulanan diserahkan dalam format Excel."],
        "should_have_fact": False
    },
    {
        "id": 20,
        "name": "neg_sit_wifi_slow",
        "category": "situasional_wifi_lemot",
        "turns": ["Waduh koneksi Wi-Fi kosan lagi lemot parah nih sore ini, buka browser aja muter terus."],
        "should_have_fact": False
    },
    {
        "id": 21,
        "name": "neg_sit_tired_deadline",
        "category": "situasional_curhat_tugas",
        "turns": ["Lagi pusing banget nih tugas kuliah numpuk dan deadline besok jam 8 pagi."],
        "should_have_fact": False
    },
    {
        "id": 22,
        "name": "neg_joke_mars_agent",
        "category": "candaan_alien_mars",
        "turns": ["Jangan bilang siapa-siapa ya, sebenernya aku ini agen rahasia dari planet Mars yang nyamar."],
        "should_have_fact": False
    },
    {
        "id": 23,
        "name": "neg_secret_root_password",
        "category": "rahasia_password_root",
        "turns": ["Catat ya, password root server staging: P@ssw0rdRoot2026! jangan sampai lupa."],
        "should_have_fact": False
    },
    {
        "id": 24,
        "name": "neg_secret_openai_api_key",
        "category": "rahasia_api_key_openai",
        "turns": ["Simpan secret key OpenAI ku ini: sk-proj-1234567890abcdef1234567890abcdef di database."],
        "should_have_fact": False
    },
    {
        "id": 25,
        "name": "neg_multi_docker_question",
        "category": "multi_pesan_tanya_biasa",
        "turns": [
            "Tes satu dua.",
            "Tadi kita bahas apa ya?",
            "Oh iya, tolong buatkan ringkasan 3 poin tentang apa itu Docker container."
        ],
        "should_have_fact": False
    },
    {
        "id": 26,
        "name": "neg_opinion_monolith_architecture",
        "category": "opini_arsitektur_teknologi",
        "turns": ["Menurutku arsitektur monolith modular jauh lebih masuk akal daripada microservices untuk startup awal."],
        "should_have_fact": False
    }
]

def query_llm_reflection(turns: List[str], model: str = "qwen2.5:3b") -> List[FactProposal]:
    """Menjalankan ekstraksi refleksi via Ollama chat dengan format schema Pydantic."""
    transcript_text = "\n".join([f"- User: {t}" for t in turns])
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

def main():
    print("=" * 75)
    print("SET UJI BARU REFLECTOR: 26 TRANSKRIP (3 RUNS, TOTAL 78 EVALUASI)")
    print("Model: qwen2.5:3b | Temperature: 0.0 | Isolasi Direktori: TempDir")
    print("=" * 75)

    # Direktori sementara murni (100% isolasi)
    temp_dir = tempfile.mkdtemp()
    proposals_file = Path(temp_dir) / "pending_facts.json"
    daemon = ReflectorDaemon(proposals_file=proposals_file)

    pos_fixtures = [f for f in NEW_FIXTURES if f["should_have_fact"]]
    neg_fixtures = [f for f in NEW_FIXTURES if not f["should_have_fact"]]

    # Struktur pencatatan metrik per run
    # run -> {"recalled": int, "missed": int, "false_proposals": int, "true_negatives": int}
    run_metrics = {1: {"recall_hit": 0, "recall_miss": 0, "false_proposals": 0, "true_neg": 0},
                   2: {"recall_hit": 0, "recall_miss": 0, "false_proposals": 0, "true_neg": 0},
                   3: {"recall_hit": 0, "recall_miss": 0, "false_proposals": 0, "true_neg": 0}}

    transcript_runs_data = []

    total_start = time.time()

    for item in NEW_FIXTURES:
        fid = item["id"]
        fname = item["name"]
        cat = item["category"]
        turns = item["turns"]
        is_pos = item["should_have_fact"]

        run_results = []

        for r in [1, 2, 3]:
            try:
                proposals = query_llm_reflection(turns)
            except Exception as e:
                print(f"[ERROR Run {r} ID {fid}] {e}")
                proposals = []

            valid_proposals = []
            rejected_proposals = []

            for p in proposals:
                ok, msg = daemon.validate_and_record_proposal(p, turns, f"test_session_{fid}_r{r}")
                if ok:
                    valid_proposals.append(p)
                else:
                    rejected_proposals.append((p, msg))

            # Evaluasi terpisah
            if is_pos:
                if len(valid_proposals) >= 1:
                    run_metrics[r]["recall_hit"] += 1
                    status = "RECALL_HIT"
                else:
                    run_metrics[r]["recall_miss"] += 1
                    status = "RECALL_MISS"
            else:
                if len(valid_proposals) == 0:
                    run_metrics[r]["true_neg"] += 1
                    status = "TRUE_NEG"
                else:
                    run_metrics[r]["false_proposals"] += len(valid_proposals)
                    status = f"FALSE_PROP({len(valid_proposals)})"

            run_results.append({
                "run": r,
                "status": status,
                "proposals_count": len(valid_proposals),
                "proposals": [{"key": p.key, "val": p.value} for p in valid_proposals],
                "rejected": [msg for _, msg in rejected_proposals]
            })

        transcript_runs_data.append({
            "id": fid,
            "name": fname,
            "category": cat,
            "is_positive": is_pos,
            "sample_turn": turns[0] if len(turns) == 1 else f"[{len(turns)} turns] {turns[1][:50]}...",
            "runs": run_results
        })

    total_dur = round(time.time() - total_start, 1)
    shutil.rmtree(temp_dir, ignore_errors=True)

    # ==========================
    # LAPORAN ANGKA MENTAH
    # ==========================

    print("\n" + "=" * 75)
    print("1. TABEL HASIL MENTAH PER TRANSKRIP (ID 1 - 26, RUN 1 - 3)")
    print("=" * 75)
    header = f"{'ID':<3} | {'Tipe':<7} | {'Kategori / Nama':<28} | {'Run 1':<12} | {'Run 2':<12} | {'Run 3':<12} | Proposal Diterima Kode"
    print(header)
    print("-" * 105)

    for tr in transcript_runs_data:
        tipe_str = "POSITIF" if tr["is_positive"] else "NEGATIF"
        name_str = f"[{tr['id']}] {tr['name'][:24]}"
        r1_s = tr["runs"][0]["status"]
        r2_s = tr["runs"][1]["status"]
        r3_s = tr["runs"][2]["status"]

        # Ambil sampel proposal run 1
        p_samples = tr["runs"][0]["proposals"]
        p_summary = ", ".join([f"{p['key']}='{p['val'][:25]}'" for p in p_samples]) if p_samples else "-"

        print(f"{tr['id']:<3} | {tipe_str:<7} | {name_str:<28} | {r1_s:<12} | {r2_s:<12} | {r3_s:<12} | {p_summary}")

    print("\n" + "=" * 75)
    print("2. METRIK TERPISAH: RECALL FAKTA VALID (13 TRANSKRIP POSITIF)")
    print("=" * 75)
    total_pos_evals = len(pos_fixtures) * 3
    total_recall_hits = sum(run_metrics[r]["recall_hit"] for r in [1, 2, 3])
    total_recall_miss = sum(run_metrics[r]["recall_miss"] for r in [1, 2, 3])

    print(f"Run 1 Recall        : {run_metrics[1]['recall_hit']} / {len(pos_fixtures)} ({round(run_metrics[1]['recall_hit']/len(pos_fixtures)*100, 1)}%) | Terlewat (Miss): {run_metrics[1]['recall_miss']}")
    print(f"Run 2 Recall        : {run_metrics[2]['recall_hit']} / {len(pos_fixtures)} ({round(run_metrics[2]['recall_hit']/len(pos_fixtures)*100, 1)}%) | Terlewat (Miss): {run_metrics[2]['recall_miss']}")
    print(f"Run 3 Recall        : {run_metrics[3]['recall_hit']} / {len(pos_fixtures)} ({round(run_metrics[3]['recall_hit']/len(pos_fixtures)*100, 1)}%) | Terlewat (Miss): {run_metrics[3]['recall_miss']}")
    print(f"TOTAL RECALL GABUNGAN: {total_recall_hits} / {total_pos_evals} ({round(total_recall_hits/total_pos_evals*100, 1)}%) | Total Missed: {total_recall_miss}")

    print("\n" + "=" * 75)
    print("3. METRIK TERPISAH: FALSE PROPOSALS (13 TRANSKRIP NEGATIF)")
    print("=" * 75)
    total_neg_evals = len(neg_fixtures) * 3
    total_fps = sum(run_metrics[r]["false_proposals"] for r in [1, 2, 3])
    total_true_negs = sum(run_metrics[r]["true_neg"] for r in [1, 2, 3])

    print(f"Run 1 False Proposals: {run_metrics[1]['false_proposals']} usulan palsu | Tepat Menolak (True Neg): {run_metrics[1]['true_neg']} / {len(neg_fixtures)}")
    print(f"Run 2 False Proposals: {run_metrics[2]['false_proposals']} usulan palsu | Tepat Menolak (True Neg): {run_metrics[2]['true_neg']} / {len(neg_fixtures)}")
    print(f"Run 3 False Proposals: {run_metrics[3]['false_proposals']} usulan palsu | Tepat Menolak (True Neg): {run_metrics[3]['true_neg']} / {len(neg_fixtures)}")
    print(f"TOTAL FALSE PROPOSALS: {total_fps} usulan palsu dari {total_neg_evals} evaluasi negatif")
    print(f"TOTAL TRUE NEGATIVES : {total_true_negs} / {total_neg_evals} ({round(total_true_negs/total_neg_evals*100, 1)}%)")
    print(f"\nDurasi pengujian total: {total_dur} detik.")
    print("=" * 75)

if __name__ == "__main__":
    main()
