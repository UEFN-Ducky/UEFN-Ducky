"""Linux capture: region crop, the Wayland ScreenCast portal (mocked: one consent, then the
restore token), and a real X11 grab of a fake EWMH window under Xvfb (Linux CI)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from frontend import native_capture as nc


def test_crop_bgra_inside_and_outside_the_frame() -> None:
    fw, fh = 4, 3
    data = bytes(i % 256 for i in range(fw * fh * 4))
    px = lambda x, y: data[(y * fw + x) * 4:(y * fw + x) * 4 + 4]  # noqa: E731
    assert nc.crop_bgra(data, fw, fh, 1, 1, 2, 2) == px(1, 1) + px(2, 1) + px(1, 2) + px(2, 2)
    edge = nc.crop_bgra(data, fw, fh, -1, 0, 2, 1)  # half off the left: black, then pixel (0, 0)
    assert edge == b"\0\0\0\0" + px(0, 0)
    assert nc.crop_bgra(data, fw, fh, 10, 10, 2, 2) == bytes(16)


class _FakePortal:
    """The ScreenCast portal: a consent dialog unless a valid restore token comes in."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.consents = 0
        self.issued: set[str] = set()

    def request(self, method: str, *args):
        self.calls.append((method, args))
        if method == "CreateSession":
            return {"session_handle": "/org/freedesktop/portal/desktop/session/1_2/s"}
        if method == "SelectSources":
            opts = args[-1]
            assert opts["persist_mode"] == 2 and opts["types"] == 1
            self._restored = opts.get("restore_token") in self.issued
            return {}
        if method == "Start":
            if not self._restored:
                self.consents += 1
            token = f"tok{len(self.issued)}"
            self.issued.add(token)
            return {"restore_token": token, "streams": [(42, {"position": ("(ii)", (100, 50))})]}
        raise AssertionError(method)

    def open_pipewire_remote(self, session: str) -> int:
        return 7


class _FakeStream:
    def __init__(self, fd: int, node: int) -> None:
        assert (fd, node) == (7, 42)

    def frame(self):
        w, h = 200, 100
        return w, h, bytes([1, 2, 3, 255]) * (w * h)


def test_portal_asks_once_then_reuses_the_restore_token(tmp_path) -> None:
    portal = _FakePortal()
    token = tmp_path / "tok"
    first = nc.PortalGrabber(bus=portal, stream_factory=_FakeStream, token_path=token)
    assert portal.consents == 1 and token.read_text() == "tok0"
    assert "restore_token" not in portal.calls[1][1][-1]
    nc.PortalGrabber(bus=portal, stream_factory=_FakeStream, token_path=token)
    assert portal.consents == 1, "the second start must be silent"
    assert portal.calls[4][1][-1]["restore_token"] == "tok0"
    assert token.read_text() == "tok1", "each start's new token replaces the used one"
    # Desktop (110, 60) is frame (10, 10) of the monitor at (100, 50).
    out = first.grab(110, 60, 4, 2)
    assert out == bytes([1, 2, 3, 255]) * 8


def test_portal_grab_into_fills_the_buffer(tmp_path) -> None:
    import ctypes

    g = nc.PortalGrabber(bus=_FakePortal(), stream_factory=_FakeStream, token_path=tmp_path / "t")
    buf = (ctypes.c_char * (6 * 4 * 4))()
    assert g.grab_into(100, 50, 6, 4, ctypes.addressof(buf))
    assert bytes(buf) == bytes([1, 2, 3, 255]) * 24


def test_linux_grabber_picks_the_portal_on_wayland(monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert nc.is_wayland()
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert not nc.is_wayland()


def _xvfb_ready() -> bool:
    if not sys.platform.startswith("linux") or not shutil.which("Xvfb"):
        return False
    try:
        import mss  # noqa: F401
        import Xlib  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.fixture
def xvfb(monkeypatch):
    if not _xvfb_ready():
        pytest.skip("needs Linux with Xvfb, mss and python-xlib")
    disp = f":{90 + os.getpid() % 100}"
    proc = subprocess.Popen(["Xvfb", disp, "-screen", "0", "1600x900x24", "-nolisten", "tcp"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    monkeypatch.setenv("DISPLAY", disp)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    from Xlib import display

    for _ in range(50):
        try:
            d = display.Display(disp)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.skip("Xvfb did not start")
    yield d
    d.close()
    proc.kill()
    proc.wait()


def fake_ewmh_window(d, x: int, y: int, w: int, h: int, *, title: str = "fake", pid: int = 0,
                     transient_for=None, modal: bool = False):
    """A mapped window listed in the root's _NET_CLIENT_LIST, as a window manager would."""
    from Xlib import X, Xatom

    root = d.screen().root
    win = root.create_window(x, y, w, h, 0, d.screen().root_depth, X.InputOutput, X.CopyFromParent,
                             background_pixel=d.screen().white_pixel, override_redirect=True)
    atom = d.intern_atom
    win.change_property(atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [pid or os.getpid()])
    win.change_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"), 8, title.encode())
    win.set_wm_class("fake", "FakeApp")
    if transient_for is not None:
        win.set_wm_transient_for(transient_for)
    if modal:
        win.change_property(atom("_NET_WM_STATE"), Xatom.ATOM, 32, [atom("_NET_WM_STATE_MODAL")])
    win.map()
    old = root.get_full_property(atom("_NET_CLIENT_LIST"), Xatom.WINDOW)
    root.change_property(atom("_NET_CLIENT_LIST"), Xatom.WINDOW, 32, list(old.value if old else []) + [win.id])
    d.sync()
    return win


def test_x11_window_capture_is_its_size(xvfb, monkeypatch) -> None:
    from backend.sandbox import capture
    from frontend.ui_web import tool_captures

    fake_ewmh_window(xvfb, 10, 20, 1280, 720, title="sandbox app")
    time.sleep(0.2)
    wins = capture.windows_of([os.getpid()])
    assert [w["title"] for w in wins] == ["sandbox app"]
    assert wins[0]["rect"] == {"left": 10, "top": 20, "right": 1290, "bottom": 740}
    saved = {}
    monkeypatch.setattr(tool_captures, "save_capture_for_agents",
                        lambda png, prefix, conv_id="": saved.update(png=png) or {"ok": True, "path": "x.png"})
    out = capture.capture(wins[0], "chat")
    assert out["ok"] and (out["width"], out["height"]) == (1280, 720)
    from PIL import Image
    import io

    img = Image.open(io.BytesIO(saved["png"]))
    assert img.size == (1280, 720)
    assert img.getpixel((640, 360)) == (255, 255, 255)  # the window's white, not the black root


def test_x11_region_grab_and_capturer(xvfb) -> None:
    import ctypes

    assert len(nc.grab(0, 0, 64, 32)) == 64 * 32 * 4
    got = []
    buf = (ctypes.c_char * (64 * 32 * 4))()
    cap = nc.Capturer(lambda: (0, 0, 64, 32), 10, lambda w, h: ctypes.addressof(buf),
                      lambda w, h: got.append((w, h))).start()
    deadline = time.time() + 3
    while not got and time.time() < deadline:
        time.sleep(0.05)
    cap.stop()
    assert got and got[0] == (64, 32)
