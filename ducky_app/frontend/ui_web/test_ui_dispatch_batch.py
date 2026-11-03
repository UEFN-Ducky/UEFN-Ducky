"""Replies queued behind a slow evaluate_js go to the window together, in order."""

from __future__ import annotations

import threading
import weakref

import pytest

from frontend.ui_web import ui_dispatch as dispatch


@pytest.fixture
def channels(monkeypatch):
    monkeypatch.setattr(dispatch, "_channels", {})
    monkeypatch.setattr(dispatch, "_closed_windows", weakref.WeakValueDictionary())
    monkeypatch.setattr(dispatch, "_ensure_watchdog", lambda: None)
    monkeypatch.setattr(dispatch, "_label_for", lambda window: "test")
    yield
    for channel in list(dispatch._channels.values()):
        channel.queue.put(("stop", None))
        channel.queue.join()


class SlowFirstWindow:
    def __init__(self):
        self.calls: list[str] = []
        self.entered, self.release = threading.Event(), threading.Event()

    def evaluate_js(self, js):
        self.calls.append(js)
        if len(self.calls) == 1:
            self.entered.set()
            assert self.release.wait(5)


def test_a_burst_behind_a_slow_call_is_one_evaluate_js_in_order(channels):
    win = SlowFirstWindow()
    dispatch.schedule_evaluate_js(win, "r(0)")
    assert win.entered.wait(2)
    for i in range(1, 51):
        dispatch.schedule_evaluate_js(win, f"r({i})", source="browser_pane_set_bounds")
    win.release.set()
    dispatch._channels[id(win)].queue.join()
    assert len(win.calls) <= 3
    joined = "".join(win.calls)
    positions = [joined.index(f"r({i})") for i in range(51)]
    assert positions == sorted(positions)
    # Each reply sits in its own try, so one that throws cannot drop the rest.
    assert joined.count("catch(e)") == 50


def test_a_call_queued_mid_burst_runs_after_the_scripts_before_it(channels):
    win = SlowFirstWindow()
    order: list[str] = []
    dispatch.schedule_evaluate_js(win, "a")
    assert win.entered.wait(2)
    ch = dispatch._channels[id(win)]
    dispatch.schedule_evaluate_js(win, "b")
    ch.submit("call", lambda: order.append(f"call after {len(win.calls)} evaluate_js"))
    dispatch.schedule_evaluate_js(win, "c")
    win.release.set()
    ch.queue.join()
    assert order == ["call after 2 evaluate_js"]
    assert win.calls[-1] == "c"


def test_a_burst_still_traces_one_row_per_reply(channels, monkeypatch):
    from frontend import perf_trace

    rows: list[dict] = []
    monkeypatch.setattr(perf_trace, "trace", lambda kind, name, ms, **meta: rows.append(meta))
    win = SlowFirstWindow()
    dispatch.schedule_evaluate_js(win, "x", source="first")
    assert win.entered.wait(2)
    for _ in range(10):
        dispatch.schedule_evaluate_js(win, "y", source="browser_pane_set_bounds")
    win.release.set()
    dispatch._channels[id(win)].queue.join()
    assert [r["source"] for r in rows].count("browser_pane_set_bounds") == 10
