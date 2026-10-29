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


def test_trim_only_when_hidden_idle_big_and_not_too_soon():
    big = {"webview_gb": 2.5, "agents": 0}
    t = memory.TRIM_EVERY_S + 1
    assert memory.should_trim(big, active=False, now=t, last_at=0.0)
    assert not memory.should_trim(big, active=True, now=t, last_at=0.0)  # the person is looking
    assert not memory.should_trim({**big, "agents": 1}, active=False, now=t, last_at=0.0)  # a turn runs
    assert not memory.should_trim({**big, "webview_gb": 1.2}, active=False, now=t, last_at=0.0)
    assert not memory.should_trim(big, active=False, now=t, last_at=t - 60)  # trimmed a minute ago
    assert not memory.should_trim({"webview_gb": "?"}, active=False, now=t, last_at=0.0)


def test_a_bloated_hidden_window_reloads_once_and_keeps_its_chats(window, monkeypatch):
    from frontend import error_log
    from frontend.ui_web import panel_api

    queued, reloaded, marked, activity = [], [], [], []
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    monkeypatch.setattr(panel_api, "mark_crash_reload", lambda: marked.append(1))
    monkeypatch.setattr(error_log, "record_activity", lambda src, msg: activity.append(src))
    window.native.BeginInvoke = queued.append
    window.native.Controls[0].CoreWebView2.Reload = lambda: reloaded.append(1)
    window.native.Visible = False
    beat = {"webview_gb": 2.4, "agents": 0}
    assert memory.trim_if_bloated(window, beat)
    assert not memory.trim_if_bloated(window, beat)  # the 30-minute gap holds
    queued.pop()()
    assert reloaded == [1] and marked == [1] and activity == ["webview"]


def test_a_window_shown_before_the_queued_trim_is_left_alone(window, monkeypatch):
    queued, reloaded = [], []
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    window.native.BeginInvoke = queued.append
    window.native.Controls[0].CoreWebView2.Reload = lambda: reloaded.append(1)
    window.native.Visible = False
    assert memory.trim_if_bloated(window, {"webview_gb": 3.0, "agents": 0})
    window.native.Visible = True
    queued.pop()()
    assert reloaded == []


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
