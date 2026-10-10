import os
import sys
import json
import time
import shutil
import tempfile
import datetime
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.core.react_loop import ReActCoordinator
from enki.memory.database import MemoryDatabase
from enki.memory.profile_manager import ProfileManager
from enki.memory.recall import (
    is_recall_triggered,
    build_fts_query,
    format_recall_block
)
from tests.benchmark_common import (
    evaluate_benchmark_turn,
    BenchmarkLock,
    check_ollama_idle,
    create_benchmark_jsonl_header
)
from tests.test_react_benchmark_set4 import (
    setup_real_temporary_workspace,
    configure_workspace_test_suite,
    FIXTURE_MANIFEST
)

SMOKE_PROMPTS = [
    # 1. read_file
    {
        "id": "SMOKE_P01_READ",
        "category": "FILE",
        "prompt": "bisa tolong bacakan isi file requirements.txt di workspace kita?",
        "expected_tool": "read_file",
        "expected_arg": "requirements.txt",
        "expected_key": FIXTURE_MANIFEST["files"]["requirements.txt"]["key"]
    },
    # 2. git diff
    {
        "id": "SMOKE_P02_GIT",
        "category": "GIT",
        "prompt": "tolong cek git diff sebentar ada perubahan kode apa di workspace?",
        "expected_tool": "git",
        "expected_arg": "diff",
        "expected_key": FIXTURE_MANIFEST["git"]["diff"]["key"]
    },
    # 3. run_unittests (passing)
    {
        "id": "SMOKE_P03_TEST",
        "category": "TEST",
        "prompt": "coba jalankan seluruh unit test proyek kita sekarang",
        "expected_tool": "run_unittests",
        "expected_arg": None,
        "fixture_variant": "passing",
        "expected_key": FIXTURE_MANIFEST["tests"]["passing"]["key"]
    },
    # 4. MEM Profile
    {
        "id": "SMOKE_P04_PROFILE",
        "category": "MEM",
        "mode": "profile",
        "prompt": "apa sistem operasi yang kita pakai di server staging kita?",
        "expected_tool": None,
        "expected_key": "debian",
        "memory_fact": "Sistem operasi server staging kita adalah Debian 12 Bookworm."
    },
    # 5. MEM Recall
    {
        "id": "SMOKE_P05_RECALL",
        "category": "MEM",
        "mode": "recall",
        "prompt": "kamu ingat kemarin kita sepakat pakai format log apa di microservice?",
        "expected_tool": None,
        "expected_key": "json",
        "memory_inject": (
            "user",
            "Untuk standar format log semua microservice backend, kita sepakat pakai structured JSON log.",
            "assistant",
            "Baik, format structured JSON log dicatat sebagai standar logging microservice."
        )
    },
    # 6. NEG Berpemicu Aktif
    {
        "id": "SMOKE_P06_NEG",
        "category": "NEG",
        "has_trigger": True,
        "prompt": "kemarin siapa penemu mikroskop elektron pertama di dunia?",
        "expected_tool": None,
        "expected_key": None
    },
    # 7. run_unittests (failing)
    {
        "id": "SMOKE_P07_TEST_FAIL",
        "category": "TEST",
        "prompt": "coba jalankan seluruh unit test proyek kita sekarang dan laporkan statusnya",
        "expected_tool": "run_unittests",
        "expected_arg": None,
        "fixture_variant": "failing",
        "expected_key": FIXTURE_MANIFEST["tests"]["failing"]["key"]
    },
    # 8. git status
    {
        "id": "SMOKE_P08_GIT_STATUS",
        "category": "GIT",
        "prompt": "bisa tolong periksa git status untuk melihat kondisi branch saat ini?",
        "expected_tool": "git",
        "expected_arg": "status",
        "expected_key": FIXTURE_MANIFEST["git"]["status"]["key"]
    }
]

