import os
import sys
import re
import json
import random
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from tests.calibrate_passive_recall import extract_substantive_tokens, STOPWORDS_AND_TRIGGERS

# --------------------------------------------------------------------------
# 1. Fakta Memori dalam Korpus (15 fakta domain baru tanpa tumpang tindih)
# --------------------------------------------------------------------------
CORPUS_MEMORY_FACTS = [
    ("mem_01", "user", "kita sepakati database analitik gudang pakai apa?", "assistant", "Database analitik pemrosesan data gudang kita memutuskan memakai embedded DuckDB."),
    ("mem_02", "user", "format serialisasi data sensor iot kita pakai apa?", "assistant", "Kita sepakati memakai Protobuf binary untuk serialisasi payload sensor."),
    ("mem_03", "user", "siapa qa engineer yang bertanggung jawab atas automated testing?", "assistant", "QA engineer yang memegang automasi testing adalah Rian Ardianto."),
    ("mem_04", "user", "arsip backup bulanan sistem disimpan di vendor cloud mana?", "assistant", "Arsip cadangan data bulanan disimpan di Wasabi hot cloud storage."),
    ("mem_05", "user", "mesin pencari dokumen internal berjalan di port berapa?", "assistant", "Service pencarian dokumen internal menggunakan Meilisearch pada port 7700."),
    ("mem_06", "user", "protokol komunikasi sinkron antar mikroservis kita mau pakai apa?", "assistant", "Komunikasi antar mikroservis backend disepakati menggunakan gRPC over HTTP/2."),
    ("mem_07", "user", "session cache user disimpan di redis atau apa?", "assistant", "Session cache user kita tetapkan menggunakan cluster KeyDB."),
    ("mem_08", "user", "edge reverse proxy gateway kita pakai software apa?", "assistant", "Edge reverse proxy dan auto SSL gateway menggunakan Traefik."),
    ("mem_09", "user", "modul web scraper kita ditulis pakai bahasa apa?", "assistant", "Modul web scraping performa tinggi ditulis ulang dalam bahasa Nim."),
    ("mem_10", "user", "agen pengumpul log sistem kita pakai apa?", "assistant", "Pengumpul log sistem dialirkan menggunakan daemon Fluentbit."),
    ("mem_11", "user", "font standar antarmuka UI aplikasi apa ya?", "assistant", "Tipografi standar antarmuka aplikasi menggunakan font Inter Tight."),
    ("mem_12", "user", "framework css styling frontend pakai apa?", "assistant", "Styling antarmuka dashboard menggunakan Tailwind CSS v4."),
    ("mem_13", "user", "event bus message broker backend kita apa?", "assistant", "Event bus message streaming backend menggunakan Apache Pulsar."),
    ("mem_14", "user", "bundler build tool frontend dashboard kita apa?", "assistant", "Build tool dan module bundler frontend menggunakan Turbopack."),
    ("mem_15", "user", "distro linux server staging dan produksi kita apa?", "assistant", "Distro Linux server operasional menggunakan AlmaLinux sembilan.")
]

