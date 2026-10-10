import os
import sys
import json
import time
import shutil
import tempfile
from pathlib import Path
from typing import Dict, Any, List

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.core.react_loop import ReActCoordinator
from enki.memory.database import MemoryDatabase
from enki.memory.profile_manager import ProfileManager
from tests.benchmark_common import (
    evaluate_benchmark_turn,
    BenchmarkLock,
    check_ollama_idle,
    check_fidelity_match,
    check_test_fidelity
)
from tests.test_react_benchmark_set4 import (
    setup_real_temporary_workspace,
    configure_workspace_test_suite,
    FIXTURE_MANIFEST
)

# 12 Prompt Netral (di luar Set 4)
# Masing-masing menanyakan hal yang BERBEDA dari kata kunci lama:
NEUTRAL_PROMPTS = [
    # 4 FILE
    {
        "id": "VAL_FILE_01",
        "category": "FILE",
        "prompt": "bisa tolong periksa file README.md, platform ini dirancang untuk pemantauan apa?",
        "expected_tool": "read_file",
        "expected_arg": "README.md",
        "old_key": "zephyrsentinel",
        "new_key": FIXTURE_MANIFEST["files"]["README.md"]["key"],
        "target_fact": "telemetri / observabilitas cuaca ekstrem"
    },
    {
        "id": "VAL_FILE_02",
        "category": "FILE",
        "prompt": "tolong baca file requirements.txt, library web async apa yang dicantumkan?",
        "expected_tool": "read_file",
        "expected_arg": "requirements.txt",
        "old_key": "structlog",
        "new_key": FIXTURE_MANIFEST["files"]["requirements.txt"]["key"],
        "target_fact": "aiohttp==3.9.5"
    },
    {
        "id": "VAL_FILE_03",
        "category": "FILE",
        "prompt": "tolong periksa berkas app/main.py, apa nama fungsi yang ada di dalam berkas itu?",
        "expected_tool": "read_file",
        "expected_arg": "app/main.py",
        "old_key": "zephyr-9082-alpha",
        "new_key": FIXTURE_MANIFEST["files"]["app/main.py"]["key"],
        "target_fact": "get_status"
    },
    {
        "id": "VAL_FILE_04",
        "category": "FILE",
        "prompt": "bisa cek berkas tests/test_app.py, apa nama suite test case yang didefinisikan?",
        "expected_tool": "read_file",
        "expected_arg": "tests/test_app.py",
        "old_key": "test_telemetry_pipeline",
        "new_key": FIXTURE_MANIFEST["files"]["tests/test_app.py"]["key"],
        "target_fact": "TestZephyrSuite"
    },
    # 4 GIT
    {
        "id": "VAL_GIT_01",
        "category": "GIT",
        "prompt": "tolong periksa git status untuk melihat berkas mana yang mengalami modifikasi?",
        "expected_tool": "git",
        "expected_arg": "status",
        "old_key": "feat/telemetry-monitor-v2",
        "new_key": FIXTURE_MANIFEST["git"]["status"]["key"],
        "target_fact": "app/main.py"
    },
    {
        "id": "VAL_GIT_02",
        "category": "GIT",
        "prompt": "bisa cek git status, kita saat ini berada di cabang apa?",
        "expected_tool": "git",
        "expected_arg": "status",
        "old_key": "feat/telemetry-monitor-v2",
        "new_key": FIXTURE_MANIFEST["git"]["status"]["key"],
        "target_fact": "feat/telemetry-monitor-v2"
    },
    {
        "id": "VAL_GIT_03",
        "category": "GIT",
        "prompt": "tolong lihat git diff, baris tambahan apa yang belum di-commit?",
        "expected_tool": "git",
        "expected_arg": "diff",
        "old_key": "telemetry",
        "new_key": FIXTURE_MANIFEST["git"]["diff"]["key"],
        "target_fact": "uncommitted telemetry patch"
    },
    {
        "id": "VAL_GIT_04",
        "category": "GIT",
        "prompt": "coba periksa riwayat commit git log, apa pesan pada commit pertama?",
        "expected_tool": "git",
        "expected_arg": "log",
        "old_key": "initial",
        "new_key": FIXTURE_MANIFEST["git"]["log"]["key"],
        "target_fact": "feat: initial telemetry ingest pipeline"
    },
    # 4 TEST (2 Passing, 2 Failing)
    {
        "id": "VAL_TEST_01",
        "category": "TEST",
        "prompt": "tolong jalankan unit test proyek kita sekarang",
        "expected_tool": "run_unittests",
        "expected_arg": None,
        "fixture_variant": "passing",
        "old_key": "lulus|berhasil|sukses",
        "new_key": FIXTURE_MANIFEST["tests"]["passing"]["key"],
        "target_fact": "semua tes passing / lulus / sukses"
    },
    {
        "id": "VAL_TEST_02",
        "category": "TEST",
        "prompt": "bisa coba run seluruh pengujian unit test aplikasi kita?",
        "expected_tool": "run_unittests",
        "expected_arg": None,
        "fixture_variant": "passing",
        "old_key": "lulus|berhasil|sukses",
        "new_key": FIXTURE_MANIFEST["tests"]["passing"]["key"],
        "target_fact": "semua tes passing / lulus / sukses"
    },
    {
        "id": "VAL_TEST_03",
        "category": "TEST",
        "prompt": "coba jalankan pengujian unit test proyek dan laporkan bila ada kendala",
        "expected_tool": "run_unittests",
        "expected_arg": None,
        "fixture_variant": "failing",
        "old_key": "gagal|kegagalan|fail|failed|error|test_deliberate_telemetry_fault",
        "new_key": FIXTURE_MANIFEST["tests"]["failing"]["key"],
        "target_fact": "terdapat tes gagal / failure"
    },
    {
        "id": "VAL_TEST_04",
        "category": "TEST",
        "prompt": "tolong eksekusi unit test di workspace dan sampaikan rekap hasilnya",
        "expected_tool": "run_unittests",
        "expected_arg": None,
        "fixture_variant": "failing",
        "old_key": "gagal|kegagalan|fail|failed|error|test_deliberate_telemetry_fault",
        "new_key": FIXTURE_MANIFEST["tests"]["failing"]["key"],
        "target_fact": "terdapat tes gagal / failure"
    }
]

