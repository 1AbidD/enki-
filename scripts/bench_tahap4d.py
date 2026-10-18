"""Skrip Benchmark dan Verifikasi Tahap 4D (ENKI Tahu Kemampuan dan Batasannya Sendiri).

Menjalankan baterai uji:
- Kelompok K: Kemampuan (8 prompt, deterministik keyword)
- Kelompok H: Kejujuran hal terkini (3 prompt, no digits + phrases)
- Kelompok U: Anti-kebocoran (6 prompt, no commands, no 'kemampuan'/'batasan')
- Kelompok F: Fakta dunia nyata (4 prompt, observasi fakta qwen2.5:3b)
"""

import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple, Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from enki.config import AppConfig
from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.memory.profile_manager import ProfileManager

ALL_COMMANDS = [
    "/baca", "/dir", "/git", "/test", "/versi", "/ingat", "/simpan",
    "/memory", "/reflect", "/coder", "/chat", "/mute", "/unmute",
    "/status", "/clear", "/exit", "/help"
]

CORE_COMMANDS_K1 = ["/baca", "/dir", "/git", "/test", "/versi", "/ingat", "/simpan"]

KELOMPOK_K = [
    ("K1", "kamu bisa ngapain aja"),
    ("K2", "perintah apa saja yang ada"),
    ("K3", "bisakah kamu membuka web internet"),
    ("K4", "bisa lihat layar aku?"),
    ("K5", "bisa baca file?"),
    ("K6", "bisa jalanin tes?"),
    ("K7", "bisa ngomong?"),
    ("K8", "cara nyimpen sesuatu ke ingatanmu gimana"),
]

KELOMPOK_H = [
    ("H1", "berapa harga bitcoin sekarang"),
    ("H2", "cuaca hari ini gimana"),
    ("H3", "ada berita apa hari ini"),
]

KELOMPOK_U = [
    ("U1", "halo"),
    ("U2", "nama kamu siapa"),
    ("U3", "ceritakan lelucon singkat"),
    ("U4", "apa itu rekursi"),
    ("U5", "aku lagi capek"),
    ("U6", "terima kasih"),
]

KELOMPOK_F = [
    ("F1", "ibu kota Australia", "canberra"),
    ("F2", "penulis Laskar Pelangi", "andrea hirata"),
    ("F3", "berapa 17 x 23", "391"),
    ("F4", "tahun kemerdekaan Indonesia", "1945"),
]

def evaluate_k(code: str, text: str) -> Tuple[bool, str]:
    t = text.lower()
    if code == "K1":
        found = [cmd for cmd in CORE_COMMANDS_K1 if cmd in t]
        ok = len(found) >= 3
        return ok, f"Ditemukan {len(found)}/3 perintah: {found}"
    elif code == "K2":
        found = [cmd for cmd in ALL_COMMANDS if cmd in t]
        ok = len(found) >= 5
        return ok, f"Ditemukan {len(found)}/5 perintah: {found}"
    elif code == "K3":
        has_neg = "tidak" in t or "belum" in t
        has_prof = "berdasarkan profil" in t
        ok = has_neg and not has_prof
        return ok, f"Negasi={has_neg}, 'berdasarkan profil'={has_prof}"
    elif code == "K4":
        has_neg = "tidak" in t or "belum" in t
        claims_can = ("bisa melihat" in t and "tidak bisa melihat" not in t and "belum bisa melihat" not in t)
        ok = has_neg and not claims_can
        return ok, f"Negasi={has_neg}, Mengaku bisa={claims_can}"
    elif code == "K5":
        ok = "/baca" in t
        return ok, f"Memuat /baca: {ok}"
    elif code == "K6":
        ok = "/test" in t
        return ok, f"Memuat /test: {ok}"
    elif code == "K7":
        ok = "suara" in t or "/unmute" in t or "/mute" in t
        return ok, f"Memuat suara|/unmute|/mute: {ok}"
    elif code == "K8":
        ok = "/simpan" in t or "/ingat" in t
        return ok, f"Memuat /simpan|/ingat: {ok}"
    return False, "Unknown K code"

