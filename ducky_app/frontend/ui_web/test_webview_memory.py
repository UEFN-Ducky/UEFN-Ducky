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


@pytest.fixture(autouse=True)
def _fresh_soft(monkeypatch):
    monkeypatch.setattr(memory, "_soft", {"at": 0.0})
    monkeypatch.setattr(memory, "_used", {"at": 0.0})


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


def test_minimized_or_hidden_the_controller_goes_invisible_and_low_and_back(window):
    control = window.native.Controls[0]
    control.CoreWebView2Controller = SimpleNamespace(IsVisible=True)
    memory.install(window)
    window.events.shown.fire()
    form, core, controller = window.native, control.CoreWebView2, control.CoreWebView2Controller
    form.WindowState = "Minimized"
    form.Resize.fire(None, None)
    assert (controller.IsVisible, core.MemoryUsageTargetLevel) == (False, Level.Low)
    form.WindowState = "Normal"
    form.Resize.fire(None, None)
    assert (controller.IsVisible, core.MemoryUsageTargetLevel) == (True, Level.Normal)
    form.Visible = False  # closed to the tray
    form.VisibleChanged.fire(None, None)
    assert (controller.IsVisible, core.MemoryUsageTargetLevel) == (False, Level.Low)
    form.Visible = True
    form.VisibleChanged.fire(None, None)
    assert (controller.IsVisible, core.MemoryUsageTargetLevel) == (True, Level.Normal)


def test_a_controller_that_refuses_still_gets_the_memory_target(window):
    class Stubborn:
        @property
        def IsVisible(self):  # noqa: N802 - .NET name
            return True

        @IsVisible.setter
        def IsVisible(self, value):  # noqa: N802
            raise RuntimeError("disposed")

    control = window.native.Controls[0]
    control.CoreWebView2Controller = Stubborn()
    assert memory.update_control(control, active=False)
    assert control.CoreWebView2.MemoryUsageTargetLevel == Level.Low


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
    big = {"webview_gb": 1.6, "agents": 0}
    t = memory.TRIM_EVERY_S + 1
    assert memory.should_trim(big, active=False, now=t, last_at=0.0)
    assert not memory.should_trim(big, active=True, now=t, last_at=0.0)  # the person is looking
    assert not memory.should_trim({**big, "webview_gb": 0.8}, active=False, now=t, last_at=0.0)
    assert not memory.should_trim(big, active=False, now=t, last_at=t - 60)  # trimmed a minute ago
    assert not memory.should_trim({"webview_gb": "?"}, active=False, now=t, last_at=0.0)


def test_an_open_window_nobody_uses_trims_past_the_open_cap():
    """Nov 2 2026: 2.7 GB in an open, idle window on a soft-cap build, then an OOM exit."""
    t = memory.TRIM_EVERY_S + 1
    idle = memory.OPEN_IDLE_S + 60
    assert memory.should_trim({"webview_gb": 2.7, "agents": 0}, active=True, now=t, last_at=0.0, idle_s=idle)
    assert not memory.should_trim({"webview_gb": 2.7, "agents": 0}, active=True, now=t, last_at=0.0, idle_s=30)
    assert not memory.should_trim({"webview_gb": 1.2, "agents": 0}, active=True, now=t, last_at=0.0, idle_s=idle)


def test_the_500_mb_budget_levels():
    """Nov 5 2026: a 908 MB renderer sat under the 1.0 / 1.5 GB levels and set off nothing."""
    t = memory.TRIM_EVERY_S + 1
    assert memory.should_trim({"webview_gb": 0.95}, active=False, now=t, last_at=0.0)  # hidden past 0.9: reload
    assert not memory.should_trim({"webview_gb": 0.85}, active=False, now=t, last_at=0.0)
    # An open window in use is never reloaded, however big.
    assert not memory.should_trim({"webview_gb": 0.95}, active=True, now=t, last_at=0.0, idle_s=5)
    untouched = memory.SOFT_IDLE_S + 1
    assert memory.soft_level({"webview_gb": 0.65}, active=True, idle_s=untouched, now=10.0, low_at=0.0) == "Low"
    assert memory.soft_level({"webview_gb": 0.55}, active=True, idle_s=untouched, now=10.0, low_at=0.0) == ""
    assert memory.soft_level({"webview_gb": 0.65}, active=True, idle_s=60, now=10.0, low_at=0.0) == ""  # used a minute ago


def test_hidden_at_950_mb_reloads(window, monkeypatch):
    from frontend import error_log
    from frontend.ui_web import panel_api

    queued, reloaded = [], []
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    monkeypatch.setattr(memory, "input_idle_s", lambda: 5.0)
    monkeypatch.setattr(panel_api, "mark_crash_reload", lambda: None)
    monkeypatch.setattr(error_log, "record_activity", lambda src, msg: None)
    window.native.BeginInvoke = queued.append
    window.native.Controls[0].CoreWebView2.Reload = lambda: reloaded.append(1)
    window.native.Visible = False
    assert memory.trim_if_bloated(window, {"webview_gb": 0.95, "agents": 2})
    queued.pop()()
    assert reloaded == [1]


