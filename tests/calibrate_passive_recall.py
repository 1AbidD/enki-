import os
import re
import json
import sqlite3
from pathlib import Path
from typing import List, Dict, Any, Set, Tuple

# Stopwords & Conversational Fillers
STOPWORDS_AND_TRIGGERS = {
    # Pemicu
    "kemarin", "pernah", "ingat", "sebelumnya", "tadi", "waktu", "itu", "remember", "earlier",
    # Stopword & Kata Tugas Bahasa Indonesia
    "apa", "yang", "kita", "kamu", "aku", "saya", "dia", "mereka", "dong", "tolong",
    "ya", "sih", "kan", "lah", "kah", "dan", "di", "ke", "dari", "ini", "ada",
    "bisa", "mau", "pada", "untuk", "dengan", "apakah", "gimana", "bagaimana",
    "kenapa", "mengapa", "soal", "tentang", "nggak", "tidak", "kok",
    "siapa", "kapan", "mana", "dimana", "oleh", "dalam", "yg", "bantu", "cara",
    "hari", "bikin", "buat", "buatkan", "kasih", "tahu", "cek", "pake", "coba",
    "jelaskan", "seperti", "kira", "agak", "banget", "aja",
    # Stopword Bahasa Inggris
    "what", "the", "is", "are", "you", "we", "i", "a", "an", "in", "on", "at", "to",
    "for", "of", "with", "about", "did", "do", "does", "have", "has", "had", "how", "why"
}

def extract_substantive_tokens(user_text: str) -> List[str]:
    cleaned_chars = re.sub(r'[^\w\s]', ' ', user_text)
    words = cleaned_chars.split()
    substantive = []
    seen = set()
    for w in words:
        w_lower = w.lower().strip()
        if len(w_lower) >= 2 and w_lower not in STOPWORDS_AND_TRIGGERS and w_lower not in seen:
            substantive.append(w_lower)
            seen.add(w_lower)
    return substantive

def setup_calibration_database(db_path: Path) -> sqlite3.Connection:
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
    return conn

def insert_turn(conn: sqlite3.Connection, session_id: str, role: str, content: str):
    with conn:
        cursor = conn.execute(
            "INSERT INTO turns (session_id, role, content) VALUES (?, ?, ?)",
            (session_id, role, content)
        )
        turn_id = cursor.lastrowid
        conn.execute(
            "INSERT INTO turns_fts (turn_id, session_id, role, content) VALUES (?, ?, ?, ?)",
            (turn_id, session_id, role, content)
        )

# 15 Fakta Positif dalam Memori Lama
POSITIVE_MEMORIES = [
    ("mem_01", "user", "kita putuskan database utama pakai apa?", "assistant", "Database utama proyek kita adalah SQLite dengan mode WAL dan FTS5 untuk pencarian teks."),
    ("mem_02", "user", "layanan pembayaran kita tulis pakai bahasa dan framework apa?", "assistant", "Backend service pembayaran kita dibangun dengan Python FastAPI."),
    ("mem_03", "user", "server staging kita jalan di port berapa?", "assistant", "Kita sepakati server staging berjalan di port 9042."),
    ("mem_04", "user", "siapa yang jadi tech lead proyek enki?", "assistant", "Tech lead dan ketua tim proyek enki adalah Budi Santoso."),
    ("mem_05", "user", "cache layer kita pakai apa ya?", "assistant", "Kita memakai Redis sebagai cache memory di port default 6379."),
    ("mem_06", "user", "otomatisasi deploy server pakai tool apa?", "assistant", "Otomatisasi deployment menggunakan fabfile dengan Python Fabric."),
    ("mem_07", "user", "framework frontend dashboard apa yang dipakai?", "assistant", "Frontend dashboard kita menggunakan Vue 3 dengan bundling Vite."),
    ("mem_08", "user", "konfigurasi CI kita pakai apa?", "assistant", "Pipeline CI menggunakan GitHub Actions dengan runner lokal self-hosted."),
    ("mem_09", "user", "koleksi log server dikumpulkan ke mana?", "assistant", "Log server diagregasi menggunakan Vector dan disimpan di Grafana Loki."),
    ("mem_10", "user", "modul kompresi performa tinggi ditulis pakai apa?", "assistant", "Modul kompresi data ditulis ulang menggunakan bahasa Rust."),
    ("mem_11", "user", "antrean message broker pakai apa?", "assistant", "Message broker sistem menggunakan cluster RabbitMQ."),
    ("mem_12", "user", "terminal multiplexer standar tim apa?", "assistant", "Standar terminal multiplexer tim adalah tmux dengan tmux-resurrect."),
    ("mem_13", "user", "text editor utama di server staging apa?", "assistant", "Editor default di staging server adalah Vim dengan lazy vim plugin."),
    ("mem_14", "user", "penyimpanan backup database ditaruh di mana?", "assistant", "Backup database diunggah ke MinIO object storage S3-compatible."),
    ("mem_15", "user", "reverse proxy dan sertifikat SSL diurus oleh apa?", "assistant", "Reverse proxy gateway menggunakan Caddy server dengan auto-SSL.")
]

