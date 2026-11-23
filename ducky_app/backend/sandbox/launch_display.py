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
last_error = ""  # why the last start gave no display (Xvfb's exit code and stderr)


def _start(xvfb: str, timeout_s: float = 10.0) -> str:
    global _proc, _display, last_error
    import tempfile

    log = tempfile.TemporaryFile()
    r, w = os.pipe()
    try:
        _proc = subprocess.Popen([xvfb, "-displayfd", str(w), "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                 pass_fds=(w,), start_new_session=True)
    except OSError as exc:
        os.close(r)
        last_error = f"Xvfb did not start: {exc}"
        return ""
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
        code = None
        try:
            code = _proc.wait(timeout=1) if _proc is not None else None  # EOF: it is exiting
        except subprocess.TimeoutExpired:
            pass
        stop()
        log.seek(0)
        tail = log.read()[-600:].decode("utf-8", "replace").strip()
        last_error = f"Xvfb gave no display (exit {code}, wrote {buf!r}): {tail}"
        return ""
    log.close()  # a running Xvfb keeps writing to its own copy; nothing reads it
    last_error = ""
    _display = f":{num.decode()}"
    return _display


def _find_xvfb() -> str:
    """Xvfb on this process's PATH, else on the system's default PATH (a desktop session or a
    caller may have a PATH without /usr/bin)."""
    return shutil.which("Xvfb") or shutil.which("Xvfb", path=os.pathsep.join(("/usr/bin", "/bin", "/usr/local/bin"))) or ""


def hidden_display() -> str:
    """``:N`` of the shared Xvfb (started now if needed), or '' (``last_error`` says why)."""
    global last_error
    with _lock:
        if _proc is not None and _proc.poll() is None and _display:
            return _display
        xvfb = _find_xvfb()
        if not xvfb:
            last_error = "Xvfb is not installed (apt install xvfb): GUI apps get no display."
            return ""
        return _start(xvfb)


_SESSION_DISPLAY = ("DISPLAY", "WAYLAND_DISPLAY", "WAYLAND_SOCKET", "XAUTHORITY")


def hidden_env(env: dict[str, str]) -> dict[str, str]:
    """``env`` with the person's own display taken out and the hidden Xvfb put in. Without a
    working Xvfb the child gets no display at all: a GUI app fails headless (``last_error``
    says why) instead of opening on the person's screen."""
    out = {k: v for k, v in env.items() if k not in _SESSION_DISPLAY}
    display = hidden_display()
    if display:
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
