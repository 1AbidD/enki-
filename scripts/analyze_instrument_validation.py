import json
from pathlib import Path
from collections import defaultdict

data = json.loads(Path("docs/evidence/instrument_validation.json").read_text(encoding="utf-8"))

by_prompt = defaultdict(list)
for r in data:
    by_prompt[r["id"]].append(r)

print("=" * 80)
print("12 PROMPT VALIDASI LENGKAP & JUMLAH JAWABAN UNIK ANTAR 3 SEED")
print("=" * 80)
total_unique = 0
for pid, recs in by_prompt.items():
    prompt_text = recs[0]["prompt"]
    unique_answers = set(r["final_response"].strip() for r in recs)
    total_unique += len(unique_answers)
    print(f"[{pid}] (Jawaban Unik: {len(unique_answers)}/3)")
    print(f"  Prompt: {prompt_text}")

print(f"\nTotal Jawaban Unik di 36 run: {total_unique} / 36")
print(f"Jumlah Sampel Independen Efektif (Prompt Unik): {len(by_prompt)} prompt")

print("\n" + "=" * 80)
print("JAWABAN LENGKAP VAL_FILE_04 (SEED 42, 43, 44)")
print("=" * 80)
for r in by_prompt["VAL_FILE_04"]:
    print(f"--- Seed {r['seed']} ---")
    print(r["final_response"])

print("\n" + "=" * 80)
print("JAWABAN LENGKAP 3 BARIS ACAK: VAL_FILE_02, VAL_GIT_01, VAL_TEST_03")
print("=" * 80)
for pid in ["VAL_FILE_02", "VAL_GIT_01", "VAL_TEST_03"]:
    print(f"\n*** Prompt ID: {pid} ***")
    for r in by_prompt[pid]:
        print(f"--- Seed {r['seed']} ---")
        print(r["final_response"])