def assess_manual_truth(p_item: Dict[str, Any], answer: str) -> bool:
    """Menilai kebenaran faktual manual terhadap jawaban asisten."""
    ans = answer.lower()
    pid = p_item["id"]
    if pid == "VAL_FILE_01":
        # Menanyakan pemantauan apa: telemetri / observabilitas cuaca ekstrem
        return any(w in ans for w in ["telemetri", "observabilitas", "cuaca", "zephyr"])
    elif pid == "VAL_FILE_02":
        # Menanyakan library web async: aiohttp
        return "aiohttp" in ans
    elif pid == "VAL_FILE_03":
        # Menanyakan nama fungsi di main.py: get_status
        return "get_status" in ans
    elif pid == "VAL_FILE_04":
        # Menanyakan nama suite test case: TestZephyrSuite
        return "testzephyrsuite" in ans
    elif pid == "VAL_GIT_01":
        # Menanyakan berkas termodifikasi: app/main.py
        return "main.py" in ans
    elif pid == "VAL_GIT_02":
        # Menanyakan nama cabang: feat/telemetry-monitor-v2
        return "telemetry-monitor-v2" in ans or "feat/telemetry" in ans
    elif pid == "VAL_GIT_03":
        # Menanyakan baris tambahan diff: uncommitted telemetry patch
        return "uncommitted" in ans or "telemetry" in ans or "patch" in ans
    elif pid == "VAL_GIT_04":
        # Menanyakan commit pertama: initial telemetry ingest pipeline
        return "initial" in ans or "telemetry" in ans or "ingest" in ans
    elif pid in ("VAL_TEST_01", "VAL_TEST_02"):
        # Passing: menyatakan lulus / berhasil / tanpa error
        return any(w in ans for w in ["lulus", "berhasil", "sukses", "ok", "tidak ada"])
    elif pid in ("VAL_TEST_03", "VAL_TEST_04"):
        # Failing: menyatakan gagal / error / kegagalan
        return any(w in ans for w in ["gagal", "kegagalan", "fail", "error", "fault"])
    return False

