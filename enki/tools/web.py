"""Subsistem Web Tools untuk ENKI (Tahap 6).

Menyediakan:
1. Validasi URL & Pertahanan SSRF (skema http/https, port 80/443, tolak IP privat/loopback/link-local/userinfo/panjang).
2. Fetcher HTTP aman dengan batas waktu (15s total), batas unduhan (1.5MB), allowlist MIME text/html/plain/xhtml, dan proteksi redirect (maks 3 hop).
3. Ekstraksi teks web menggunakan trafilatura (lazy import) dengan fallback stdlib html.parser, dipotong maks 6000 karakter.
4. Handler REST API Wikipedia (/wiki) dengan fallback id -> en.
5. Pembuka URL peramban web (/buka) aman.
6. Pencatatan audit ke tool_audit.log.
"""

import datetime
import html
import html.parser
import ipaddress
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from enki.config import get_log_dir

USER_AGENT = "ENKI/0.2 (local personal assistant)"
ALLOWED_SCHEMES = {"http", "https"}
ALLOWED_PORTS = {80, 443}
ALLOWED_MIME_PREFIXES = ("text/html", "text/plain", "application/xhtml+xml")
MAX_URL_LENGTH = 2048
MAX_REDIRECTS = 3


def log_web_audit(url: str, status_code: int, byte_count: int, duration_ms: float, error: str = "") -> None:
    """Mencatat aktivitas akses web ke logs/tool_audit.log."""
    try:
        log_dir = get_log_dir()
        log_file = log_dir / "tool_audit.log"
        now_iso = datetime.datetime.now().isoformat()
        err_field = f" | error={error}" if error else ""
        entry = (
            f"[{now_iso}] tool=web | url={url} | status={status_code} | "
            f"bytes={byte_count} | duration_ms={duration_ms:.2f}{err_field}\n"
        )
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


def _is_private_ip_obj(ip: Any) -> bool:
    """Memeriksa apakah objek IP address merupakan IP privat, loopback, link-local, dsb."""
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    ip_str = str(ip)
    if ip_str in ("0.0.0.0", "127.0.0.1", "::1", "169.254.169.254"):
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        m = ip.ipv4_mapped
        if m.is_private or m.is_loopback or m.is_link_local or str(m) in ("0.0.0.0", "127.0.0.1"):
            return True
    return False


def _try_parse_ip_literal(hostname: str) -> Optional[Any]:
    """Mencoba mem-parsing string hostname sebagai IP literal dalam berbagai format."""
    # Bersihkan bracket IPv6
    clean_host = hostname.strip("[]")

    # 1. Parsing langsung (IPv4 / IPv6 standar)
    try:
        return ipaddress.ip_address(clean_host)
    except ValueError:
        pass

    # 2. Integer desimal murni (misal: "2130706433" -> 127.0.0.1)
    if clean_host.isdigit():
        try:
            val = int(clean_host)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except Exception:
            pass

    # 3. Heksadesimal murni (misal: "0x7f000001")
    if clean_host.lower().startswith("0x"):
        try:
            val = int(clean_host, 16)
            if 0 <= val <= 0xFFFFFFFF:
                return ipaddress.IPv4Address(val)
        except Exception:
            pass

    # 4. Format oktet bertitik dengan desimal/hex/oktal/singkat (misal: "127.1", "0x7f.0.0.1")
    parts = clean_host.split(".")
    if len(parts) in (2, 3, 4):
        parsed_octets = []
        is_all_numeric = True
        for p in parts:
            try:
                if p.lower().startswith("0x"):
                    num = int(p, 16)
                elif p.startswith("0") and len(p) > 1 and p.isdigit():
                    num = int(p, 8)
                else:
                    num = int(p)
                parsed_octets.append(num)
            except ValueError:
                is_all_numeric = False
                break
        if is_all_numeric:
            try:
                if len(parsed_octets) == 2:  # A.B -> A.0.0.B
                    ipv4_int = (parsed_octets[0] << 24) | parsed_octets[1]
                elif len(parsed_octets) == 3:  # A.B.C -> A.B.0.C
                    ipv4_int = (parsed_octets[0] << 24) | (parsed_octets[1] << 16) | parsed_octets[2]
                elif len(parsed_octets) == 4:
                    ipv4_int = (
                        (parsed_octets[0] << 24)
                        | (parsed_octets[1] << 16)
                        | (parsed_octets[2] << 8)
                        | parsed_octets[3]
                    )
                else:
                    ipv4_int = None
                if ipv4_int is not None and 0 <= ipv4_int <= 0xFFFFFFFF:
                    return ipaddress.IPv4Address(ipv4_int)
            except Exception:
                pass

    return None


