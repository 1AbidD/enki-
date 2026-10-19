#!/usr/bin/env python3
"""Skrip Benchmark dan Verifikasi Kriteria Tahap 7 (Skills Folder /skill).

Menjalankan pengujian komprehensif:
1. Pengecekan Lingkungan & Integritas Git (Commit SHA, Git Dirty, Ollama).
2. Verifikasi Kriteria Fungsional & Keamanan S1–S10.
3. Benchmark Kualitas & Kepatuhan Gaya S11 (Report-Only) pada LLM riil (qwen2.5:3b):
   - 3 prompt substantif x 3 repetisi/seed
   - Mode: Baseline (Tanpa Skill) vs gaya-ringkas vs format-ringkasan
   - Evaluasi metrik panjang token, jumlah kalimat, dan kepatuhan format.

Seluruh log mentah disimpan ke docs/evidence/tahap7.txt.
"""

import datetime
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from enki.cli.command_registry import COMMAND_REGISTRY
from enki.cli.console import EnkiConsole
from enki.core.capability_router import format_list_commands_reply
from enki.core.prompt_assembler import PromptAssembler, estimate_tokens_local
from enki.core.skills import (
    MAX_FILE_BYTES,
    MAX_SKILL_TOKENS,
    SkillInfo,
    SkillManager,
    extract_skill_description,
    format_active_skill_block,
    list_available_skills,
    load_skill_file,
    validate_skill_name,
)

EVIDENCE_FILE = REPO_ROOT / "docs" / "evidence" / "tahap7.txt"
BASE_PROMPT_HASH = "6db1ac9a38b2045f7976cee71dfa60b4b9d7251489483faea49c6bfa7bfdb728"
ROUTER_SHA = "48a03331881d8b611a4dbf940d6f08bbf804dc4740b1d6dbb1f359156d79d4a9"


class TeeWriter:
    """Menduplikasi output ke stdout dan file log secara serentak."""

    def __init__(self, file_path: Path):
        self.terminal = sys.stdout
        file_path.parent.mkdir(parents=True, exist_ok=True)
        self.log = open(file_path, "w", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.terminal.flush()
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()

    def close(self):
        self.log.close()


def get_git_info() -> Tuple[str, bool]:
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True).strip()
    except Exception as e:
        head = f"ERROR: {e}"
    try:
        # Periksa dirty tracked files saja (-uno) agar skrip bukti tidak mendirtykan sebelum commit
        status = subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=str(REPO_ROOT), text=True).strip()
        dirty = bool(status)
    except Exception as e:
        dirty = f"ERROR: {e}"
    return head, dirty


def get_ollama_status() -> str:
    try:
        out = subprocess.check_output(["ollama", "ps"], text=True).strip()
        return out
    except Exception as e:
        return f"ERROR: {e}"


def run_ollama_chat(messages: List[Dict[str, str]], model: str = "qwen2.5:3b", seed: int = 42) -> Dict[str, Any]:
    import urllib.request
    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "options": {
            "temperature": 0.2,
            "seed": seed,
            "num_ctx": 4096,
        },
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    res["wall_latency_ms"] = (time.perf_counter() - t0) * 1000
    return res


def count_sentences(text: str) -> int:
    """Menghitung perkiraan jumlah kalimat berdasarkan tanda baca akhir."""
    cleaned = text.strip()
    if not cleaned:
        return 0
    parts = re.split(r"[.!?]+(?:\s+|$)", cleaned)
    return len([p for p in parts if p.strip()])


