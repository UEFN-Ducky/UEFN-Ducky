"""Keep Ducky's window usable while the PC is busy (UEFN, a game, a test run).

Nov 17 2026: 5,306 window freezes in 5 hours, 10 minutes frozen in all, and Ducky's own
scripts ran for 10 seconds of that. The window was waiting on the PC (graphics memory 96%
full, UEFN on 2.5 cores, 81% of RAM in use) and nothing recorded which of the three it was.

- A freeze of FREEZE_MS or more is recorded with the PC's last 30 s sample (``pc_now``).
- The window's processes run at Above Normal priority, so UEFN and background jobs at
  Normal can't push them aside.
- How full the graphics card is goes into ``launcher/gpu.json``. A start within
  NOTE_MAX_AGE_S of a full card draws the window without the card: Ducky-Launcher's
  ``gpu_was_full`` (native_launcher/app/src/lib.rs) and ``browser_arguments`` here.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

FREEZE_MS = 200.0
NOTE_MAX_AGE_S = 600.0
SOFTWARE_ARG = "--disable-gpu"
# Auto-pick a screen for getDisplayMedia (substring matches Entire screen / Screen 1). Raw LAN
# host candidates (no mDNS obfuscation) so a phone on the same Wi-Fi connects direct.
BROWSER_ARGS = "--auto-select-desktop-capture-source=creen --disable-features=WebRtcHideLocalIpsWithMdns"
_WINDOW_EXES = ("msedgewebview2.exe", "ducky-launcher.exe")
_NORMAL, _ABOVE_NORMAL = 0x20, 0x8000

# {gpu: share of the card's memory in use, free_gb, cpu: percent of all cores}, 30 s old at most.
pc_now: dict[str, float] = {}
# Drawing without the card: samples in a row with room on it, and whether the person was told.
ROOM_SHARE, ROOM_BEATS = 0.80, 2
_room = {"beats": 0, "told": False}


def gpu_back(share: float, alert: Any = None) -> bool:
    """One notice per session when this window was started without the card and the card has had
    room for ROOM_BEATS samples: only a new start draws with it again. True when it told."""
    if _room["told"] or not drawing_without_gpu():
        return False
    _room["beats"] = _room["beats"] + 1 if share < ROOM_SHARE else 0
    if _room["beats"] < ROOM_BEATS:
        return False
    _room["told"] = True
    if alert is None:
        from frontend.notifications import alert
    alert("UEFN Ducky", "Drawing without the graphics card. Restart Ducky to use it again.", level="info",
          detail=("The card was full when Ducky started, so its window (3D panels too) is drawn on the CPU. "
                  f"It is {share * 100:.0f}% full now: close and reopen Ducky (not UEFN) for smooth 3D."))
    return True


def note_path() -> Path:
    from frontend.native_switch import launcher_dir

    return launcher_dir() / "gpu.json"


def gpu_was_full(now: float | None = None) -> bool:
    """The last note is under NOTE_MAX_AGE_S old and says the card was full."""
    try:
        from frontend.gpu_memory import FULL_SHARE

        note = json.loads(note_path().read_text(encoding="utf-8"))
        age = (time.time() if now is None else now) - float(note["at"])
        return float(note["share"]) >= FULL_SHARE and -60.0 <= age < NOTE_MAX_AGE_S
    except (OSError, ValueError, KeyError, TypeError):
        return False


def browser_arguments() -> str:
    """WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS for the Python window."""
    return f"{BROWSER_ARGS} {SOFTWARE_ARG}" if gpu_was_full() else BROWSER_ARGS


def drawing_without_gpu() -> bool:
    return SOFTWARE_ARG in os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")


def without_software(env: dict[str, str]) -> dict[str, str]:
    """``env`` (changed in place) with SOFTWARE_ARG taken out of the WebView2 arguments; the
    variable goes when nothing else is left, so the next start decides afresh."""
    key = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
    if key in env:
        rest = " ".join(a for a in env[key].split() if a != SOFTWARE_ARG)
        if rest:
            env[key] = rest
        else:
            env.pop(key)
    return env


def window_pids(procs: dict[int, Any], me: int, launcher: int = 0) -> list[int]:
    """The processes that draw Ducky's window: the launcher (or this process, the Python
    window) and the WebView2 processes under it. Never agents, terminals or tool servers."""
    from backend.util import proc_table

    root = launcher or me
    return [pid for pid in proc_table.tree(procs, root)
            if pid == root or str(procs[pid].name).lower() in _WINDOW_EXES]


def _kernel32() -> Any:
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.GetPriorityClass.argtypes = k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    return k32


def raise_window_priority() -> int:
    """Above Normal for the window's processes still at Normal; how many were raised. WebView2
    puts a hidden page's process at Idle on purpose: those are left alone."""
    if os.name != "nt":
        return 0
    from backend.util import proc_table
    from frontend.app_versions import backend_mode

    k32 = _kernel32()
    raised = 0
    from frontend.frozen_process import LAUNCHER_EXE

    procs = proc_table.table()
    launcher = proc_table.ancestor(procs, os.getpid(), LAUNCHER_EXE) if backend_mode() else None
    for pid in window_pids(procs, os.getpid(), launcher.pid if launcher else 0):
        handle = k32.OpenProcess(0x0200 | 0x1000, False, int(pid))  # SET_INFORMATION | QUERY_LIMITED_INFORMATION
        if not handle:
            continue
        try:
            if k32.GetPriorityClass(handle) == _NORMAL and k32.SetPriorityClass(handle, _ABOVE_NORMAL):
                raised += 1
        finally:
            k32.CloseHandle(handle)
    return raised


def on_heartbeat(beat: dict[str, Any]) -> None:
    """boot_report's 30 s sample, after low_memory's (it reads the graphics card). Never raises."""
    try:
        free = beat.get("free_gb")
        if isinstance(free, (int, float)) and not isinstance(free, bool):
            pc_now["free_gb"] = float(free)
        try:
            import psutil

            pc_now["cpu"] = float(psutil.cpu_percent(None))
        except Exception:
            pass
        from frontend import low_memory

        share = low_memory.last_gpu_share
        if share is not None:
            pc_now["gpu"] = float(share)
            gpu_back(float(share))
            path = note_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"share": float(share), "at": time.time()}), encoding="utf-8")
        raise_window_priority()
    except Exception:
        pass


def freeze_meta(kind: str, duration_ms: float) -> dict[str, float]:
    """What a window freeze is recorded with: ``pc_gpu``, ``pc_free_gb``, ``pc_cpu``."""
    if kind != "ui_stall" or duration_ms < FREEZE_MS:
        return {}
    return {f"pc_{k}": v for k, v in pc_now.items()}


def freeze_cause(rows: list[dict[str, Any]]) -> str:
    """'During them the graphics card was 96% full, the CPU 36% busy and 12.2 GB of memory was
    free.' from the freezes' recorded samples (their medians); '' when none has one."""
    def mid(key: str) -> float | None:
        vals = sorted(float(r[key]) for r in rows if isinstance(r.get(key), (int, float)))
        return vals[len(vals) // 2] if vals else None

    gpu, cpu, free = mid("pc_gpu"), mid("pc_cpu"), mid("pc_free_gb")
    parts = [f"the graphics card was {gpu * 100:.0f}% full" if gpu is not None else "",
             f"the CPU {cpu:.0f}% busy" if cpu is not None else "",
             f"{free:.1f} GB of memory was free" if free is not None else ""]
    parts = [p for p in parts if p]
    return "During them " + ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] + "." if parts else ""
