#!/usr/bin/env python3
"""Harness Tahap 6-FIX-3 (K1-K3): deteksi otomatis munculnya jendela Edge + matriks kondisi.

K1  Detektor: daftar jendela top-level milik msedge.exe (EnumWindows + GetWindowTextW +
    GetWindowThreadProcessId + IsWindowVisible, nama proses via psutil). Sukses = jendela
    TERLIHAT milik msedge.exe dengan judul mengandung "Example Domain" yang (hwnd, judul)-nya
    tidak ada sebelum peluncuran, muncul dalam 8 detik.
K2  Matriks: kondisi A/B/C x 7 metode x N percobaan, reset penuh per percobaan.
K3  Hasil: docs/evidence/tahap6_open_url_matrix.txt + .json

PERINGATAN: script ini MEMATIKAN Edge (taskkill /f /im msedge.exe) berulang kali dan membuka
jendela Edge di layar. Simpan pekerjaan di Edge sebelum menjalankan.

Pemakaian:
    .\\venv\\Scripts\\python scripts\\probe_open_url.py --list        # hanya daftar jendela Edge (aman)
    .\\venv\\Scripts\\python scripts\\probe_open_url.py               # matriks penuh (minta konfirmasi)
    .\\venv\\Scripts\\python scripts\\probe_open_url.py --yes         # matriks tanpa prompt (setelah izin)
"""

import argparse
import ctypes
import datetime
import json
import os
import subprocess
import sys
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_TXT = REPO_ROOT / "docs" / "evidence" / "tahap6_open_url_matrix.txt"
EVIDENCE_JSON = REPO_ROOT / "docs" / "evidence" / "tahap6_open_url_matrix.json"

TEST_URL = "https://example.com/"
MARKER = "Example Domain"
DETECT_TIMEOUT_S = 8.0
POLL_INTERVAL_S = 0.25
GAP_S = 8.0
WM_CLOSE = 0x0010

user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM) if sys.platform == "win32" else None


# ----------------------------------------------------------------------------- detektor K1
def edge_pids() -> set:
    pids = set()
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower() == "msedge.exe":
                pids.add(p.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return pids


def list_edge_windows() -> List[Dict[str, Any]]:
    """Jendela top-level milik msedge.exe: hwnd, pid, judul, terlihat."""
    pids = edge_pids()
    out: List[Dict[str, Any]] = []
    if not pids:
        return out

    def _cb(hwnd, _lp):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            out.append({
                "hwnd": int(hwnd or 0),
                "pid": int(pid.value),
                "title": buf.value,
                "visible": bool(user32.IsWindowVisible(hwnd)),
            })
        return True

    user32.EnumWindows(_EnumProc(_cb), 0)
    return out


def visible_titled(wins: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [w for w in wins if w["visible"] and w["title"]]


def snapshot() -> Dict[str, Any]:
    wins = list_edge_windows()
    vis = visible_titled(wins)
    return {
        "edge_procs": len(edge_pids()),
        "visible_windows": len(vis),
        "visible_titles": [w["title"] for w in vis],
        "_keys": {(w["hwnd"], w["title"]) for w in vis},
    }


def detect_new_marker(before_keys: set, timeout: float = DETECT_TIMEOUT_S) -> Tuple[bool, Optional[float], Optional[str]]:
    t0 = time.perf_counter()
    while True:
        for w in visible_titled(list_edge_windows()):
            if MARKER in w["title"] and (w["hwnd"], w["title"]) not in before_keys:
                return True, time.perf_counter() - t0, w["title"]
        if time.perf_counter() - t0 >= timeout:
            return False, None, None
        time.sleep(POLL_INTERVAL_S)


# ----------------------------------------------------------------------------- lingkungan
def session_id(pid: int) -> Optional[int]:
    sid = wintypes.DWORD()
    ok = kernel32.ProcessIdToSessionId(wintypes.DWORD(pid), ctypes.byref(sid))
    return int(sid.value) if ok else None


def explorer_sessions() -> List[Optional[int]]:
    res = []
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() == "explorer.exe":
            res.append(session_id(p.pid))
    return res


def find_msedge_path() -> Optional[str]:
    import winreg
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, sub) as k:
                val, _ = winreg.QueryValueEx(k, "")
                if val and os.path.exists(val):
                    return val
        except OSError:
            continue
    return None


def default_https_progid() -> Optional[str]:
    import winreg
    sub = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as k:
            val, _ = winreg.QueryValueEx(k, "ProgId")
            return val
    except OSError:
        return None


def get_git_info() -> Tuple[str, bool]:
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True).strip()
    except Exception as e:
        head = f"ERROR: {e}"
    try:
        st = subprocess.check_output(["git", "status", "--porcelain", "-uno"], cwd=str(REPO_ROOT), text=True).strip()
        lines = [l for l in st.splitlines() if "docs/evidence/tahap6_" not in l.replace("\\", "/")]
        dirty = bool(lines)
    except Exception:
        dirty = True
    return head, dirty


