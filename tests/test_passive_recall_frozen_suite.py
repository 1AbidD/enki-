"""
Suite Uji Tambahan Passive Recall (Dibekukan / Frozen):
- 21 Prompt Negatif Berpenanda (mengandung marker konteks & berbagi token dengan korpus pengembang)
- 13 Prompt Positif Tanpa Penanda (memuat target fakta memori tanpa penanda konteks dan tanpa pemicu aktif)
"""

import math
import sqlite3
import re
from typing import Dict, Any, List, Tuple
import sys
from pathlib import Path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from enki.memory.recall import extract_substantive_tokens, PASSIVE_CONTEXT_MARKERS_REGEX
from tests.calibrate_new_dual_corpus import (
    CORPUS_MEMORY_FACTS,
    CONVERSATION_TURNS,
    setup_corpus_db,
    compute_df
)

# ----------------------------------------------------------------------
# 1. Test Set Tambahan Dibekukan (FROZEN SUITE)
# ----------------------------------------------------------------------
FROZEN_NEGATIVE_MARKER_PROMPTS = [
    {"id": "FROZEN_NEG_M_01", "prompt": "gimana cara bikin branch baru di git yang biasa kita pakai?"},
    {"id": "FROZEN_NEG_M_02", "prompt": "apa beda git commit sama git push di workflow tim kita?"},
    {"id": "FROZEN_NEG_M_03", "prompt": "bagaimana cara mengatasi syntax error di kodingan python proyek kita?"},
    {"id": "FROZEN_NEG_M_04", "prompt": "kenapa virtual environment python penting buat proyekku?"},
    {"id": "FROZEN_NEG_M_05", "prompt": "gimana best practice menulis unit test di repo kita?"},
    {"id": "FROZEN_NEG_M_06", "prompt": "apa bedanya struktur data list dan dictionary di python kita?"},
    {"id": "FROZEN_NEG_M_07", "prompt": "cara membaca file teks baris per baris di python proyekku gimana?"},
    {"id": "FROZEN_NEG_M_08", "prompt": "kegunaan linter kode otomatis untuk tim kita apa ya?"},
    {"id": "FROZEN_NEG_M_09", "prompt": "bagaimana cara merge conflict branch git diselesaikan oleh tim kita?"},
    {"id": "FROZEN_NEG_M_10", "prompt": "fungsi command pip install di environment python kita buat apa?"},
    {"id": "FROZEN_NEG_M_11", "prompt": "kenapa container docker aplikasi kita perlu multi-stage build?"},
    {"id": "FROZEN_NEG_M_12", "prompt": "gimana cara optimasi query database relasional di backend kita?"},
    {"id": "FROZEN_NEG_M_13", "prompt": "apa kelebihan async await dibanding synchronous di service kita?"},
    {"id": "FROZEN_NEG_M_14", "prompt": "format docstring standar PEP 257 di kode python kita seperti apa?"},
    {"id": "FROZEN_NEG_M_15", "prompt": "bagaimana cara membuat rest api dengan response json di proyekku?"},
    {"id": "FROZEN_NEG_M_16", "prompt": "apa kegunaan docker compose untuk menjalankan service lokal kita?"},
    {"id": "FROZEN_NEG_M_17", "prompt": "cara setup webhook github untuk repositori tim kita gimana?"},
    {"id": "FROZEN_NEG_M_18", "prompt": "bagaimana penanganan exception handling yang rapi di fungsi python kita?"},
    {"id": "FROZEN_NEG_M_19", "prompt": "apa beda rebase dan merge saat update branch git kita?"},
    {"id": "FROZEN_NEG_M_20", "prompt": "gimana cara logging error terstruktur di aplikasi backend proyekku?"},
    {"id": "FROZEN_NEG_M_21", "prompt": "kenapa kita butuh schema migration saat ubah struktur database?"}
]

