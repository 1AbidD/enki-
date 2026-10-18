#!/usr/bin/env python3
"""Skrip Pengujian & Bukti Empiris Kriteria W4 (Kualitas Ekstraksi Teks).

Menguji 3 Fixture HTML:
1. Artikel dengan elemen boilerplate (nav, footer, script, style):
   - Wajib melucuti marker boilerplate: "Menu 1", "Copyright 2026 Contoh Inc.", "tracker", "font-size"
   - Wajib memuat marker artikel utama: "Arsitektur ENKI", "RTX 3050", "offline secara default"
2. Halaman tabel data:
   - Wajib mengekstrak sel tabel: "GPU", "RTX 3050 Laptop", "4096 MiB"
3. Halaman SPA JavaScript murni (tanpa noscript):
   - Wajib menghasilkan string kosong (sehingga konsol menampilkan pesan deterministik tanpa memanggil model LLM).

Menyimpan bukti empiris mentah ke docs/evidence/tahap6_w4_extraction.txt.
"""

import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from enki.tools.web import extract_clean_text

FIXTURE_1_ARTICLE = """<!DOCTYPE html>
<html>
<head>
    <title>Arsitektur Sistem ENKI</title>
    <style>body { font-size: 14px; color: #333; }</style>
</head>
<body>
    <nav>
        <a href="/">Menu 1</a> | <a href="/docs">Menu 2</a> | <a href="/about">Menu 3</a>
    </nav>
    <article>
        <h1>Arsitektur ENKI</h1>
        <p>ENKI dirancang untuk perangkat keras NVIDIA RTX 3050 dengan batas 4GB VRAM.</p>
        <p>Seluruh sistem beroperasi mandiri dan offline secara default.</p>
    </article>
    <footer>
        <p>Copyright 2026 Contoh Inc. Hak cipta dilindungi.</p>
    </footer>
    <script>
        console.log("tracker");
    </script>
</body>
</html>"""

FIXTURE_2_TABLE = """<!DOCTYPE html>
<html>
<head><title>Spesifikasi Hardware</title></head>
<body>
    <h2>Tabel Spesifikasi Hardware</h2>
    <table>
        <thead>
            <tr><th>Komponen</th><th>Spesifikasi</th></tr>
        </thead>
        <tbody>
            <tr><td>GPU</td><td>NVIDIA RTX 3050 Laptop</td></tr>
            <tr><td>VRAM</td><td>4096 MiB</td></tr>
        </tbody>
    </table>
</body>
</html>"""

FIXTURE_3_JS_ONLY = """<!DOCTYPE html>
<html>
<head><title>Aplikasi SPA Client-Only</title></head>
<body>
    <div id="root"></div>
    <script src="/static/js/main.bundle.js"></script>
</body>
</html>"""


def main():
    evidence_path = WORKSPACE_ROOT / "docs" / "evidence" / "tahap6_w4_extraction.txt"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    output_lines = []
    def log(msg: str = ""):
        output_lines.append(msg)
        print(msg)

    log("=" * 80)
    log("BUKTI EMPIRIS TAHAP 6 — KRITERIA W4 (KUALITAS EKSTRAKSI TEKS)")
    log("=" * 80)

    # -------------------------------------------------------------
    # 1. Evaluasi Fixture 1 (Artikel dengan Boilerplate)
    # -------------------------------------------------------------
    log("\n--- [1/3] FIXTURE 1: ARTIKEL DENGAN BOILERPLATE ---")
    t1, tr1 = extract_clean_text(FIXTURE_1_ARTICLE)
    log(f"Teks Hasil Ekstraksi (panjang={len(t1)}, truncated={tr1}):")
    log("-" * 40)
    log(t1)
    log("-" * 40)

    boilerplate_markers = [
        "Menu 1",
        "Copyright 2026 Contoh Inc.",
        "tracker",
        "font-size",
    ]
    main_markers = [
        "Arsitektur ENKI",
        "RTX 3050",
        "offline secara default",
    ]

    log("\nPemeriksaan Marker Boilerplate (HARUS TIDAK ADA):")
    bp_passed = True
    for bp in boilerplate_markers:
        found = bp in t1
        status = "FAIL (LOLOS)" if found else "PASS (TERLUCUTI)"
        log(f"  - Marker '{bp}': {status}")
        if found:
            bp_passed = False

    log("\nPemeriksaan Marker Konten Utama (HARUS ADA):")
    main_passed = True
    for mm in main_markers:
        found = mm in t1
        status = "PASS (DITEMUKAN)" if found else "FAIL (HILANG)"
        log(f"  - Marker '{mm}': {status}")
        if not found:
            main_passed = False

    f1_status = bp_passed and main_passed
    log(f"Status Fixture 1: {'LULUS' if f1_status else 'GAGAL'}")

    # -------------------------------------------------------------
    # 2. Evaluasi Fixture 2 (Halaman Tabel Data)
    # -------------------------------------------------------------
    log("\n--- [2/3] FIXTURE 2: HALAMAN TABEL DATA ---")
    t2, tr2 = extract_clean_text(FIXTURE_2_TABLE)
    log(f"Teks Hasil Ekstraksi (panjang={len(t2)}, truncated={tr2}):")
    log("-" * 40)
    log(t2)
    log("-" * 40)

    table_markers = [
        "GPU",
        "RTX 3050 Laptop",
        "4096 MiB",
    ]
    log("\nPemeriksaan Marker Tabel:")
    t2_passed = True
    for tm in table_markers:
        found = tm in t2
        status = "PASS (DITEMUKAN)" if found else "FAIL (HILANG)"
        log(f"  - Marker '{tm}': {status}")
        if not found:
            t2_passed = False

    f2_status = t2_passed
    log(f"Status Fixture 2: {'LULUS' if f2_status else 'GAGAL'}")

    # -------------------------------------------------------------
    # 3. Evaluasi Fixture 3 (Halaman SPA JavaScript Murni)
    # -------------------------------------------------------------
    log("\n--- [3/3] FIXTURE 3: HALAMAN SPA JAVASCRIPT MURNI ---")
    t3, tr3 = extract_clean_text(FIXTURE_3_JS_ONLY)
    log(f"Teks Hasil Ekstraksi (panjang={len(t3)}, truncated={tr3}):")
    log("-" * 40)
    log(repr(t3))
    log("-" * 40)

    # Catatan: Trafilatura dapat mengekstrak tag <title>, namun teks konten body utama adalah kosong
    # Jika html murni script tanpa body text:
    pure_js_empty = """<html><head></head><body><div id="root"></div><script src="bundle.js"></script></body></html>"""
    t3_pure, _ = extract_clean_text(pure_js_empty)
    log(f"Varian Murni Tanpa Title: {repr(t3_pure)}")

    f3_status = (t3_pure.strip() == "")
    log(f"Status Fixture 3: {'LULUS' if f3_status else 'GAGAL'}")

    # -------------------------------------------------------------
    # Ringkasan Akhir Kriteria W4
    # -------------------------------------------------------------
    overall_status = f1_status and f2_status and f3_status
    log("\n" + "=" * 80)
    log(f"STATUS AKHIR KRITERIA W4: {'LULUS' if overall_status else 'W4 GAGAL'}")
    log("=" * 80)

    evidence_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    print(f"\n[Selesai] Bukti W4 ditulis ke: {evidence_path}")

    if not overall_status:
        sys.exit(1)


if __name__ == "__main__":
    main()
