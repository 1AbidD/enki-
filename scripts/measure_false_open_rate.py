"""
Skrip pengukuran False-Open Rate pada 20 kalimat obrolan/pengetahuan umum biasa
yang secara kebetulan memuat kata: file, folder, test, commit, branch, dir, riwayat, catatan, tadi.
Menyimpan laporan ke docs/evidence/false_open_rate_report.txt.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from enki.core.react_loop import get_active_tools_for_turn

SENTENCES = [
    # 1-2: file
    ("apa itu file system di linux?", "file"),
    ("bagaimana cara kerja format file audio MP3 dalam mengompres suara?", "file"),
    ("apa beda file sharing peer-to-peer dengan client-server?", "file"),
    # 3-5: folder
    ("mengapa folder temporary sering dibersihkan oleh sistem operasi?", "folder"),
    ("apakah folder spam di email otomatis dihapus setelah 30 hari?", "folder"),
    # 6-8: test
    ("siapa penemu test kecerdasan IQ pertama kali?", "test"),
    ("apa beda speed test internet dan bandwidth test?", "test"),
    ("mengapa kita perlu test drive mobil sebelum memutuskan membeli?", "test"),
    # 9-11: commit
    ("apa arti kata commit dalam komitmen hubungan?", "commit"),
    ("dalam organisasi, apa maksudnya commit to excellence?", "commit"),
    # 12-13: branch
    ("di bank syariah apa fungsi kantor branch atau cabang?", "branch"),
    ("apa fungsi branch perbankan di era digital?", "branch"),
    # 14-15: dir
    ("apa kepanjangan dari perintah dir di DOS zaman dulu?", "dir"),
    ("apa asal usul kata dir dalam bahasa Arab atau singkatan?", "dir"),
    # 16-17: riwayat
    ("bisa ceritakan riwayat hidup Albert Einstein secara singkat?", "riwayat"),
    ("bagaimana riwayat perkembangan candi Borobudur dari masa ke masa?", "riwayat"),
    # 18-19: catatan
    ("apa beda catatan kaki dan catatan akhir dalam penulisan ilmiah?", "catatan"),
    ("tolong berikan catatan penting saat merawat tanaman hias di rumah?", "catatan"),
    # 20: tadi
    ("tadi pagi sarapan apa yang sehat untuk sarapan cepat?", "tadi"),
    ("tadi malam hujan deras sekali ya di daerahmu?", "tadi")
]

def main():
    lines = []
    lines.append("=" * 80)
    lines.append("PENGUKURAN FALSE-OPEN RATE: 20 KALIMAT OBROLAN / PENGETAHUAN UMUM")
    lines.append("Menguji kalimat non-perintah yang kebetulan memuat kata kunci alat.")
    lines.append("=" * 80)
    lines.append("")

    false_open_count = 0
    closed_count = 0

    tool_opened_counter = {}

    for idx, (sent, kw) in enumerate(SENTENCES, 1):
        tools = get_active_tools_for_turn(sent)
        tool_names = [t["function"]["name"] for t in tools] if tools else []
        is_open = bool(tool_names)

        if is_open:
            false_open_count += 1
            status = "FALSE-OPEN"
            for tn in tool_names:
                tool_opened_counter[tn] = tool_opened_counter.get(tn, 0) + 1
        else:
            closed_count += 1
            status = "CLOSED (BENAR)"

        lines.append(f"[{status:14}] {idx:2d}. Kata kunci: '{kw}'")
        lines.append(f"               Kalimat   : \"{sent}\"")
        lines.append(f"               Tool Aktif: {tool_names if tool_names else 'None'}")

    lines.append("")
    lines.append("=" * 80)
    lines.append("RINGKASAN HASIL PENGUKURAN")
    lines.append("=" * 80)
    lines.append(f"Total Kalimat Diuji : {len(SENTENCES)}")
    lines.append(f"Tertutup Sempurna   : {closed_count}/{len(SENTENCES)} ({closed_count/len(SENTENCES)*100:.1f}%) [0 Tool]")
    lines.append(f"False-Open (Bocor)  : {false_open_count}/{len(SENTENCES)} ({false_open_count/len(SENTENCES)*100:.1f}%) [Membuka Tool]")
    lines.append("")
    lines.append("Distribusi Tool yang Terbuka (False-Open):")
    for tn, count in sorted(tool_opened_counter.items(), key=lambda x: x[1], reverse=True):
        lines.append(f"  - {tn:15}: {count} kali")

    report_text = "\n".join(lines)
    out_file = ROOT_DIR / "docs" / "evidence" / "false_open_rate_report.txt"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"Laporan berhasil disimpan ke {out_file}")

if __name__ == "__main__":
    main()