# 15 Prompt Uji Positif
POSITIVE_PROMPTS = [
    {"id": "POS_01", "prompt": "apa database utama proyek kita ya?", "expected_key": "sqlite"},
    {"id": "POS_02", "prompt": "bahasa apa yg kita pake buat backend service pembayaran?", "expected_key": "fastapi"},
    {"id": "POS_03", "prompt": "port berapa yg kita sepakati buat running server staging?", "expected_key": "9042"},
    {"id": "POS_04", "prompt": "siapa ketua tim atau lead engineer proyek enki waktu itu?", "expected_key": "budi"},
    {"id": "POS_05", "prompt": "port dan tipe cache service kita apa kemarin?", "expected_key": "redis"},
    {"id": "POS_06", "prompt": "alat otomatisasi deploy apa yang kita pilih?", "expected_key": "fabfile"},
    {"id": "POS_07", "prompt": "frontend web dashboard kita dibangun pakai apa ya?", "expected_key": "vue"},
    {"id": "POS_08", "prompt": "gimana setup CI pipeline pengujian kita?", "expected_key": "actions"},
    {"id": "POS_09", "prompt": "service apa yang dipakai buat ngumpulin log sistem?", "expected_key": "loki"},
    {"id": "POS_10", "prompt": "kita mau rewrite modul kompresi pakai bahasa apa?", "expected_key": "rust"},
    {"id": "POS_11", "prompt": "message broker internal kita pakai apa ya?", "expected_key": "rabbitmq"},
    {"id": "POS_12", "prompt": "multiplexer terminal standar tim kita apa?", "expected_key": "tmux"},
    {"id": "POS_13", "prompt": "editor favorit di server staging apa?", "expected_key": "vim"},
    {"id": "POS_14", "prompt": "object storage buat nyimpan backup database apa?", "expected_key": "minio"},
    {"id": "POS_15", "prompt": "reverse proxy gateway web kita pakai apa?", "expected_key": "caddy"}
]

