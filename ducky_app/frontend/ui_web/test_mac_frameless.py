"""macOS title bar and header drag, with fake AppKit objects."""

from __future__ import annotations

import sys
import types

from frontend.ui_web import mac_frameless, win_frameless


class _Button:
    def __init__(self) -> None:
        self.hidden = True

    def setHidden_(self, on: bool) -> None:
        self.hidden = on


class _NSWindow:
    def __init__(self) -> None:
        self.mask = 1 | 2 | 4 | 8
        self.buttons = {k: _Button() for k in (0, 1, 2)}
        self.transparent = False
        self.title_visibility = 0
        self.origin = types.SimpleNamespace(x=100.0, y=200.0)
        self.moves: list[tuple[float, float]] = []
        self.events: list = []

    def styleMask(self):
        return self.mask

    def setStyleMask_(self, mask):
        self.mask = mask

    def setTitlebarAppearsTransparent_(self, on):
        self.transparent = on

    def setTitleVisibility_(self, v):
        self.title_visibility = v

    def standardWindowButton_(self, kind):
        return self.buttons[kind]

    def frame(self):
        return types.SimpleNamespace(origin=self.origin)

    def nextEventMatchingMask_untilDate_inMode_dequeue_(self, mask, date, mode, dequeue):
        return self.events.pop(0) if self.events else None

    def setFrameOrigin_(self, point):
        self.moves.append(point)


def test_traffic_lights_stay_over_a_full_size_transparent_title_bar():
    ns = _NSWindow()
    mac_frameless.apply_native(ns)
    assert ns.mask & mac_frameless.FULL_SIZE_CONTENT
    assert ns.transparent and ns.title_visibility == mac_frameless.TITLE_HIDDEN
    assert not any(b.hidden for b in ns.buttons.values())


def test_refresh_window_chrome_queues_it_on_the_cocoa_loop(monkeypatch):
    later: list = []
    helper = types.SimpleNamespace(callAfter=lambda fn, *a: later.append((fn, a)))
    pkg = types.ModuleType("PyObjCTools")
    pkg.AppHelper = helper
    monkeypatch.setitem(sys.modules, "PyObjCTools", pkg)
    monkeypatch.setattr(sys, "platform", "darwin")
    ns = _NSWindow()
    assert win_frameless.refresh_window_chrome(types.SimpleNamespace(native=ns)) is True
    fn, args = later.pop()
    fn(*args)
    assert not ns.buttons[0].hidden


def _fake_appkit(monkeypatch, buttons: list[int], mouse: list[tuple[float, float]]):
    appkit = types.ModuleType("AppKit")
    appkit.NSEvent = types.SimpleNamespace(
        pressedMouseButtons=lambda: buttons.pop(0) if len(buttons) > 1 else buttons[0],
        mouseLocation=lambda: types.SimpleNamespace(x=mouse[0][0], y=mouse.pop(0)[1] if len(mouse) > 1 else mouse[0][1]),
    )
    appkit.NSDate = types.SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda s: s)
    appkit.NSEventTrackingRunLoopMode = "tracking"
    monkeypatch.setitem(sys.modules, "AppKit", appkit)


def test_header_drag_moves_the_window_with_the_mouse_until_release(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    # held for the start check and two loop rounds, then released
    _fake_appkit(monkeypatch, [1, 1, 1, 0], [(10.0, 10.0), (10.0, 30.0), (10.0, 50.0)])
    ns = _NSWindow()
    assert win_frameless.begin_native_window_move(types.SimpleNamespace(native=ns)) is True
    assert ns.moves[0] == (100.0, 220.0)
    assert ns.moves[-1] == (100.0, 240.0)


def test_no_drag_once_the_button_is_already_up(monkeypatch):
    _fake_appkit(monkeypatch, [0], [(0.0, 0.0)])
    ns = _NSWindow()
    assert mac_frameless.begin_move(types.SimpleNamespace(native=ns)) is False
    assert ns.moves == []
