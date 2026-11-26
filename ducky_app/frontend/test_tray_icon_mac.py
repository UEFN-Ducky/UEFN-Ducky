"""macOS menu bar icon, built with fake AppKit objects (no Mac needed)."""

from __future__ import annotations

import sys
import threading
import types

import pytest

from frontend import tray_icon


class _Obj:
    """Any AppKit object: records calls, chains alloc/init."""

    def __init__(self, name: str, log: list) -> None:
        self._name, self._log = name, log

    def __getattr__(self, attr: str):
        def call(*args):
            self._log.append((self._name, attr, args))
            if attr in ("alloc", "init", "initWithTitle_action_keyEquivalent_", "initWithContentsOfFile_",
                        "systemStatusBar", "statusItemWithLength_", "button", "sharedApplication"):
                return _Obj(f"{self._name}.{attr}", self._log)
            if attr == "delegate":
                return types.SimpleNamespace(class__=lambda: "AppDelegate")
            return None

        return call


@pytest.fixture
def fake_appkit(monkeypatch):
    log: list = []
    later: list = []
    appkit = types.ModuleType("AppKit")
    for name in ("NSStatusBar", "NSMenu", "NSMenuItem", "NSImage", "NSApplication"):
        setattr(appkit, name, _Obj(name, log))
    appkit.NSVariableStatusItemLength = -1
    helper = types.SimpleNamespace(callAfter=lambda fn, *a: later.append((fn, a)))
    pyobjctools = types.ModuleType("PyObjCTools")
    pyobjctools.AppHelper = helper
    added: list = []
    objc = types.ModuleType("objc")
    objc.selector = lambda fn, selector, signature: (selector, fn)
    objc.classAddMethods = lambda cls, methods: added.append((cls, methods))
    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "PyObjCTools", pyobjctools)
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", helper)
    monkeypatch.setitem(sys.modules, "objc", objc)

    class Target:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

    monkeypatch.setattr(tray_icon, "_mac_menu_target_class", lambda: Target)
    monkeypatch.setattr(sys, "platform", "darwin")
    return {"log": log, "later": later, "added": added}


def _run_later(fake) -> None:
    while fake["later"]:
        fn, args = fake["later"].pop(0)
        fn(*args)


def test_mac_uses_the_menu_bar_item(fake_appkit):
    assert tray_icon.tray_supported() is True
    assert tray_icon.tray_class() is tray_icon.MacStatusItem


def test_status_item_is_built_in_the_cocoa_loop_with_open_and_exit(fake_appkit):
    shown, exited = threading.Event(), threading.Event()
    item = tray_icon.MacStatusItem(None, on_show=shown.set, on_exit=exited.set, tooltip="UEFN Ducky", status="offline")
    item.run_daemon()
    assert item._item is None  # nothing built off the main loop
    _run_later(fake_appkit)
    log = fake_appkit["log"]
    titles = [args[0] for (_n, attr, args) in log if attr == "initWithTitle_action_keyEquivalent_"]
    actions = [args[1] for (_n, attr, args) in log if attr == "initWithTitle_action_keyEquivalent_"]
    assert titles == ["Open UEFN Ducky", "Exit all"] and actions == ["open:", "exit:"]
    assert any(attr == "setToolTip_" and args == ("UEFN Ducky",) for (_n, attr, args) in log)
    assert any(attr == "setMenu_" for (_n, attr, _a) in log)
    assert item._target.owner is item


def test_dock_click_reopens_the_window(fake_appkit):
    shown = threading.Event()
    item = tray_icon.MacStatusItem(None, on_show=shown.set, on_exit=lambda: None, tooltip="t")
    item.run_daemon()
    _run_later(fake_appkit)
    (cls, [(selector, reopen)]), = fake_appkit["added"]
    assert cls == "AppDelegate" and selector == b"applicationShouldHandleReopen:hasVisibleWindows:"
    assert reopen(None, None, False) is True
    assert shown.wait(2)


def test_mode_change_swaps_the_image_on_the_main_loop(fake_appkit, monkeypatch, tmp_path):
    png = tmp_path / "x.png"
    png.write_bytes(b"")
    monkeypatch.setattr(tray_icon, "resolve_status_png_path", lambda mode: png)
    item = tray_icon.MacStatusItem(None, on_show=lambda: None, on_exit=lambda: None, tooltip="t", status="offline")
    item.run_daemon()
    _run_later(fake_appkit)
    fake_appkit["log"].clear()
    item.set_connection_mode("online")
    assert not any(attr == "setImage_" for (_n, attr, _a) in fake_appkit["log"])
    _run_later(fake_appkit)
    assert any(attr == "setImage_" for (_n, attr, _a) in fake_appkit["log"])
    item.stop()
    _run_later(fake_appkit)
    assert any(attr == "removeStatusItem_" for (_n, attr, _a) in fake_appkit["log"])
