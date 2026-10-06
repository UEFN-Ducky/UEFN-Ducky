"""WebView2 ProcessFailed recovery — blank pane must Reload, not stay dead."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web import webview_recover as wr


class _Evt:
    def __init__(self) -> None:
        self.subs: list[object] = []

    def __iadd__(self, fn: object) -> _Evt:
        self.subs.append(fn)
        return self


class _Core:
    def __init__(self) -> None:
        self.reloads = 0
        self.navigated: list[str] = []
        self.Source = "http://127.0.0.1:4199/"
        self._handlers: list[object] = []
        self.ProcessFailed = _Evt()

    def Reload(self) -> None:
        self.reloads += 1

    def Navigate(self, src: str) -> None:
        self.navigated.append(src)


class _FailingReload(_Core):
    def Reload(self) -> None:
        raise RuntimeError("dead")


def test_reload_on_recover(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    core = _Core()
    assert wr.recover_core_webview(core, reason="gpu")["action"] == "reload"
    assert core.reloads == 1


def test_navigate_when_reload_raises(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    core = _FailingReload()
    assert wr.recover_core_webview(core, reason="browser")["action"] == "navigate"
    assert core.navigated == [core.Source]


def test_debounce_second_call(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    core = _Core()
    wr.recover_core_webview(core, reason="a")
    assert wr.recover_core_webview(core, reason="b")["action"] == "debounced"
    assert core.reloads == 1


def test_gpu_exit_does_not_reload(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    monkeypatch.setattr(wr, "_attached", set())
    core = _Core()
    hidden: list[str] = []
    handler = wr.attach_process_failed(core, label="pane:x", on_fail=hidden.append)
    assert handler is not None
    handler(None, SimpleNamespace(ProcessFailedKind="GpuProcessExited"))
    handler(None, SimpleNamespace(ProcessFailedKind=6))
    handler(None, SimpleNamespace(ProcessFailedKind="UtilityProcessExited"))
    assert hidden == []
    assert core.reloads == 0


def test_attach_hides_then_reloads_on_renderer_death(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    monkeypatch.setattr(wr, "_attached", set())
    core = _Core()
    hidden: list[str] = []
    handler = wr.attach_process_failed(core, label="pane:x", on_fail=hidden.append)
    assert handler is not None
    core._handlers.append(handler)
    handler(None, SimpleNamespace(ProcessFailedKind="RenderProcessExited"))
    assert hidden == ["pane:x:renderprocessexited"]
    assert core.reloads == 1
    assert wr.attach_process_failed(core, label="pane:x") is None


def test_should_recover_kinds() -> None:
    assert wr.should_recover_process_fail("RenderProcessExited")
    assert wr.should_recover_process_fail(1)
    assert wr.should_recover_process_fail("BrowserProcessExited")
    assert not wr.should_recover_process_fail("GpuProcessExited")
    assert not wr.should_recover_process_fail(6)
    assert not wr.should_recover_process_fail("UtilityProcessExited")
    assert not wr.should_recover_process_fail("")
    # Busy is not dead: an unresponsive page is waited for, not reloaded.
    assert not wr.should_recover_process_fail("RenderProcessUnresponsive")
    assert not wr.should_recover_process_fail(2)
    assert wr.is_unresponsive_kind("RenderProcessUnresponsive")
    assert wr.is_unresponsive_kind("2")
    assert not wr.is_unresponsive_kind("RenderProcessExited")


def _unresponsive_setup(monkeypatch):
    monkeypatch.setattr(wr, "_last_recover", {})
    monkeypatch.setattr(wr, "_attached", set())
    monkeypatch.setattr(wr, "_unresponsive", {})
    logged: list[str] = []
    monkeypatch.setattr("frontend.error_log.record_activity", lambda _s, m: logged.append(m))
    monkeypatch.setattr("frontend.error_log.record_error", lambda _s, m: logged.append(m))
    clock = [1000.0]
    monkeypatch.setattr(wr.time, "monotonic", lambda: clock[0])
    core = _Core()
    hidden: list[str] = []
    handler = wr.attach_process_failed(core, label="main", on_fail=hidden.append)
    assert handler is not None
    return core, handler, hidden, clock, logged


def _busy(handler, clock, seconds: float) -> None:
    clock[0] += seconds
    handler(None, SimpleNamespace(ProcessFailedKind="RenderProcessUnresponsive"))


def test_a_busy_page_is_not_reloaded(monkeypatch) -> None:
    """The freeze-then-reload-everything bug: a page busy for under a minute
    (long script, PC busy with UEFN) recovers on its own and keeps its state."""
    core, handler, hidden, clock, logged = _unresponsive_setup(monkeypatch)
    _busy(handler, clock, 0)
    for _ in range(11):  # WebView2 repeats the event every few seconds: 55 s busy
        _busy(handler, clock, 5)
    assert core.reloads == 0 and hidden == []
    assert logged == ["WebView2 page busy (main); waiting for it instead of reloading"]


def test_a_page_hung_for_a_minute_is_reloaded_once(monkeypatch) -> None:
    core, handler, hidden, clock, _logged = _unresponsive_setup(monkeypatch)
    _busy(handler, clock, 0)
    for _ in range(12):  # 60 s without answering
        _busy(handler, clock, 5)
    assert core.reloads == 1 and hidden == ["main:renderprocessunresponsive"]
    _busy(handler, clock, 5)  # the reloaded page starts a fresh wait
    assert core.reloads == 1


def test_a_page_that_answered_in_between_starts_a_new_wait(monkeypatch) -> None:
    core, handler, _hidden, clock, logged = _unresponsive_setup(monkeypatch)
    _busy(handler, clock, 0)
    for _ in range(10):  # busy 50 s
        _busy(handler, clock, 5)
    _busy(handler, clock, 30)  # quiet 30 s: it answered, this is a new freeze
    for _ in range(10):  # busy another 50 s
        _busy(handler, clock, 5)
    assert core.reloads == 0
    assert len(logged) == 2


def test_a_crash_after_busy_still_reloads_at_once(monkeypatch) -> None:
    core, handler, hidden, clock, _logged = _unresponsive_setup(monkeypatch)
    _busy(handler, clock, 0)
    clock[0] += 4
    handler(None, SimpleNamespace(ProcessFailedKind="RenderProcessExited"))
    assert core.reloads == 1 and hidden == ["main:renderprocessexited"]
    assert wr._unresponsive == {}


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
