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
    .\\venv\\Scripts\\python scripts\\probe_open_url.py --visibility-list  # snapshot M1, tanpa membuka browser
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
import uuid
import webbrowser
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import psutil

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_TXT = REPO_ROOT / "docs" / "evidence" / "tahap6_open_url_matrix.txt"
EVIDENCE_JSON = REPO_ROOT / "docs" / "evidence" / "tahap6_open_url_matrix.json"
VISIBILITY_EVIDENCE = REPO_ROOT / "docs" / "evidence" / "tahap6_fix4_visibility.txt"
VISIBILITY_EVIDENCE_JSON = REPO_ROOT / "docs" / "evidence" / "tahap6_fix4_visibility.json"

TEST_URL = "https://example.com/"
MARKER = "Example Domain"
DETECT_TIMEOUT_S = 8.0
POLL_INTERVAL_S = 0.25
GAP_S = 8.0
WM_CLOSE = 0x0010

user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM) if sys.platform == "win32" else None


def _intersects(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    return (
        a[0] < a[2] and a[1] < a[3]
        and b[0] < b[2] and b[1] < b[3]
        and a[0] < b[2] and a[2] > b[0]
        and a[1] < b[3] and a[3] > b[1]
    )


def evaluate_window_visibility(hwnd: int, windows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate user-visible/front metrics from mocked or collected top-level window data."""
    own = next(w for w in windows if w["hwnd"] == hwnd)
    rect = own.get("rect")
    monitor_rects = own.get("monitor_rects") or []
    on_monitor = bool(
        rect
        and rect[0] < rect[2]
        and rect[1] < rect[3]
        and any(
            rect[0] >= monitor[0] and rect[1] >= monitor[1]
            and rect[2] <= monitor[2] and rect[3] <= monitor[3]
            for monitor in monitor_rects
        )
    )
    overlaps = [
        w for w in windows
        if w["hwnd"] != hwnd
        and w.get("visible")
        and not w.get("iconic")
        and w.get("cloaked") is False
        and w.get("rect")
        and rect
        and _intersects(rect, w["rect"])
    ]
    topmost = all(
        w.get("z_order") is not None
        and own.get("z_order") is not None
        and own["z_order"] < w["z_order"]
        for w in overlaps
    )
    visible = bool(
        own.get("visible")
        and not own.get("iconic")
        and own.get("cloaked") is False
        and on_monitor
    )
    foreground = own.get("foreground") is True
    in_front = bool(visible and (foreground or topmost))
    return {
        **own,
        "on_monitor": on_monitor,
        "foreground": foreground,
        "overlapping_hwnds": [w["hwnd"] for w in overlaps],
        "z_order_topmost_intersecting": topmost,
        "visible_according_to_criteria": visible,
        "visible_in_front": in_front,
    }


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", _Rect),
        ("rcWork", _Rect),
        ("dwFlags", wintypes.DWORD),
    ]


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


def _rect_tuple(rect: _Rect) -> Tuple[int, int, int, int]:
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def _monitor_rectangles() -> List[Tuple[int, int, int, int]]:
    if sys.platform != "win32":
        return []
    monitor_enum = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(_Rect), wintypes.LPARAM,
    )
    rects: List[Tuple[int, int, int, int]] = []

    def _cb(monitor, _dc, _rect, _data):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            rects.append(_rect_tuple(info.rcMonitor))
        return True

    user32.EnumDisplayMonitors(None, None, monitor_enum(_cb), 0)
    return rects


def _is_on_current_virtual_desktop(hwnd: int) -> Tuple[Optional[bool], Optional[str]]:
    if sys.platform != "win32":
        return None, "Windows API unavailable"
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    initialized = False
    instance = ctypes.c_void_p()
    try:
        hr = ole32.CoInitializeEx(None, 0x2)
        initialized = hr in (0, 1)
        clsid = _GUID.from_buffer_copy(
            uuid.UUID("AA509086-5CA9-4C25-8F95-589D3C07B48A").bytes_le
        )
        iid = _GUID.from_buffer_copy(
            uuid.UUID("A5CD92FF-29BE-454C-8D04-D82879FB3F1B").bytes_le
        )
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(instance)
        )
        if hr != 0 or not instance.value:
            return None, f"CoCreateInstance HRESULT=0x{hr & 0xFFFFFFFF:08X}"
        vtable = ctypes.cast(
            instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        check = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wintypes.HWND, ctypes.POINTER(wintypes.BOOL)
        )(vtable[3])
        result = wintypes.BOOL()
        hr = check(instance, wintypes.HWND(hwnd), ctypes.byref(result))
        if hr != 0:
            return None, f"IsWindowOnCurrentVirtualDesktop HRESULT=0x{hr & 0xFFFFFFFF:08X}"
        return bool(result.value), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"
    finally:
        if instance.value:
            try:
                vtable = ctypes.cast(
                    instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
                ).contents
                release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
                release(instance)
            except Exception:
                pass
        if initialized:
            ole32.CoUninitialize()


def list_all_windows_with_visibility(browser_names: Tuple[str, ...] = ("msedge.exe",)) -> List[Dict[str, Any]]:
    """Return top-level window visibility and z-order data for selected browser processes."""
    if sys.platform != "win32":
        return []
    browser_pids: Dict[int, str] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if name in browser_names:
                browser_pids[int(proc.info["pid"])] = name
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    monitor_rects = _monitor_rectangles()
    foreground_hwnd = int(user32.GetForegroundWindow() or 0)
    all_rows: List[Dict[str, Any]] = []

    def _cb(hwnd, _lp):
        hwnd_int = int(hwnd or 0)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = _Rect()
        has_rect = bool(user32.GetWindowRect(hwnd, ctypes.byref(rect)))
        title_len = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if title_len:
            title_buf = ctypes.create_unicode_buffer(title_len + 1)
            user32.GetWindowTextW(hwnd, title_buf, title_len + 1)
            title = title_buf.value
        cloaked_value = wintypes.DWORD()
        try:
            hr = ctypes.WinDLL("dwmapi", use_last_error=True).DwmGetWindowAttribute(
                hwnd, 14, ctypes.byref(cloaked_value), ctypes.sizeof(cloaked_value)
            )
            cloaked: Optional[bool] = bool(cloaked_value.value) if hr == 0 else None
            cloaked_error = None if hr == 0 else f"HRESULT=0x{hr & 0xFFFFFFFF:08X}"
        except Exception as exc:
            cloaked = None
            cloaked_error = f"{type(exc).__name__}: {exc}"
        row = {
            "hwnd": hwnd_int,
            "pid": int(pid.value),
            "process": browser_pids.get(int(pid.value)),
            "title": title,
            "visible": bool(user32.IsWindowVisible(hwnd)),
            "iconic": bool(user32.IsIconic(hwnd)),
            "cloaked": cloaked,
            "cloaked_error": cloaked_error,
            "rect": _rect_tuple(rect) if has_rect else None,
            "monitor_rects": monitor_rects,
            "foreground": hwnd_int == foreground_hwnd,
            "current_virtual_desktop": None,
            "virtual_desktop_error": None,
        }
        if row["process"] is not None:
            row["current_virtual_desktop"], row["virtual_desktop_error"] = (
                _is_on_current_virtual_desktop(hwnd_int)
            )
        all_rows.append(row)
        return True

    user32.EnumWindows(_EnumProc(_cb), 0)

    hwnd = user32.GetTopWindow(None)
    z_order = 0
    while hwnd:
        hwnd_int = int(hwnd)
        for row in all_rows:
            if row["hwnd"] == hwnd_int:
                row["z_order"] = z_order
        hwnd = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT
        z_order += 1

    browser_rows = [row for row in all_rows if row["process"] is not None]
    for row in browser_rows:
        row.update(evaluate_window_visibility(row["hwnd"], all_rows))
    return browser_rows


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


def find_brave_path() -> Optional[str]:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"))
        / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files"))
        / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
    ]
    if sys.platform == "win32":
        import winreg
        sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\brave.exe"
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    value, _ = winreg.QueryValueEx(key, "")
                    if value and os.path.exists(value):
                        return value
            except OSError:
                continue
    return next((str(path) for path in candidates if path.is_file()), None)


def default_https_progid() -> Optional[str]:
    import winreg
    sub = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as k:
            val, _ = winreg.QueryValueEx(k, "ProgId")
            return val
    except OSError:
        return None


def edge_profile_inventory() -> Dict[str, Any]:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return {"available": False, "reason": "LOCALAPPDATA tidak tersedia"}
    local_state = Path(local_app_data) / "Microsoft" / "Edge" / "User Data" / "Local State"
    if not local_state.is_file():
        return {"available": False, "reason": "Local State tidak ditemukan"}
    try:
        with local_state.open("r", encoding="utf-8") as state_file:
            state = json.load(state_file)
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    profile_state = state.get("profile", {})
    active = profile_state.get("last_used")
    info_cache = profile_state.get("info_cache", {})
    profiles = [
        {
            "directory": directory,
            "display_name": details.get("name"),
            "active_last_used": directory == active,
        }
        for directory, details in sorted(info_cache.items())
    ]
    return {
        "available": True,
        "last_used": active,
        "profiles": profiles,
    }


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


def get_strict_git_info() -> Tuple[str, bool, List[str]]:
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT), text=True
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1"], cwd=str(REPO_ROOT), text=True
        )
        entries = status.splitlines()
        return head, bool(entries), entries
    except Exception as exc:
        return f"ERROR: {exc}", True, [f"ERROR: {type(exc).__name__}: {exc}"]


def capture_visibility_report() -> Dict[str, Any]:
    head, dirty, status_entries = get_strict_git_info()
    windows = list_all_windows_with_visibility(("msedge.exe", "brave.exe"))
    edge_top_level_windows = [
        window for window in windows if window["process"] == "msedge.exe"
    ]
    edge_minimize_targets = [
        window for window in edge_top_level_windows
        if window["visible"] or window["iconic"]
        if window["title"] not in ("Default IME", "MSCTFIME UI")
    ]
    edge_visible_windows = [
        window for window in edge_minimize_targets
        if window["visible"] and not window["iconic"]
    ]
    foreground_hwnd = int(user32.GetForegroundWindow() or 0) if sys.platform == "win32" else 0
    foreground_pid = wintypes.DWORD()
    foreground_maximized: Optional[bool] = None
    foreground_process = None
    if foreground_hwnd:
        user32.GetWindowThreadProcessId(
            wintypes.HWND(foreground_hwnd), ctypes.byref(foreground_pid)
        )
        foreground_maximized = bool(user32.IsZoomed(wintypes.HWND(foreground_hwnd)))
        try:
            foreground_process = psutil.Process(foreground_pid.value).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            foreground_process = "unavailable"
    for window in windows:
        title = window.get("title") or ""
        if title and title not in ("Default IME", "MSCTFIME UI") and "Example Domain" not in title:
            window["title"] = "[unrelated browser title redacted]"
        window.pop("monitor_rects", None)
    browser_paths = {"edge": find_msedge_path(), "brave": find_brave_path()}

    readiness = {
        "R1": {
            "ready": bool(edge_visible_windows and foreground_process
                          and foreground_process.lower() != "msedge.exe"
                          and foreground_maximized),
            "reason": "Edge visible, non-Edge foreground maximized"
            if edge_visible_windows and foreground_process and foreground_process.lower() != "msedge.exe"
            and foreground_maximized
            else "R1 tidak dikondisikan: perlu Edge visible dan aplikasi non-Edge maximized di foreground",
        },
        "R2": {
            "ready": bool(edge_minimize_targets),
            "reason": "Ada HWND Edge yang dapat diminimalkan untuk pengujian"
            if edge_minimize_targets
            else f"Ada {len(edge_top_level_windows)} HWND Edge, tetapi 0 jendela Edge visible/minimized yang aman ditargetkan",
        },
        "R3": {
            "ready": False,
            "reason": "Tidak diuji: tidak tersedia enumerasi/pembuatan desktop virtual yang aman dengan API publik ini",
        },
        "R4": {
            "ready": False,
            "reason": "Tidak dijalankan: reset A/B/C akan menghentikan Edge dan belum ada izin eksplisit untuk taskkill",
        },
    }
    methods = [
        "launcher_explorer",
        "os_startfile",
        "browser_direct",
        "browser_new_window",
        "browser_new_tab",
        "browser_active_profile",
        "launcher_explorer_focus",
    ]
    matrix = [
        {
            "browser": browser,
            "condition": condition,
            "method": method,
            "n_requested": 5,
            "n_observed": 0,
            "status": "NOT_RUN_PRECONDITION",
            "reason": readiness[condition]["reason"],
            "components": {
                "visible": None,
                "iconic": None,
                "cloaked": None,
                "on_monitor": None,
                "foreground": None,
                "z_order_topmost_intersecting": None,
                "visible_in_front": None,
            },
        }
        for browser in ("edge", "brave")
        if browser_paths[browser] is not None or browser == "edge"
        for condition in ("R1", "R2")
        for method in methods
    ]
    return {
        "time": datetime.datetime.now().isoformat(),
        "git_head": head,
        "git_dirty": dirty,
        "git_status_entries": status_entries,
        "python": sys.executable,
        "harness_session_id": session_id(os.getpid()),
        "explorer_session_ids": explorer_sessions(),
        "default_https_progid": default_https_progid(),
        "edge_process_count": len(edge_pids()),
        "edge_top_level_window_count": len(edge_top_level_windows),
        "edge_minimize_target_count": len(edge_minimize_targets),
        "edge_visible_window_count": len(edge_visible_windows),
        "brave_process_count": count_browser_processes("brave.exe"),
        "edge_profiles": edge_profile_inventory(),
        "browser_available": {
            "edge": browser_paths["edge"] is not None,
            "brave": browser_paths["brave"] is not None,
        },
        "foreground": {
            "pid": foreground_pid.value or None,
            "process": foreground_process,
            "maximized": foreground_maximized,
            "is_browser": foreground_process in ("msedge.exe", "brave.exe"),
        },
        "condition_readiness": readiness,
        "method_order": methods,
        "matrix": matrix,
        "windows": windows,
    }


def count_browser_processes(browser_name: str) -> int:
    count = 0
    for process in psutil.process_iter(["name"]):
        try:
            if (process.info.get("name") or "").lower() == browser_name:
                count += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return count


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
    def __init__(self, path: Path, mode: str = "w"):
        self.term = sys.stdout
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, mode, encoding="utf-8")

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
    ap.add_argument("--resume", action="store_true", help="Lanjutkan dari percobaan yang sudah tercatat di evidence.")
    ap.add_argument("--list", action="store_true", help="Hanya cetak jendela Edge saat ini (tanpa taskkill).")
    ap.add_argument("--visibility-list", action="store_true", help="Catat metrik keterlihatan tanpa meluncurkan browser.")
    ap.add_argument("--visibility-note", default="", help="Catatan run yang dicantumkan di evidence snapshot.")
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
    if args.visibility_list:
        report = capture_visibility_report()
        report["run_note"] = args.visibility_note
        with VISIBILITY_EVIDENCE_JSON.open("w", encoding="utf-8") as evidence_file:
            json.dump(report, evidence_file, ensure_ascii=False, indent=2)
            evidence_file.write("\n")
        header = (
            f"Git HEAD: {report['git_head']}\n"
            f"Git Dirty={report['git_dirty']}\n"
            f"Dijalankan dari tree bersih: {not report['git_dirty']}\n"
            "Run: snapshot diagnostik, bukan resume; script tidak diedit di tengah run.\n"
            f"Catatan: {report['run_note'] or '(tidak ada)'}\n\n"
        )
        with VISIBILITY_EVIDENCE.open("w", encoding="utf-8") as evidence_file:
            evidence_file.write(header)
            json.dump(report, evidence_file, ensure_ascii=False, indent=2)
            evidence_file.write("\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Evidence: {VISIBILITY_EVIDENCE.relative_to(REPO_ROOT)}")
        return

    edge_path = find_msedge_path()
    if not edge_path:
        print("msedge.exe tidak ditemukan di App Paths.")
        sys.exit(2)

    confirm_or_exit(args.yes)

    import re
    line_pat = re.compile(
        r"^\[([ABC])\]\s+(\S+)\s+#(\d+)\s+success=(True|False)\s+latency=(\S+)\s+setup_ok=(True|False)\s+pre_contam=(True|False)"
    )
    existing_keys = set()
    records: List[Dict[str, Any]] = []
    if args.resume and EVIDENCE_TXT.exists():
        with open(EVIDENCE_TXT, "r", encoding="utf-8") as f:
            for line in f:
                m = line_pat.match(line)
                if m:
                    c, mname, tr = m.group(1), m.group(2), int(m.group(3))
                    existing_keys.add((c, mname, tr))
                    lat = float(m.group(5)) if m.group(5) != "None" else None
                    records.append({
                        "cond": c,
                        "method": mname,
                        "trial": tr,
                        "success": m.group(4) == "True",
                        "latency_s": lat,
                        "setup": {
                            "setup_ok": m.group(6) == "True",
                            "background_running": "bg_procs_after_close=" in line and "bg_procs_after_close=0" not in line,
                        },
                        "pre_contaminated": m.group(7) == "True",
                        "detected_title": line.split("title=")[-1].strip() if "title=" in line else None,
                    })
        print(f"[--resume] Dimuat {len(records)} percobaan yang sudah selesai dari {EVIDENCE_TXT.name}.")

    tee = Tee(EVIDENCE_TXT, mode="a" if existing_keys else "w")
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

    if not existing_keys:
        print("=" * 75)
        print("TAHAP 6-FIX-3: MATRIKS KONDISI EDGE x METODE (deteksi jendela otomatis K1)")
        print("=" * 75)
        for k, v in env.items():
            print(f"{k:22}: {v}")
        print("-" * 75)

    for cond in conds:
        for mname, fn in methods:
            for i in range(1, args.n + 1):
                if (cond, mname, i) in existing_keys:
                    continue
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
