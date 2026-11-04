"""Scripts nobody reads the result of never wait on a busy page (40 seats: a 9.2 s freeze)."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from frontend.ui_web import ui_dispatch as dispatch
from frontend.ui_web import webview_recover as recover


class _Generic:
    """Stands in for .NET generic delegate types: Action[Task[String]](fn) -> fn."""

    def __getitem__(self, _item):
        return self

    def __call__(self, fn=None):
        return fn


class _Pending:
    def __init__(self, page, script):
        self.page, self.script = page, script

    def ContinueWith(self, cb, *_scheduler):  # noqa: N802 - .NET name
        self.page.callbacks.append(cb)


class _Control:
    """A WebView2 control whose page runs scripts only when the test says so."""

    def __init__(self):
        self.posted: list[str] = []
        self.callbacks: list = []

    def BeginInvoke(self, action):  # noqa: N802
        action()  # the UI thread takes it now; the page runs it later

    def ExecuteScriptAsync(self, script):  # noqa: N802
        self.posted.append(script)
        return _Pending(self, script)

    def answer_all(self):
        for cb in self.callbacks:
            cb(None)
        self.callbacks.clear()


@pytest.fixture
def window(monkeypatch):
    generic = _Generic()
    monkeypatch.setattr(recover, "_net_types", lambda: (generic, generic, object, generic, generic))
    ctrl = _Control()
    browser = SimpleNamespace(webview=ctrl, syncContextTaskScheduler=object())
    calls: list[str] = []
    win = SimpleNamespace(native=SimpleNamespace(browser=browser), title="main",
                          evaluate_js=lambda js: calls.append(js))
    return win, ctrl, calls


def test_a_busy_page_holds_the_caller_at_most_the_short_wait(window):
    win, ctrl, _ = window
    t0 = time.monotonic()
    assert recover.post_js(win, "r(1)", wait_s=0.2)
    took = time.monotonic() - t0
    assert ctrl.posted == ["r(1)"]
    assert 0.15 <= took < 1.0  # was: until the page answered (9.2 s in the 40-seat run)


def test_a_page_that_answers_frees_the_caller_at_once(window, monkeypatch):
    win, ctrl, _ = window
    monkeypatch.setattr(ctrl, "BeginInvoke", lambda action: (action(), ctrl.answer_all()))
    t0 = time.monotonic()
    assert recover.post_js(win, "r(1)", wait_s=5.0)
    assert time.monotonic() - t0 < 0.5


def test_no_webview2_control_falls_back_and_a_dead_browser_posts_nothing(window):
    win, ctrl, _ = window
    assert recover.post_js(SimpleNamespace(), "x") is False
    win.native.browser._ducky_dead = True
    assert recover.post_js(win, "x") is True and ctrl.posted == []


@pytest.fixture
def channels(monkeypatch):
    monkeypatch.setattr(dispatch, "_channels", {})
    monkeypatch.setattr(dispatch, "_ensure_watchdog", lambda: None)
    monkeypatch.setattr(dispatch, "_label_for", lambda w: "main")


def test_the_dispatch_worker_posts_in_order_and_never_waits_for_results(window, channels, monkeypatch):
    win, ctrl, calls = window
    monkeypatch.setattr(recover, "POST_WAIT_S", 0.05)
    monkeypatch.setattr(recover.post_js, "__defaults__", (0.05,))
    t0 = time.monotonic()
    for i in range(20):
        dispatch.schedule_evaluate_js(win, f"r({i})")
    dispatch._channels[id(win)].queue.join()
    assert calls == []  # pywebview's blocking evaluate_js was not used
    joined = "".join(ctrl.posted)
    assert [joined.index(f"r({i})") for i in range(20)] == sorted(joined.index(f"r({i})") for i in range(20))
    assert len(ctrl.posted) <= 20 and time.monotonic() - t0 < 20 * 0.05 + 1.0


def test_a_hundred_agent_events_in_a_second_make_no_evaluate_js(monkeypatch):
    """Agent events for chats that aren't on screen go over the panel's HTTP event stream,
    coalesced; none becomes an evaluate_js (0 of the allowed 10)."""
    from frontend.ui_web import panel_api, panel_httpd

    published: list[list] = []
    monkeypatch.setattr(panel_httpd, "publish_panel_events", lambda events: published.append(list(events)))
    monkeypatch.setattr(panel_api, "_live_batcher", None)
    scripts: list[str] = []
    monkeypatch.setattr(dispatch, "schedule_evaluate_js", lambda w, js, **kw: scripts.append(js))
    api = panel_api.PanelApi.__new__(panel_api.PanelApi)
    api._agent_push_lock = threading.Lock()
    api._agent_push_pending = []
    api._agent_push_timer = None
    win = SimpleNamespace(evaluate_js=lambda js: scripts.append(js))
    monkeypatch.setattr(api, "_all_windows", lambda: [win], raising=False)
    for i in range(100):
        api._push({"type": "text_delta", "conv_id": f"bg{i % 10}", "run_id": "r", "text": "x"})
    timer = api._agent_push_timer
    if timer is not None:
        timer.cancel()
    api._flush_agent_pushes()
    assert scripts == []
    assert published and sum(len(batch) for batch in published) <= 100