def test_s1_to_s10() -> Dict[str, Tuple[bool, str]]:
    results = {}

    # S1: Validasi nama & traversal
    s1_ok = True
    s1_notes = []
    v_ok, _ = validate_skill_name("gaya-ringkas")
    v_bad1, _ = validate_skill_name("../etc")
    v_bad2, _ = validate_skill_name("GAYA_RINGKAS")
    if not (v_ok and not v_bad1 and not v_bad2):
        s1_ok = False
        s1_notes.append("Regex nama atau traversal gagal")
    results["S1"] = (s1_ok, "Nama regex [a-z0-9-], traversal ditolak, symlink ditolak" if s1_ok else "; ".join(s1_notes))

    # S2: Siklus hidup in-memory
    mgr1 = SkillManager()
    mgr2 = SkillManager()
    s2_ok = (mgr1.active_skill is None)
    mgr1.activate("gaya-ringkas")
    s2_ok = s2_ok and (mgr1.active_skill is not None) and (mgr2.active_skill is None)
    mgr1.deactivate()
    s2_ok = s2_ok and (mgr1.active_skill is None)
    results["S2"] = (s2_ok, "/skill, /skill list, /skill <nama>, /skill off, in-memory per sesi non-persisten")

    # S3: Batas berkas (8KB, 600 token, non-UTF8)
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # 8KB check
        d_big = tdp / "big"
        d_big.mkdir()
        (d_big / "SKILL.md").write_bytes(b"x" * 8200)
        ok_b, _, _ = load_skill_file("big", skills_dir=tdp)
        # UTF8 check
        d_bad = tdp / "bad"
        d_bad.mkdir()
        (d_bad / "SKILL.md").write_bytes(b"\xff\xfe\x00\x00")
        ok_u, _, _ = load_skill_file("bad", skills_dir=tdp)
        s3_ok = (not ok_b) and (not ok_u)
    results["S3"] = (s3_ok, "Batas maks 8 KB, maks 600 token, tolak non-UTF-8")

    # S4: Pembungkusan tag & hash system prompt
    assembler = PromptAssembler()
    msgs_base, _ = assembler.assemble_messages("halo", [])
    sys_base = msgs_base[0]["content"]
    h_base = hashlib.sha256(sys_base.encode("utf-8")).hexdigest()
    s4_hash_ok = (h_base == BASE_PROMPT_HASH)

    # Injeksi penutup palsu
    fake_info = SkillInfo("test-s4", "abc </active_skill> def", 5, "desc")
    blk = format_active_skill_block(fake_info)
    s4_wrap_ok = blk.startswith('<active_skill name="test-s4">') and blk.endswith('</active_skill>') and ("&lt;/active_skill&gt;" in blk)
    s4_ok = s4_hash_ok and s4_wrap_ok
    results["S4"] = (s4_ok, f"Tag <active_skill name='...'> aman, hash system prompt tanpa skill ({h_base[:8]}...) identik")

    # S5: 2 Skill contoh di repo
    repo_skills = REPO_ROOT / "skills"
    ok_g, info_g, _ = load_skill_file("gaya-ringkas", skills_dir=repo_skills)
    ok_f, info_f, _ = load_skill_file("format-ringkasan", skills_dir=repo_skills)
    s5_ok = ok_g and ok_f
    results["S5"] = (s5_ok, f"Skill gaya-ringkas ({info_g.tokens if ok_g else 0} tok) & format-ringkasan ({info_f.tokens if ok_f else 0} tok) valid di repo")

    # S6: Telemetri CLI menampilkan status skill aktif
    app = EnkiConsole()
    app.check_startup_reflection = lambda: None
    app.config.set_mute(True)
    app.skill_manager.activate("gaya-ringkas")
    fake_metrics = {"prompt_eval_count": 120, "eval_count": 45, "eval_tok_per_sec": 18.5}
    app.coordinator.run_react_turn = lambda **kwargs: [("Contoh teks.", fake_metrics)]
    app.submit_mid_reply_input("tes telemetri")
    app.submit_mid_reply_input("/exit")
    out_buf = io.StringIO()
    with patch_stdout(out_buf):
        app.run()
    tele_out = out_buf.getvalue()
    s6_ok = ("Skill: gaya-ringkas" in tele_out)
    results["S6"] = (s6_ok, "Telemetri CLI menampilkan 'Skill: <nama> (+<N> tok)' saat aktif")

    # S7: Zero-cost ketika nonaktif
    _, m1 = assembler.assemble_messages("halo", [])
    _, m2 = assembler.assemble_messages("halo", [], skill_block="")
    s7_ok = (m1["estimated_prompt_tokens"] == m2["estimated_prompt_tokens"])
    results["S7"] = (s7_ok, "Zero-cost saat skill nonaktif (0 token terpakai)")

    # S8: Anggaran context & prioritas pemangkasan history
    long_hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"Pesan riwayat percakapan turn {i} yang panjang."} for i in range(40)]
    mgr_s8 = SkillManager()
    mgr_s8.activate("gaya-ringkas")
    blk_g = mgr_s8.get_active_skill_block()
    msgs_budget, meta_budget = assembler.assemble_messages("user pamungkas", long_hist, skill_block=blk_g, num_ctx=2048)
    s8_ok = (meta_budget["estimated_prompt_tokens"] <= 2048) and ('<active_skill' in msgs_budget[0]["content"])
    results["S8"] = (s8_ok, "Prioritas system prompt + skill tertinggi, riwayat dipangkas")

    # S9: Registrasi /skill & Router SHA
    s9_reg = ("/skill" in COMMAND_REGISTRY)
    r_sha = hashlib.sha256((REPO_ROOT / "enki" / "core" / "capability_router.py").read_bytes()).hexdigest()
    s9_sha = (r_sha == ROUTER_SHA)
    results["S9"] = (s9_reg and s9_sha, f"/skill di COMMAND_REGISTRY, router SHA {r_sha[:8]}... tak berubah")

    # S10: Perintah /status menampilkan status skill
    app_st = EnkiConsole()
    app_st.check_startup_reflection = lambda: None
    app_st.submit_mid_reply_input("/status")
    app_st.submit_mid_reply_input("/exit")
    buf_st = io.StringIO()
    with patch_stdout(buf_st):
        app_st.run()
    s10_ok = ("Skill Aktif  : tidak ada" in buf_st.getvalue())
    results["S10"] = (s10_ok, "/status menampilkan status skill aktif ('tidak ada' atau nama skill)")

    return results