def validate_url(
    url: str,
    allow_private_hosts: bool = False,
    resolver: Optional[Callable[[str, int], List[str]]] = None,
) -> Tuple[bool, str, Optional[str]]:
    """Memvalidasi URL terhadap aturan keamanan SSRF dan parameter W1.

    Returns:
        (is_valid, normalized_url, error_message)
    """
    if not isinstance(url, str) or not url.strip():
        return False, "", "URL tidak boleh kosong."

    raw_url = url.strip()
    if len(raw_url) > MAX_URL_LENGTH:
        return False, "", f"Panjang URL ({len(raw_url)}) melebihi batas maksimal {MAX_URL_LENGTH} karakter."

    # Periksa spasi di tengah URL
    if " " in raw_url:
        return False, "", "URL tidak boleh mengandung karakter spasi."

    # Jika tanpa skema, default ke https://
    # (hanya jika tidak mengandung skema terlarang lain seperti file:, ftp:, etc.)
    if "://" not in raw_url:
        forbidden_schemes = ("file:", "ftp:", "javascript:", "data:", "mailto:", "tel:", "ssh:")
        for s in forbidden_schemes:
            if raw_url.lower().startswith(s):
                return False, "", f"Skema URL '{s[:-1]}' dilarang keras."
        normalized = "https://" + raw_url
    else:
        normalized = raw_url

    try:
        parsed = urllib.parse.urlsplit(normalized)
    except Exception as e:
        return False, "", f"Format URL tidak dapat di-parse: {e}"

    # 1. Skema
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return False, "", f"Skema '{scheme}' tidak diizinkan (hanya http dan https)."

    # 2. Userinfo
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        return False, "", "URL tidak boleh mengandung kredensial userinfo (user:pass@)."

    # 3. Port
    port = parsed.port
    if port is not None and port not in ALLOWED_PORTS:
        if not allow_private_hosts:
            return False, "", f"Port {port} tidak diizinkan (hanya port 80 dan 443)."

    # 4. Hostname
    hostname = parsed.hostname
    if not hostname:
        return False, "", "URL tidak memiliki hostname yang valid."

    hostname_lower = hostname.lower()
    if not allow_private_hosts and (hostname_lower == "localhost" or hostname_lower.endswith(".local")):
        return False, "", f"Hostname '{hostname}' adalah alamat lokal/privat yang dilarang."

    # 5. Deteksi IP Literal
    ip_obj = _try_parse_ip_literal(hostname_lower)
    if ip_obj is not None:
        if not allow_private_hosts and _is_private_ip_obj(ip_obj):
            return False, "", f"Akses ke IP privat/loopback/link-local ({ip_obj}) dilarang keras."
    else:
        # 6. Resolusi DNS untuk domain publik
        target_port = port or (443 if scheme == "https" else 80)
        resolved_ips: List[str] = []
        if resolver is not None:
            try:
                resolved_ips = resolver(hostname, target_port)
            except Exception as e:
                return False, "", f"Gagal menyelesaikan DNS untuk '{hostname}': {e}"
        else:
            try:
                addr_info = socket.getaddrinfo(hostname, target_port, proto=socket.IPPROTO_TCP)
                resolved_ips = list({info[4][0] for info in addr_info})
            except Exception as e:
                return False, "", f"Gagal menyelesaikan DNS untuk '{hostname}': {e}"

        if not resolved_ips:
            return False, "", f"Hostname '{hostname}' tidak menghasilkan alamat IP publik yang valid."

        if not allow_private_hosts:
            for ip_str in resolved_ips:
                parsed_res_ip = _try_parse_ip_literal(ip_str)
                if parsed_res_ip is None or _is_private_ip_obj(parsed_res_ip):
                    return (
                        False,
                        "",
                        f"Hostname '{hostname}' menyelesaikan ke IP privat ({ip_str}) yang dilarang.",
                    )

    # Susun kembali URL ternormalisasi
    reconstructed = urllib.parse.urlunsplit((
        scheme,
        parsed.netloc,
        parsed.path or "/",
        parsed.query,
        parsed.fragment,
    ))
    return True, reconstructed, None


