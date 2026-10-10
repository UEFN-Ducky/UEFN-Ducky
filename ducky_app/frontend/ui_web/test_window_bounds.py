"""Window position saving: one pending save per drag, not a thread per mouse move."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web import window_bounds as wb


class _Event:
    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self) -> None:
        for handler in list(self.handlers):
            handler()


def test_a_drag_keeps_one_pending_save(monkeypatch) -> None:
    """WinForms sends moved/resized per mouse move; each one used to start (and
    cancel) its own Timer thread, ~100 thread starts a second while dragging."""
    clock = [100.0]
    timers: list = []

    class FakeTimer:
        def __init__(self, interval, function, args=None, kwargs=None) -> None:
            self.interval = interval
            self.function = function
            self.started_at: float | None = None
            self.cancelled = False
            self.ran = False
            self.daemon = False
            timers.append(self)

        def start(self) -> None:
            self.started_at = clock[0]

        def cancel(self) -> None:
            self.cancelled = True

    saved: list[float] = []
    monkeypatch.setattr(wb.threading, "Timer", FakeTimer)
    monkeypatch.setattr(wb, "time", SimpleNamespace(monotonic=lambda: clock[0]), raising=False)
    monkeypatch.setattr(wb, "save_bounds", lambda *args: saved.append(clock[0]))
    window = SimpleNamespace(
        x=10, y=20, width=800, height=600,
        events=SimpleNamespace(moved=_Event(), resized=_Event(), closing=_Event()),
    )
    wb.track(window, "main")
    for n in range(200):
        (window.events.moved if n % 2 else window.events.resized).fire()
        clock[0] += 0.005
    last_event = clock[0] - 0.005

    while True:  # run every live timer when it falls due
        due = [t for t in timers if t.started_at is not None and not t.cancelled and not t.ran]
        if not due:
            break
        timer = min(due, key=lambda t: t.started_at + t.interval)
        clock[0] = max(clock[0], timer.started_at + timer.interval)
        timer.ran = True
        timer.function()

    assert len(timers) <= 2, f"{len(timers)} timer threads for one drag"
    assert len(saved) == 1
    assert abs(saved[0] - (last_event + 0.8)) < 0.06
