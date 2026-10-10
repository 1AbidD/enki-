import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.test_passive_recall_frozen_suite import FROZEN_NEGATIVE_MARKER_PROMPTS
from tests.calibrate_new_dual_corpus import setup_corpus_db, compute_df
from enki.memory.recall import extract_substantive_tokens

db_path = Path('tests/results/corpus_Corpus_B_Realistis.db')
conn = setup_corpus_db(db_path, is_realistic=True)
df_table, total_docs = compute_df(conn)

print(f'=== RINCIAN 13 FALSE POSITIVES PADA CORPUS B (TOTAL DOCS: {total_docs}) ===')
fp_idx = 1
for item in FROZEN_NEGATIVE_MARKER_PROMPTS:
    prompt = item['prompt']
    tokens = extract_substantive_tokens(prompt)
    fts_query = ' OR '.join(f'"{t}"' for t in tokens)
    sql = 'SELECT turn_id, session_id, role, content FROM turns_fts WHERE turns_fts MATCH ? LIMIT 5'
    cursor = conn.execute(sql, (fts_query,))
    candidates = cursor.fetchall()
    
    for c in candidates:
        content_lower = c['content'].lower()
        matched = {t for t in tokens if re.search(r'\b' + re.escape(t) + r'\b', content_lower)}
        if len(matched) >= 2:
            low_df = {t for t in matched if df_table.get(t, 0.0) <= 5.0}
            if len(low_df) >= 1:
                df_details = {t: f'{df_table.get(t, 0.0):.2f}%' for t in matched}
                print(f'{fp_idx}. ID: {item["id"]}')
                print(f'   Query           : "{prompt}"')
                print(f'   Matched Tokens  : {list(matched)}')
                print(f'   DF Tokens       : {df_details}')
                print(f'   Low DF (<=5%)   : {list(low_df)}')
                print(f'   Turn Terpanggil : [{c["turn_id"]}] ({c["role"]}) "{c["content"]}"')
                print(f'   Sumber          : {c["session_id"]}')
                fp_idx += 1
                break

conn.close()
if db_path.exists():
    db_path.unlink()
