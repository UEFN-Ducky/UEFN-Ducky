"""macOS capture with fake Quartz / ScreenCaptureKit / mss: grabs, the snip, the permission note."""

from __future__ import annotations

import io
import sys
import types

import pytest

from frontend import native_capture as nc
from frontend.ui_web import snip_overlay


def _quartz(monkeypatch, allowed: bool, asked: list) -> None:
    q = types.ModuleType("Quartz")
    q.CGPreflightScreenCaptureAccess = lambda: allowed
    q.CGRequestScreenCaptureAccess = lambda: asked.append(True)
    q.CGRectMake = lambda *r: r
    monkeypatch.setitem(sys.modules, "Quartz", q)


def test_retina_frames_come_back_at_the_point_size_asked_for():
    # 2x2 pixels per point, rows padded to 24 bytes: one blue, one red point wide.
    row = bytes([255, 0, 0, 255] * 2 + [0, 0, 255, 255] * 2) + b"\0" * 8
    out = nc._sized_bgra(row * 4, (4, 4), 24, 2, 2)
    assert len(out) == 2 * 2 * 4
    assert out[:4] == bytes([255, 0, 0, 255]) and out[4:8] == bytes([0, 0, 255, 255])
    same = bytes(range(16))
    assert nc._sized_bgra(same, (2, 2), 8, 2, 2) == same


def test_grabber_falls_back_to_mss_without_screencapturekit(monkeypatch):
    _quartz(monkeypatch, True, [])
    monkeypatch.setattr(nc, "_sck_image", lambda *r: None)  # before macOS 15.2
    grabs = []

    class _Mss:
        def grab(self, mon):
            grabs.append(mon)
            return types.SimpleNamespace(raw=bytearray(b"\x01\x02\x03\xff" * 6), size=(3, 2))

        def close(self):
            grabs.append("closed")

    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(mss=_Mss))
    g = nc.MacGrabber()
    assert g.grab(10, 20, 3, 2) == b"\x01\x02\x03\xff" * 6
    g.close()
    assert grabs == [{"left": 10, "top": 20, "width": 3, "height": 2}, "closed"]


def test_grabber_refuses_without_screen_recording(monkeypatch):
    asked: list = []
    _quartz(monkeypatch, False, asked)
    with pytest.raises(PermissionError, match="Screen Recording"):
        nc.MacGrabber()
    assert asked == [True]  # macOS was asked to show its own prompt


def test_snip_without_permission_offers_the_settings_pane(monkeypatch):
    _quartz(monkeypatch, False, [])
    opened, asked = [], []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(snip_overlay.platform_info, "IS_LINUX", False)
    import frontend.mac_alert as mac_alert

    monkeypatch.setattr(mac_alert, "ask", lambda title, msg, buttons=("OK",), **kw: asked.append(buttons) or 0)
    monkeypatch.setattr(nc.subprocess, "run", lambda cmd, **kw: opened.append(cmd))
    got = snip_overlay.snip_screen_interactive()
    assert got["ok"] is False and got["reason"] == "permission" and got["settings_url"] == nc.SCREEN_RECORDING_SETTINGS
    assert asked == [("Open System Settings", "Not now")]
    assert opened == [["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"]]


@pytest.mark.parametrize("picked", [True, False])
def test_snip_returns_the_picked_region_or_cancelled(monkeypatch, picked):
    from PIL import Image

    _quartz(monkeypatch, True, [])
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(snip_overlay.platform_info, "IS_LINUX", False)
    runs = []

    def screencapture(cmd, **kw):
        runs.append(cmd[:3])
        if picked:  # the person dragged a box; Esc leaves the file empty
            buf = io.BytesIO()
            Image.new("RGB", (30, 20), "red").save(buf, "PNG")
            with open(cmd[-1], "wb") as f:
                f.write(buf.getvalue())

    monkeypatch.setattr(snip_overlay.subprocess, "run", screencapture)
    got = snip_overlay.snip_screen_interactive()
    assert runs == [["/usr/sbin/screencapture", "-i", "-x"]]
    if picked:
        assert got["ok"] and (got["width"], got["height"]) == (30, 20) and got["mime"] == "image/png"
    else:
        assert got == {"ok": False, "reason": "cancelled"}
