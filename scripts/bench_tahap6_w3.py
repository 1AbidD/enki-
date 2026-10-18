#!/usr/bin/env python3
"""Skrip Pengujian & Bukti Empiris Kriteria W3 Sungguhan (Server HTTP Riil Lokal).

Menguji penegakan batas sumber daya pada koneksi HTTP jaringan riil:
1. Streaming respon besar (10 MB):
   - Server mengalirkan 10 MB data chunk.
   - Klien streaming berhenti membaca segera setelah melewati 1.5 MB (1.572.864 byte).
   - Wajib: byte yang diterima <= 1.5 MB + 1 chunk (<= 1.581.056 byte) dan koneksi ditutup.
2. Server lambat (Slow response):
   - Server menunda respon selama 4 detik.
   - Klien menetapkan timeout 1.0 detik.
   - Wajib: timeout ditegakkan, pemanggilan gagal dengan TimeoutError dalam ~1 detik.

Menyimpan bukti empiris ke docs/evidence/tahap6_w3_limits.txt.
"""

import http.server
import sys
import threading
import time
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from enki.tools.web import fetch_web_page


class ChunkedHTTPHandler(http.server.BaseHTTPRequestHandler):
    """Handler server lokal untuk mengalirkan 10MB atau menunda respon."""

    def do_GET(self):
        if self.path == "/stream_10mb":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()

            chunk = b"X" * 65536  # 64 KB per chunk
            total_sent = 0
            # Alirkan hingga 160 chunk = 10.485.760 byte (~10 MB)
            try:
                for i in range(160):
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    total_sent += len(chunk)
            except (ConnectionResetError, BrokenPipeError):
                # Klien berhasil memutuskan koneksi lebih awal
                pass
            except Exception:
                pass

        elif self.path == "/slow":
            # Tunda 4 detik sebelum mengirim header
            time.sleep(4.0)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Respon lambat yang seharusnya terkena timeout.")
            except Exception:
                pass

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Diamkan log konsol server agar output bersih
        pass


def main():
    evidence_path = WORKSPACE_ROOT / "docs" / "evidence" / "tahap6_w3_limits.txt"
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    output_lines = []
    def log(msg: str = ""):
        output_lines.append(msg)
        print(msg)

    log("=" * 80)
    log("BUKTI EMPIRIS TAHAP 6 — KRITERIA W3 (BATASAN SUMBER DAYA JARINGAN RIIL)")
    log("=" * 80)

    # 1. Jalankan server lokal di port dinamis (port 0)
    server = http.server.HTTPServer(("127.0.0.1", 0), ChunkedHTTPHandler)
    server_port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    log(f"Server HTTP Pengujian Aktif di http://127.0.0.1:{server_port}")

    # -------------------------------------------------------------
    # Uji 1: Streaming Respon 10 MB (Penghentian Dini pada 1.5 MB)
    # -------------------------------------------------------------
    log("\n--- [1/2] PENGUJIAN STREAMING 10 MB (BATAS MAKS 1.5 MB) ---")
    url_10mb = f"http://127.0.0.1:{server_port}/stream_10mb"

    t0 = time.perf_counter()
    ok_10mb, body_10mb, err_10mb, meta_10mb = fetch_web_page(
        url_10mb,
        allow_private_hosts=True,
        timeout_seconds=15.0,
    )
    dur_10mb = (time.perf_counter() - t0) * 1000

    bytes_received = meta_10mb.get("bytes_downloaded", 0)
    max_allowed_bytes = 1572864 + 8192  # 1.5 MB + 1 chunk buffer (8 KB)

    log(f"URL Target            : {url_10mb}")
    log(f"Status Sukses         : {ok_10mb} (Diharapkan False)")
    log(f"Pesan Error           : {err_10mb}")
    log(f"Byte Diterima Klien   : {bytes_received} byte")
    log(f"Batas Maksimal Aman   : {max_allowed_bytes} byte (1.5 MB + 1 chunk)")
    log(f"Durasi Transaksi      : {dur_10mb:.2f} ms")

    test1_passed = (
        not ok_10mb
        and "melebihi batas maksimal" in (err_10mb or "").lower()
        and 1572864 < bytes_received <= max_allowed_bytes
    )
    log(f"Hasil Uji Streaming 10 MB: {'PASS (LULUS)' if test1_passed else 'FAIL (GAGAL)'}")

    # -------------------------------------------------------------
    # Uji 2: Penegakan Timeout Riil (Server Lambat 4 Detik, Batas 1 Detik)
    # -------------------------------------------------------------
    log("\n--- [2/2] PENGUJIAN TIMEOUT RIIL (SERVER LAMBAT 4S, BATAS 1S) ---")
    url_slow = f"http://127.0.0.1:{server_port}/slow"

    t0_slow = time.perf_counter()
    ok_slow, body_slow, err_slow, meta_slow = fetch_web_page(
        url_slow,
        allow_private_hosts=True,
        timeout_seconds=1.0,
    )
    dur_slow = time.perf_counter() - t0_slow

    log(f"URL Target            : {url_slow}")
    log(f"Status Sukses         : {ok_slow} (Diharapkan False)")
    log(f"Pesan Error           : {err_slow}")
    log(f"Durasi Transaksi      : {dur_slow:.2f} detik (Diharapkan ~1 detik, < 2.5s)")

    test2_passed = (
        not ok_slow
        and "timeout" in (err_slow or "").lower()
        and 0.8 <= dur_slow < 2.5
    )
    log(f"Hasil Uji Timeout Riil   : {'PASS (LULUS)' if test2_passed else 'FAIL (GAGAL)'}")

    server.shutdown()

    # -------------------------------------------------------------
    # Ringkasan Status Kriteria W3
    # -------------------------------------------------------------
    overall_status = test1_passed and test2_passed
    log("\n" + "=" * 80)
    log(f"STATUS AKHIR KRITERIA W3 (SUNGGUHAN): {'LULUS' if overall_status else 'W3 GAGAL'}")
    log("=" * 80)

    evidence_path.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    print(f"\n[Selesai] Bukti W3 ditulis ke: {evidence_path}")

    if not overall_status:
        sys.exit(1)


if __name__ == "__main__":
    main()
