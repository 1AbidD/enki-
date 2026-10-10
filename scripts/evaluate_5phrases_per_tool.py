"""
Skrip evaluasi 30 frasa uji gating deterministik (5 frasa per kategori alat).
Menghasilkan keluaran mentah nama tool yang aktif dari skema fungsi (bukan dari kata kunci).
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from enki.core.react_loop import get_active_tools_for_turn

PHRASES_PER_CATEGORY = {
    "read_file": [
        "tolong tampilkan isi README.md",
        "bisa tolong baca berkas konfigurasi?",
        "coba periksa file config.json kita",
        "buka catatan di ./docs/catatan.txt",
        "bisa bantu baca skrip implementasi utama kita?"
    ],
    "git": [
        "cek git status saat ini",
        "tolong buat commit baru untuk perubahan tadi",
        "ada berapa branch di repository ini?",
        "tampilkan git diff terakhir",
        "bagaimana riwayat perubahan terakhir di repo kita?"
    ],
    "run_unittests": [
        "jalankan unit test sekarang",
        "coba jalanin semua test di proyek ini",
        "tolong run pytest untuk memeriksa regresi",
        "jalankan tes kode sebelum push",
        "coba cek hasil pengujian perangkat lunak"
    ],
    "python_version": [
        "cek versi python yang terpasang",
        "python version berapa yang kita gunakan?",
        "apakah python di komputer ini versi terbaru?",
        "tolong tampilkan version python di mesin ini",
        "cek versi interpreter bahasa yang sedang aktif"
    ],
    "list_dir": [
        "tampilkan daftar berkas di folder saat ini",
        "apa saja isi dari direktori workspace?",
        "coba jalankan ls untuk melihat berkas",
        "bisa bantu list isi dir proyek?",
        "tunjukkan seluruh isi struktur proyek"
    ],
    "search_memory": [
        "kemarin kita sempat diskusi soal arsitektur apa ya?",
        "ingat tidak preferensi bahasa yang saya sebutkan?",
        "pernahkah kita membahas masalah koneksi database sebelumnya?",
        "buka riwayat percakapan lama tentang deployment",
        "apa nama domain server yang saya ceritakan pada obrolan awal?"
    ]
}

def main():
    lines = []
    lines.append("=" * 80)
    lines.append("KELUARAN MENTAH GATING DETERMINISTIK: 30 FRASA UJI PER KATEGORI ALAT")
    lines.append("Catatan: Nama tools diambil langsung dari [schema['function']['name']]")
    lines.append("=" * 80)
    lines.append("")

    total_count = 0
    match_count = 0

    for category, phrases in PHRASES_PER_CATEGORY.items():
        lines.append(f"Kategori Target: [{category}]")
        lines.append("-" * 80)
        cat_matches = 0
        for idx, phrase in enumerate(phrases, 1):
            total_count += 1
            tools = get_active_tools_for_turn(phrase)
            tool_names = [t["function"]["name"] for t in tools] if tools else []
            is_matched = (category in tool_names)
            if is_matched:
                cat_matches += 1
                match_count += 1

            status = "MATCH" if is_matched else "NO_MATCH"
            lines.append(f"  [{status}] {idx}. \"{phrase}\"")
            lines.append(f"          Tools Aktif : {tool_names if tool_names else 'None'}")

        lines.append(f"Ringkasan [{category}]: {cat_matches}/5 cocok")
        lines.append("")

    lines.append("=" * 80)
    lines.append("RINGKASAN TOTAL")
    lines.append(f"Total Frasa : {total_count}")
    lines.append(f"Total Cocok : {match_count} ({match_count/total_count*100:.1f}%)")
    lines.append(f"Tidak Cocok : {total_count - match_count} ({(total_count - match_count)/total_count*100:.1f}%) [Limitasi leksikal]")
    lines.append("=" * 80)

    report_content = "\n".join(lines)
    out_file = ROOT_DIR / "docs" / "evidence" / "gating_5phrases_report.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report_content)
    print(f"Laporan berhasil ditulis ke {out_file}")

if __name__ == "__main__":
    main()
