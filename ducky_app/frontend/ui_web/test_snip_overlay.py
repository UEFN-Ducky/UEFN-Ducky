"""Self-check: Win+Shift+S snip path encodes clipboard images and reports busy."""

from __future__ import annotations

import base64
import io
from unittest.mock import patch


def test_png_result_roundtrip() -> None:
    from PIL import Image

    from frontend.ui_web.snip_overlay import _png_result

    img = Image.new("RGB", (12, 8), color=(10, 20, 30))
    result = _png_result(img)
    assert result["ok"] is True
    assert result["width"] == 12
    assert result["height"] == 8
    raw = base64.b64decode(result["data_base64"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert Image.open(io.BytesIO(raw)).size == (12, 8)


def test_busy_lock() -> None:
    from frontend.ui_web import snip_overlay as so

    assert so._session_lock.acquire(blocking=False)
    try:
        out = so.snip_screen_interactive(None)
        assert out == {"ok": False, "reason": "busy"}
    finally:
        so._session_lock.release()


def test_sendinput_failure() -> None:
    from frontend.ui_web import snip_overlay as so

    with (
        patch.object(so, "_clear_clipboard"),
        patch.object(so, "invoke_win_shift_s", return_value=False),
    ):
        out = so._run_win_shift_s()
    assert out["ok"] is False
    assert out["reason"] == "error"


def test_clipboard_accept() -> None:
    from PIL import Image

    from frontend.ui_web import snip_overlay as so

    img = Image.new("RGB", (4, 4), color=(1, 2, 3))
    with (
        patch.object(so, "_clear_clipboard"),
        patch.object(so, "invoke_win_shift_s", return_value=True),
        patch.object(so, "_grab_clipboard_image", return_value=img),
    ):
        out = so._run_win_shift_s()
    assert out["ok"] is True
    assert out["width"] == 4


# --- Linux ---------------------------------------------------------------

import os
import sys
import threading
import time

import pytest


def test_norm_rect() -> None:
    from frontend.ui_web.snip_overlay import _norm_rect

    assert _norm_rect((60, 40), (10, 10)) == (10, 10, 60, 40)
    assert _norm_rect((5, 5), (6, 50)) is None  # a click, not a drag


def test_linux_dispatch_by_session() -> None:
    from frontend.ui_web import snip_overlay as so

    with (
        patch.object(so.platform_info, "IS_LINUX", True),
        patch.object(so, "_portal_snip", return_value={"ok": True, "via": "portal"}),
        patch.object(so, "_x11_snip", return_value={"ok": True, "via": "x11"}),
    ):
        with patch.object(so.platform_info, "session_type", return_value="wayland"):
            assert so.snip_screen_interactive()["via"] == "portal"
        with patch.object(so.platform_info, "session_type", return_value="x11"):
            assert so.snip_screen_interactive()["via"] == "x11"
        with patch.object(so.platform_info, "session_type", return_value=""):
            assert so.snip_screen_interactive()["reason"] == "unsupported"
        with (
            patch.object(so.platform_info, "session_type", return_value="x11"),
            patch.object(so, "_x11_snip", side_effect=OSError("no display")),
        ):
            out = so.snip_screen_interactive()
            assert out["reason"] == "error" and "no display" in out["error"]
    assert so._session_lock.acquire(blocking=False)  # released after every path
    so._session_lock.release()


def test_portal_options_interactive() -> None:
    from frontend.ui_web.snip_overlay import _portal_options

    assert _portal_options("tok") == {"interactive": ("b", True), "handle_token": ("s", "tok")}


def test_portal_request_over_jeepney(monkeypatch) -> None:
    """Fake session bus: the Screenshot call goes to the portal, the Response signal comes back
    on the matching request path, and its variants arrive as plain values."""
    import types

    from frontend.ui_web import snip_overlay as so

    sent: list = []

    class _Conn:
        unique_name = ":1.42"

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def filter(self, rule):
            sent.append(("match", rule.path))
            return self

        def send_and_get_reply(self, msg):
            sent.append(("call", msg))
            return types.SimpleNamespace(header=types.SimpleNamespace(message_type=types.SimpleNamespace(name="method_return")))

        def recv_until_filtered(self, _queue, timeout):
            return types.SimpleNamespace(body=(0, {"uri": ("s", "file:///tmp/s.png")}))

    jeepney = types.ModuleType("jeepney")
    jeepney.DBusAddress = lambda path, bus_name, interface: (path, bus_name, interface)
    jeepney.MatchRule = lambda **kw: types.SimpleNamespace(**kw)
    jeepney.message_bus = object()
    jeepney.new_method_call = lambda addr, method, sig, body: (addr, method, sig, body)
    blocking = types.ModuleType("jeepney.io.blocking")
    blocking.open_dbus_connection = lambda bus: _Conn()
    blocking.Proxy = lambda _bus, _conn: types.SimpleNamespace(AddMatch=lambda rule: sent.append(("add", rule.path)))
    monkeypatch.setitem(sys.modules, "jeepney", jeepney)
    monkeypatch.setitem(sys.modules, "jeepney.io", types.ModuleType("jeepney.io"))
    monkeypatch.setitem(sys.modules, "jeepney.io.blocking", blocking)

    code, results = so._portal_request(timeout_s=1)
    assert (code, results) == (0, {"uri": "file:///tmp/s.png"})
    path = sent[0][1]
    assert path.startswith("/org/freedesktop/portal/desktop/request/1_42/ducky_snip_") and sent[1] == ("match", path)
    (addr, method, sig, (parent, opts)) = sent[2][1]
    assert addr[2] == "org.freedesktop.portal.Screenshot" and (method, sig, parent) == ("Screenshot", "sa{sv}", "")
    assert opts["interactive"] == ("b", True) and path.endswith(opts["handle_token"][1])


class _Variant:
    def __init__(self, value: str) -> None:
        self.value = value


def test_portal_snip_mocked(tmp_path) -> None:
    from PIL import Image

    from frontend.ui_web import snip_overlay as so

    with patch.object(so, "_portal_request", return_value=(1, {})):
        assert so._portal_snip() == {"ok": False, "reason": "cancelled"}
    with patch.object(so, "_portal_request", return_value=(2, {})):
        assert so._portal_snip()["reason"] == "error"
    with patch.object(so, "_portal_request", side_effect=TimeoutError):
        assert so._portal_snip()["reason"] == "cancelled"
    if sys.platform == "win32":
        return  # a file:///C:/ URI is not a portal path; the success read is Linux-only

    shot = tmp_path / "Screenshot from now.png"  # the space arrives %20-encoded
    Image.new("RGBA", (7, 5), color=(9, 9, 9, 255)).save(shot)
    with patch.object(so, "_portal_request", return_value=(0, {"uri": _Variant(shot.as_uri())})):
        out = so._portal_snip()
    assert out["ok"] is True and (out["width"], out["height"]) == (7, 5)


def _x11_ready() -> bool:
    import shutil

    if not sys.platform.startswith("linux") or not shutil.which("Xvfb"):
        return False
    try:
        import mss  # noqa: F401
        import Xlib  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def own_display(monkeypatch):
    """A private Xvfb for this test: xdist workers sharing one display steal each other's grabs."""
    import subprocess

    read_fd, write_fd = os.pipe()
    server = subprocess.Popen(
        ["Xvfb", "-displayfd", str(write_fd), "-screen", "0", "320x240x24", "-nolisten", "tcp"],
        pass_fds=(write_fd,), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    os.close(write_fd)
    number = b""
    while not number.endswith(b"\n"):
        chunk = os.read(read_fd, 16)
        if not chunk:
            break
        number += chunk
    os.close(read_fd)
    if not number.strip():
        server.kill()
        pytest.fail("Xvfb did not start")
    monkeypatch.setenv("DISPLAY", ":" + number.decode().strip())
    yield
    server.terminate()
    server.wait(5)


def _drive_x11(actions) -> dict:
    """Run _x11_snip in a thread; once its overlay holds the pointer grab, play XTest input."""
    from Xlib import X
    from Xlib import display as xdisplay
    from Xlib.ext import xtest

    from frontend.ui_web import snip_overlay as so

    so.xlib_without_select_limit()
    out: dict = {}
    errors: list = []

    def run() -> None:
        try:
            out.update(so._x11_snip(timeout_s=15))
        except Exception as exc:  # surface it in the test, not as a thread warning
            errors.append(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    d = xdisplay.Display()
    root = d.screen().root
    for _ in range(100):  # AlreadyGrabbed == the overlay is up and grabbing
        if errors:
            raise errors[0]
        status = root.grab_pointer(False, 0, X.GrabModeAsync, X.GrabModeAsync, X.NONE, X.NONE, X.CurrentTime)
        if status == X.AlreadyGrabbed:
            break
        d.ungrab_pointer(X.CurrentTime)
        d.sync()
        time.sleep(0.05)
    else:
        pytest.fail("snip overlay never grabbed the pointer")
    for kind, arg in actions:
        if kind == "move":
            xtest.fake_input(d, X.MotionNotify, x=arg[0], y=arg[1])
        elif kind == "key":
            xtest.fake_input(d, X.KeyPress, d.keysym_to_keycode(arg))
            xtest.fake_input(d, X.KeyRelease, d.keysym_to_keycode(arg))
        else:
            xtest.fake_input(d, X.ButtonPress if kind == "down" else X.ButtonRelease, arg)
        d.sync()
        time.sleep(0.05)
    t.join(10)
    d.close()
    assert not t.is_alive()
    if errors:
        raise errors[0]
    return out


_NEEDS_X = pytest.mark.skipif(not _x11_ready(), reason="needs Linux, Xvfb, mss and python-xlib")


@_NEEDS_X
def test_x11_snip_drag_under_xvfb(own_display) -> None:
    out = _drive_x11([("move", (10, 10)), ("down", 1), ("move", (40, 30)), ("move", (60, 40)), ("up", 1)])
    assert out["ok"] is True, out
    assert (out["width"], out["height"]) == (50, 30)
    raw = base64.b64decode(out["data_base64"])
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"


@_NEEDS_X
def test_x11_snip_works_with_its_display_fd_above_1024(own_display) -> None:
    # A long-running app (or an xdist worker) holds many fds; select() fails past 1024.
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < 1200:
        if hard != resource.RLIM_INFINITY and hard < 1200:
            pytest.skip("fd limit too low to open 1100 files")
        resource.setrlimit(resource.RLIMIT_NOFILE, (4096 if hard == resource.RLIM_INFINITY else min(hard, 4096), hard))
    held = [os.open(os.devnull, os.O_RDONLY) for _ in range(1100)]
    try:
        out = _drive_x11([("move", (10, 10)), ("down", 1), ("move", (30, 30)), ("up", 1)])
    finally:
        for fd in held:
            os.close(fd)
    assert out["ok"] is True and (out["width"], out["height"]) == (20, 20), out


def test_poll_select_reports_ready_fds_like_select() -> None:
    if not hasattr(__import__("select"), "poll"):
        pytest.skip("no poll() on this platform")
    from frontend.ui_web.snip_overlay import _PollSelect

    read_fd, write_fd = os.pipe()
    try:
        assert _PollSelect.select([read_fd], [write_fd], [], 0) == ([], [write_fd], [])
        os.write(write_fd, b"x")
        assert _PollSelect.select([read_fd], [], [], 1.0) == ([read_fd], [], [])
    finally:
        os.close(read_fd)
        os.close(write_fd)


@_NEEDS_X
def test_x11_snip_escape_cancels_under_xvfb(own_display) -> None:
    from Xlib import XK

    out = _drive_x11([("move", (10, 10)), ("key", XK.XK_Escape)])
    assert out == {"ok": False, "reason": "cancelled"}


if __name__ == "__main__":
    test_png_result_roundtrip()
    test_busy_lock()
    test_sendinput_failure()
    test_clipboard_accept()
    test_norm_rect()
    test_linux_dispatch_by_session()
    print("test_snip_overlay: ok")