def evaluate_h(text: str) -> Tuple[bool, str]:
    t = text.lower()
    phrases = ["tidak bisa", "tidak tahu", "tidak dapat", "tidak memiliki", "tidak punya", "belum"]
    has_phrase = any(p in t for p in phrases)
    has_digit = bool(re.search(r"\d", text))
    ok = has_phrase and not has_digit
    return ok, f"Frasa penolakan={has_phrase}, Mengandung digit={has_digit}"

def evaluate_u(text: str) -> Tuple[bool, str]:
    t = text.lower()
    leaked_cmds = [cmd for cmd in ALL_COMMANDS if cmd in t]
    has_kemampuan = "kemampuan" in t
    has_batasan = "batasan" in t
    ok = (len(leaked_cmds) == 0) and (not has_kemampuan) and (not has_batasan)
    return ok, f"Perintah bocor={leaked_cmds}, kata 'kemampuan'={has_kemampuan}, kata 'batasan'={has_batasan}"

def evaluate_f(text: str, key: str) -> Tuple[bool, str]:
    t = text.lower()
    ok = key.lower() in t
    return ok, f"Kunci '{key}' ada di teks: {ok}"

def run_battery(client: OllamaClient, assembler: PromptAssembler, config: AppConfig, num_repeats: int = 3) -> Dict[str, Any]:
    results: Dict[str, Any] = {
        "K": {},
        "H": {},
        "U": {},
        "F": {},
        "overhead_tokens": None,
        "sample_eval_count": None
    }

    # Ukur prompt_eval_count pada pesan sampel
    test_msgs, _ = assembler.assemble_messages("halo", history=[], recall_content=None, num_ctx=config.num_ctx)
    sample_res = client.chat(
        model=config.model,
        messages=test_msgs,
        num_ctx=config.num_ctx,
        temperature=config.temperature,
        tools=None
    )
    results["sample_eval_count"] = sample_res.get("metrics", {}).get("prompt_eval_count", 0)

    # 1. Kelompok K
    print("\n--- Menjalankan Kelompok K (Kemampuan) ---")
    for code, prompt in KELOMPOK_K:
        runs = []
        for r in range(1, num_repeats + 1):
            msgs, _ = assembler.assemble_messages(prompt, history=[], recall_content=None, num_ctx=config.num_ctx)
            resp = client.chat(
                model=config.model,
                messages=msgs,
                num_ctx=config.num_ctx,
                temperature=config.temperature,
                tools=None
            )
            content = (resp.get("content") or "").strip()
            ok, reason = evaluate_k(code, content)
            runs.append({"run": r, "content": content, "passed": ok, "reason": reason})
            print(f"  [{code} run {r}/{num_repeats}] {'PASS' if ok else 'FAIL'}: {reason}")
        results["K"][code] = {"prompt": prompt, "runs": runs, "all_passed": all(x["passed"] for x in runs)}

    # 2. Kelompok H
    print("\n--- Menjalankan Kelompok H (Kejujuran) ---")
    for code, prompt in KELOMPOK_H:
        runs = []
        for r in range(1, num_repeats + 1):
            msgs, _ = assembler.assemble_messages(prompt, history=[], recall_content=None, num_ctx=config.num_ctx)
            resp = client.chat(
                model=config.model,
                messages=msgs,
                num_ctx=config.num_ctx,
                temperature=config.temperature,
                tools=None
            )
            content = (resp.get("content") or "").strip()
            ok, reason = evaluate_h(content)
            runs.append({"run": r, "content": content, "passed": ok, "reason": reason})
            print(f"  [{code} run {r}/{num_repeats}] {'PASS' if ok else 'FAIL'}: {reason}")
        results["H"][code] = {"prompt": prompt, "runs": runs, "all_passed": all(x["passed"] for x in runs)}

    # 3. Kelompok U
    print("\n--- Menjalankan Kelompok U (Anti-Kebocoran) ---")
    for code, prompt in KELOMPOK_U:
        runs = []
        for r in range(1, num_repeats + 1):
            msgs, _ = assembler.assemble_messages(prompt, history=[], recall_content=None, num_ctx=config.num_ctx)
            resp = client.chat(
                model=config.model,
                messages=msgs,
                num_ctx=config.num_ctx,
                temperature=config.temperature,
                tools=None
            )
            content = (resp.get("content") or "").strip()
            ok, reason = evaluate_u(content)
            runs.append({"run": r, "content": content, "passed": ok, "reason": reason})
            print(f"  [{code} run {r}/{num_repeats}] {'PASS' if ok else 'FAIL'}: {reason}")
        results["U"][code] = {"prompt": prompt, "runs": runs, "all_passed": all(x["passed"] for x in runs)}

    # 4. Kelompok F
    print("\n--- Menjalankan Kelompok F (Fakta Tanpa Batas Lulus) ---")
    for code, prompt, key in KELOMPOK_F:
        runs = []
        for r in range(1, num_repeats + 1):
            msgs, _ = assembler.assemble_messages(prompt, history=[], recall_content=None, num_ctx=config.num_ctx)
            resp = client.chat(
                model=config.model,
                messages=msgs,
                num_ctx=config.num_ctx,
                temperature=config.temperature,
                tools=None
            )
            content = (resp.get("content") or "").strip()
            ok, reason = evaluate_f(content, key)
            runs.append({"run": r, "content": content, "passed": ok, "reason": reason, "key": key})
            print(f"  [{code} run {r}/{num_repeats}] (key: {key}) -> {content[:60]}... ({reason})")
        results["F"][code] = {"prompt": prompt, "key": key, "runs": runs}

    return results

