from enum import Enum
from types import SimpleNamespace

import pytest

from frontend.ui_web import webview_memory as memory


class Level(Enum):
    Normal = 0
    Low = 1


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self

    def fire(self, *args):
        for handler in list(self.handlers):
            handler(*args)


@pytest.fixture
def window(monkeypatch):
    monkeypatch.setattr(memory, "enabled", lambda: True)
    control = SimpleNamespace(CoreWebView2=SimpleNamespace(MemoryUsageTargetLevel=Level.Normal), Visible=True)
    form = SimpleNamespace(
        IsDisposed=False, Visible=True, WindowState="Normal", Controls=[control],
        InvokeRequired=False, VisibleChanged=Event(), Resize=Event(),
    )
    return SimpleNamespace(native=form, events=SimpleNamespace(shown=Event(), loaded=Event(), closed=Event()))


def test_hide_restore_and_minimize_keep_scripts_running(window):
    memory.install(window)
    window.events.shown.fire()
    form = window.native
    core = form.Controls[0].CoreWebView2
    form.Visible = False
    form.VisibleChanged.fire(None, None)
    assert core.MemoryUsageTargetLevel == Level.Low
    form.Visible = True
    form.VisibleChanged.fire(None, None)
    assert core.MemoryUsageTargetLevel == Level.Normal
    form.WindowState = "Minimized"
    form.Resize.fire(None, None)
    assert core.MemoryUsageTargetLevel == Level.Low
    form.WindowState = "Maximized"
    form.Resize.fire(None, None)
    assert core.MemoryUsageTargetLevel == Level.Normal


def test_loaded_retries_core_initialization_and_closed_detaches(window):
    control = window.native.Controls[0]
    control.CoreWebView2 = None
    memory.install(window)
    window.events.shown.fire()
    window.native.Visible = False
    control.CoreWebView2 = SimpleNamespace(MemoryUsageTargetLevel=Level.Normal)
    window.events.loaded.fire()
    assert control.CoreWebView2.MemoryUsageTargetLevel == Level.Low
    assert len(window.native.VisibleChanged.handlers) == 1
    window.events.closed.fire()
    assert not window.native.VisibleChanged.handlers
    assert not window.native.Resize.handlers
    assert not window.events.loaded.handlers


def test_hidden_browser_pane_stays_low_on_parent_restore(window):
    control = window.native.Controls[0]
    control.Visible = False
    memory.update_form(window.native)
    assert control.CoreWebView2.MemoryUsageTargetLevel == Level.Low
    control.Visible = True
    memory.update_form(window.native)
    assert control.CoreWebView2.MemoryUsageTargetLevel == Level.Normal


def test_unsupported_core_and_opt_out_are_safe(window, monkeypatch):
    assert not memory.update_control(SimpleNamespace(CoreWebView2=object()), active=False)
    monkeypatch.setattr(memory, "enabled", lambda: False)
    memory.install(window)
    assert not window.events.shown.handlers
    assert not memory.update_control(window.native.Controls[0], active=False)


def test_async_attachment_reads_latest_visibility(window, monkeypatch):
    queued = []
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    window.native.InvokeRequired = True
    window.native.BeginInvoke = queued.append
    memory.install(window)
    window.events.shown.fire()
    window.native.Visible = False
    queued.pop()()
    assert window.native.Controls[0].CoreWebView2.MemoryUsageTargetLevel == Level.Low


def test_closed_window_ignores_queued_attachment(window, monkeypatch):
    queued = []
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    window.native.InvokeRequired = True
    window.native.BeginInvoke = queued.append
    memory.install(window)
    window.events.shown.fire()
    window.events.closed.fire()
    queued.pop()()
    assert not window.native.Resize.handlers
