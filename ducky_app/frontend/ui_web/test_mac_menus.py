"""macOS menu bar: Undo/Redo in Edit, a Window menu, and Cmd+Q running Ducky's exit."""

from __future__ import annotations

import sys
import threading
import types

from frontend import tray_icon
from frontend.ui_web import webview_native_menu as nm


class _Item:
    def __init__(self, title="", action=None, key="", separator=False):
        self.title, self.action, self.key, self.separator = title, action, key, separator
        self.sub = None
        self.target = None

    @classmethod
    def alloc(cls):
        return cls()

    def initWithTitle_action_keyEquivalent_(self, title, action, key):
        self.title, self.action, self.key = title, action, key
        return self

    @classmethod
    def separatorItem(cls):
        return cls(separator=True)

    def keyEquivalent(self):
        return self.key

    def submenu(self):
        return self.sub

    def setSubmenu_(self, menu):
        self.sub = menu

    def setTarget_(self, target):
        self.target = target

    def setAction_(self, action):
        self.action = action


class _Menu:
    def __init__(self, title="", items=()):
        self.title, self.items = title, list(items)

    @classmethod
    def alloc(cls):
        return cls()

    def initWithTitle_(self, title):
        self.title = title
        return self

    def numberOfItems(self):
        return len(self.items)

    def itemAtIndex_(self, i):
        return self.items[i]

    def insertItem_atIndex_(self, item, i):
        self.items.insert(i, item)

    def addItem_(self, item):
        self.items.append(item)


def _pywebview_bar():
    """The bar pywebview 6 builds: App (About … Quit), Edit (Cut/Copy/Paste/Select All), View."""
    app_menu = _Menu("App", [_Item("About", "orderFrontStandardAboutPanel:"), _Item("Hide", "hide:", "h"),
                             _Item("Quit", "terminate:", "q")])
    edit = _Menu("Edit", [_Item("Cut", "cut:", "x"), _Item("Copy", "copy:", "c"), _Item("Paste", "paste:", "v"),
                          _Item("Select All", "selectAll:", "a")])
    view = _Menu("View", [_Item("Enter Full Screen", "toggleFullScreen:", "f")])
    bar = _Menu("", [])
    for m in (app_menu, edit, view):
        holder = _Item(m.title)
        holder.setSubmenu_(m)
        bar.addItem_(holder)
    return bar, app_menu, edit


def _fake(monkeypatch, bar):
    app = types.SimpleNamespace(windows=None)
    app.mainMenu = lambda: bar
    app.windowsMenu = lambda: app.windows
    app.setWindowsMenu_ = lambda m: setattr(app, "windows", m)
    appkit = types.ModuleType("AppKit")
    appkit.NSApplication = types.SimpleNamespace(sharedApplication=lambda: app)
    appkit.NSMenuItem = _Item
    appkit.NSMenu = _Menu
    monkeypatch.setitem(sys.modules, "AppKit", appkit)

    class Target:
        @classmethod
        def alloc(cls):
            return cls()

        def init(self):
            return self

        def exit_(self, sender):
            self.owner._dispatch(self.owner._on_exit)

    monkeypatch.setattr(tray_icon, "_mac_menu_target_class", lambda: Target)
    return app


def test_edit_gets_undo_redo_and_a_window_menu_is_added(monkeypatch):
    bar, _app_menu, edit = _pywebview_bar()
    app = _fake(monkeypatch, bar)
    assert nm.mac_complete_menus(lambda: None) is True
    assert [(i.title, i.action, i.key) for i in edit.items[:2]] == [("Undo", "undo:", "z"), ("Redo", "redo:", "Z")]
    assert edit.items[2].separator
    assert [i.key for i in app.windows.items] == ["m", "", "w"]
    assert bar.items[-1].sub is app.windows
    nm.mac_complete_menus(lambda: None)  # a second call adds nothing
    assert [i.key for i in edit.items].count("z") == 1
    assert sum(1 for i in bar.items if i.title == "Window") == 1


def test_cmd_q_runs_duckys_exit_not_the_window_close(monkeypatch):
    bar, app_menu, _edit = _pywebview_bar()
    _fake(monkeypatch, bar)
    quit_done = threading.Event()
    nm.mac_complete_menus(quit_done.set)
    quit_item = app_menu.items[-1]
    assert quit_item.action == "exit:" and quit_item.target is not None
    quit_item.target.exit_(quit_item)
    assert quit_done.wait(2)