# --------------------------------------------------------------------------
# 2. Percakapan Biasa Non-Memori (untuk Korpus A dan B)
# --------------------------------------------------------------------------
CONVERSATION_TURNS = [
    ("ses_dev_01", "user", "bagaimana cara membuat virtual environment python?"),
    ("ses_dev_01", "assistant", "Jalankan python -m venv venv lalu aktifkan dengan source venv/bin/activate."),
    ("ses_dev_02", "user", "apa fungsi git commit dalam version control?"),
    ("ses_dev_02", "assistant", "Menyimpan snapshot perubahan yang telah di-stage ke riwayat lokal repo."),
    ("ses_dev_03", "user", "bagaimana cara menangani exception try catch di python?"),
    ("ses_dev_03", "assistant", "Gunakan blok try except Exception as e untuk menangkap runtime error."),
    ("ses_dev_04", "user", "apa bedanya list dan dictionary di python?"),
    ("ses_dev_04", "assistant", "List adalah koleksi urut berindeks integer, dictionary menyimpan pasangan key-value."),
    ("ses_dev_05", "user", "bagaimana cara install dependencies dari requirements.txt?"),
    ("ses_dev_05", "assistant", "Gunakan perintah pip install -r requirements.txt di terminal."),
    ("ses_dev_06", "user", "apa kegunaan linter seperti flake8 atau ruff?"),
    ("ses_dev_06", "assistant", "Menganalisis kode statis untuk mendeteksi bug sintaks dan pelanggaran style guide."),
    ("ses_dev_07", "user", "bagaimana cara membuat commit git yang rapi?"),
    ("ses_dev_07", "assistant", "Gunakan format conventional commits seperti feat, fix, docs dengan deskripsi jelas."),
    ("ses_dev_08", "user", "apa beda synchronous dan asynchronous execution?"),
    ("ses_dev_08", "assistant", "Synchronous mengeksekusi blocking satu per satu, async memungkinkan operasi I/O tanpa blokir."),
    ("ses_dev_09", "user", "bagaimana cara membaca file csv di python?"),
    ("ses_dev_09", "assistant", "Bisa menggunakan modul bawaan csv.reader atau library pandas read_csv."),
    ("ses_dev_10", "user", "apa itu unit test dan kenapa penting?"),
    ("ses_dev_10", "assistant", "Pengujian komponen fungsi terkecil secara terisolasi untuk mencegah regresi bug."),
    ("ses_dev_11", "user", "bagaimana cara membuat branching di git?"),
    ("ses_dev_11", "assistant", "Gunakan git checkout -b nama-branch atau git switch -c nama-branch."),
    ("ses_dev_12", "user", "apa kegunaan docker container untuk aplikasi web?"),
    ("ses_dev_12", "assistant", "Membungkus aplikasi beserta seluruh dependensinya agar berjalan konsisten di semua host."),
    ("ses_dev_13", "user", "bagaimana format docstring standar python?"),
    ("ses_dev_13", "assistant", "Gunakan triple quote di bawah definisi fungsi mengikuti format Google style atau PEP 257."),
    ("ses_dev_14", "user", "apa itu rest api dan apa kegunaannya?"),
    ("ses_dev_14", "assistant", "Antarmuka berbasis HTTP dengan format JSON untuk komunikasi antar client dan server."),
    ("ses_dev_15", "user", "bagaimana cara mengatasi merge conflict git?"),
    ("ses_dev_15", "assistant", "Buka file yang berkonflik, pilih bagian kode yang valid, stage file lalu selesaikan commit.")
]