# 40 Prompt Uji Negatif
NEGATIVE_PROMPTS = [
    "halo selamat pagi, gimana kabarmu hari ini?",
    "siapa pencipta bahasa pemrograman python dan kapan pertama kali dirilis?",
    "buatkan resep singkat cara bikin nasi goreng kampung yang enak dan gurih",
    "apa beda framework dan library dalam pemrograman web?",
    "jelaskan konsep dasar object oriented programming untuk pemula",
    "bagaimana cara kerja binary search algorithm?",
    "buatkan fungsi python untuk membalik urutan string",
    "apa fungsi dari tag html <head> dan perbedaannya dengan <body>?",
    "gimana tips mengatasi insomnia dan pola tidur yg berantakan?",
    "kapan bahasa pemrograman C pertama kali diciptakan dan oleh siapa?",
    "bantu bikinin draf email izin sakit untuk bos kantor besok",
    "jelaskan perbedaan antara process dan thread dalam sistem operasi",
    "apa itu big O notation dan kenapa penting dalam analisis algoritma?",
    "bagaimana cara menghitung luas lingkaran jika diketahui jari-jarinya?",
    "apa fungsi indeks pada database relasional secara umum?",
    "jelaskan konsep asynchronous programming dan event loop",
    "bagaimana cara membuat commit git yang baik dan deskriptif?",
    "apa perbedaan HTTP dan HTTPS dalam keamanan jaringan?",
    "tolong buatkan contoh kode loop for dan while di javascript",
    "apa itu REST API dan apa saja metode HTTP yang paling sering dipakai?",
    "bagaimana tips produktif bekerja dari rumah secara efektif?",
    "jelaskan apa itu recursion beserta contoh sederhana fungsi faktorial",
    "apa perbedaan stack dan queue dalam struktur data?",
    "bagaimana cara kerja garbage collection di runtime modern?",
    "apa kegunaan docker container dibanding virtual machine konvensional?",
    "buatkan template format surat lamaran kerja sederhana",
    "bagaimana cara kerja DNS resolving di internet?",
    "apa saja prinsip SOLID dalam perancangan perangkat lunak?",
    "jelaskan cara kerja hashing function seperti SHA-256",
    "apa perbedaan SQL dan NoSQL secara konseptual umum?",
    "bagaimana cara kerja TCP handshake tiga arah?",
    "apa beda compiler dan interpreter dalam eksekusi kode?",
    "jelaskan apa yang dimaksud dengan dependency injection",
    "bagaimana cara merawat laptop agar baterai lebih awet?",
    "apa keuntungan menggunakan model arsitektur microservices?",
    "jelaskan konsep closure dalam bahasa pemrograman fungsional",
    "bagaimana cara mengatasi stres saat beban kerja menumpuk?",
    "apa itu deadlock dan bagaimana kondisi terjadinya?",
    "jelaskan apa itu version control system dan sejarah singkatnya",
    "apa saja jenis join dalam query database SQL relasional?"
]

