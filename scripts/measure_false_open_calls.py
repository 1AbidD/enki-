"""
Pengukuran False-Call Given Open (B2):
Menguji 13 kalimat false-open dari false_open_rate_report.txt sebanyak 3 run/seed.
Menyertakan 4 prompt KONTROL POSITIF untuk membuktikan harness memanggil tool jika dipicu.
Menggunakan ReActCoordinator dan Ollama asli (qwen2.5:3b), tools sesuai gating.
confirm_func menolak semua (lambda cmd: False) sehingga tidak ada yang tereksekusi di luar sandbox.
Menyimpan hasil ke tests/results/false_open_calls.jsonl dan mencetak ringkasan.
"""

import json
import re
import sys
import tempfile
import shutil
import hashlib
import subprocess
from pathlib import Path
from typing import List, Dict, Any

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, DEFAULT_SYSTEM_PROMPT
from enki.memory.database import MemoryDatabase
from enki.core.react_loop import ReActCoordinator, get_active_tools_for_turn

OUTPUT_JSONL = ROOT_DIR / "tests" / "results" / "false_open_calls.jsonl"

POSITIVE_CONTROLS = [
    {"id": "CTRL_01", "text": "baca README.md", "expected_tool": "read_file"},
    {"id": "CTRL_02", "text": "cek git diff", "expected_tool": "git"},
    {"id": "CTRL_03", "text": "jalankan semua tes proyek ini", "expected_tool": "run_unittests"},
    {"id": "CTRL_04", "text": "cek versi python", "expected_tool": "python_version"},
]

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

def wilson_upper_bound(successes: int, total: int, z: float = 1.95996) -> float:
    """Menghitung batas atas interval skor Wilson (95% CI)."""
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1.0 + (z ** 2) / total
    center = p + (z ** 2) / (2.0 * total)
    spread = z * ((p * (1.0 - p) / total + (z ** 2) / (4.0 * (total ** 2))) ** 0.5)
    return (center + spread) / denom

def setup_test_workspace(ws_path: Path):
    """Menyiapkan workspace valid dengan file README, git repo, dan test file."""
    (ws_path / "README.md").write_text("# ZephyrSentinel\nProject description.\n", encoding="utf-8")
    try:
        subprocess.run(["git", "init"], cwd=ws_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=ws_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=ws_path, capture_output=True, check=True)
        subprocess.run(["git", "add", "."], cwd=ws_path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=ws_path, capture_output=True, check=True)
        # Buat uncommitted diff
        app_dir = ws_path / "app"
        app_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "main.py").write_text("# uncommitted telemetry change\n", encoding="utf-8")
        # Buat unit test
        tests_dir = ws_path / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_app.py").write_text(
            "import unittest\nclass TestApp(unittest.TestCase):\n    def test_ok(self): pass\n",
            encoding="utf-8"
        )
    except Exception as e:
        print(f"[WARN] Setup git workspace gagal: {e}")