def run_smoke():
    client = OllamaClient()
    check_ollama_idle(client)

    temp_dir = tempfile.mkdtemp(prefix="enki_smoke_plumbing_")
    ws_path = Path(temp_dir) / "workspace"
    data_path = Path(temp_dir) / "data"
    log_path = Path(temp_dir) / "logs"

    data_path.mkdir(parents=True, exist_ok=True)
    log_path.mkdir(parents=True, exist_ok=True)

    os.environ["ENKI_DATA_DIR"] = str(data_path)
    os.environ["ENKI_LOG_DIR"] = str(log_path)

    # Menggunakan setup workspace nyata Set 4
    setup_real_temporary_workspace(ws_path)

    db_path = log_path / "sessions.db"
    db = MemoryDatabase(db_path=db_path)

    profile_mgr = ProfileManager(memory_dir=data_path / "memory")

    # Inisialisasi fakta
    for p in SMOKE_PROMPTS:
        if p.get("mode") == "profile":
            f_key = p["id"].lower()
            f_val = p.get("memory_fact")
            profile_mgr.add_fact(f_key, f_val)
        elif p.get("mode") == "recall" and p.get("memory_inject"):
            u_role, u_msg, a_role, a_msg = p["memory_inject"]
            s_old = db.create_session()
            db.add_turn(s_old, u_role, u_msg)
            db.add_turn(s_old, a_role, a_msg)

    # Tambahkan background sessions agar korpus memori memiliki frekuensi realistis
    for i in range(1, 15):
        s_bg = db.create_session()
        db.add_turn(s_bg, "user", f"bagaimana perkembangan modul sistem {i}?")
        db.add_turn(s_bg, "assistant", f"modul sistem {i} berjalan lancar tanpa error.")

    confirmed_log = []
    def confirm_func(cmd: str) -> bool:
        confirmed_log.append(cmd)
        return True

    assembler = PromptAssembler(
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        profile=profile_mgr.get_profile_text()
    )

    coordinator = ReActCoordinator(
        client=client,
        assembler=assembler,
        workspace_root=ws_path,
        db=db,
        confirm_func=confirm_func,
        audit_log_path=data_path / "tool_audit.log",
        max_iterations=4,
        turn_timeout_sec=120
    )

    results_dir = ROOT_DIR / "tests" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    out_file = results_dir / "smoke_plumbing_raw.jsonl"
    if out_file.exists():
        out_file.unlink()

    model = "qwen2.5:3b"

    # 1. Warm-up model sebelum timing & sebelum pembacaan ollama ps
    print("[WARM-UP] Memuat model qwen2.5:3b ke VRAM dengan num_ctx=8192...")
    try:
        client.chat(model=model, messages=[{"role": "user", "content": "ping"}], num_predict=2, num_ctx=8192)
    except Exception as e:
        print(f"[WARM-UP WARNING] {e}")

    # 2. Tulis Metadata Header (dibuat SETELAH warm-up agar ollama ps mencerminkan model yang sedang aktif)
    header = create_benchmark_jsonl_header(runner_num_ctx=8192)

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(json.dumps(header, ensure_ascii=False) + "\n")

    session_id = db.create_session()

    print("=" * 70)
    print("MEMULAI SMOKE TEST PLUMBING (8 PROMPT - REAL REACT & OLLAMA)")
    print("=" * 70)

    for p in SMOKE_PROMPTS:
        # Konfigurasi suite test sesuai kebutuhan varian turn
        variant = p.get("fixture_variant", "passing")
        configure_workspace_test_suite(ws_path, variant=variant)

        confirmed_log.clear()  # Reset konfirmasi perintah per turn
        t_start = time.time()
        tool_calls_rec = []
        tool_obs_rec = []
        timeout_reason = "none"

        def trace_cb(evt: str, msg: str):
            if evt == "tool_call":
                tool_calls_rec.append(msg)
            elif evt == "tool_result":
                tool_obs_rec.append(msg)
            elif evt == "timeout":
                nonlocal timeout_reason
                timeout_reason = "turn_timeout"

        # Evaluasi Active Recall jika ada pemicu
        recall_content = None
        active_recall_injected = False
        recall_source = "none"
        if is_recall_triggered(p["prompt"]):
            fts_q = build_fts_query(p["prompt"])
            if fts_q:
                snips = db.search_fts(fts_q, exclude_session_id=session_id, limit=3)
                if snips:
                    recall_content = format_recall_block(snips, max_tokens=300)
                    active_recall_injected = True
                    recall_source = "active_fts5"

        chunks = []
        final_metrics = {}
        is_timeout = False

        started_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            for chunk, metrics in coordinator.run_react_turn(
                user_input=p["prompt"],
                session_id=session_id,
                history=[],
                model=model,
                recall_content=recall_content,
                trace_callback=trace_cb
            ):
                if chunk:
                    chunks.append(chunk)
                if metrics:
                    final_metrics = metrics
        except TimeoutError:
            is_timeout = True
            timeout_reason = "no_first_token" if len(chunks) == 0 else "http_request_timeout"
        except Exception as e:
            if "timeout" in str(e).lower() or "timed out" in str(e).lower():
                is_timeout = True
                timeout_reason = "no_first_token" if len(chunks) == 0 else "http_request_timeout"

        if is_timeout and len(chunks) == 0:
            timeout_reason = "no_first_token"
        elif timeout_reason == "turn_timeout" and len(chunks) == 0:
            is_timeout = True
            timeout_reason = "no_first_token"

        ended_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        total_turn_duration_sec = round(time.time() - t_start, 3)
        final_metrics["total_turn_duration_sec"] = total_turn_duration_sec
        final_text = "".join(chunks).strip()

        first_tool_call = None
        first_tool_arg = None
        if tool_calls_rec:
            raw_c = tool_calls_rec[0]
            pidx = raw_c.find("(")
            if pidx != -1:
                first_tool_call = raw_c[:pidx].strip()
                first_tool_arg = raw_c[pidx + 1 : -1].strip()

        eval_res = evaluate_benchmark_turn(
            expected_tool=p.get("expected_tool"),
            expected_arg=p.get("expected_arg"),
            expected_key=p.get("expected_key"),
            tool_calls_recorded=tool_calls_rec,
            tool_observations_recorded=tool_obs_rec,
            final_text=final_text,
            is_timeout=is_timeout,
            timeout_reason=timeout_reason,
            fixture_manifest=FIXTURE_MANIFEST,
            fixture_variant=p.get("fixture_variant")
        )

        record = {
            "id": p["id"],
            "category": p["category"],
            "prompt_input": p["prompt"],
            "expected_tool": p.get("expected_tool"),
            "expected_arg": p.get("expected_arg"),
            "expected_key": p.get("expected_key"),
            "called_tool": first_tool_call,
            "called_arg": first_tool_arg,
            "active_recall_injected": active_recall_injected,
            "passive_recall_injected": False,
            "recall_source": recall_source,
            "recall_content": recall_content,
            "tool_calls": tool_calls_rec,
            "tool_observations": tool_obs_rec,
            "final_response": final_text,
            "confirmed_commands": list(confirmed_log),
            "evaluation": eval_res,
            "metrics": final_metrics,
            "started_at": started_at,
            "ended_at": ended_at,
            "total_turn_duration_sec": total_turn_duration_sec
        }

        with open(out_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        status_str = "OK" if eval_res["is_success"] else "FAIL"
        dur = round(time.time() - t_start, 2)
        print(f"[{p['id']}] {status_str} ({dur}s) | Tool: {first_tool_call} | Routing: {eval_res['routing_ok']} | Fidelity: {eval_res['fidelity_ok']}")

    db.close()
    shutil.rmtree(temp_dir, ignore_errors=True)
    if "ENKI_DATA_DIR" in os.environ:
        del os.environ["ENKI_DATA_DIR"]
    if "ENKI_LOG_DIR" in os.environ:
        del os.environ["ENKI_LOG_DIR"]

    print("\nSmoke test plumbing selesai. File hasil:", out_file)

if __name__ == "__main__":
    with BenchmarkLock():
        run_smoke()