# ----------------------------------------------------------------------------- kontrol Edge
def kill_edge(timeout: float = 10.0) -> int:
    subprocess.run(["taskkill", "/f", "/im", "msedge.exe"], capture_output=True, text=True)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        n = len(edge_pids())
        if n == 0:
            return 0
        time.sleep(0.25)
    return len(edge_pids())


def wait_visible_window(timeout: float = 15.0) -> bool:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if visible_titled(list_edge_windows()):
            return True
        time.sleep(0.25)
    return False


def close_visible_windows_wm_close() -> int:
    n = 0
    for w in visible_titled(list_edge_windows()):
        user32.PostMessageW(wintypes.HWND(w["hwnd"]), WM_CLOSE, 0, 0)
        n += 1
    return n


_KEEP: List[Any] = []  # referensi Popen agar tidak memicu ResourceWarning


def prepare_condition(cond: str, edge_path: str) -> Dict[str, Any]:
    info: Dict[str, Any] = {"cond": cond}
    info["procs_after_kill"] = kill_edge()
    if cond == "A":
        info["setup_ok"] = info["procs_after_kill"] == 0
        return info
    _KEEP.append(subprocess.Popen([edge_path, "about:blank"]))
    info["window_appeared"] = wait_visible_window()
    time.sleep(1.0)
    if cond == "B":
        info["setup_ok"] = info["procs_after_kill"] == 0 and info["window_appeared"]
        return info
    # C
    info["wm_close_sent"] = close_visible_windows_wm_close()
    time.sleep(5.0)
    info["procs_after_close"] = len(edge_pids())
    info["visible_after_close"] = len(visible_titled(list_edge_windows()))
    info["background_running"] = info["procs_after_close"] > 0
    info["setup_ok"] = info["procs_after_kill"] == 0 and info["window_appeared"] and info["visible_after_close"] == 0
    return info


# ----------------------------------------------------------------------------- metode K2
def build_methods(edge_path: str) -> List[Tuple[str, Callable[[str], Any]]]:
    no_win = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return [
        ("startfile", lambda u: os.startfile(u)),
        ("webbrowser", lambda u: webbrowser.open(u)),
        ("explorer", lambda u: _KEEP.append(subprocess.Popen(["explorer.exe", u]))),
        ("cmd_start", lambda u: _KEEP.append(subprocess.Popen(["cmd", "/c", "start", "", u], creationflags=no_win))),
        ("powershell", lambda u: _KEEP.append(subprocess.Popen(["powershell", "-NoProfile", "-Command", "Start-Process", u]))),
        ("msedge_direct", lambda u: _KEEP.append(subprocess.Popen([edge_path, u]))),
        ("msedge_newtab", lambda u: _KEEP.append(subprocess.Popen([edge_path, "--new-tab", u]))),
    ]


def run_trial(cond: str, method: str, fn: Callable[[str], Any], edge_path: str) -> Dict[str, Any]:
    rec: Dict[str, Any] = {"cond": cond, "method": method, "ts": datetime.datetime.now().isoformat()}
    rec["setup"] = prepare_condition(cond, edge_path)
    before = snapshot()
    rec["before"] = {k: v for k, v in before.items() if k != "_keys"}
    rec["pre_contaminated"] = any(MARKER in t for t in before["visible_titles"])
    rec["launch_error"] = None
    rec["launch_return"] = None
    try:
        r = fn(TEST_URL)
        rec["launch_return"] = r if isinstance(r, (bool, int, str)) or r is None else str(r)
    except Exception as e:
        rec["launch_error"] = f"{type(e).__name__}: {e}"
    ok, latency, title = detect_new_marker(before["_keys"])
    rec["success"] = ok
    rec["latency_s"] = round(latency, 3) if latency is not None else None
    rec["detected_title"] = title
    after = snapshot()
    rec["after"] = {k: v for k, v in after.items() if k != "_keys"}
    return rec


class Tee:
    def __init__(self, path: Path):
        self.term = sys.stdout
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "w", encoding="utf-8")

    def write(self, s):
        self.term.write(s)
        self.term.flush()
        if not self.f.closed:
            self.f.write(s)
            self.f.flush()

    def flush(self):
        self.term.flush()
        if not self.f.closed:
            self.f.flush()

    def close(self):
        sys.stdout = self.term
        self.f.close()