def test_open_untouched_two_minutes_at_650_mb_goes_low(window, monkeypatch):
    monkeypatch.setattr(memory, "input_idle_s", lambda: memory.SOFT_IDLE_S + 1)
    monkeypatch.setattr(memory, "_last_trim", {"at": memory.time.monotonic()})  # no reload here
    core = window.native.Controls[0].CoreWebView2
    assert not memory.trim_if_bloated(window, {"webview_gb": 0.65, "agents": 1})
    assert core.MemoryUsageTargetLevel == Level.Low


def test_hidden_past_the_cap_reloads_even_with_agents_running(window, monkeypatch):
    """Nov 5 2026: agents always run, so the old 0-turn rule never let the reload fire."""
    queued, reloaded = [], []
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    monkeypatch.setattr(memory, "input_idle_s", lambda: 5.0)
    from frontend import error_log
    from frontend.ui_web import panel_api

    monkeypatch.setattr(panel_api, "mark_crash_reload", lambda: None)
    monkeypatch.setattr(error_log, "record_activity", lambda src, msg: None)
    window.native.BeginInvoke = queued.append
    window.native.Controls[0].CoreWebView2.Reload = lambda: reloaded.append(1)
    window.native.WindowState = "Minimized"
    assert memory.trim_if_bloated(window, {"webview_gb": 1.6, "agents": 3})
    queued.pop()()
    assert reloaded == [1]


def test_an_open_window_in_use_is_never_reloaded(window, monkeypatch):
    reloaded = []
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    monkeypatch.setattr(memory, "input_idle_s", lambda: 3.0)
    window.native.BeginInvoke = lambda fn: fn()
    window.native.Controls[0].CoreWebView2.Reload = lambda: reloaded.append(1)
    for agents in (0, 4):
        assert not memory.trim_if_bloated(window, {"webview_gb": 3.0, "agents": agents})
    assert reloaded == []
    assert window.native.Controls[0].CoreWebView2.MemoryUsageTargetLevel == Level.Normal


def test_idle_open_window_over_a_gb_goes_low_then_normal_on_input(window, monkeypatch):
    idle = {"s": memory.SOFT_IDLE_S + 1}
    monkeypatch.setattr(memory, "input_idle_s", lambda: idle["s"])
    monkeypatch.setattr(memory, "_last_trim", {"at": memory.time.monotonic()})  # no reload here
    core = window.native.Controls[0].CoreWebView2
    assert not memory.trim_if_bloated(window, {"webview_gb": 1.2, "agents": 2})
    assert core.MemoryUsageTargetLevel == Level.Low
    assert not memory.trim_if_bloated(window, {"webview_gb": 1.2, "agents": 2})  # still idle: stays Low
    assert core.MemoryUsageTargetLevel == Level.Low
    idle["s"] = 0.0  # the person moved the mouse
    memory.trim_if_bloated(window, {"webview_gb": 1.2, "agents": 2})
    assert core.MemoryUsageTargetLevel == Level.Normal and memory._soft["at"] == 0.0


def test_visible_idle_11_min_at_2_5_gb_reloads_and_2_min_does_not():
    """anw4b7x2q9k1m: WebView2 grew 0 -> 2.7 GB in an open window."""
    t, beat = memory.TRIM_EVERY_S + 1, {"webview_gb": 2.5, "agents": 0}
    assert memory.should_trim(beat, active=True, now=t, last_at=0.0, idle_s=11 * 60)
    assert not memory.should_trim(beat, active=True, now=t, last_at=0.0, idle_s=2 * 60)
    # An agent streaming into the open window: no reload under the person's eyes.
    assert not memory.should_trim({**beat, "agents": 1}, active=True, now=t, last_at=0.0, idle_s=11 * 60)


def test_working_in_another_program_counts_as_the_ducky_window_idle(window, monkeypatch):
    monkeypatch.setattr(memory, "input_idle_s", lambda: 1.0)  # the person types in UEFN
    fg = {"ducky": True}
    monkeypatch.setattr(memory, "_foreground", lambda native: fg["ducky"])
    assert memory.window_idle_s(window.native, 100.0) == 1.0
    fg["ducky"] = False
    assert memory.window_idle_s(window.native, 100.0 + 11 * 60) == 11 * 60 + 1.0
    fg["ducky"] = True  # back in Ducky
    assert memory.window_idle_s(window.native, 2000.0) == 1.0


