"""Finished UI work and closed windows must not keep payloads alive."""

from __future__ import annotations

import gc
import threading
import weakref
from types import SimpleNamespace

import pytest

from frontend.ui_web import ui_dispatch as dispatch


class Script(str):
    pass


class Window:
    def evaluate_js(self, js):
        pass


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


def test_idle_worker_releases_completed_script(channels):
    window = Window()
    script = Script("x" * 1_000_000)
    reference = weakref.ref(script)
    dispatch.schedule_evaluate_js(window, script)
    channel = dispatch._channels[id(window)]
    del script
    channel.queue.join()
    gc.collect()
    assert reference() is None


def test_idle_ops_worker_releases_completed_closure(channels):
    owner = Window()
    reference = weakref.ref(owner)
    dispatch.schedule_call(lambda captured=owner: captured)
    del owner
    dispatch._channels[0].queue.join()
    gc.collect()
    assert reference() is None


def test_closed_stalled_window_releases_pending_scripts(channels):
    entered, release = threading.Event(), threading.Event()

    class StalledWindow(Window):
        def evaluate_js(self, js):
            entered.set()
            assert release.wait(5)

    window = StalledWindow()
    dispatch.schedule_evaluate_js(window, "in-flight")
    channel = dispatch._channels[id(window)]
    assert entered.wait(2)
    script = Script("queued" * 100_000)
    reference = weakref.ref(script)
    dispatch.schedule_evaluate_js(window, script)
    del script
    try:
        dispatch.drop_window(window)
        gc.collect()
        assert reference() is None
    finally:
        release.set()
        channel.queue.join()


def test_late_push_does_not_recreate_closed_window_worker(channels):
    window = Window()
    dispatch.schedule_evaluate_js(window, "before")
    dispatch._channels[id(window)].queue.join()
    dispatch.drop_window(window)
    dispatch.schedule_evaluate_js(window, "after")
    assert id(window) not in dispatch._channels


def test_other_window_remains_ordered_when_one_closes(channels):
    seen = []

    class RecordingWindow(Window):
        def evaluate_js(self, js):
            seen.append(js)

    closed, live = Window(), RecordingWindow()
    dispatch.drop_window(closed)
    for script in ("first", "second", "third"):
        dispatch.schedule_evaluate_js(live, script)
    dispatch._channels[id(live)].queue.join()
    assert seen == ["first", "second", "third"]


def test_repeated_close_releases_windows_and_rejects_stale_producers(channels):
    references = []
    for _ in range(30):
        window = Window()
        references.append(weakref.ref(window))
        dispatch.schedule_evaluate_js(window, "work")
        channel = dispatch._channels[id(window)]
        channel.queue.join()
        dispatch.drop_window(window)
        dispatch.drop_window(window)
        channel.queue.join()
        # A producer may have looked up its channel just before destruction.
        channel.submit("js", (window, "late", 0, 0))
        assert channel.queue.empty()
    del window
    gc.collect()
    assert all(reference() is None for reference in references)
    assert not dispatch._closed_windows
    assert not dispatch._channels


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for handler in self.handlers:
            handler()


def test_native_focus_close_cleans_worker_and_returns_tabs(channels, monkeypatch):
    from frontend.ui_web import focus_windows as focus

    window = Window()
    window.events = SimpleNamespace(closing=Event(), closed=Event(), shown=Event())
    group = SimpleNamespace(window=window, tabs={"chat:a": "A"}, wid="focus-test")
    returned, dropped = [], []
    monkeypatch.setattr(focus, "_focus_groups", [group])
    monkeypatch.setattr(focus, "_closing_windows", set())
    monkeypatch.setattr(focus, "_return_tabs_to_main", returned.append)
    monkeypatch.setattr(focus, "_drop_registry_window", dropped.append)
    focus._wire_group_window(group)
    dispatch.schedule_evaluate_js(window, "before")
    dispatch._channels[id(window)].queue.join()
    window.events.closing.fire()
    window.events.closed.fire()
    assert returned == [{"chat:a": "A"}]
    assert dropped == ["focus-test"]
    assert id(window) not in dispatch._channels
    assert id(window) not in focus._closing_windows