def confirm_or_exit(assume_yes: bool) -> None:
    print("!" * 75)
    print("PERINGATAN: script ini akan MEMATIKAN Microsoft Edge (taskkill /f /im msedge.exe)")
    print("berulang kali dan membuka jendela Edge di layar. Tab yang belum disimpan bisa hilang.")
    print("!" * 75)
    if assume_yes:
        print("[--yes] Konfirmasi diberikan lewat argumen baris perintah.")
        return
    ans = input("Ketik 'ya' untuk melanjutkan: ").strip().lower()
    if ans != "ya":
        print("Dibatalkan.")
        sys.exit(1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--list", action="store_true", help="Hanya cetak jendela Edge saat ini (tanpa taskkill).")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--conds", default="A,B,C")
    ap.add_argument("--methods", default="")
    args = ap.parse_args()

    if sys.platform != "win32":
        print("Hanya untuk Windows.")
        return

    if args.list:
        snap = snapshot()
        print(json.dumps({k: v for k, v in snap.items() if k != "_keys"}, ensure_ascii=False, indent=2))
        print("session harness:", session_id(os.getpid()), "| session explorer:", explorer_sessions())
        return

    edge_path = find_msedge_path()
    if not edge_path:
        print("msedge.exe tidak ditemukan di App Paths.")
        sys.exit(2)

    confirm_or_exit(args.yes)
    tee = Tee(EVIDENCE_TXT)
    sys.stdout = tee

    head, dirty = get_git_info()
    conds = [c.strip() for c in args.conds.split(",") if c.strip()]
    methods = build_methods(edge_path)
    if args.methods:
        wanted = {m.strip() for m in args.methods.split(",")}
        methods = [m for m in methods if m[0] in wanted]

    env = {
        "time": datetime.datetime.now().isoformat(),
        "git_head": head,
        "git_dirty": dirty,
        "python": sys.executable,
        "msedge_path": edge_path,
        "default_https_progid": default_https_progid(),
        "harness_session_id": session_id(os.getpid()),
        "explorer_session_ids": explorer_sessions(),
        "n": args.n,
        "conds": conds,
        "methods": [m[0] for m in methods],
        "detect_timeout_s": DETECT_TIMEOUT_S,
        "gap_s": GAP_S,
        "url": TEST_URL,
    }
    print("=" * 75)
    print("TAHAP 6-FIX-3: MATRIKS KONDISI EDGE x METODE (deteksi jendela otomatis K1)")
    print("=" * 75)
    for k, v in env.items():
        print(f"{k:22}: {v}")
    print("-" * 75)

    records: List[Dict[str, Any]] = []
    for cond in conds:
        for mname, fn in methods:
            for i in range(1, args.n + 1):
                rec = run_trial(cond, mname, fn, edge_path)
                rec["trial"] = i
                records.append(rec)
                s = rec["setup"]
                extra = ""
                if cond == "C":
                    extra = f" bg_procs_after_close={s.get('procs_after_close')} vis_after_close={s.get('visible_after_close')}"
                print(
                    f"[{cond}] {mname:14} #{i} success={rec['success']!s:5} latency={rec['latency_s']} "
                    f"setup_ok={s.get('setup_ok')} pre_contam={rec['pre_contaminated']} "
                    f"procs {rec['before']['edge_procs']}->{rec['after']['edge_procs']} "
                    f"vis {rec['before']['visible_windows']}->{rec['after']['visible_windows']} "
                    f"err={rec['launch_error']} ret={rec['launch_return']}{extra} "
                    f"title={rec['detected_title']!r}"
                )
                time.sleep(GAP_S)

    kill_edge()

    # tabel
    print("\n" + "=" * 75)
    print("TABEL HASIL (sukses/N)  [setup_gagal, pre_contaminated dalam kurung jika > 0]")
    print("=" * 75)
    header = f"{'metode':15}" + "".join(f"{c:>12}" for c in conds) + f"{'total':>10}"
    print(header)
    table: Dict[str, Dict[str, Any]] = {}
    for mname, _ in methods:
        row = f"{mname:15}"
        tot_s = tot_n = 0
        table[mname] = {}
        for cond in conds:
            rs = [r for r in records if r["method"] == mname and r["cond"] == cond]
            s = sum(1 for r in rs if r["success"])
            bad_setup = sum(1 for r in rs if not r["setup"].get("setup_ok"))
            contam = sum(1 for r in rs if r["pre_contaminated"])
            tot_s += s
            tot_n += len(rs)
            table[mname][cond] = {"success": s, "n": len(rs), "setup_fail": bad_setup, "pre_contaminated": contam}
            cell = f"{s}/{len(rs)}"
            if bad_setup or contam:
                cell += f"({bad_setup},{contam})"
            row += f"{cell:>12}"
        table[mname]["total"] = {"success": tot_s, "n": tot_n}
        row += f"{f'{tot_s}/{tot_n}':>10}"
        print(row)

    if "C" in conds:
        cr = [r for r in records if r["cond"] == "C"]
        bg = sum(1 for r in cr if r["setup"].get("background_running"))
        print(f"\nKondisi C: msedge.exe masih berjalan di latar setelah WM_CLOSE + 5 s pada {bg}/{len(cr)} percobaan.")

    reliable: List[str] = []
    if {"A", "B", "C"} <= set(conds):
        for m, _ in methods:
            if all(table[m][c]["success"] == table[m][c]["n"] == args.n for c in ("A", "B", "C")):
                reliable.append(m)
    print(f"Metode andal (5/5 di A, B, C) menurut K4: {reliable if reliable else 'TIDAK ADA'}")

    with open(EVIDENCE_JSON, "w", encoding="utf-8") as f:
        json.dump({"env": env, "table": table, "reliable": reliable, "records": records}, f, ensure_ascii=False, indent=1)
    print(f"JSON: {EVIDENCE_JSON.relative_to(REPO_ROOT)}")
    tee.close()


if __name__ == "__main__":
    main()