class _HTMLTextExtractor(html.parser.HTMLParser):
    """Ekstraktor teks cadangan berbasis stdlib html.parser."""

    def __init__(self):
        super().__init__()
        self.text_parts: List[str] = []
        self.ignore_depth = 0
        self.ignore_tags = {
            "script", "style", "noscript", "svg", "nav", "footer", "header", "aside"
        }

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.ignore_tags:
            self.ignore_depth += 1
        elif tag.lower() in ("p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in self.ignore_tags and self.ignore_depth > 0:
            self.ignore_depth -= 1
        elif tag.lower() in ("p", "div", "tr", "li"):
            self.text_parts.append("\n")

    def handle_data(self, data):
        if self.ignore_depth == 0:
            self.text_parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        # Normalisasi spasi dan baris
        lines = [re.sub(r"[ \t]+", " ", l).strip() for l in raw.splitlines()]
        cleaned = "\n".join(l for l in lines if l)
        return cleaned


def extract_clean_text(html_content: str, max_chars: int = 6000) -> Tuple[str, bool]:
    """Mengekstrak teks utama dari HTML menggunakan trafilatura dengan fallback ke stdlib html.parser.

    Returns:
        (extracted_text, is_truncated)
    """
    if not html_content or not html_content.strip():
        return "", False

    extracted = None
    try:
        # Lazy import trafilatura
        import trafilatura

        extracted = trafilatura.extract(
            html_content,
            include_tables=True,
            include_links=False,
            include_comments=False,
            no_fallback=False,
        )
    except Exception:
        extracted = None

    if not extracted or not extracted.strip():
        # Fallback ke stdlib
        parser = _HTMLTextExtractor()
        try:
            parser.feed(html_content)
            extracted = parser.get_text()
        except Exception:
            extracted = ""

    extracted = (extracted or "").strip()
    if not extracted:
        return "", False

    is_truncated = len(extracted) > max_chars
    if is_truncated:
        extracted = extracted[:max_chars] + f"\n... [Teks dipotong: maks {max_chars} karakter]"

    return extracted, is_truncated


def fetch_web_page(
    url: str,
    timeout_seconds: float = 15.0,
    max_bytes: int = 1572864,
    allow_private_hosts: bool = False,
    resolver: Optional[Callable[[str, int], List[str]]] = None,
    transport: Optional[Callable] = None,
    allowed_mime_prefixes: Optional[Tuple[str, ...]] = None,
) -> Tuple[bool, str, Optional[str], Dict[str, Any]]:
    """Mengambil konten teks dari URL web dengan penegakan batasan keamanan dan ukuran.

    Returns:
        (success, body_content, error_message, meta_dict)
    """
    t0 = time.perf_counter()
    current_url = url
    redirect_chain = []
    meta: Dict[str, Any] = {
        "final_url": current_url,
        "redirect_count": 0,
        "status_code": 0,
        "bytes_downloaded": 0,
        "duration_ms": 0.0,
        "content_type": "",
    }

    visited = set()

    for hop in range(MAX_REDIRECTS + 1):
        is_val, norm_url, err = validate_url(
            current_url,
            allow_private_hosts=allow_private_hosts,
            resolver=resolver,
        )
        if not is_val:
            dur = (time.perf_counter() - t0) * 1000
            meta["duration_ms"] = dur
            log_web_audit(current_url, 0, 0, dur, error=err or "Validasi URL gagal")
            return False, "", err, meta

        if norm_url in visited:
            dur = (time.perf_counter() - t0) * 1000
            meta["duration_ms"] = dur
            err_msg = f"Terdeteksi loop pengalihan (redirect loop) ke: {norm_url}"
            log_web_audit(norm_url, 310, 0, dur, error=err_msg)
            return False, "", err_msg, meta

        visited.add(norm_url)
        current_url = norm_url
        meta["final_url"] = current_url

        if hop > MAX_REDIRECTS:
            dur = (time.perf_counter() - t0) * 1000
            meta["duration_ms"] = dur
            err_msg = f"Pengalihan URL melebihi batas maksimal {MAX_REDIRECTS} kali hop."
            log_web_audit(current_url, 310, 0, dur, error=err_msg)
            return False, "", err_msg, meta

        # Eksekusi HTTP Request
        status_code = 0
        headers_dict: Dict[str, str] = {}
        raw_body = bytearray()

        if transport is not None:
            try:
                resp_obj = transport(current_url, timeout=timeout_seconds)
                status_code = getattr(resp_obj, "status", getattr(resp_obj, "status_code", 200))
                headers_dict = {k.lower(): v for k, v in getattr(resp_obj, "headers", {}).items()}
                body_stream = getattr(resp_obj, "body", b"")
                if isinstance(body_stream, str):
                    raw_body = bytearray(body_stream.encode("utf-8"))
                else:
                    raw_body = bytearray(body_stream)
            except Exception as e:
                dur = (time.perf_counter() - t0) * 1000
                meta["duration_ms"] = dur
                err_msg = f"Gagal mengambil URL via transport: {type(e).__name__}: {e}"
                log_web_audit(current_url, 0, 0, dur, error=err_msg)
                return False, "", err_msg, meta
        else:
            # Gunakan requests (lazy import) dengan streaming
            try:
                import requests

                # Timeout tuple: (connect_timeout, read_timeout)
                conn_timeout = min(5.0, timeout_seconds)
                read_timeout = max(1.0, timeout_seconds - conn_timeout)

                req_headers = {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,text/plain,*/*",
                    "Accept-Encoding": "gzip, deflate",
                }

                # allow_redirects=False agar setiap hop divalidasi SSRF
                with requests.get(
                    current_url,
                    headers=req_headers,
                    timeout=(conn_timeout, read_timeout),
                    stream=True,
                    allow_redirects=False,
                ) as resp:
                    status_code = resp.status_code
                    headers_dict = {k.lower(): v for k, v in resp.headers.items()}

                    # Jika redirect, tangani secara manual
                    if status_code in (301, 302, 303, 307, 308):
                        location = headers_dict.get("location")
                        if not location:
                            dur = (time.perf_counter() - t0) * 1000
                            meta["duration_ms"] = dur
                            err_msg = f"Server mengembalikan status {status_code} tanpa header Location."
                            log_web_audit(current_url, status_code, 0, dur, error=err_msg)
                            return False, "", err_msg, meta

                        # Normalisasi Location relatif
                        next_url = urllib.parse.urljoin(current_url, location)
                        current_url = next_url
                        meta["redirect_count"] += 1
                        redirect_chain.append(next_url)
                        continue

                    # Periksa Content-Type
                    ctype = headers_dict.get("content-type", "").lower()
                    meta["content_type"] = ctype
                    effective_mimes = allowed_mime_prefixes or ALLOWED_MIME_PREFIXES
                    is_allowed_mime = any(ctype.startswith(p) for p in effective_mimes)
                    if not is_allowed_mime:
                        dur = (time.perf_counter() - t0) * 1000
                        meta["duration_ms"] = dur
                        err_msg = f"Tipe konten '{ctype}' tidak diizinkan."
                        log_web_audit(current_url, status_code, 0, dur, error=err_msg)
                        return False, "", err_msg, meta

                    # Periksa Content-Length jika ada
                    clength_hdr = headers_dict.get("content-length")
                    if clength_hdr and clength_hdr.isdigit():
                        if int(clength_hdr) > max_bytes:
                            dur = (time.perf_counter() - t0) * 1000
                            meta["duration_ms"] = dur
                            err_msg = f"Ukuran berkas ({clength_hdr} bytes) melampaui batas maksimal {max_bytes} bytes (1.5 MB)."
                            log_web_audit(current_url, status_code, 0, dur, error=err_msg)
                            return False, "", err_msg, meta

                    # Streaming baca potongan data hingga max_bytes
                    for chunk in resp.iter_content(chunk_size=8192):
                        if chunk:
                            raw_body.extend(chunk)
                            if len(raw_body) > max_bytes:
                                # Hentikan stream seketika
                                break

            except Exception as e:
                dur = (time.perf_counter() - t0) * 1000
                meta["duration_ms"] = dur
                err_msg = f"Kesalahan koneksi HTTP: {type(e).__name__}: {e}"
                log_web_audit(current_url, 0, len(raw_body), dur, error=err_msg)
                return False, "", err_msg, meta

        meta["status_code"] = status_code
        meta["bytes_downloaded"] = len(raw_body)

        # Jika status redirect pada transport mock
        if status_code in (301, 302, 303, 307, 308):
            location = headers_dict.get("location")
            if not location:
                dur = (time.perf_counter() - t0) * 1000
                meta["duration_ms"] = dur
                err_msg = f"Server mengembalikan status {status_code} tanpa header Location."
                log_web_audit(current_url, status_code, len(raw_body), dur, error=err_msg)
                return False, "", err_msg, meta
            next_url = urllib.parse.urljoin(current_url, location)
            current_url = next_url
            meta["redirect_count"] += 1
            continue

        if not (200 <= status_code < 300):
            dur = (time.perf_counter() - t0) * 1000
            meta["duration_ms"] = dur
            err_msg = f"Permintaan web mengembalikan status error HTTP {status_code}."
            log_web_audit(current_url, status_code, len(raw_body), dur, error=err_msg)
            return False, "", err_msg, meta

        # Periksa Content-Type
        ctype = headers_dict.get("content-type", "").lower()
        meta["content_type"] = ctype
        effective_mimes = allowed_mime_prefixes or ALLOWED_MIME_PREFIXES
        if ctype and not any(ctype.startswith(p) for p in effective_mimes):
            dur = (time.perf_counter() - t0) * 1000
            meta["duration_ms"] = dur
            err_msg = f"Tipe konten '{ctype}' tidak diizinkan."
            log_web_audit(current_url, status_code, len(raw_body), dur, error=err_msg)
            return False, "", err_msg, meta

        # Periksa ulang ukuran setelah unduh
        if len(raw_body) > max_bytes:
            dur = (time.perf_counter() - t0) * 1000
            meta["duration_ms"] = dur
            err_msg = f"Ukuran konten melebihi batas maksimal {max_bytes} bytes."
            log_web_audit(current_url, status_code, len(raw_body), dur, error=err_msg)
            return False, "", err_msg, meta

        # Dekode karakter teks
        ctype = headers_dict.get("content-type", "").lower()
        meta["content_type"] = ctype
        encoding = "utf-8"
        if "charset=" in ctype:
            encoding = ctype.split("charset=")[-1].split(";")[0].strip()

        try:
            decoded_text = raw_body.decode(encoding)
        except Exception:
            try:
                decoded_text = raw_body.decode("utf-8", errors="replace")
            except Exception:
                decoded_text = raw_body.decode("latin-1", errors="replace")

        dur = (time.perf_counter() - t0) * 1000
        meta["duration_ms"] = dur
        log_web_audit(current_url, status_code, len(raw_body), dur)
        return True, decoded_text, None, meta

    dur = (time.perf_counter() - t0) * 1000
    meta["duration_ms"] = dur
    err_msg = f"Pengalihan URL melebihi batas maksimal {MAX_REDIRECTS} kali."
    log_web_audit(current_url, 310, 0, dur, error=err_msg)
    return False, "", err_msg, meta


def fetch_wikipedia_summary(
    topic: str,
    timeout_seconds: float = 15.0,
    allow_private_hosts: bool = False,
    transport: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Mengambil ringkasan topik dari REST API Wikipedia (id -> fallback en).

    Returns:
        {
            "status": "ok" | "not_found" | "disambiguation" | "error",
            "lang": "id" | "en",
            "title": str,
            "extract": str,
            "url": str,
            "error_message": Optional[str],
            "duration_ms": float
        }
    """
    clean_topic = topic.strip()
    if not clean_topic:
        return {
            "status": "error",
            "lang": "",
            "title": "",
            "extract": "",
            "url": "",
            "error_message": "Topik Wikipedia tidak boleh kosong.",
            "duration_ms": 0.0,
        }

    encoded_topic = urllib.parse.quote(clean_topic)
    endpoints = [
        ("id", f"https://id.wikipedia.org/api/rest_v1/page/summary/{encoded_topic}"),
        ("en", f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_topic}"),
    ]

    t0 = time.perf_counter()

    for lang, api_url in endpoints:
        ok, body, err, meta = fetch_web_page(
            api_url,
            timeout_seconds=timeout_seconds,
            allow_private_hosts=allow_private_hosts,
            transport=transport,
            allowed_mime_prefixes=("application/json", "text/json"),
        )

        if not ok:
            # Jika 404 pada id, coba fallback ke en
            if meta.get("status_code") == 404:
                continue
            dur = (time.perf_counter() - t0) * 1000
            return {
                "status": "error",
                "lang": lang,
                "title": clean_topic,
                "extract": "",
                "url": api_url,
                "error_message": err or f"HTTP {meta.get('status_code')}",
                "duration_ms": dur,
            }

        try:
            import json
            data = json.loads(body)
        except Exception as e:
            dur = (time.perf_counter() - t0) * 1000
            return {
                "status": "error",
                "lang": lang,
                "title": clean_topic,
                "extract": "",
                "url": api_url,
                "error_message": f"Respon API bukan JSON yang valid: {e}",
                "duration_ms": dur,
            }

        res_type = data.get("type", "standard")
        page_url = (
            data.get("content_urls", {}).get("desktop", {}).get("page")
            or f"https://{lang}.wikipedia.org/wiki/{encoded_topic}"
        )
        title = data.get("title", clean_topic)
        extract = data.get("extract", "")

        dur = (time.perf_counter() - t0) * 1000

        if res_type == "disambiguation":
            return {
                "status": "disambiguation",
                "lang": lang,
                "title": title,
                "extract": extract,
                "url": page_url,
                "error_message": f"Topik '{clean_topic}' memiliki makna ganda (disambiguasi) di Wikipedia ({lang.upper()}).",
                "duration_ms": dur,
            }

        if res_type == "standard" and extract:
            return {
                "status": "ok",
                "lang": lang,
                "title": title,
                "extract": extract,
                "url": page_url,
                "error_message": None,
                "duration_ms": dur,
            }

    dur = (time.perf_counter() - t0) * 1000
    return {
        "status": "not_found",
        "lang": "",
        "title": clean_topic,
        "extract": "",
        "url": "",
        "error_message": f"Topik '{clean_topic}' tidak ditemukan di Wikipedia (Bahasa Indonesia maupun Inggris).",
        "duration_ms": dur,
    }


def open_in_browser(
    url: str,
    allow_private_hosts: bool = False,
) -> Tuple[bool, Optional[str]]:
    """Membuka tautan di peramban sistem setelah validasi keamanan URL."""
    import os
    import sys

    is_val, norm_url, err = validate_url(url, allow_private_hosts=allow_private_hosts)
    if not is_val:
        return False, err

    opened = False
    err_msg: Optional[str] = None

    if sys.platform == "win32":
        try:
            os.startfile(norm_url)
            opened = True
        except Exception as e_startfile:
            try:
                res = webbrowser.open(norm_url)
                if res:
                    opened = True
                else:
                    err_msg = f"os.startfile gagal ({e_startfile}) dan webbrowser.open mengembalikan False"
            except Exception as e_web:
                err_msg = f"os.startfile gagal ({e_startfile}) dan webbrowser.open gagal ({e_web})"
    else:
        try:
            res = webbrowser.open(norm_url)
            if res:
                opened = True
            else:
                err_msg = "webbrowser.open mengembalikan False"
        except Exception as e:
            err_msg = f"webbrowser.open gagal: {e}"

    # Pencatatan audit ke tool_audit.log
    try:
        log_dir = get_log_dir()
        log_file = log_dir / "tool_audit.log"
        now_iso = datetime.datetime.now().isoformat()
        status_str = "OK" if opened else "ERROR"
        err_field = f" | error={err_msg}" if err_msg else ""
        entry = f"[{now_iso}] tool=open_browser | url={norm_url} | status={status_str}{err_field}\n"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass

    if opened:
        return True, None
    else:
        return False, err_msg or "Gagal membuka peramban sistem."