def load_real_transcripts_into_corpus(conn: sqlite3.Connection):
    # 1. 15 Memori Positif
    for sess, u_role, u_text, a_role, a_text in POSITIVE_MEMORIES:
        insert_turn(conn, sess, u_role, u_text)
        insert_turn(conn, sess, a_role, a_text)

    # 2. Transkrip dari suite 26
    from tests.test_reflector_new_suite import NEW_FIXTURES
    for item in NEW_FIXTURES:
        s_id = f"suite26_fix_{item['id']}"
        for turn_text in item["turns"]:
            insert_turn(conn, s_id, "user", turn_text)

    # 3. Transkrip dari Set 2
    set2_path = Path("tests/results/benchmark_set2_raw.jsonl")
    if set2_path.exists():
        with open(set2_path, "r", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                s_id = f"set2_run_{d.get('run_idx', 1)}_{d.get('id', '')}"
                insert_turn(conn, s_id, "user", d.get("prompt_input", ""))
                if d.get("final_response"):
                    insert_turn(conn, s_id, "assistant", d.get("final_response"))

    # 4. Transkrip dari Set 3
    set3_path = Path("tests/results/benchmark_set3_raw.jsonl")
    if set3_path.exists():
        with open(set3_path, "r", encoding="utf-8") as f:
            for line in f:
                d = json.loads(line)
                s_id = f"set3_run_{d.get('run_idx', 1)}_{d.get('id', '')}"
                insert_turn(conn, s_id, "user", d.get("prompt_input", ""))
                if d.get("final_response"):
                    insert_turn(conn, s_id, "assistant", d.get("final_response"))

    # 5. Percakapan coding umum tambahan
    dev_extra = [
        ("ses_extra_1", "user", "gimana cara install package python pakai pip?"),
        ("ses_extra_1", "assistant", "Gunakan pip install nama-package di terminal command line."),
        ("ses_extra_2", "user", "apa kegunaan docker container untuk testing?"),
        ("ses_extra_2", "assistant", "Memastikan environment test identik dan terisolasi dari host OS."),
        ("ses_extra_3", "user", "cara mengatasi merge conflict di git bagaimana?"),
        ("ses_extra_3", "assistant", "Buka editor, pilih perubahan yang benar, stage file lalu git commit."),
        ("ses_extra_4", "user", "bagaimana struktur project python yang rapi?"),
        ("ses_extra_4", "assistant", "Pisahkan source code di folder package, test di tests/, dan config di root."),
        ("ses_extra_5", "user", "apa beda git pull dan git fetch?"),
        ("ses_extra_5", "assistant", "Git fetch mengunduh commit baru tanpa merge, git pull langsung merge."),
        ("ses_extra_6", "user", "kenapa virtual environment python penting?"),
        ("ses_extra_6", "assistant", "Mencegah konflik versi dependency antar project yang berbeda."),
        ("ses_extra_7", "user", "cara menulis docstring di python seperti apa?"),
        ("ses_extra_7", "assistant", "Gunakan triple quote tepat di bawah nama fungsi atau class."),
        ("ses_extra_8", "user", "apa itu linting kode dan kenapa perlu di CI?"),
        ("ses_extra_8", "assistant", "Linter memeriksa format dan potensi error sebelum kode di-merge."),
    ]
    for s_id, r, c in dev_extra:
        insert_turn(conn, s_id, r, c)

    cursor = conn.execute("SELECT count(*) as c FROM turns")
    total = cursor.fetchone()["c"]
    print(f"Total turns di corpus realistis: {total}")

def compute_document_frequencies(conn: sqlite3.Connection) -> Dict[str, float]:
    cursor = conn.execute("SELECT count(*) as c FROM turns")
    total_docs = cursor.fetchone()["c"]

    cursor = conn.execute("SELECT content FROM turns")
    df_counts: Dict[str, int] = {}

    for row in cursor.fetchall():
        words = set(extract_substantive_tokens(row["content"]))
        for w in words:
            df_counts[w] = df_counts.get(w, 0) + 1

    df_pct: Dict[str, float] = {}
    for w, count in df_counts.items():
        df_pct[w] = (count / total_docs) * 100.0

    return df_pct

def test_passive_recall_rule(
    conn: sqlite3.Connection,
    prompt: str,
    rule_type: str,
    df_table: Dict[str, float],
    df_threshold_pct: float = 10.0
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

    for content in candidates:
        content_lower = content.lower()
        matched = {t for t in tokens if re.search(r'\b' + re.escape(t) + r'\b', content_lower)}
        if not matched:
            continue

        if rule_type == "old_len7":
            # Aturan lama: minimal 2 kata cocok, ATAU 1 kata jika panjang >= 7
            is_rare_len = any(len(t) >= 7 for t in matched)
            if len(matched) >= 2 or (len(matched) == 1 and is_rare_len):
                return True

        elif rule_type == "new_df_direct":
            # Pengganti langsung len>=7: jika len(matched)>=2 OR (len(matched)==1 AND df <= X%)
            # Di mana kata dengan DF > X% dianggap umum
            specific_tokens = {t for t in matched if df_table.get(t, 0.0) <= df_threshold_pct}
            if len(matched) >= 2 or (len(matched) == 1 and len(specific_tokens) >= 1):
                return True

        elif rule_type == "new_df_gated":
            # Filter ganda: kata umum (DF > X%) tidak boleh memicu recall sendirian maupun berpasangan sesama kata umum.
            # Wajib ada minimal 1 kata spesifik (DF <= X%), DAN (minimal 2 kata cocok ATAU kata itu sangat spesifik DF <= 1%)
            specific_tokens = {t for t in matched if df_table.get(t, 0.0) <= df_threshold_pct}
            very_specific = {t for t in matched if df_table.get(t, 0.0) <= min(df_threshold_pct, 1.5)}
            if (len(matched) >= 2 and len(specific_tokens) >= 1) or len(very_specific) >= 1:
                return True

    return False

def main():
    db_file = Path("tests/calibration_corpus.db")
    conn = setup_calibration_database(db_file)
    load_real_transcripts_into_corpus(conn)

    df_table = compute_document_frequencies(conn)
    cursor = conn.execute("SELECT count(*) as c FROM turns")
    total_turns = cursor.fetchone()["c"]

    print("\nCONTOH DOCUMENT FREQUENCY (DF) KATA-KATA DALAM CORPUS (Total Turns: %d):" % total_turns)
    sample_words = [
        "python", "kode", "git", "file", "database", "sqlite", "fastapi", "vue", "rust",
        "caddy", "vim", "tmux", "9042", "budi", "redis", "fabfile", "error", "framework",
        "service", "pembayaran", "staging", "server"
    ]
    for w in sample_words:
        pct = df_table.get(w, 0.0)
        print(f"  - '{w:<12}': {pct:5.2f}% turn (len={len(w)})")

    # Evaluasi Rules
    rules_to_test = [
        ("old_len7", 0.0, "Aturan Lama (Len >= 7)"),
        ("new_df_direct", 5.0, "Direct DF <= 5%"),
        ("new_df_direct", 10.0, "Direct DF <= 10%"),
        ("new_df_direct", 15.0, "Direct DF <= 15%"),
        ("new_df_direct", 20.0, "Direct DF <= 20%"),
        ("new_df_gated", 5.0, "Gated DF <= 5%"),
        ("new_df_gated", 10.0, "Gated DF <= 10%"),
        ("new_df_gated", 15.0, "Gated DF <= 15%"),
        ("new_df_gated", 20.0, "Gated DF <= 20%"),
    ]

    print("\n" + "=" * 90)
    print("HASIL KALIBRASI RECALL PASIF (40 NEGATIF, 15 POSITIF):")
    print(f"{'Metode / Threshold':<26} | {'FP Negatif (n=40)':<18} | {'TP Positif (n=15)':<18} | {'Presisi':<10} | {'Recall':<10}")
    print("-" * 90)

    for rule_code, th_val, label in rules_to_test:
        fp_count = 0
        for neg_prompt in NEGATIVE_PROMPTS:
            triggered = test_passive_recall_rule(conn, neg_prompt, rule_code, df_table, df_threshold_pct=th_val)
            if triggered:
                fp_count += 1

        tp_count = 0
        for pos_item in POSITIVE_PROMPTS:
            triggered = test_passive_recall_rule(conn, pos_item["prompt"], rule_code, df_table, df_threshold_pct=th_val)
            if triggered:
                tp_count += 1

        fp_pct = (fp_count / len(NEGATIVE_PROMPTS)) * 100
        tp_pct = (tp_count / len(POSITIVE_PROMPTS)) * 100

        precision = (tp_count / (tp_count + fp_count)) * 100 if (tp_count + fp_count) > 0 else 0.0
        recall = tp_pct

        fp_str = f"{fp_count}/40 ({fp_pct:.1f}%)"
        tp_str = f"{tp_count}/15 ({tp_pct:.1f}%)"
        print(f"{label:<26} | {fp_str:<18} | {tp_str:<18} | {precision:5.1f}%    | {recall:5.1f}%")

    print("=" * 90)

    conn.close()
    if db_file.exists():
        db_file.unlink()

if __name__ == "__main__":
    main()
