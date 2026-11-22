"""Tray icon helpers — no GUI required."""

from __future__ import annotations

import sys
from pathlib import Path

from frontend.tray_icon import open_png

LOGO = Path(__file__).resolve().parent / "ui_web" / "web" / "public" / "OnlineMCPIcon.png"


def test_open_png_skips_avif_codec() -> None:
    """PNG decode must not load Pillow's AVIF native codec."""
    for name in ("PIL.AvifImagePlugin", "PIL._avif"):
        sys.modules.pop(name, None)
    img = open_png(LOGO)
    assert img.size[0] > 0
    assert "PIL.AvifImagePlugin" not in sys.modules
    assert "PIL._avif" not in sys.modules


class _FakeIcon:
    def __init__(self, name, image, title, menu) -> None:
        self.name, self.icon, self.title, self.menu = name, image, title, menu
        self.visible = True


def _fake_pystray():
    import types

    return types.SimpleNamespace(
        Menu=lambda *items: list(items),
        MenuItem=lambda text, action, default=False: (text, action, default),
        Icon=_FakeIcon,
    )


def test_linux_tray_needs_a_host(monkeypatch) -> None:
    from frontend import open_files, tray_icon

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("PYSTRAY_BACKEND", raising=False)
    calls = []

    def fake_call(dest, path, method, *args, **_kw):
        calls.append((method, args))
        return reply

    monkeypatch.setattr(open_files, "gdbus_call", fake_call)
    monkeypatch.setattr(tray_icon, "_imports_ok", lambda: True)  # pystray's AppIndicator needs gi
    reply = "(true,)"
    assert tray_icon.tray_supported() is True
    assert calls == [("org.freedesktop.DBus.NameHasOwner", ("org.kde.StatusNotifierWatcher",))]
    import os

    assert os.environ["PYSTRAY_BACKEND"] == "appindicator"
    reply = "(false,)"
    assert tray_icon.tray_supported() is False
    reply = None  # no session bus
    assert tray_icon.tray_supported() is False


def test_close_minimises_without_a_linux_tray(monkeypatch) -> None:
    from frontend.tray_icon import close_action

    monkeypatch.setattr(sys, "platform", "linux")
    assert close_action(None) == "minimize"
    assert close_action(object()) == "hide"
    monkeypatch.setattr(sys, "platform", "win32")
    assert close_action(None) == "hide"


def test_linux_menu_matches_windows_and_toast_is_dbus(monkeypatch) -> None:
    from frontend import tray_icon

    monkeypatch.setitem(sys.modules, "pystray", _fake_pystray())
    monkeypatch.setattr(sys, "platform", "linux")
    shown, exited = [], []
    tray = tray_icon.TrayIconController(None, on_show=lambda: shown.append(1),
                                        on_exit=lambda: exited.append(1), tooltip="UEFN Ducky")
    icon = tray._icon
    assert icon.name == "uefn_ducky" and icon.title == "UEFN Ducky"
    assert [(t, d) for t, _a, d in icon.menu] == [("Open UEFN Ducky", True), ("Exit all", False)]
    tray._dispatch = lambda fn: fn()
    icon.menu[0][1](icon, None)
    icon.menu[1][1](icon, None)
    assert shown == [1] and exited == [1]
    # Linux notifications go over D-Bus (frontend.notifications), never a Win32 balloon.
    assert tray.toast("t", "m") is False