# --------------------------------------------------------------------------
# 3. 40 Query Kalibrasi (10 Positif Parafrase, 15 Negatif Out, 15 Negatif In)
# --------------------------------------------------------------------------
CALIBRATION_QUERIES = [
    # 10 Positif Parafrase (kata tidak identik dengan kalimat memori)
    {"id": "POS_01", "type": "POS", "prompt": "buat analitik gudang sistem kita simpan di db mana ya?", "expected_key": "duckdb"},
    {"id": "POS_02", "type": "POS", "prompt": "payload data sensor iot tim kita diserialisasi jadi apa kemarin?", "expected_key": "protobuf"},
    {"id": "POS_03", "type": "POS", "prompt": "siapa nama penanggung jawab automated testing kita kemarin?", "expected_key": "rian"},
    {"id": "POS_04", "type": "POS", "prompt": "arsip backup data bulanan sistem kita simpan di cloud apa?", "expected_key": "wasabi"},
    {"id": "POS_05", "type": "POS", "prompt": "mesin pencari dokumen internal tim kita disetel di port berapa?", "expected_key": "7700"},
    {"id": "POS_06", "type": "POS", "prompt": "komunikasi antar service backend tim kita sepakat pakai protokol apa?", "expected_key": "grpc"},
    {"id": "POS_07", "type": "POS", "prompt": "session cache user tim kita kemarin disepakati disimpan di mana?", "expected_key": "keydb"},
    {"id": "POS_08", "type": "POS", "prompt": "edge proxy dan ssl tim kita dikelola software apa kemarin?", "expected_key": "traefik"},
    {"id": "POS_09", "type": "POS", "prompt": "modul web scraping data kita ditulis ulang dalam bahasa apa?", "expected_key": "nim"},
    {"id": "POS_10", "type": "POS", "prompt": "pengumpul data log sistem kita dialirkan pakai apa kemarin?", "expected_key": "fluentbit"},

    # 15 Negatif Topik Luar (di luar korpus sama sekali)
    {"id": "NEG_OUT_01", "type": "NEG_OUT", "prompt": "bagaimana resep membuat sambal matah khas bali?", "expected_key": None},
    {"id": "NEG_OUT_02", "type": "NEG_OUT", "prompt": "apa penyebab terjadinya gerhana matahari total?", "expected_key": None},
    {"id": "NEG_OUT_03", "type": "NEG_OUT", "prompt": "siapa presiden pertama amerika serikat?", "expected_key": None},
    {"id": "NEG_OUT_04", "type": "NEG_OUT", "prompt": "berapa jarak bumi ke bulan dalam kilometer?", "expected_key": None},
    {"id": "NEG_OUT_05", "type": "NEG_OUT", "prompt": "tips mengatasi insomnia dan kualitas tidur rendah", "expected_key": None},
    {"id": "NEG_OUT_06", "type": "NEG_OUT", "prompt": "bagaimana cara merawat tanaman monstera di dalam ruangan?", "expected_key": None},
    {"id": "NEG_OUT_07", "type": "NEG_OUT", "prompt": "apa perbedaan piano akustik dan digital?", "expected_key": None},
    {"id": "NEG_OUT_08", "type": "NEG_OUT", "prompt": "bagaimana sejarah penemuan penisilin oleh alexander fleming?", "expected_key": None},
    {"id": "NEG_OUT_09", "type": "NEG_OUT", "prompt": "rekomendasi destinasi wisata alam di yogyakarta", "expected_key": None},
    {"id": "NEG_OUT_10", "type": "NEG_OUT", "prompt": "cara merawat rantai sepeda motor agar tidak berkarat", "expected_key": None},
    {"id": "NEG_OUT_11", "type": "NEG_OUT", "prompt": "apa manfaat olahraga kardio untuk kesehatan jantung?", "expected_key": None},
    {"id": "NEG_OUT_12", "type": "NEG_OUT", "prompt": "bagaimana teknik pernapasan saat berenang gaya dada?", "expected_key": None},
    {"id": "NEG_OUT_13", "type": "NEG_OUT", "prompt": "kapan perang dunia kedua resmi berakhir?", "expected_key": None},
    {"id": "NEG_OUT_14", "type": "NEG_OUT", "prompt": "bagaimana cara membuat kompos organik dari sampah dapur?", "expected_key": None},
    {"id": "NEG_OUT_15", "type": "NEG_OUT", "prompt": "apa fungsi hormon insulin dalam tubuh manusia?", "expected_key": None},

    # 15 Negatif Topik di Korpus (ada kata coding/git/python tapi bukan memori kita)
    {"id": "NEG_IN_01", "type": "NEG_IN", "prompt": "gimana cara membuat fungsi rekursif di python?", "expected_key": None},
    {"id": "NEG_IN_02", "type": "NEG_IN", "prompt": "apa beda git commit dan git push?", "expected_key": None},
    {"id": "NEG_IN_03", "type": "NEG_IN", "prompt": "bagaimana cara mengatasi syntax error saat koding?", "expected_key": None},
    {"id": "NEG_IN_04", "type": "NEG_IN", "prompt": "apa kegunaan virtual environment di python?", "expected_key": None},
    {"id": "NEG_IN_05", "type": "NEG_IN", "prompt": "bagaimana cara menulis unit test yang baik?", "expected_key": None},
    {"id": "NEG_IN_06", "type": "NEG_IN", "prompt": "apa bedanya list dan dictionary dalam struktur data?", "expected_key": None},
    {"id": "NEG_IN_07", "type": "NEG_IN", "prompt": "gimana cara membaca file teks baris per baris di python?", "expected_key": None},
    {"id": "NEG_IN_08", "type": "NEG_IN", "prompt": "apa itu linter dan kenapa penting dipakai developer?", "expected_key": None},
    {"id": "NEG_IN_09", "type": "NEG_IN", "prompt": "bagaimana cara mengatasi merge conflict di git branch?", "expected_key": None},
    {"id": "NEG_IN_10", "type": "NEG_IN", "prompt": "apa fungsi command pip install dalam manajemen paket?", "expected_key": None},
    {"id": "NEG_IN_11", "type": "NEG_IN", "prompt": "halo selamat siang, apa kabar hari ini?", "expected_key": None},
    {"id": "NEG_IN_12", "type": "NEG_IN", "prompt": "bantu bikinin draf email izin tidak masuk kantor", "expected_key": None},
    {"id": "NEG_IN_13", "type": "NEG_IN", "prompt": "bagaimana cara optimize query database relasional secara umum?", "expected_key": None},
    {"id": "NEG_IN_14", "type": "NEG_IN", "prompt": "apa perbedaan asynchronous dan synchronous programming?", "expected_key": None},
    {"id": "NEG_IN_15", "type": "NEG_IN", "prompt": "bagaimana format penulisan docstring standar di python?", "expected_key": None}
]