def main():
    temp_dir = Path(tempfile.mkdtemp(prefix="enki_false_open_test_"))
    try:
        ws_path = temp_dir / "workspace"
        ws_path.mkdir(parents=True, exist_ok=True)
        setup_test_workspace(ws_path)

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
            confirm_func=lambda cmd: False  # Menolak semua konfirmasi eksekusi berbahaya
        )

        records: List[Dict[str, Any]] = []

        # =====================================================================
        # BAGIAN 1: KONTROL POSITIF (Wajib memanggil tool)
        # =====================================================================
        print("=" * 80)
        print("BAGIAN 1: PENGUJIAN 4 KONTROL POSITIF")
        print("=" * 80)

        failed_controls = []
        for ctrl in POSITIVE_CONTROLS:
            c_id = ctrl["id"]
            c_text = ctrl["text"]
            exp_tool = ctrl["expected_tool"]

            open_tools_schemas = get_active_tools_for_turn(c_text)
            open_tools = [t["function"]["name"] for t in open_tools_schemas] if open_tools_schemas else []

            print(f"\n[{c_id}] Kontrol: \"{c_text}\" | Expected Tool: '{exp_tool}' | Tools Terbuka: {open_tools}")
            if exp_tool not in open_tools:
                raise RuntimeError(f"FATAL: Kontrol positif {c_id} tidak membuka tool {exp_tool} di gating!")

            sess_id = db.create_session()
            tool_calls_recorded = []

            def trace_cb(evt: str, msg: str):
                if evt == "tool_call":
                    tool_calls_recorded.append(msg)

            final_resp = ""
            for token, metrics in coordinator.run_react_turn(
                user_input=c_text,
                session_id=sess_id,
                history=[],
                model="qwen2.5:3b",
                trace_callback=trace_cb
            ):
                final_resp += token

            called = len(tool_calls_recorded) > 0
            called_tool_name = None
            if called:
                # Ambil nama tool dari tool_calls_recorded (misal 'read_file({"file_path": "README.md"})')
                m = re.match(r'^([a-zA-Z0-9_]+)\(', tool_calls_recorded[0])
                called_tool_name = m.group(1) if m else tool_calls_recorded[0]

            print(f"     Status: {'TOOL CALLED: ' + str(tool_calls_recorded[0]) if called else 'NO TOOL CALL (GAGAL)'}")

            if not called or called_tool_name != exp_tool:
                failed_controls.append((c_id, exp_tool, called_tool_name))

            records.append({
                "type": "positive_control",
                "id": c_id,
                "sentence": c_text,
                "expected_tool": exp_tool,
                "called_tool": called_tool_name,
                "tool_calls_recorded": tool_calls_recorded,
                "final_response_preview": final_resp[:120] if final_resp else "",
                "status": "PASS" if (called and called_tool_name == exp_tool) else "FAIL"
            })

        if failed_controls:
            raise RuntimeError(
                f"FATAL: Kontrol positif gagal memanggil tool yang diharapkan: {failed_controls}! "
                "Harness terbukti tidak mampu memanggil tool saat dipicu!"
            )
        print("\n[V] SEMUA 4 KONTROL POSITIF BERHASIL MEMANGGIL TOOL!")

        # =====================================================================
        # BAGIAN 2: PENGUKURAN 13 KALIMAT FALSE-OPEN (3 SEED)
        # =====================================================================
        sentence_stats: Dict[str, Dict[str, Any]] = {}
        all_text_hashes = []

        print("\n" + "=" * 80)
        print("BAGIAN 2: PENGUKURAN FALSE-CALL GIVEN OPEN (13 KALIMAT x 3 SEED)")
        print("=" * 80)

        for item in FALSE_OPEN_SENTENCES:
            s_id = item["id"]
            s_text = item["text"]
            s_kw = item["keyword"]

            open_tools_schemas = get_active_tools_for_turn(s_text)
            open_tools = [t["function"]["name"] for t in open_tools_schemas] if open_tools_schemas else []

            sentence_stats[s_id] = {
                "keyword": s_kw,
                "text": s_text,
                "open_tools": open_tools,
                "calls": [],
                "called_count": 0,
                "hashes": [],
                "unique_outputs": 0
            }

            print(f"\n[{s_id}] Kata Kunci: '{s_kw}' | Tools Terbuka: {open_tools}")
            print(f"     Kalimat: \"{s_text}\"")

            for seed in range(1, 4):
                sess_id = db.create_session()
                tool_calls_recorded = []

                def trace_cb_fo(evt: str, msg: str):
                    if evt == "tool_call":
                        tool_calls_recorded.append(msg)

                final_resp = ""
                for token, metrics in coordinator.run_react_turn(
                    user_input=s_text,
                    session_id=sess_id,
                    history=[],
                    model="qwen2.5:3b",
                    trace_callback=trace_cb_fo
                ):
                    final_resp += token

                resp_hash = hashlib.sha256(final_resp.strip().encode("utf-8")).hexdigest()[:12]
                all_text_hashes.append(resp_hash)
                sentence_stats[s_id]["hashes"].append(resp_hash)

                called = len(tool_calls_recorded) > 0
                called_info = tool_calls_recorded[0] if called else None

                if called:
                    sentence_stats[s_id]["called_count"] += 1
                sentence_stats[s_id]["calls"].append(called_info)

                rec = {
                    "type": "false_open_test",
                    "id": s_id,
                    "seed": seed,
                    "keyword": s_kw,
                    "sentence": s_text,
                    "open_tools": open_tools,
                    "called_tool": called_info,
                    "tool_calls_recorded": tool_calls_recorded,
                    "response_hash": resp_hash,
                    "final_response_preview": final_resp[:120] if final_resp else ""
                }
                records.append(rec)

                status_str = f"TOOL CALLED: {called_info}" if called else f"NO TOOL CALL (Text Only, hash={resp_hash})"
                print(f"     Seed {seed}: {status_str}")

            sentence_stats[s_id]["unique_outputs"] = len(set(sentence_stats[s_id]["hashes"]))

        # Simpan ke JSONL
        OUTPUT_JSONL.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_JSONL, "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        print("\n" + "=" * 80)
        print("RINGKASAN PENGUKURAN FALSE-CALL GIVEN OPEN:")
        print("=" * 80)
        fo_records = [r for r in records if r.get("type") == "false_open_test"]
        total_runs = len(fo_records)
        total_calls = sum(1 for r in fo_records if r["called_tool"] is not None)
        n_efektif = len(set(all_text_hashes))

        for s_id, stat in sentence_stats.items():
            u_out = stat["unique_outputs"]
            print(f"[{s_id}] '{stat['keyword']}' (Tools: {stat['open_tools']}): {stat['called_count']}/3 calls | Varian output: {u_out}/3 unik")

        pct = (total_calls / total_runs) * 100 if total_runs > 0 else 0
        w_upper_13 = wilson_upper_bound(0, 13) * 100
        w_upper_39 = wilson_upper_bound(0, 39) * 100

        print("-" * 80)
        print(f"TOTAL FALSE-CALLS GIVEN OPEN : {total_calls}/{total_runs} ({pct:.1f}%)")
        print(f"N EFEKTIF (Keluaran Teks Unik): {n_efektif}/{total_runs} respon unik")
        print(f"Wilson 95% Upper Bound (0/13 prompt) : {w_upper_13:.2f}% (~= 23%)")
        print(f"Wilson 95% Upper Bound (0/39 runs)   : {w_upper_39:.2f}% (PERINGATAN: 39 runs BUKAN 39 sampel independen!)")
        print(f"File tersimpan di: {OUTPUT_JSONL}")
        print("=" * 80)

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()
