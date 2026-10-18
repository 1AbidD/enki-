"""Skrip Evaluasi Router Kemampuan (Tahap 4E Kriteria B3).

Format input:
  File TSV/baris: INTENT<TAB>kalimat (NONE untuk negatif)
  File JSON: array [{"intent": "...", "prompt": "..."}, ...]

Output per-baris:
  OK / MISS / FALSE-ROUTE + intent yang keluar
Ringkasan:
  Akurasi positif, false-route negatif, sha256 input, sha256 router, git HEAD.
Opsi --save:
  Menyimpan output ke docs/evidence/tahap4e_<nama>.txt
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

# Tambahkan project root ke sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from enki.core.capability_router import route_capability_inquiry


def get_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def get_git_head() -> str:
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return res.stdout.strip()
    except Exception as e:
        return f"UNKNOWN ({e})"


def load_dataset(file_path: Path) -> List[Tuple[str, str]]:
    samples: List[Tuple[str, str]] = []
    if file_path.suffix.lower() == ".json":
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for item in data:
                intent = item.get("intent", "NONE").strip()
                prompt = item.get("prompt", "").strip()
                if prompt:
                    samples.append((intent, prompt))
        elif isinstance(data, dict):
            # Format dict bertingkat
            for k, v in data.items():
                if isinstance(v, list):
                    for elem in v:
                        if isinstance(elem, str):
                            samples.append((k, elem.strip()))
                        elif isinstance(elem, dict):
                            p = elem.get("prompt", "").strip()
                            it = elem.get("intent", k).strip()
                            if p:
                                samples.append((it, p))
    else:
        with open(file_path, "r", encoding="utf-8") as f:
            for line_idx, line in enumerate(f, 1):
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                parts = raw.split("\t", 1)
                if len(parts) == 2:
                    intent, prompt = parts[0].strip(), parts[1].strip()
                elif len(parts) == 1:
                    intent, prompt = "NONE", parts[0].strip()
                else:
                    continue
                samples.append((intent, prompt))
    return samples


def run_evaluation(file_path: Path, save_path: str = None) -> int:
    if not file_path.exists():
        print(f"Error: berkas {file_path} tidak ditemukan!")
        return 1

    router_file = PROJECT_ROOT / "enki" / "core" / "capability_router.py"
    input_sha256 = get_file_sha256(file_path)
    router_sha256 = get_file_sha256(router_file)
    git_head = get_git_head()

    samples = load_dataset(file_path)

    out_lines = []
    out_lines.append("=" * 80)
    out_lines.append(f"EVALUASI ROUTER KEMAMPUAN DETERMINISTIK ENKI (Tahap 4E)")
    out_lines.append(f"Berkas Input : {file_path} (SHA-256: {input_sha256})")
    out_lines.append(f"Router Source: {router_file} (SHA-256: {router_sha256})")
    out_lines.append(f"Git HEAD     : {git_head}")
    out_lines.append(f"Jumlah Sampel: {len(samples)}")
    out_lines.append("=" * 80)
    out_lines.append("")

    total_pos = 0
    pos_ok = 0
    pos_miss = 0
    pos_wrong = 0

    total_neg = 0
    neg_ok = 0
    neg_false_route = 0

    for idx, (expected_intent, prompt) in enumerate(samples, 1):
        res = route_capability_inquiry(prompt)
        actual_intent = res.intent if res is not None else "NONE"

        if expected_intent != "NONE":
            total_pos += 1
            if actual_intent == expected_intent:
                pos_ok += 1
                status = "OK"
                detail = f"exp: {expected_intent} -> got: {actual_intent}"
            elif actual_intent == "NONE":
                pos_miss += 1
                status = "MISS"
                detail = f"exp: {expected_intent} -> got: NONE"
            else:
                pos_wrong += 1
                status = "FALSE-ROUTE"
                detail = f"exp: {expected_intent} -> got: {actual_intent}"
        else:
            total_neg += 1
            if actual_intent == "NONE":
                neg_ok += 1
                status = "OK"
                detail = "NONE -> NONE"
            else:
                neg_false_route += 1
                status = "FALSE-ROUTE"
                detail = f"NONE -> got: {actual_intent}"

        out_lines.append(f"[{idx:03d}] [{status:<11}] {detail:<42} | '{prompt}'")

    out_lines.append("")
    out_lines.append("-" * 80)
    out_lines.append("RINGKASAN EVALUASI:")
    out_lines.append(f"  Sampel Positif   : {total_pos}")
    out_lines.append(f"    - Berhasil (OK): {pos_ok} ({pos_ok/total_pos*100:.1f}%)" if total_pos else "    - Berhasil: 0")
    out_lines.append(f"    - Terlewat (MISS): {pos_miss}")
    out_lines.append(f"    - Salah Intent   : {pos_wrong}")
    out_lines.append(f"  Sampel Negatif   : {total_neg}")
    out_lines.append(f"    - Berhasil (OK): {neg_ok} ({neg_ok/total_neg*100:.1f}%)" if total_neg else "    - Berhasil: 0")
    out_lines.append(f"    - False-Route  : {neg_false_route}")
    out_lines.append("-" * 80)

    # Status Kelulusan Dev: 100% positif benar, 0 false route negatif
    passed = (pos_ok == total_pos) and (neg_false_route == 0)
    out_lines.append(f"STATUS AKHIR: {'LULUS (100% Positif & 0 False-Route)' if passed else 'GAGAL'}")
    out_lines.append("=" * 80)

    report_text = "\n".join(out_lines)
    print(report_text)

    if save_path is not None:
        if save_path == "" or save_path.lower() == "true":
            base_name = file_path.stem
            target_p = PROJECT_ROOT / "docs" / "evidence" / f"tahap4e_{base_name}.txt"
        else:
            target_p = Path(save_path)
            if not target_p.is_absolute():
                target_p = PROJECT_ROOT / target_p
        target_p.parent.mkdir(parents=True, exist_ok=True)
        with open(target_p, "w", encoding="utf-8") as f:
            f.write(report_text + "\n")
        print(f"\nBukti tersimpan di: {target_p}")

    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser(description="Evaluasi router kemampuan ENKI (Tahap 4E)")
    parser.add_argument("--file", "-f", required=True, help="Path ke berkas dataset (.tsv / .txt / .json)")
    parser.add_argument("--save", "-s", nargs="?", const="", default=None, help="Simpan hasil evaluasi ke docs/evidence/tahap4e_<nama>.txt")
    args = parser.parse_args()

    f_path = Path(args.file)
    if not f_path.is_absolute():
        f_path = PROJECT_ROOT / f_path

    code = run_evaluation(f_path, save_path=args.save)
    sys.exit(code)


if __name__ == "__main__":
    main()