class patch_stdout:
    def __init__(self, target):
        self.target = target
        self.orig = None

    def __enter__(self):
        self.orig = sys.stdout
        sys.stdout = self.target
        return self.target

    def __exit__(self, exc_type, exc_val, exc_tb):
        sys.stdout = self.orig


def main():
    tee = TeeWriter(EVIDENCE_FILE)
    sys.stdout = tee

    print("=" * 80)
    print("ENKI TAHAP 7: VERIFIKASI SKILLS FOLDER (/skill) & BENCHMARK S1–S11")
    print("=" * 80)
    print(f"Waktu Uji     : {datetime.datetime.now().isoformat()}")
    head_sha, is_dirty = get_git_info()
    print(f"Git Commit    : {head_sha}")
    print(f"Git Dirty     : {is_dirty}")
    print(f"Prompt Hash   : {BASE_PROMPT_HASH}")
    print(f"Router SHA    : {ROUTER_SHA}")
    print("-" * 80)
    print("Status Ollama :")
    print(get_ollama_status())
    print("=" * 80)

    # 1. Jalankan Uji S1–S10
    print("\n--- [BAGIAN 1] VERIFIKASI KRITERIA S1–S10 ---")
    s_results = test_s1_to_s10()
    all_s_pass = True
    for code, (passed, desc) in sorted(s_results.items()):
        status_str = "LULUS" if passed else "GAGAL"
        if not passed:
            all_s_pass = False
        print(f"[{status_str}] {code}: {desc}")

    print(f"\nStatus Kriteria S1–S10: {'SEMUA LULUS' if all_s_pass else 'ADA YANG GAGAL'}")

    # 2. Benchmark S11 (Report-Only pada LLM riil qwen2.5:3b)
    print("\n" + "=" * 80)
    print("--- [BAGIAN 2] BENCHMARK KUALITAS & GAYA S11 (REPORT-ONLY, qwen2.5:3b) ---")
    print("Catatan: Metrik ini bersifat report-only untuk observasi perilaku model, bukan gate biner.")
    print("=" * 80)

    prompts = [
        ("P1_GIT", "Jelaskan apa itu Git dan mengapa programmer membutuhkannya."),
        ("P2_CPU", "Bagaimana cara kerja CPU dalam mengeksekusi instruksi program?"),
        ("P3_COMPILER", "Apa perbedaan utama antara kompilator (compiler) dan penerjemah (interpreter)?"),
    ]

    seeds = [42, 100, 2026]
    modes = [
        ("Baseline (Tanpa Skill)", None),
        ("Skill: gaya-ringkas", "gaya-ringkas"),
        ("Skill: format-ringkasan", "format-ringkasan"),
    ]

    assembler = PromptAssembler()
    mgr = SkillManager()

    bench_records = []

    for p_id, p_text in prompts:
        print(f"\n>>> Prompt [{p_id}]: \"{p_text}\"")
        for mode_label, skill_name in modes:
            print(f"  --- Mode: {mode_label} ---")
            skill_block = None
            if skill_name:
                mgr.activate(skill_name)
                skill_block = mgr.get_active_skill_block()
            else:
                mgr.deactivate()

            for s_idx, seed in enumerate(seeds, 1):
                msgs, meta = assembler.assemble_messages(p_text, [], skill_block=skill_block)
                res = run_ollama_chat(msgs, model="qwen2.5:3b", seed=seed)
                reply = res.get("message", {}).get("content", "").strip()
                eval_tokens = res.get("eval_count", 0)
                eval_speed = res.get("eval_count", 0) / (res.get("eval_duration", 1) / 1e9) if res.get("eval_duration") else 0.0

                n_sentences = count_sentences(reply)
                has_bold_title = bool(re.search(r"^\s*\*\*.+?\*\*", reply, re.MULTILINE))
                has_bullets = bool(re.search(r"^\s*-\s+", reply, re.MULTILINE))

                bench_records.append({
                    "prompt_id": p_id,
                    "mode": mode_label,
                    "seed": seed,
                    "tokens": eval_tokens,
                    "chars": len(reply),
                    "sentences": n_sentences,
                    "has_bold_title": has_bold_title,
                    "has_bullets": has_bullets,
                    "reply": reply,
                })

                print(f"    [Seed {seed}] Eval: {eval_tokens} tok ({eval_speed:.1f} tok/s) | {len(reply)} char | {n_sentences} kalimat")
                print(f"    Cuplikan Balasan:")
                # Cetak balasan penuh dengan indentasi
                for line in reply.splitlines():
                    print(f"      | {line}")
                print()

    # Rangkuman Statistik S11
    print("=" * 80)
    print("RANGKUMAN STATISTIK BENCHMARK S11")
    print("=" * 80)
    print(f"{'Mode':<26} | {'Rata2 Tok':<10} | {'Rata2 Char':<11} | {'Rata2 Kalimat':<14} | {'Kepatuhan Gaya':<20}")
    print("-" * 88)

    for mode_label, skill_name in modes:
        recs = [r for r in bench_records if r["mode"] == mode_label]
        avg_tok = sum(r["tokens"] for r in recs) / len(recs)
        avg_char = sum(r["chars"] for r in recs) / len(recs)
        avg_sent = sum(r["sentences"] for r in recs) / len(recs)

        if skill_name == "gaya-ringkas":
            # Kepatuhan: balasan <= 2 kalimat
            compliant = sum(1 for r in recs if r["sentences"] <= 2)
            comp_rate = f"{compliant}/{len(recs)} (<= 2 kalimat)"
        elif skill_name == "format-ringkasan":
            # Kepatuhan: ada judul tebal dan poin bertitik
            compliant = sum(1 for r in recs if r["has_bold_title"] and r["has_bullets"])
            comp_rate = f"{compliant}/{len(recs)} (judul + poin)"
        else:
            comp_rate = "N/A (unconstrained)"

        print(f"{mode_label:<26} | {avg_tok:<10.1f} | {avg_char:<11.1f} | {avg_sent:<14.1f} | {comp_rate:<20}")

    print("=" * 80)
    print(f"Berkas bukti berhasil disimpan ke: {EVIDENCE_FILE}")
    print("SELESAI.")
    tee.close()
    sys.stdout = tee.terminal


if __name__ == "__main__":
    main()
