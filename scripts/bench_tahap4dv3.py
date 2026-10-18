"""Skrip Evaluasi HELD-OUT-V3 Tahap 4D-v3.

Menjalankan evaluasi:
1. Positif (R1-R10) melalui router kemampuan deterministik (Q1).
2. Negatif (N1-N8) melalui router kemampuan deterministik (Q2).
3. Regresi percakapan (6 prompt x 3 ulangan = 18 run) pada model LLM qwen2.5:3b (Q4).
4. Pengukuran latensi router (Q5).
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from enki.config import AppConfig
from enki.core.capability_router import route_capability_inquiry
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT

HELDOUT_PATH = Path("docs/heldout_4dv3.json")
EVIDENCE_PATH = Path("docs/evidence/tahap4dv3_heldout.txt")
LATENCY_PATH = Path("docs/evidence/tahap4dv3_latency.txt")

SLASH_CMDS = [
    "/baca", "/dir", "/git", "/test", "/versi", "/ingat", "/simpan",
    "/memory", "/reflect", "/coder", "/chat", "/mute", "/unmute",
    "/status", "/clear", "/exit", "/help"
]

def query_ollama(prompt: str, system_prompt: str, model: str = "qwen2.5:3b", temperature: float = 0.2, num_ctx: int = 8192) -> dict:
    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ],
        "options": {
            "temperature": temperature,
            "num_ctx": num_ctx
        },
        "stream": False
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))

def main():
    print("=" * 80)
    print("MEMULAI EVALUASI HELD-OUT TAHAP 4D-v3 (ROUTER DETERMINISTIK)")
    print("=" * 80)

    if not HELDOUT_PATH.exists():
        print(f"Error: {HELDOUT_PATH} tidak ditemukan!")
        sys.exit(1)

    dataset = json.loads(HELDOUT_PATH.read_text(encoding="utf-8"))
    positif_list = dataset.get("positif", [])
    negatif_list = dataset.get("negatif", [])
    regresi_list = dataset.get("regresi_percakapan", [])

    lines = []
    lines.append("=" * 90)
    lines.append("LAPORAN BATERAI UJI TAHAP 4D-v3: HELD-OUT V3 (ROUTER DETERMINISTIK + LLM REGRESI)")
    lines.append("=" * 90)
    lines.append(f"Timestamp (UTC)     : {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"Model Percakapan    : qwen2.5:3b")
    lines.append(f"Num Ctx             : 8192")
    lines.append(f"Temperature         : 0.2")

    assembler = PromptAssembler()
    msgs, _ = assembler.assemble_messages("halo")
    sys_content = msgs[0]["content"]

    lines.append("=" * 90)
    lines.append("\n[SYSTEM PROMPT DIGUNAKAN UNTUK REGRESI MODEL]:")
    lines.append("-" * 60)
    lines.append(sys_content)
    lines.append("-" * 60)

    # -------------------------------------------------------------
    # 1. EVALUASI KELOMPOK POSITIF (R1 - R10)
    # -------------------------------------------------------------
    lines.append("\n" + "=" * 90)
    lines.append("KELOMPOK POSITIF HELD-OUT (10 PROMPT) -> ROUTER DETERMINISTIK")
    lines.append("=" * 90)

    pos_pass_count = 0
    pos_latencies = []

    for item in positif_list:
        code = item["code"]
        prompt = item["prompt"]
        expected_intent = item["intent"]
        rule_desc = item.get("rule", "")

        t0 = time.perf_counter()
        res = route_capability_inquiry(prompt)
        dt_ms = (time.perf_counter() - t0) * 1000
        pos_latencies.append(dt_ms)

        is_intent_ok = (res is not None and res.intent == expected_intent)
        rule_ok = False
        reply_text = res.reply if res else "(TIDAK DIRUTING / None)"

        if res:
            r_lower = reply_text.lower()
            if expected_intent == "LIST_COMMANDS":
                found_cmds = [c for c in SLASH_CMDS if c in r_lower]
                rule_ok = len(found_cmds) >= 5
            elif expected_intent in ["CAN_INTERNET", "CAN_SCREEN"]:
                rule_ok = ("tidak" in r_lower or "belum" in r_lower)
            elif expected_intent == "CAN_READ_FILE":
                rule_ok = ("/baca" in r_lower)
            elif expected_intent == "CAN_TEST":
                rule_ok = ("/test" in r_lower)
            elif expected_intent == "CAN_SPEAK":
                rule_ok = ("/unmute" in r_lower)
            elif expected_intent == "CAN_REMEMBER":
                rule_ok = ("/simpan" in r_lower or "/ingat" in r_lower)

        full_pass = is_intent_ok and rule_ok
        if full_pass:
            pos_pass_count += 1

        status_str = "LULUS" if full_pass else "GAGAL"
        lines.append(f"\n--- [{code}] Prompt: '{prompt}' | Status: {status_str} ---")
        lines.append(f"  Expected Intent : {expected_intent} | Rule: {rule_desc}")
        lines.append(f"  Detected Intent : {res.intent if res else None} | Latensi: {dt_ms:.3f} ms")
        lines.append(f"  Jawaban Router  :\n{reply_text}")

    lines.append(f"\nRingkasan Kelompok Positif: {pos_pass_count}/10 prompt lulus.")

    # -------------------------------------------------------------
    # 2. EVALUASI KELOMPOK NEGATIF (N1 - N8)
    # -------------------------------------------------------------
    lines.append("\n" + "=" * 90)
    lines.append("KELOMPOK NEGATIF HELD-OUT (8 PROMPT) -> HARUS 0 DIRUTING (None)")
    lines.append("=" * 90)

    neg_routed_count = 0
    for item in negatif_list:
        code = item["code"]
        prompt = item["prompt"]

        t0 = time.perf_counter()
        res = route_capability_inquiry(prompt)
        dt_ms = (time.perf_counter() - t0) * 1000

        is_routed = (res is not None)
        if is_routed:
            neg_routed_count += 1

        status_str = "LULUS (Tidak Dirouting)" if not is_routed else f"GAGAL (Bocor Dirouting ke {res.intent})"
        lines.append(f"\n--- [{code}] Prompt: '{prompt}' | Status: {status_str} ---")
        lines.append(f"  Detected Intent : {res.intent if res else None} | Latensi: {dt_ms:.3f} ms")
        if res:
            lines.append(f"  Jawaban Router  :\n{res.reply}")

    lines.append(f"\nRingkasan Kelompok Negatif: {neg_routed_count}/8 prompt dirouting (Target: 0).")

    # -------------------------------------------------------------
    # 3. EVALUASI REGRESI PERCAKAPAN MODEL (6 PROMPT x 3 ULANGAN = 18 RUN)
    # -------------------------------------------------------------
    lines.append("\n" + "=" * 90)
    lines.append("KELOMPOK REGRESI PERCAKAPAN MODEL (6 PROMPT x 3 ULANGAN = 18 RUN) -> qwen2.5:3b")
    lines.append("=" * 90)

    slash_leak_count = 0
    joke_reject_count = 0
    joke_runs_ok = 0

    forbidden_joke_phrases = ["tidak bisa memberikan", "tidak dapat memberikan", "sesuai aturan"]

    for item in regresi_list:
        code = item["code"]
        prompt = item["prompt"]
        lines.append(f"\n--- [{code}] Prompt: '{prompt}' (3 Ulangan) ---")

        for run_idx in range(1, 4):
            print(f"Menjalankan Model [{code}] Run {run_idx}/3: '{prompt}'...")
            t0 = time.time()
            resp = query_ollama(prompt=prompt, system_prompt=sys_content)
            dur = round(time.time() - t0, 3)

            msg = resp.get("message", {}).get("content", "").strip()
            msg_lower = msg.lower()

            # Cek kebocoran slash
            leaked = [cmd for cmd in SLASH_CMDS if cmd in msg_lower]
            if leaked:
                slash_leak_count += 1

            # Cek lelucon jika prompt ceritain hal lucu
            joke_ok = True
            if "lucu" in prompt.lower():
                has_forbidden = any(f in msg_lower for f in forbidden_joke_phrases)
                has_length = len(msg) >= 60
                if has_forbidden or not has_length:
                    joke_reject_count += 1
                    joke_ok = False
                else:
                    joke_runs_ok += 1

            lines.append(f"  Run {run_idx} ({dur}s):")
            lines.append(f"    Slash Leaks : {leaked}")
            if "lucu" in prompt.lower():
                lines.append(f"    Joke Evaluated: Length={len(msg)} chars, Forbidden Phrases={any(f in msg_lower for f in forbidden_joke_phrases)}, Pass={joke_ok}")
            lines.append(f"    Jawaban Mentah :\n{msg}")

    lines.append(f"\nRingkasan Regresi Percakapan:")
    lines.append(f"  Total Slash Leaks : {slash_leak_count}/18 run (Target: 0)")
    lines.append(f"  Lelucon Pass      : {joke_runs_ok}/3 run (Target: 3/3)")

    # -------------------------------------------------------------
    # STATUS AKHIR
    # -------------------------------------------------------------
    q1_pass = (pos_pass_count >= 9)
    q2_pass = (neg_routed_count == 0)
    q4_pass = (slash_leak_count == 0 and joke_runs_ok == 3)

    lines.append("\n" + "=" * 90)
    lines.append("STATUS AKHIR HELD-OUT V3:")
    lines.append(f"  Q1 Positif (>=9/10 lulus)              : {'LULUS' if q1_pass else 'GAGAL'} ({pos_pass_count}/10)")
    lines.append(f"  Q2 Negatif (0/8 dirouting)             : {'LULUS' if q2_pass else 'GAGAL'} ({neg_routed_count}/8 dirouting)")
    lines.append(f"  Q4 Regresi Percakapan (0 slash, 3 joke): {'LULUS' if q4_pass else 'GAGAL'} (leaks={slash_leak_count}, joke={joke_runs_ok}/3)")
    lines.append("=" * 90)

    EVIDENCE_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nBukti tersimpan di: {EVIDENCE_PATH}")

    # -------------------------------------------------------------
    # 4. PENGUKURAN LATENSI RESMI ROUTER (100 SAMPLE)
    # -------------------------------------------------------------
    print("Mengukur latensi router secara presisi (100 sampel)...")
    all_sample_prompts = [p["prompt"] for p in positif_list] + [p["prompt"] for p in negatif_list]
    lat_records = []
    for _ in range(10):
        for p in all_sample_prompts:
            t_start = time.perf_counter()
            _ = route_capability_inquiry(p)
            dt = (time.perf_counter() - t_start) * 1000
            lat_records.append(dt)

    lat_records.sort()
    med_lat = lat_records[len(lat_records) // 2]
    p95_lat = lat_records[int(len(lat_records) * 0.95)]
    min_lat = min(lat_records)
    max_lat = max(lat_records)

    lat_report = (
        f"PENGUKURAN LATENSI ROUTER DETERMINISTIK (Tahap 4D-v3)\n"
        f"Jumlah Sampel : {len(lat_records)}\n"
        f"Min Latensi   : {min_lat:.4f} ms\n"
        f"Median Latensi: {med_lat:.4f} ms (Target <= 20.0 ms)\n"
        f"P95 Latensi   : {p95_lat:.4f} ms\n"
        f"Max Latensi   : {max_lat:.4f} ms\n"
        f"Status Q5     : {'LULUS' if med_lat <= 20.0 else 'GAGAL'}\n"
    )
    LATENCY_PATH.write_text(lat_report, encoding="utf-8")
    print(lat_report)

if __name__ == "__main__":
    main()