def split_dev_holdout(queries: List[Dict[str, Any]], seed: int = 42) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Membagi query menjadi 50% DEV dan 50% HOLDOUT secara berstrata dan reproducible."""
    rnd = random.Random(seed)
    pos = [q for q in queries if q["type"] == "POS"]
    neg_out = [q for q in queries if q["type"] == "NEG_OUT"]
    neg_in = [q for q in queries if q["type"] == "NEG_IN"]

    rnd.shuffle(pos)
    rnd.shuffle(neg_out)
    rnd.shuffle(neg_in)

    dev = pos[:5] + neg_out[:8] + neg_in[:7]
    holdout = pos[5:] + neg_out[8:] + neg_in[7:]

    return dev, holdout

def setup_corpus_db(db_path: Path, is_realistic: bool = False) -> sqlite3.Connection:
    """Membangun database korpus kalibrasi tanpa menyertakan query uji."""
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    with conn:
        conn.execute("""
            CREATE TABLE turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE VIRTUAL TABLE turns_fts USING fts5(
                turn_id UNINDEXED,
                session_id UNINDEXED,
                role UNINDEXED,
                content
            )
        """)

    # 1. Masukkan Fakta Memori
    for s_id, u_r, u_c, a_r, a_c in CORPUS_MEMORY_FACTS:
        insert_turn_item(conn, s_id, u_r, u_c)
        insert_turn_item(conn, s_id, a_r, a_c)

    # 2. Masukkan Percakapan Developer Biasa
    for s_id, r, c in CONVERSATION_TURNS:
        insert_turn_item(conn, s_id, r, c)

    # 3. Jika Korpus Realistis (Kondisi B): tambahkan fixture suite 26 (tanpa query uji)
    if is_realistic:
        from tests.test_reflector_new_suite import NEW_FIXTURES
        for item in NEW_FIXTURES:
            s_id = f"suite26_fix_{item['id']}"
            for turn_text in item["turns"]:
                insert_turn_item(conn, s_id, "user", turn_text)

        # Tambahkan turns percakapan non-uji tambahan untuk mencapai >250 turns
        for i in range(1, 101):
            s_id = f"background_dev_{i}"
            insert_turn_item(conn, s_id, "user", f"Log analisis deployment service background worker tahap {i}: status normal tanpa anomali.")
            insert_turn_item(conn, s_id, "assistant", f"Tercatat service worker {i} berjalan sesuai SLA uptime 99.9%.")

    return conn

def insert_turn_item(conn: sqlite3.Connection, session_id: str, role: str, content: str):
    with conn:
        cursor = conn.execute(
            "INSERT INTO turns (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content)
        )
        tid = cursor.lastrowid
        conn.execute(
            "INSERT INTO turns_fts (turn_id, session_id, role, content) VALUES (?, ?, ?, ?)",
            (tid, session_id, role, content)
        )

def compute_df(conn: sqlite3.Connection) -> Tuple[Dict[str, float], int]:
    cursor = conn.execute("SELECT count(*) as c FROM turns")
    total_docs = cursor.fetchone()["c"]

    cursor = conn.execute("SELECT content FROM turns")
    counts: Dict[str, int] = {}
    for row in cursor.fetchall():
        w_set = set(extract_substantive_tokens(row["content"]))
        for w in w_set:
            counts[w] = counts.get(w, 0) + 1

    df_pct = {w: (cnt / total_docs) * 100.0 for w, cnt in counts.items()}
    return df_pct, total_docs

# Penanda konteks & kepemilikan untuk R3
CONTEXT_MARKERS_REGEX = re.compile(
    r'\b(kita|kami|kemarin|pernah|dulu|waktu itu|tim kita|proyek kita|proyekku|task-?ku|repo-?ku)\b',
    re.IGNORECASE
)

def evaluate_rule(
    conn: sqlite3.Connection,
    prompt: str,
    rule_name: str,
    df_table: Dict[str, float],
    df_threshold_pct: float = 5.0
) -> bool:
    tokens = extract_substantive_tokens(prompt)
    if not tokens:
        return False

    fts_query = " OR ".join(f'"{t}"' for t in tokens)
    sql = "SELECT content FROM turns_fts WHERE turns_fts MATCH ? LIMIT 5"
    try:
        cursor = conn.execute(sql, (fts_query,))
        candidates = [row["content"] for row in cursor.fetchall()]
    except Exception:
        return False

    if not candidates:
        return False

    has_context_marker = bool(CONTEXT_MARKERS_REGEX.search(prompt))

    for content in candidates:
        content_lower = content.lower()
        matched = {t for t in tokens if re.search(r'\b' + re.escape(t) + r'\b', content_lower)}
        if not matched:
            continue

        if rule_name == "R1":
            # R1: Minimal 2 token cocok (tanpa klausul "atau 1 token unik")
            if len(matched) >= 2:
                return True

        elif rule_name == "R2":
            # R2: R1 + token yang cocok harus punya DF <= X% di korpus
            # Di sini minimal 2 token cocok, DAN keduanya (atau minimal 1) punya DF <= threshold
            specific_tokens = {t for t in matched if df_table.get(t, 0.0) <= df_threshold_pct}
            if len(matched) >= 2 and len(specific_tokens) >= 1:
                return True

        elif rule_name == "R3":
            # R3: R1 + harus ada penanda konteks percakapan lampau / kepemilikan user
            if len(matched) >= 2 and has_context_marker:
                return True

        elif rule_name == "R3_strict":
            # R3 Strict: R2 + penanda konteks
            specific_tokens = {t for t in matched if df_table.get(t, 0.0) <= df_threshold_pct}
            if len(matched) >= 2 and len(specific_tokens) >= 1 and has_context_marker:
                return True

    return False

def run_suite_evaluation(
    conn: sqlite3.Connection,
    dataset: List[Dict[str, Any]],
    df_table: Dict[str, float],
    rules: List[Tuple[str, float]]
) -> Dict[str, Any]:
    results = {}
    for r_name, df_thresh in rules:
        key = f"{r_name}_df{df_thresh:.1f}" if "df" in r_name.lower() or r_name in ("R2", "R3_strict") else r_name
        pos_total = sum(1 for q in dataset if q["type"] == "POS")
        neg_total = sum(1 for q in dataset if q["type"] != "POS")

        tp = 0
        fp = 0
        details = []

        for q in dataset:
            triggered = evaluate_rule(conn, q["prompt"], r_name, df_table, df_threshold_pct=df_thresh)
            is_pos = (q["type"] == "POS")
            if is_pos and triggered:
                tp += 1
            elif not is_pos and triggered:
                fp += 1

            details.append({
                "id": q["id"],
                "type": q["type"],
                "prompt": q["prompt"],
                "triggered": triggered,
                "is_pos": is_pos
            })

        recall = (tp / pos_total * 100.0) if pos_total else 0.0
        fp_rate = (fp / neg_total * 100.0) if neg_total else 0.0

        results[key] = {
            "rule": r_name,
            "threshold": df_thresh,
            "tp": tp,
            "pos_total": pos_total,
            "fp": fp,
            "neg_total": neg_total,
            "recall": round(recall, 1),
            "fp_rate": round(fp_rate, 1),
            "details": details
        }

    return results

if __name__ == "__main__":
    SEED = 42
    dev_set, holdout_set = split_dev_holdout(CALIBRATION_QUERIES, seed=SEED)

    print("=" * 80)
    print(f"SPLIT DATASET (SEED={SEED}):")
    print(f"  DEV SET    : {len(dev_set)} prompt (POS: {sum(1 for q in dev_set if q['type']=='POS')}, NEG: {sum(1 for q in dev_set if q['type']!='POS')})")
    print(f"  HOLDOUT SET: {len(holdout_set)} prompt (POS: {sum(1 for q in holdout_set if q['type']=='POS')}, NEG: {sum(1 for q in holdout_set if q['type']!='POS')})")
    print("=" * 80)

    db_a = Path("tests/calib_corpus_a.db")
    db_b = Path("tests/calib_corpus_b.db")

    conn_a = setup_corpus_db(db_a, is_realistic=False)
    conn_b = setup_corpus_db(db_b, is_realistic=True)

    df_a, total_a = compute_df(conn_a)
    df_b, total_b = compute_df(conn_b)

    print(f"Korpus Kondisi A (Kecil)      : {total_a} turns")
    print(f"Korpus Kondisi B (Realistis)  : {total_b} turns")
    print("=" * 80)

    rules_to_eval = [
        ("R1", 0.0),
        ("R2", 5.0),
        ("R2", 10.0),
        ("R3", 0.0),
        ("R3_strict", 5.0),
        ("R3_strict", 10.0)
    ]

    print("\n--- EVALUASI DEV SET (Kondisi A: Korpus Kecil) ---")
    res_dev_a = run_suite_evaluation(conn_a, dev_set, df_a, rules_to_eval)
    for k, v in res_dev_a.items():
        print(f"  {k:<15} | Recall: {v['tp']}/{v['pos_total']} ({v['recall']:5.1f}%) | FP: {v['fp']}/{v['neg_total']} ({v['fp_rate']:5.1f}%)")

    print("\n--- EVALUASI DEV SET (Kondisi B: Korpus Realistis) ---")
    res_dev_b = run_suite_evaluation(conn_b, dev_set, df_b, rules_to_eval)
    for k, v in res_dev_b.items():
        print(f"  {k:<15} | Recall: {v['tp']}/{v['pos_total']} ({v['recall']:5.1f}%) | FP: {v['fp']}/{v['neg_total']} ({v['fp_rate']:5.1f}%)")

    # Pilih aturan terbaik berdasarkan DEV set:
    # Syarat gate: FP Rate <= 10% dan Recall >= 60% pada kedua kondisi
    best_rule_key = None
    best_rule_name = None
    best_thresh = 0.0
    for k in res_dev_b:
        r_a = res_dev_a[k]
        r_b = res_dev_b[k]
        if r_a["fp_rate"] <= 10.0 and r_b["fp_rate"] <= 10.0 and r_a["recall"] >= 60.0 and r_b["recall"] >= 60.0:
            best_rule_key = k
            best_rule_name = r_b["rule"]
            best_thresh = r_b["threshold"]
            break

    print("\n" + "=" * 80)
    print(f"ATURAN TERPILIH DARI DEV SET: {best_rule_key}")
    print("=" * 80)

    if best_rule_key:
        print(f"\n--- PENGUJIAN HOLDOUT SET TUNGGAL (Aturan: {best_rule_key}) ---")
        holdout_rules = [(best_rule_name, best_thresh)]
        res_hold_a = run_suite_evaluation(conn_a, holdout_set, df_a, holdout_rules)
        res_hold_b = run_suite_evaluation(conn_b, holdout_set, df_b, holdout_rules)

        ha = res_hold_a[best_rule_key]
        hb = res_hold_b[best_rule_key]

        print(f"  HOLDOUT Kondisi A: Recall: {ha['tp']}/{ha['pos_total']} ({ha['recall']}%) | FP: {ha['fp']}/{ha['neg_total']} ({ha['fp_rate']}%)")
        print(f"  HOLDOUT Kondisi B: Recall: {hb['tp']}/{hb['pos_total']} ({hb['recall']}%) | FP: {hb['fp']}/{hb['neg_total']} ({hb['fp_rate']}%)")

        print("\nRINCIAN QUERY HOLDOUT SET (Kondisi B):")
        for item in hb["details"]:
            trig_str = "TRIGGERED" if item["triggered"] else "NONE"
            exp_str = "POS" if item["is_pos"] else "NEG"
            ok_str = "OK" if (item["is_pos"] == item["triggered"]) else "FAIL"
            print(f"  [{item['id']:<10}] {exp_str} -> {trig_str:<9} | {ok_str:<4} | '{item['prompt']}'")
    else:
        print("PERINGATAN: Tidak ada aturan yang lulus gate di DEV set (FP <= 10% dan Recall >= 60%).")

    conn_a.close()
    conn_b.close()
    if db_a.exists(): db_a.unlink()
    if db_b.exists(): db_b.unlink()
