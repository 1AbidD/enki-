"""Skrip probe jaringan untuk Tahap 6 Langkah 0b.

Memeriksa keterjangkauan DNS dan HTTPS dengan verifikasi TLS aktif untuk:
- example.com
- id.wikipedia.org (endpoint REST summary)
- en.wikipedia.org
- pypi.org
- github.com

Menuliskan bukti empiris ke docs/evidence/tahap6_net_probe.txt.
"""

import datetime
import socket
import ssl
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path


TARGETS = [
    {
        "name": "example.com",
        "host": "example.com",
        "url": "https://example.com",
    },
    {
        "name": "id.wikipedia.org (REST summary)",
        "host": "id.wikipedia.org",
        "url": "https://id.wikipedia.org/api/rest_v1/page/summary/Python",
    },
    {
        "name": "en.wikipedia.org",
        "host": "en.wikipedia.org",
        "url": "https://en.wikipedia.org/api/rest_v1/page/summary/Python",
    },
    {
        "name": "pypi.org",
        "host": "pypi.org",
        "url": "https://pypi.org",
    },
    {
        "name": "github.com",
        "host": "github.com",
        "url": "https://github.com",
    },
]


def probe_target(target: dict) -> dict:
    host = target["host"]
    url = target["url"]

    # 1. DNS Resolution
    t_dns_start = time.perf_counter()
    dns_ips = []
    dns_error = None
    try:
        addr_info = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
        dns_ips = sorted(list({info[4][0] for info in addr_info}))
    except Exception as e:
        dns_error = f"{type(e).__name__}: {e}"
    t_dns = (time.perf_counter() - t_dns_start) * 1000

    # 2. HTTPS Request with strict TLS verification
    t_http_start = time.perf_counter()
    http_status = None
    http_error = None
    bytes_read = 0

    ctx = ssl.create_default_context()
    # DILARANG verify=False atau trik proxy
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ENKI/0.2 (local personal assistant)",
            "Accept": "text/html,application/xhtml+xml,application/json,*/*",
        }
    )

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=10.0) as resp:
            http_status = resp.status
            data = resp.read(2048)
            bytes_read = len(data)
    except urllib.error.HTTPError as e:
        http_status = e.code
        http_error = f"HTTPError {e.code}: {e.reason}"
    except Exception as e:
        http_error = f"{type(e).__name__}: {e}"

    t_http = (time.perf_counter() - t_http_start) * 1000

    return {
        "target": target["name"],
        "host": host,
        "url": url,
        "dns_ips": dns_ips,
        "dns_time_ms": t_dns,
        "dns_error": dns_error,
        "http_status": http_status,
        "http_time_ms": t_http,
        "http_error": http_error,
        "bytes_read": bytes_read,
        "reachable": (http_status is not None and 200 <= http_status < 400),
    }


def main():
    evidence_path = Path("docs/evidence/tahap6_net_probe.txt")
    evidence_path.parent.mkdir(parents=True, exist_ok=True)

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append("=" * 80)
    lines.append("PROBE JARINGAN TAHAP 6 (LANGKAH 0b)")
    lines.append(f"Waktu Uji   : {now_str}")
    lines.append(f"TLS Verify  : WAJIB AKTIF (ssl.CERT_REQUIRED & check_hostname=True)")
    lines.append("=" * 80)
    lines.append("")

    results = []
    for t in TARGETS:
        print(f"Memeriksa {t['name']} ({t['url']})...")
        res = probe_target(t)
        results.append(res)

        status_str = f"HTTP {res['http_status']}" if res['http_status'] else "FAILED"
        err_str = f" | Error: {res['http_error']}" if res['http_error'] else ""
        lines.append(f"Target      : {res['target']}")
        lines.append(f"URL         : {res['url']}")
        lines.append(f"DNS IP      : {', '.join(res['dns_ips']) if res['dns_ips'] else 'RESOLVE FAILED'} ({res['dns_time_ms']:.2f} ms)")
        lines.append(f"HTTPS Status: {status_str} ({res['http_time_ms']:.2f} ms){err_str}")
        lines.append(f"Keterjangkau: {'YA (OK)' if res['reachable'] else 'TIDAK (GAGAL)'}")
        lines.append("-" * 80)

    # Evaluasi Gerbang (Gate) Langkah 0b
    example_res = next((r for r in results if r["host"] == "example.com"), None)
    wiki_id_res = next((r for r in results if r["host"] == "id.wikipedia.org"), None)

    example_ok = example_res and example_res["reachable"]
    wiki_id_ok = wiki_id_res and wiki_id_res["reachable"]

    lines.append("")
    lines.append("=" * 80)
    lines.append("RINGKASAN STATUS GERBANG LANGKAH 0b:")
    lines.append(f"- example.com      : {'TERJANGKAU (PASS)' if example_ok else 'TIDAK TERJANGKAU (FAIL)'}")
    lines.append(f"- id.wikipedia.org : {'TERJANGKAU (PASS)' if wiki_id_ok else 'TIDAK TERJANGKAU (FAIL)'}")
    
    if not example_ok and not wiki_id_ok:
        lines.append("STATUS AKHIR: GAGAL TOTAL (example.com DAN id.wikipedia.org tidak terjangkau).")
        lines.append("PENGEMBANGAN HARUS BERHENTI SESUAI SOP.")
    else:
        lines.append("STATUS AKHIR: LULUS (Jaringan terjangkau dan TLS valid). Melanjutkan pembangunan Tahap 6.")
    lines.append("=" * 80)

    content = "\n".join(lines) + "\n"
    evidence_path.write_text(content, encoding="utf-8")
    print(f"\nHasil probe ditulis ke {evidence_path}:")
    print(content)

    if not example_ok and not wiki_id_ok:
        sys.exit(2)


if __name__ == "__main__":
    main()
