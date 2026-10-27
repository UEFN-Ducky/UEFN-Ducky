"""WebView2 ProcessFailed recovery — blank pane must Reload, not stay dead."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

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


def test_the_error_says_which_window_died_and_how() -> None:
    # Oct 22 2026: "webview" on 13 PCs in a week could not tell a dead app window from a
    # website that crashed in the browser pane.
    assert wr.error_source("main:RenderProcessExited") == "webview_render_exit"
    assert wr.error_source("main:0") == "webview_browser_exit"
    assert wr.error_source("pane:b3:1") == "webview_pane_render_exit"
    assert wr.error_source("pane:b3:renderprocessunresponsive") == "webview_pane_hung"
    assert wr.error_source("focus:Plan: Streak:1") == "webview_window_render_exit"
    assert wr.error_source("main") == wr.error_source("") == "webview"


# ── A dead browser process gets a new WebView2 control ───────────────────────


def test_a_dead_browser_asks_for_a_new_control_instead_of_reloading(monkeypatch) -> None:
    """Oct 22 + 23 2026: the browser died out of memory, Reload did nothing, and the
    window stayed black until Ducky was killed."""
    monkeypatch.setattr(wr, "_last_recover", {})
    monkeypatch.setattr(wr, "_attached", set())
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr("frontend.error_log.record_error", lambda s, m: errors.append((s, m)))
    core = _Core()
    rebuilds: list[str] = []
    handler = wr.attach_process_failed(core, label="main", on_browser_exit=rebuilds.append)
    handler(None, SimpleNamespace(ProcessFailedKind="BrowserProcessExited"))
    assert rebuilds == ["main:browserprocessexited"] and core.reloads == 0
    assert errors[0][0] == "webview_browser_exit"
    # A renderer crash still reloads in place.
    handler(None, SimpleNamespace(ProcessFailedKind=1))
    assert core.reloads == 1 and rebuilds == ["main:browserprocessexited"]


def test_a_crash_says_how_the_process_ended(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    monkeypatch.setattr(wr, "_attached", set())
    errors: list[tuple[str, str]] = []
    monkeypatch.setattr("frontend.error_log.record_error", lambda s, m: errors.append((s, m)))
    handler = wr.attach_process_failed(_Core(), label="main", on_browser_exit=lambda _r: None)
    handler(None, SimpleNamespace(ProcessFailedKind=0, ExitCode=-536870904))  # 0xE0000008
    assert errors[-1][0] == "webview_browser_exit_oom" and "exit oom" in errors[-1][1]
    assert wr.exit_reason(SimpleNamespace(ExitCode=0x8000FFFF)) == "x8000ffff"
    assert wr.exit_reason(SimpleNamespace(ExitCode=0x40010004)) == "killed"
    assert wr.exit_reason(SimpleNamespace()) == ""


def test_a_pane_without_a_rebuild_keeps_the_old_reload(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_last_recover", {})
    monkeypatch.setattr(wr, "_attached", set())
    core = _Core()
    hidden: list[str] = []
    handler = wr.attach_process_failed(core, label="pane:x", on_fail=hidden.append)
    handler(None, SimpleNamespace(ProcessFailedKind=0))
    assert hidden == ["pane:x:0"] and core.reloads == 1


def test_forgetting_a_replaced_core_lets_its_id_be_hooked_again(monkeypatch) -> None:
    monkeypatch.setattr(wr, "_attached", set())
    core = _Core()
    handler = wr.attach_process_failed(core, label="main")
    assert wr.attach_process_failed(core, label="main") is None
    wr.forget_process_failed(handler)
    assert wr.attach_process_failed(core, label="main") is not None


# ── After a crash the page reopens the chats it had ──────────────────────────


def _marking(monkeypatch) -> list[str]:
    monkeypatch.setattr(wr, "_last_recover", {})
    monkeypatch.setattr(wr, "_attached", set())
    monkeypatch.setattr(wr, "_unresponsive", {})
    order: list[str] = []
    monkeypatch.setattr(wr, "_mark_crash_reload", lambda: order.append("mark"))
    return order


def test_a_dead_process_marks_the_tabs_before_the_reload(monkeypatch) -> None:
    order = _marking(monkeypatch)
    core = _Core()
    core.Reload = lambda: order.append("reload")
    handler = wr.attach_process_failed(core, label="pane:x")
    handler(None, SimpleNamespace(ProcessFailedKind=0))
    assert order == ["mark", "reload"]


def test_a_dead_browser_marks_the_tabs_before_the_new_control(monkeypatch) -> None:
    order = _marking(monkeypatch)
    monkeypatch.setattr("frontend.error_log.record_error", lambda _s, _m: None)
    handler = wr.attach_process_failed(_Core(), label="main", on_browser_exit=lambda _r: order.append("rebuild"))
    handler(None, SimpleNamespace(ProcessFailedKind="BrowserProcessExited"))
    assert order == ["mark", "rebuild"]


def test_a_reload_that_is_not_a_crash_marks_nothing(monkeypatch) -> None:
    order = _marking(monkeypatch)
    core = _Core()
    assert wr.recover_core_webview(core, reason="gpu")["action"] == "reload"
    wr._last_recover.clear()
    wr.recover_core_webview(core, reason="main:renderprocessunresponsive")  # hung a minute: alive
    assert core.reloads == 2 and order == []


class _Form:
    def __init__(self, label: str) -> None:
        self.label = label
        self.IsDisposed = False


@pytest.fixture
def rebuild_env(monkeypatch):
    """The rebuild queue with timers, the UI thread and the control swap faked."""
    timers: list[tuple[float, object, tuple]] = []
    built: list[tuple[str, int]] = []
    log: list[str] = []
    monkeypatch.setattr(wr, "_rebuild_queue", [])
    monkeypatch.setattr(wr, "_rebuild_busy", False)
    monkeypatch.setattr(wr, "_later", lambda delay, fn, *args: timers.append((delay, fn, args)))
    monkeypatch.setattr(wr, "_invoke_on_form", lambda _form, fn: fn())
    monkeypatch.setattr(wr, "rebuild_browser", lambda form, attempt=0: built.append((form.label, attempt)))
    monkeypatch.setattr(wr, "_form_label", lambda form: form.label)
    monkeypatch.setattr("frontend.error_log.record_activity", lambda _s, m: log.append(m))
    monkeypatch.setattr("frontend.error_log.record_error", lambda _s, m: log.append(m))

    def run_timer() -> tuple[float, object, tuple]:
        delay, fn, args = timers.pop(0)
        fn(*args)
        return delay, fn, args

    return SimpleNamespace(timers=timers, built=built, log=log, run=run_timer)


def test_windows_are_rebuilt_one_at_a_time_main_first(rebuild_env) -> None:
    env = rebuild_env
    focus, main = _Form("window:Plan"), _Form("main")
    wr.request_browser_rebuild(focus)
    wr.request_browser_rebuild(main)
    wr.request_browser_rebuild(main)  # every window's core reports the same death
    assert [d for d, _f, _a in env.timers] == [wr._REBUILD_DELAY_S]
    env.run()
    assert env.built == [("main", 1)]
    assert env.timers[0][0] == wr._REBUILD_INIT_TIMEOUT_S  # the main control is booting
    wr._rebuild_settled(main, 1, True)  # CoreWebView2InitializationCompleted, IsSuccess
    assert env.log == ["WebView2 started again after its browser stopped (main)"]
    env.timers.pop(0)  # the main try's timeout
    env.run()  # the next window
    assert env.built == [("main", 1), ("window:Plan", 1)]
    wr._rebuild_settled(focus, 1, True)
    assert wr._rebuild_busy is False and wr._rebuild_queue == []
    assert [d for d, _f, _a in env.timers] == [wr._REBUILD_INIT_TIMEOUT_S]  # nothing else queued


def test_a_dead_browser_answers_evaluate_js_without_invoking(rebuild_env) -> None:
    # Oct 24 2026 (1.2.427): 0x80000003 while pywebview injected its JS API into a dying control.
    class _Chrome:
        def __init__(self) -> None:
            self.webview = SimpleNamespace(Invoke=lambda _fn: (_ for _ in ()).throw(AssertionError("invoked")))

    wr.install_evaluate_js_release(_Chrome)
    main = _Form("main")
    main.browser = _Chrome()
    wr.request_browser_rebuild(main)
    assert main.browser.evaluate_js("window.x", False) is None
    assert _Chrome().__dict__.get("_ducky_dead") is None  # the new control is not marked


def test_a_failed_start_is_retried_then_reported(rebuild_env) -> None:
    env = rebuild_env
    main = _Form("main")
    wr.request_browser_rebuild(main)
    for attempt in range(1, wr._REBUILD_TRIES + 1):
        env.run()  # start the try
        assert env.built[-1] == ("main", attempt)
        _delay, fn, args = env.timers.pop(0)
        fn(*args)  # init never completed within the timeout
        wr._rebuild_settled(main, attempt, True)  # a late success of that try is ignored
    assert env.log == ["WebView2 could not start again (main); restart Ducky"]
    assert wr._rebuild_queue == []


def test_a_closed_window_is_dropped(rebuild_env) -> None:
    env = rebuild_env
    form = _Form("window:Gone")
    wr.request_browser_rebuild(form)
    form.IsDisposed = True
    env.run()
    assert env.built == [] and wr._rebuild_queue == [] and env.log == []


def test_rebuild_swaps_in_a_new_control_and_frees_waiting_scripts(monkeypatch) -> None:
    import sys
    import threading
    import types

    import webview.platforms

    made: list[object] = []

    class _Control:
        def __init__(self) -> None:
            self.disposed = False
            self.at_back = False
            self.CoreWebView2InitializationCompleted = _Evt()

        def Dispose(self) -> None:
            self.disposed = True

        def SendToBack(self) -> None:
            self.at_back = True

    class _EdgeChrome:
        def __init__(self, form, window, cache_dir) -> None:
            self.args = (form, window, cache_dir)
            self.webview = _Control()
            made.append(self)

    fake_edge = types.SimpleNamespace(EdgeChrome=_EdgeChrome)
    fake_forms = types.SimpleNamespace(cache_dir="C:/profile")
    monkeypatch.setitem(sys.modules, "webview.platforms.edgechromium", fake_edge)
    monkeypatch.setitem(sys.modules, "webview.platforms.winforms", fake_forms)
    monkeypatch.setattr(webview.platforms, "edgechromium", fake_edge, raising=False)
    monkeypatch.setattr(webview.platforms, "winforms", fake_forms, raising=False)
    settled: list[tuple] = []
    monkeypatch.setattr(wr, "_rebuild_settled", lambda form, attempt, ok: settled.append((attempt, ok)))
    monkeypatch.setattr(wr, "_attached", {41})

    removed: list[object] = []
    old_control = _Control()
    stuck = threading.Semaphore(0)
    old = SimpleNamespace(
        _ducky_js_waiters={stuck},
        _ducky_process_failed_handler=SimpleNamespace(core_key=41),
    )
    form = SimpleNamespace(
        browser=old,
        webview=old_control,
        pywebview_window=SimpleNamespace(title="UEFN Ducky"),
        Controls=SimpleNamespace(Remove=removed.append),
    )
    browser = wr.rebuild_browser(form, attempt=2)
    assert stuck.acquire(timeout=0)  # the dead browser's evaluate_js returned
    assert 41 not in wr._attached
    assert removed == [old_control] and old_control.disposed
    assert made == [browser] and browser.args == (form, form.pywebview_window, "C:/profile")
    assert form.browser is browser and form.webview is browser.webview and browser.webview.at_back
    (ready,) = browser.webview.CoreWebView2InitializationCompleted.subs
    ready(None, SimpleNamespace(IsSuccess=True))
    assert settled == [(2, True)]


# ── evaluate_js never waits forever ──────────────────────────────────────────


class _Generic:
    """Stands in for a .NET generic type: Func[Object](fn) is fn."""

    def __getitem__(self, _item):
        return lambda fn: fn


class _Task:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self._result, self._error = result, error

    @property
    def Result(self):
        if self._error is not None:
            raise self._error
        return self._result


def _edge_with(task: _Task | None, monkeypatch):
    """An EdgeChrome-like object whose script task is ``task`` (None: never finishes)."""
    g = _Generic()
    monkeypatch.setattr(wr, "_net_types", lambda: (g, g, None, None, g))

    class _Pending:
        def ContinueWith(self, callback, _scheduler):
            if task is not None:
                callback(task)

    class _Edge:
        syncContextTaskScheduler = None

        def __init__(self) -> None:
            self.webview = SimpleNamespace(
                Invoke=lambda fn: fn(), ExecuteScriptAsync=lambda _script: _Pending()
            )

    wr.install_evaluate_js_release(_Edge)
    return _Edge()


def test_evaluate_js_reads_the_result_like_pywebview(monkeypatch) -> None:
    import json

    edge = _edge_with(_Task(json.dumps(json.dumps({"a": 1}))), monkeypatch)
    assert edge.evaluate_js("x", True) == {"a": 1}
    assert edge.evaluate_js("x", False) == json.dumps({"a": 1})


def test_evaluate_js_on_a_dead_browser_answers_none(monkeypatch) -> None:
    edge = _edge_with(_Task(error=RuntimeError("browser gone")), monkeypatch)
    assert edge.evaluate_js("x", True) is None
    assert edge._ducky_js_waiters == set()


def test_a_waiting_evaluate_js_is_freed_by_the_rebuild(monkeypatch) -> None:
    import threading

    edge = _edge_with(None, monkeypatch)
    out: list[object] = []
    t = threading.Thread(target=lambda: out.append(edge.evaluate_js("x", True)), daemon=True)
    t.start()
    for _ in range(200):
        if edge.__dict__.get("_ducky_js_waiters"):
            break
        t.join(0.01)
    wr.release_js_waiters(edge)
    t.join(2)
    assert not t.is_alive() and out == [None]
