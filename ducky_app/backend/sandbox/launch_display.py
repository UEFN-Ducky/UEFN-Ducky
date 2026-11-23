"""Linux: a hidden X display (Xvfb) for sandbox_launch, so a launched GUI app never opens a
window on the person's screen. Windows never calls this (it hides windows its own way).

``hidden_env(env)`` returns ``env`` pointed at one shared Xvfb, started on first use. Xvfb
picks a free display itself (``-displayfd``), so two Duckies never race for ``:N``.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import threading

IS_LINUX = sys.platform.startswith("linux")

_lock = threading.Lock()
_proc: subprocess.Popen | None = None
_display = ""


def _start(xvfb: str, timeout_s: float = 10.0) -> str:
    global _proc, _display
    r, w = os.pipe()
    try:
        _proc = subprocess.Popen([xvfb, "-displayfd", str(w), "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 pass_fds=(w,), start_new_session=True)
    finally:
        os.close(w)
    import select

    buf = b""
    try:
        while b"\n" not in buf:
            ready, _, _ = select.select([r], [], [], timeout_s)
            chunk = os.read(r, 32) if ready else b""
            if not chunk:
                break
            buf += chunk
    finally:
        os.close(r)
    num = buf.strip()
    if not num.isdigit():
        stop()
        return ""
    _display = f":{num.decode()}"
    return _display


def hidden_display() -> str:
    """``:N`` of the shared Xvfb (started now if needed), or '' when Xvfb isn't installed."""
    with _lock:
        if _proc is not None and _proc.poll() is None and _display:
            return _display
        xvfb = shutil.which("Xvfb")
        return _start(xvfb) if xvfb else ""


def hidden_env(env: dict[str, str]) -> dict[str, str]:
    """``env`` on the hidden display (Wayland dropped so toolkits use it). Unchanged when
    Xvfb is missing: the app then shows on the person's desktop, as before."""
    display = hidden_display()
    if not display:
        return env
    out = {k: v for k, v in env.items() if k != "WAYLAND_DISPLAY"}
    out["DISPLAY"] = display
    out["GDK_BACKEND"] = "x11"
    out["QT_QPA_PLATFORM"] = "xcb"
    return out


def stop() -> None:
    global _proc, _display
    proc, _proc, _display = _proc, None, ""
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


atexit.register(stop)