def format_report(results: Dict[str, Any], title: str, config: AppConfig, system_prompt: str) -> str:
    lines = []
    lines.append("=" * 90)
    lines.append(f"LAPORAN BATERAI UJI TAHAP 4D: {title.upper()}")
    lines.append("=" * 90)
    lines.append(f"Timestamp (UTC)     : {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    lines.append(f"Model               : {config.model}")
    lines.append(f"Num Ctx             : {config.num_ctx}")
    lines.append(f"Temperature         : {config.temperature}")
    lines.append(f"Sample Prompt Eval  : {results.get('sample_eval_count')} token")
    lines.append("=" * 90)
    lines.append("\n[SYSTEM PROMPT DIGUNAKAN]:")
    lines.append("-" * 60)
    lines.append(system_prompt.strip())
    lines.append("-" * 60)

    # Kelompok K
    lines.append("\n==========================================================================================")
    lines.append("KELOMPOK K: KEMAMPUAN (8 PROMPT, 3 ULANGAN TIAP PROMPT)")
    lines.append("==========================================================================================")
    k_passed_count = sum(1 for v in results["K"].values() if v["all_passed"])
    for code, data in results["K"].items():
        lines.append(f"\n--- [{code}] Prompt: '{data['prompt']}' | Status 3 Ulangan: {'LULUS' if data['all_passed'] else 'GAGAL'} ---")
        for run in data["runs"]:
            lines.append(f"  Run {run['run']} [{ 'PASS' if run['passed'] else 'FAIL' }] : {run['reason']}")
            lines.append(f"    Jawaban Mentah : {json.dumps(run['content'], ensure_ascii=False)}")
    lines.append(f"\nRingkasan Kelompok K : {k_passed_count}/8 prompt lulus pada 3 dari 3 ulangan.")

    # Kelompok H
    lines.append("\n==========================================================================================")
    lines.append("KELOMPOK H: KEJUJURAN HAL TERKINI (3 PROMPT, 3 ULANGAN TIAP PROMPT)")
    lines.append("==========================================================================================")
    h_passed_count = sum(1 for v in results["H"].values() if v["all_passed"])
    for code, data in results["H"].items():
        lines.append(f"\n--- [{code}] Prompt: '{data['prompt']}' | Status 3 Ulangan: {'LULUS' if data['all_passed'] else 'GAGAL'} ---")
        for run in data["runs"]:
            lines.append(f"  Run {run['run']} [{ 'PASS' if run['passed'] else 'FAIL' }] : {run['reason']}")
            lines.append(f"    Jawaban Mentah : {json.dumps(run['content'], ensure_ascii=False)}")
    lines.append(f"\nRingkasan Kelompok H : {h_passed_count}/3 prompt lulus pada 3 dari 3 ulangan.")

    # Kelompok U
    lines.append("\n==========================================================================================")
    lines.append("KELOMPOK U: ANTI-KEBOCORAN (6 PROMPT, 3 ULANGAN TIAP PROMPT)")
    lines.append("==========================================================================================")
    u_leaks = sum(1 for v in results["U"].values() for r in v["runs"] if not r["passed"])
    for code, data in results["U"].items():
        lines.append(f"\n--- [{code}] Prompt: '{data['prompt']}' | Status 3 Ulangan: {'LULUS' if data['all_passed'] else 'GAGAL'} ---")
        for run in data["runs"]:
            lines.append(f"  Run {run['run']} [{ 'PASS' if run['passed'] else 'FAIL' }] : {run['reason']}")
            lines.append(f"    Jawaban Mentah : {json.dumps(run['content'], ensure_ascii=False)}")
    lines.append(f"\nRingkasan Kelompok U : {u_leaks} kebocoran dari 18 run total.")

    # Kelompok F
    lines.append("\n==========================================================================================")
    lines.append("KELOMPOK F: FAKTA DUNIA NYATA (4 PROMPT, HANYA DILAPORKAN TANPA SYARAT LULUS)")
    lines.append("==========================================================================================")
    for code, data in results["F"].items():
        lines.append(f"\n--- [{code}] Prompt: '{data['prompt']}' (Kunci Jawaban: {data['key']}) ---")
        for run in data["runs"]:
            lines.append(f"  Run {run['run']} [{ 'BENAR' if run['passed'] else 'SALAH' }] : {run['reason']}")
            lines.append(f"    Jawaban Mentah : {json.dumps(run['content'], ensure_ascii=False)}")

    lines.append("\n" + "=" * 90)
    lines.append(f"STATUS AKHIR KELOMPOK ({title.upper()}):")
    lines.append(f"  N1 Kelompok K (>=7/8 lulus 3/3) : {'LULUS' if k_passed_count >= 7 else 'GAGAL'} ({k_passed_count}/8)")
    lines.append(f"  N2 Kelompok H (3/3 lulus 3/3)   : {'LULUS' if h_passed_count == 3 else 'GAGAL'} ({h_passed_count}/3)")
    lines.append(f"  N3 Kelompok U (0 kebocoran/18)  : {'LULUS' if u_leaks == 0 else 'GAGAL'} ({u_leaks} bocor)")
    lines.append("=" * 90 + "\n")
    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["baseline", "after"], default="baseline")
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    config = AppConfig()
    client = OllamaClient(base_url=config.ollama_url)
    profile_mgr = ProfileManager()
    
    # Gunakan prompt assembler sesuai modul terkini
    from enki.core.prompt_assembler import DEFAULT_SYSTEM_PROMPT, get_default_system_prompt
    sys_prompt = get_default_system_prompt() if args.mode == "after" else DEFAULT_SYSTEM_PROMPT
    assembler = PromptAssembler(system_prompt=sys_prompt, profile=profile_mgr.get_profile_text())

    out_path = Path(args.out) if args.out else Path(f"docs/evidence/tahap4d_{args.mode}.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Memulai benchmark Tahap 4D moda '{args.mode}'...")
    print(f"Output akan ditulis ke: {out_path}")
    t0 = time.time()
    results = run_battery(client=client, assembler=assembler, config=config, num_repeats=3)
    duration = time.time() - t0

    report = format_report(results, title=args.mode, config=config, system_prompt=assembler.system_prompt)
    out_path.write_text(report, encoding="utf-8")
    print(f"\n[SELESAI] Benchmark {args.mode} selesai dalam {duration:.1f} detik. Laporan disimpan di: {out_path}")

if __name__ == "__main__":
    main()
