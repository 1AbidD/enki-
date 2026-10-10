"""
Pengukuran False-Call Given Open (B2):
Menguji 13 kalimat false-open dari false_open_rate_report.txt sebanyak 3 run/seed.
Menggunakan ReActCoordinator dan Ollama asli (qwen2.5:3b), tools sesuai gating.
confirm_func menolak semua (lambda cmd: False) sehingga tidak ada yang tereksekusi.
Menyimpan hasil ke tests/results/false_open_calls.jsonl dan mencetak ringkasan.
"""

import json
import time
import sys
import tempfile
import shutil
from pathlib import Path
from typing import List, Dict, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.memory.database import MemoryDatabase
from enki.core.react_loop import ReActCoordinator, get_active_tools_for_turn

OUTPUT_JSONL = ROOT_DIR / "tests" / "results" / "false_open_calls.jsonl"

FALSE_OPEN_SENTENCES = [
    {"id": "FO_01", "keyword": "file", "text": "apa itu file system di linux?"},
    {"id": "FO_02", "keyword": "file", "text": "bagaimana cara kerja format file audio MP3 dalam mengompres suara?"},
    {"id": "FO_03", "keyword": "file", "text": "apa beda file sharing peer-to-peer dengan client-server?"},
    {"id": "FO_04", "keyword": "folder", "text": "mengapa folder temporary sering dibersihkan oleh sistem operasi?"},
    {"id": "FO_05", "keyword": "folder", "text": "apakah folder spam di email otomatis dihapus setelah 30 hari?"},
    {"id": "FO_06", "keyword": "commit", "text": "apa arti kata commit dalam komitmen hubungan?"},
    {"id": "FO_07", "keyword": "commit", "text": "dalam organisasi, apa maksudnya commit to excellence?"},
    {"id": "FO_08", "keyword": "branch", "text": "di bank syariah apa fungsi kantor branch atau cabang?"},
    {"id": "FO_09", "keyword": "branch", "text": "apa fungsi branch perbankan di era digital?"},
    {"id": "FO_10", "keyword": "dir", "text": "apa kepanjangan dari perintah dir di DOS zaman dulu?"},
    {"id": "FO_11", "keyword": "dir", "text": "apa asal usul kata dir dalam bahasa Arab atau singkatan?"},
    {"id": "FO_12", "keyword": "tadi", "text": "tadi pagi sarapan apa yang sehat untuk sarapan cepat?"},
    {"id": "FO_13", "keyword": "tadi", "text": "tadi malam hujan deras sekali ya di daerahmu?"},
]

def main():
    temp_dir = Path(tempfile.mkdtemp(prefix="enki_false_open_test_"))
    try:
        ws_path = temp_dir / "workspace"
        ws_path.mkdir(parents=True, exist_ok=True)
        db_path = temp_dir / "test_memory.db"
        db = MemoryDatabase(db_path=db_path)

        client = OllamaClient()
        if not client.is_ready():
            raise RuntimeError("Server Ollama tidak aktif di http://127.0.0.1:11434!")

        assembler = PromptAssembler(system_prompt=DEFAULT_SYSTEM_PROMPT, profile="")

        coordinator = ReActCoordinator(
            client=client,
            assembler=assembler,
            workspace_root=ws_path,
            db=db,
            confirm_func=lambda cmd: False  # Menolak semua konfirmasi eksekusi
        )

        records: List[Dict[str, Any]] = []
        sentence_stats: Dict[str, Dict[str, Any]] = {}

        print("=" * 80)
        print("MEMULAI PENGUKURAN FALSE-CALL GIVEN OPEN (13 KALIMAT x 3 SEED)")
        print("=" * 80)

        for item in FALSE_OPEN_SENTENCES:
            s_id = item["id"]
            s_text = item["text"]
            s_kw = item["keyword"]

            # Tools yang terbuka saat gating
            open_tools_schemas = get_active_tools_for_turn(s_text)
            open_tools = [t["function"]["name"] for t in open_tools_schemas] if open_tools_schemas else []

            sentence_stats[s_id] = {
                "keyword": s_kw,
                "text": s_text,
                "open_tools": open_tools,
                "calls": [],
                "called_count": 0
            }

            print(f"\n[{s_id}] Kata Kunci: '{s_kw}' | Tools Terbuka: {open_tools}")
            print(f"     Kalimat: \"{s_text}\"")

            for seed in range(1, 4):
                sess_id = db.create_session()
                tool_calls_recorded = []

                def trace_cb(evt: str, msg: str):
                    if evt == "tool_call":
                        tool_calls_recorded.append(msg)

                final_resp = ""
                for token, metrics in coordinator.run_react_turn(
                    user_input=s_text,
                    session_id=sess_id,
                    history=[],
                    model="qwen2.5:3b",
                    trace_callback=trace_cb
                ):
                    final_resp += token

                called = len(tool_calls_recorded) > 0
                called_info = tool_calls_recorded[0] if called else None

                if called:
                    sentence_stats[s_id]["called_count"] += 1
                sentence_stats[s_id]["calls"].append(called_info)

                rec = {
                    "id": s_id,
                    "seed": seed,
                    "keyword": s_kw,
                    "sentence": s_text,
                    "open_tools": open_tools,
                    "called_tool": called_info,
                    "tool_calls_recorded": tool_calls_recorded,
                    "final_response_preview": final_resp[:120] if final_resp else ""
                }
                records.append(rec)

                status_str = f"TOOL CALLED: {called_info}" if called else "NO TOOL CALL (Text Only)"
                print(f"     Seed {seed}: {status_str}")

        # Simpan ke JSONL
        OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        print("\n" + "=" * 80)
        print("RINGKASAN PENGUKURAN FALSE-CALL GIVEN OPEN:")
        print("=" * 80)
        total_runs = len(records)
        total_calls = sum(1 for r in records if r["called_tool"] is not None)

        for s_id, stat in sentence_stats.items():
            print(f"[{s_id}] '{stat['keyword']}' (Tools: {stat['open_tools']}): {stat['called_count']}/3 calls")
            for i, c in enumerate(stat["calls"], 1):
                if c:
                    print(f"       Seed {i}: {c}")
                else:
                    print(f"       Seed {i}: None (Respon Teks)")

        pct = (total_calls / total_runs) * 100 if total_runs > 0 else 0
        print("-" * 80)
        print(f"TOTAL FALSE-CALLS GIVEN OPEN: {total_calls}/{total_runs} ({pct:.1f}%)")
        print(f"File tersimpan di: {OUTPUT_JSONL}")
        print("=" * 80)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