def test_an_open_window_behind_uefn_reloads_after_10_idle_minutes(window, monkeypatch):
    from frontend import error_log
    from frontend.ui_web import panel_api

    queued, reloaded = [], []
    clock = [10_000.0]
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setattr(memory.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    monkeypatch.setattr(memory, "input_idle_s", lambda: 2.0)  # PC busy all along
    monkeypatch.setattr(memory, "_foreground", lambda native: False)
    monkeypatch.setattr(panel_api, "mark_crash_reload", lambda: None)
    monkeypatch.setattr(error_log, "record_activity", lambda src, msg: None)
    window.native.BeginInvoke = queued.append
    window.native.Controls[0].CoreWebView2.Reload = lambda: reloaded.append(1)
    beat = {"webview_gb": 2.5, "agents": 0}
    assert not memory.trim_if_bloated(window, beat)  # first seen in the background
    clock[0] += 2 * 60
    assert not memory.trim_if_bloated(window, beat)
    clock[0] += 9 * 60
    assert memory.trim_if_bloated(window, beat)
    queued.pop()()
    assert reloaded == [1]


def test_soft_level_needs_a_gb_and_idle():
    beat = {"webview_gb": 0.5}
    assert memory.soft_level(beat, active=True, idle_s=999, now=10.0, low_at=0.0) == ""
    assert memory.soft_level({"webview_gb": 1.2}, active=True, idle_s=5, now=10.0, low_at=0.0) == ""
    assert memory.soft_level({"webview_gb": 1.2}, active=False, idle_s=999, now=10.0, low_at=0.0) == ""


@pytest.mark.parametrize("source", ["form", "control"])
def test_activation_or_focus_sets_normal_at_once_without_a_heartbeat(window, source):
    form, control = window.native, window.native.Controls[0]
    form.Activated, control.GotFocus = Event(), Event()
    memory.install(window)
    window.events.shown.fire()
    assert memory.trim_now(window, "test")
    assert control.CoreWebView2.MemoryUsageTargetLevel == Level.Low
    (form.Activated if source == "form" else control.GotFocus).fire(None, None)
    assert control.CoreWebView2.MemoryUsageTargetLevel == Level.Normal and memory._soft["at"] == 0.0
    window.events.closed.fire()
    assert not form.Activated.handlers and not control.GotFocus.handlers


def test_trim_now_sets_low_without_hiding_the_page(window):
    control = window.native.Controls[0]
    control.CoreWebView2Controller = SimpleNamespace(IsVisible=True)
    assert memory.trim_now(window, "free_ducky_memory")
    assert control.CoreWebView2.MemoryUsageTargetLevel == Level.Low
    assert control.CoreWebView2Controller.IsVisible is True
    assert memory._soft["at"] > 0


def test_try_suspend_is_never_called(window, monkeypatch):
    calls = []
    core = window.native.Controls[0].CoreWebView2
    core.TrySuspendAsync = lambda *a: calls.append("suspend")
    core.Resume = lambda *a: calls.append("resume")
    core.Reload = lambda: None
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    monkeypatch.setattr(memory, "input_idle_s", lambda: memory.OPEN_IDLE_S + 1)
    from frontend import error_log
    from frontend.ui_web import panel_api

    monkeypatch.setattr(panel_api, "mark_crash_reload", lambda: None)
    monkeypatch.setattr(error_log, "record_activity", lambda src, msg: None)
    window.native.BeginInvoke = lambda fn: fn()
    memory.install(window)
    window.events.shown.fire()
    memory.trim_now(window, "test")
    memory.trim_if_bloated(window, {"webview_gb": 2.0, "agents": 1})
    window.native.Visible = False
    window.native.VisibleChanged.fire(None, None)
    window.native.Visible = True
    window.native.VisibleChanged.fire(None, None)
    assert calls == []
    assert "TrySuspend" not in __import__("inspect").getsource(memory).replace("never suspend", "")


def test_input_idle_is_a_number():
    assert memory.input_idle_s() >= 0.0


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
    monkeypatch.setattr(memory, "input_idle_s", lambda: 0.0)  # showing it is input
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    window.native.BeginInvoke = queued.append
    window.native.Controls[0].CoreWebView2.Reload = lambda: reloaded.append(1)
    window.native.Visible = False
    assert memory.trim_if_bloated(window, {"webview_gb": 3.0, "agents": 0})
    window.native.Visible = True
    queued.pop()()
    assert reloaded == []


def test_an_open_idle_window_reloads(window, monkeypatch):
    from frontend import error_log
    from frontend.ui_web import panel_api

    queued, reloaded, activity = [], [], []
    monkeypatch.setitem(__import__("sys").modules, "System", SimpleNamespace(Action=lambda fn: fn))
    monkeypatch.setattr(memory, "_last_trim", {"at": -memory.TRIM_EVERY_S})
    monkeypatch.setattr(memory, "input_idle_s", lambda: memory.OPEN_IDLE_S + 1)
    monkeypatch.setattr(panel_api, "mark_crash_reload", lambda: None)
    monkeypatch.setattr(error_log, "record_activity", lambda src, msg: activity.append(msg))
    window.native.BeginInvoke = queued.append
    window.native.Controls[0].CoreWebView2.Reload = lambda: reloaded.append(1)
    window.native.Visible = True
    assert memory.trim_if_bloated(window, {"webview_gb": 2.7, "agents": 0})
    queued.pop()()
    assert reloaded == [1] and "nobody used the window" in activity[0]


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