FROZEN_POSITIVE_MARKERLESS_PROMPTS = [
    {"id": "FROZEN_POS_NM_01", "prompt": "database analitik pemrosesan data gudang apa?", "expected_key": "duckdb"},
    {"id": "FROZEN_POS_NM_02", "prompt": "format serialisasi payload data sensor iot apa?", "expected_key": "protobuf"},
    {"id": "FROZEN_POS_NM_03", "prompt": "nama penanggung jawab qa automasi testing siapa?", "expected_key": "rian"},
    {"id": "FROZEN_POS_NM_04", "prompt": "lokasi cloud arsip cadangan data bulanan di mana?", "expected_key": "wasabi"},
    {"id": "FROZEN_POS_NM_05", "prompt": "port service pencarian dokumen internal berapa?", "expected_key": "7700"},
    {"id": "FROZEN_POS_NM_06", "prompt": "protokol komunikasi sinkron antar mikroservis apa?", "expected_key": "grpc"},
    {"id": "FROZEN_POS_NM_07", "prompt": "tempat penyimpanan session cache user di mana?", "expected_key": "keydb"},
    {"id": "FROZEN_POS_NM_08", "prompt": "software edge proxy dan ssl termination apa?", "expected_key": "traefik"},
    {"id": "FROZEN_POS_NM_09", "prompt": "bahasa penulisan ulang modul web scraping apa?", "expected_key": "nim"},
    {"id": "FROZEN_POS_NM_10", "prompt": "software pengumpul aliran data log sistem apa?", "expected_key": "fluentbit"},
    {"id": "FROZEN_POS_NM_11", "prompt": "port running service meilisearch dokumen internal berapa?", "expected_key": "7700"},
    {"id": "FROZEN_POS_NM_12", "prompt": "engine serialisasi binary data sensor iot apa?", "expected_key": "protobuf"},
    {"id": "FROZEN_POS_NM_13", "prompt": "storage vendor backup data bulanan sistem apa?", "expected_key": "wasabi"}
]

def wilson_score_interval(successes: int, total: int, z: float = 1.96) -> Tuple[float, float, float]:
    """Menghitung proporsi dan 95% Wilson Score Interval."""
    if total == 0:
        return 0.0, 0.0, 0.0
    p = successes / total
    denom = 1.0 + (z ** 2) / total
    center = (p + (z ** 2) / (2 * total)) / denom
    margin = (z / denom) * math.sqrt((p * (1 - p) / total) + (z ** 2) / (4 * (total ** 2)))
    low = max(0.0, center - margin)
    high = min(1.0, center + margin)
    return p, low, high

def evaluate_r3_strict(
    conn: sqlite3.Connection,
    prompt: str,
    df_table: Dict[str, float],
    df_threshold_pct: float = 5.0
) -> Tuple[bool, List[str]]:
    """Evaluasi aturan R3_strict:
    1. Penanda konteks aktif / lampau
    2. Minimal 2 token cocok dalam satu turn kandidat (R1)
    3. Minimal 1 dari token yang cocok ber-DF rendah (DF <= df_threshold_pct)
    """
    has_marker = bool(PASSIVE_CONTEXT_MARKERS_REGEX.search(prompt))
    if not has_marker:
        return False, []

    tokens = extract_substantive_tokens(prompt)
    if len(tokens) < 2:
        return False, []

    fts_query = " OR ".join(f'"{t}"' for t in tokens)
    sql = "SELECT content FROM turns_fts WHERE turns_fts MATCH ? LIMIT 5"
    try:
        cursor = conn.execute(sql, (fts_query,))
        candidates = [row["content"] for row in cursor.fetchall()]
    except Exception:
        return False, []

    matched_reasons = []
    for content in candidates:
        content_lower = content.lower()
        matched = {t for t in tokens if re.search(r'\b' + re.escape(t) + r'\b', content_lower)}
        if len(matched) >= 2:
            low_df = {t for t in matched if df_table.get(t, 0.0) <= df_threshold_pct}
            if len(low_df) >= 1:
                matched_reasons.append(f"Turn match: {list(matched)}, Low DF tokens: {list(low_df)}")
                return True, matched_reasons

    return False, []