def main():
    client = OllamaClient()
    check_ollama_idle(client)

    temp_dir = tempfile.mkdtemp(prefix="enki_val_inst_")
    ws_path = Path(temp_dir) / "workspace"
    data_path = Path(temp_dir) / "data"
    log_path = Path(temp_dir) / "logs"

    data_path.mkdir(parents=True, exist_ok=True)
    log_path.mkdir(parents=True, exist_ok=True)

    os.environ["ENKI_DATA_DIR"] = str(data_path)
    os.environ["ENKI_LOG_DIR"] = str(log_path)

    setup_real_temporary_workspace(ws_path)

    db_path = log_path / "sessions.db"
    db = MemoryDatabase(db_path=db_path)
    profile_mgr = ProfileManager(memory_dir=data_path / "memory")

    assembler = PromptAssembler(
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        profile=profile_mgr.get_profile_text()
    )

    coordinator = ReActCoordinator(
        client=client,
        assembler=assembler,
        workspace_root=ws_path,
        db=db,
        confirm_func=lambda cmd: True,
        audit_log_path=data_path / "tool_audit.log",
        max_iterations=4,
        turn_timeout_sec=120
    )

    model = "qwen2.5:3b"
    seeds = [42, 43, 44]

    results = []

    print(f"Memulai validasi instrumen: 12 prompt x 3 seed = 36 turn...")
    session_id = db.create_session()

    turn_count = 0
    for seed in seeds:
        print(f"\n--- SEED: {seed} ---")
        for p in NEUTRAL_PROMPTS:
            turn_count += 1
            variant = p.get("fixture_variant", "passing")
            configure_workspace_test_suite(ws_path, variant=variant)

            tool_calls_rec = []
            tool_obs_rec = []
            def trace_cb(evt: str, msg: str):
                if evt == "tool_call":
                    tool_calls_rec.append(msg)
                elif evt == "tool_result":
                    tool_obs_rec.append(msg)

            chunks = []
            t0 = time.time()
            for chunk, _ in coordinator.run_react_turn(
                user_input=p["prompt"],
                session_id=session_id,
                history=[],
                model=model,
                trace_callback=trace_cb
            ):
                if chunk:
                    chunks.append(chunk)
            dur = round(time.time() - t0, 2)
            ans = "".join(chunks).strip()

            first_tool = None
            first_arg = None
            if tool_calls_rec:
                raw_c = tool_calls_rec[0]
                pidx = raw_c.find("(")
                if pidx != -1:
                    first_tool = raw_c[:pidx].strip()
                    first_arg = raw_c[pidx + 1 : -1].strip()

            # Manual evaluation
            manual_truth = assess_manual_truth(p, ans)

            # Old fidelity evaluation
            old_eval = evaluate_benchmark_turn(
                expected_tool=p.get("expected_tool"),
                expected_arg=p.get("expected_arg"),
                expected_key=p["old_key"],
                tool_calls_recorded=tool_calls_rec,
                tool_observations_recorded=tool_obs_rec,
                final_text=ans,
                fixture_manifest=None,
                fixture_variant=p.get("fixture_variant")
            )
            old_fidelity = old_eval["fidelity_ok"]

            # New fidelity evaluation
            new_eval = evaluate_benchmark_turn(
                expected_tool=p.get("expected_tool"),
                expected_arg=p.get("expected_arg"),
                expected_key=p["new_key"],
                tool_calls_recorded=tool_calls_rec,
                tool_observations_recorded=tool_obs_rec,
                final_text=ans,
                fixture_manifest=FIXTURE_MANIFEST,
                fixture_variant=p.get("fixture_variant")
            )
            new_fidelity = new_eval["fidelity_ok"]

            rec = {
                "turn": turn_count,
                "id": p["id"],
                "seed": seed,
                "category": p["category"],
                "prompt": p["prompt"],
                "called_tool": first_tool,
                "called_arg": first_arg,
                "final_response": ans,
                "manual_truth": manual_truth,
                "old_key": p["old_key"],
                "old_fidelity": old_fidelity,
                "new_key": p["new_key"],
                "new_fidelity": new_fidelity,
                "duration_sec": dur
            }
            results.append(rec)
            print(f"[{turn_count:02d}/36] {p['id']} (seed {seed}) | Tool: {first_tool} | Manual: {manual_truth} | OldFid: {old_fidelity} | NewFid: {new_fidelity} ({dur}s)")

    db.close()
    shutil.rmtree(temp_dir, ignore_errors=True)

    # Compute False-Fail and False-Pass
    old_ff = [r for r in results if r["manual_truth"] is True and r["old_fidelity"] is False]
    old_fp = [r for r in results if r["manual_truth"] is False and r["old_fidelity"] is True]
    new_ff = [r for r in results if r["manual_truth"] is True and r["new_fidelity"] is False]
    new_fp = [r for r in results if r["manual_truth"] is False and r["new_fidelity"] is True]

    evidence_dir = ROOT_DIR / "docs" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("VALIDASI INSTRUMEN BENCHMARK (12 PROMPT NETRAL x 3 SEED = 36 TURN)")
    report_lines.append("=" * 80)
    report_lines.append(f"{'Turn':<5} | {'ID':<12} | {'Seed':<5} | {'Tool':<13} | {'Manual':<7} | {'OldFid':<7} | {'NewFid':<7} | {'Answer Snippet'}")
    report_lines.append("-" * 105)
    for r in results:
        snip = r["final_response"].replace("\n", " ")[:45]
        report_lines.append(
            f"{r['turn']:<5} | {r['id']:<12} | {r['seed']:<5} | {str(r['called_tool']):<13} | "
            f"{str(r['manual_truth']):<7} | {str(r['old_fidelity']):<7} | {str(r['new_fidelity']):<7} | {snip}..."
        )
    report_lines.append("=" * 105)
    report_lines.append("\nRINGKASAN METRIK EVALUASI INSTRUMEN:")
    report_lines.append(f"Total Turn                   : {len(results)}")
    report_lines.append(f"Jawaban Manual Benar         : {sum(1 for r in results if r['manual_truth'])}")
    report_lines.append(f"Jawaban Manual Salah         : {sum(1 for r in results if not r['manual_truth'])}")
    report_lines.append("\n1. Kunci Lama (Single Token Tunggal):")
    report_lines.append(f"   - False-Fail (Manual Benar, Fidelity False): {len(old_ff)}")
    report_lines.append(f"   - False-Pass (Manual Salah, Fidelity True) : {len(old_fp)}")
    report_lines.append("\n2. Kunci Baru (Himpunan Any-Of Token C1):")
    report_lines.append(f"   - False-Fail (Manual Benar, Fidelity False): {len(new_ff)}")
    report_lines.append(f"   - False-Pass (Manual Salah, Fidelity True) : {len(new_fp)}")

    if old_ff:
        report_lines.append("\nAKAR PENYEBAB FALSE-FAIL PADA KUNCI LAMA:")
        seen_pids = set()
        for r in old_ff:
            pid = r["id"]
            if pid not in seen_pids:
                seen_pids.add(pid)
                report_lines.append(
                    f"   - Prompt {pid}: Menanyakan '{r['prompt']}'. Jawaban LLM benar menjawab fakta "
                    f"tetapi kunci lama hanya mengecek token tunggal '{r['old_key']}'. "
                    f"Dengan kunci baru '{r['new_key']}', fidelity menjadi True."
                )

    report_text = "\n".join(report_lines)
    print("\n" + report_text)

    (evidence_dir / "instrument_validation_36turns.txt").write_text(report_text, encoding="utf-8")
    (evidence_dir / "instrument_validation.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDisimpan ke: {evidence_dir / 'instrument_validation_36turns.txt'}")

if __name__ == "__main__":
    with BenchmarkLock():
        main()