def run_evaluation_on_corpus(corpus_name: str, is_realistic: bool) -> Dict[str, Any]:
    db_path = Path(f"tests/results/corpus_{corpus_name}.db")
    conn = setup_corpus_db(db_path, is_realistic=is_realistic)
    df_table, total_docs = compute_df(conn)

    # 1. Evaluasi Negatif Berpenanda (FP Rate)
    fp_count = 0
    neg_results = []
    for item in FROZEN_NEGATIVE_MARKER_PROMPTS:
        triggered, reasons = evaluate_r3_strict(conn, item["prompt"], df_table, df_threshold_pct=5.0)
        if triggered:
            fp_count += 1
        neg_results.append({
            "id": item["id"],
            "prompt": item["prompt"],
            "triggered": triggered,
            "reasons": reasons
        })

    # 2. Evaluasi Positif Tanpa Penanda (Recall)
    tp_count = 0
    pos_results = []
    for item in FROZEN_POSITIVE_MARKERLESS_PROMPTS:
        triggered, reasons = evaluate_r3_strict(conn, item["prompt"], df_table, df_threshold_pct=5.0)
        if triggered:
            tp_count += 1
        pos_results.append({
            "id": item["id"],
            "prompt": item["prompt"],
            "triggered": triggered,
            "reasons": reasons
        })

    conn.close()
    if db_path.exists():
        db_path.unlink()

    n_neg = len(FROZEN_NEGATIVE_MARKER_PROMPTS)
    p_fp, low_fp, high_fp = wilson_score_interval(fp_count, n_neg)

    n_pos = len(FROZEN_POSITIVE_MARKERLESS_PROMPTS)
    p_rec, low_rec, high_rec = wilson_score_interval(tp_count, n_pos)

    return {
        "corpus": corpus_name,
        "total_docs": total_docs,
        "negatives": {
            "n": n_neg,
            "fp_count": fp_count,
            "fp_rate": p_fp,
            "ci_95": (low_fp, high_fp),
            "details": neg_results
        },
        "positives_markerless": {
            "n": n_pos,
            "tp_count": tp_count,
            "recall": p_rec,
            "ci_95": (low_rec, high_rec),
            "details": pos_results
        }
    }

if __name__ == "__main__":
    print("=" * 80)
    print("MENJALANKAN EVALUASI RESMI R3_STRICT PADA DUA KORPUS (FROZEN SUITE)")
    print("=" * 80)

    for c_name, is_real in [("Corpus_A_Kecil", False), ("Corpus_B_Realistis", True)]:
        res = run_evaluation_on_corpus(c_name, is_real)
        print(f"\n--- HASIL EVALUASI: {res['corpus']} (Total Turns: {res['total_docs']}) ---")
        
        neg = res["negatives"]
        print(f"1. Kelompok Negatif Berpenanda (Berbagi Topik Developer):")
        print(f"   Sample size (n) : {neg['n']}")
        print(f"   False Proposals : {neg['fp_count']} / {neg['n']} ({neg['fp_rate']*100:.2f}%)")
        print(f"   Wilson 95% CI   : [{neg['ci_95'][0]*100:.2f}%, {neg['ci_95'][1]*100:.2f}%]")
        
        pos = res["positives_markerless"]
        print(f"2. Kelompok Positif Tanpa Penanda Konteks (Keterbatasan Model):")
        print(f"   Sample size (n) : {pos['n']}")
        print(f"   Recalled        : {pos['tp_count']} / {pos['n']} ({pos['recall']*100:.2f}%)")
        print(f"   Wilson 95% CI   : [{pos['ci_95'][0]*100:.2f}%, {pos['ci_95'][1]*100:.2f}%]")

        if neg["fp_rate"] > 0.10:
            print(f"\n[PERINGATAN KRITIS]: FP kelompok berpenanda ({neg['fp_rate']*100:.2f}%) MELEBIHI batas 10%! STOP DAN LAPORKAN!")
            break
        else:
            print(f"   >>> Status Gate: LOLOS (FP <= 10%)")
