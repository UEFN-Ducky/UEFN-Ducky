"""``--backend`` lifecycle: stdin's end and ``bye``, the launcher dying, a second instance."""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
import time
import types

import pytest

from frontend.native_host import headless, lifecycle


def _lines(*messages: object) -> io.BytesIO:
    raw = b"".join((m if isinstance(m, bytes) else json.dumps(m).encode("utf-8")) + b"\n" for m in messages)
    return io.BytesIO(raw)


# --- stdin: EOF and bye -----------------------------------------------------------------


def test_stdin_eof_ends_the_session_after_the_calls() -> None:
    calls: list[dict] = []
    reason = lifecycle.serve(
        _lines({"t": "call", "id": "1", "name": "a", "args": []}, b"", b"not json", [1, 2],
               {"t": "call", "id": "2", "name": "b", "args": [], "extra": True}),
        on_call=calls.append,
    )
    assert reason == "stdin_eof"
    assert [c["id"] for c in calls] == ["1", "2"]


def test_bye_ends_the_session_and_later_lines_are_not_read() -> None:
    calls: list[dict] = []
    reason = lifecycle.serve(
        _lines({"t": "bye", "reason": "window_closed"}, {"t": "call", "id": "late", "name": "a"}),
        on_call=calls.append,
    )
    assert reason == "bye:window_closed"
    assert calls == []
    assert lifecycle.serve(_lines({"t": "bye"}), on_call=calls.append) == "bye"


def test_an_oversize_line_is_skipped(monkeypatch) -> None:
    monkeypatch.setattr(lifecycle, "MAX_LINE_BYTES", 64)
    calls: list[dict] = []
    big = json.dumps({"t": "call", "id": "big", "name": "a", "args": ["x" * 500]}).encode("utf-8")
    reason = lifecycle.serve(_lines(big, {"t": "call", "id": "ok", "name": "a"}), on_call=calls.append)
    assert reason == "stdin_eof"
    assert [c["id"] for c in calls] == ["ok"]


def test_shutdown_runs_the_app_exit_once(monkeypatch) -> None:
    from frontend.ui_web import shutdown

    exits: list[dict] = []
    monkeypatch.setattr(shutdown, "request_app_exit", lambda **kw: exits.append(kw))
    api = object()
    ending = lifecycle.Shutdown(api, wait_s=0)
    assert ending.run("stdin_eof") is True
    assert ending.run("parent_exited") is False
    assert ending.reason == "stdin_eof"
    assert exits == [{"api": api, "tk_root": None, "window_holder": {}}]


def test_shutdown_marks_the_run_closed_when_the_exit_path_fails(monkeypatch) -> None:
    from frontend import usage_stats

    closed: list[int] = []
    monkeypatch.setattr(usage_stats, "app_closing", lambda: closed.append(1))

    def broken(**_kw):
        raise RuntimeError("no exit")

    assert lifecycle.Shutdown(object(), exit_fn=broken, wait_s=0).run("app") is True
    assert closed == [1]
    # The normal path marks it itself (abort_and_quit): never twice.
    assert lifecycle.Shutdown(object(), exit_fn=lambda **_kw: None, wait_s=0).run("app") is True
    assert closed == [1]


# --- the whole backend, in process, with the panel API and server stubbed ----------------


class _Api:
    def bind_window(self, window, *, on_hide, on_exit):
        self.on_exit = on_exit

    def ping(self, n):
        return n + 1


@pytest.fixture
def stubbed_backend(monkeypatch):
    from frontend import app_versions, notifications
    from frontend.ui_web import webview_app

    fake_workers = types.ModuleType("backend.workspace.diff_workers")
    fake_workers.enable = lambda: None
    monkeypatch.setitem(sys.modules, "backend.workspace.diff_workers", fake_workers)
    from backend.store import db as store_db

    monkeypatch.setattr(store_db, "allow_rebuild", lambda: None)
    monkeypatch.setenv("UEFN_DUCKY_PANEL_PORT", "4568")
    monkeypatch.setattr(app_versions, "mark_ready", lambda version: None)
    monkeypatch.setattr(notifications, "bind_desktop", lambda toast, open_target: None)
    monkeypatch.setattr(webview_app, "make_background_work", lambda: (lambda: None))
    monkeypatch.setattr(webview_app, "boot_panel_api", _Api)
    monkeypatch.setattr(webview_app, "install_workspace_services", lambda: None)
    monkeypatch.setattr(webview_app, "webview_profile_dir", lambda: "P:/webview")
    monkeypatch.delenv("UEFN_DUCKY_BACKEND_UI_PORT", raising=False)
    monkeypatch.setattr(
        webview_app, "start_panel_server",
        lambda ui_port=None: ("http://127.0.0.1:4567/", False, "http://127.0.0.1:4567/"),
    )
    monkeypatch.setattr(lifecycle, "watch_parent", lambda pid, on_dead: None)
    ended: list[str] = []

    class _Shutdown(lifecycle.Shutdown):
        def __init__(self, api):
            super().__init__(api, exit_fn=lambda **_: ended.append(self.reason or ""), wait_s=0)

    monkeypatch.setattr(lifecycle, "Shutdown", _Shutdown)
    monkeypatch.setattr(lifecycle, "second_instance", lambda paths, links: (lifecycle.FIRST, None))
    return ended


def _run(stdin: io.BytesIO) -> tuple[int, list[dict]]:
    out = io.BytesIO()
    code = headless.run_backend(["UEFN-Ducky.exe", "--backend"], stdin=stdin, stdout=out)
    return code, [json.loads(line) for line in out.getvalue().splitlines()]


def test_backend_stdin_eof_runs_the_graceful_exit(stubbed_backend) -> None:
    code, lines = _run(io.BytesIO(b""))
    assert code == 0
    assert lines[0]["t"] == "hello" and lines[0]["port"] == 4567
    assert stubbed_backend == ["stdin_eof"]


def test_backend_registers_its_window_so_live_chat_events_reach_the_page(stubbed_backend, monkeypatch) -> None:
    """Nov 6 2026 (1.2.472): with no window registered PanelApi._push dropped every live
    chat event, so every chat in the launcher sat on "Thinking about the next step"."""
    from frontend.ui_web import focus_windows

    monkeypatch.setattr(focus_windows, "_main_window", None)
    _run(io.BytesIO(b""))
    assert [type(w) for w in focus_windows.all_windows()] == [lifecycle.BackendWindow]


def test_backend_without_the_env_var_serves_todays_port(stubbed_backend, monkeypatch) -> None:
    from frontend.ui_web import webview_app

    asked: list = []
    monkeypatch.setattr(webview_app, "start_panel_server", lambda ui_port=None: (
        asked.append(ui_port) or ("http://127.0.0.1:4567/", False, "http://127.0.0.1:4567/")))
    _code, lines = _run(io.BytesIO(b""))
    assert asked == [None]
    assert lines[0]["port"] == 4567 and lines[0]["origin"] == "http://127.0.0.1:4567"
    assert "public_port" not in lines[0]


def test_backend_env_var_binds_an_internal_port_and_hello_reports_it(stubbed_backend, monkeypatch) -> None:
    from frontend.ui_web import panel_httpd, webview_app

    asked: list = []
    monkeypatch.setenv("UEFN_DUCKY_BACKEND_UI_PORT", "0")
    monkeypatch.setattr(panel_httpd, "PANEL_UI_HTTP_PORT", 4567)
    monkeypatch.setattr(panel_httpd, "panel_ui_bound_port", lambda: 51234)
    monkeypatch.setattr(webview_app, "start_panel_server", lambda ui_port=None: (
        asked.append(ui_port) or ("http://127.0.0.1:4567/", False, "http://127.0.0.1:4567/")))
    _code, lines = _run(io.BytesIO(b""))
    assert asked == [0]
    hello = lines[0]
    assert hello["port"] == 51234 and hello["origin"] == "http://127.0.0.1:51234"
    assert hello["public_port"] == 4567
    assert hello["url"] == "http://127.0.0.1:4567/"  # the page loads from the launcher's origin


def test_backend_bye_runs_the_graceful_exit_and_answers_calls_first(stubbed_backend, monkeypatch) -> None:
    done = threading.Event()
    real = headless.dispatch_call

    def _dispatch(api, message):
        try:
            return real(api, message)
        finally:
            done.set()

    monkeypatch.setattr(headless, "dispatch_call", _dispatch)

    class _SlowBye(io.BytesIO):
        def readline(self, size=-1):
            line = super().readline(size)
            if b'"bye"' in line:
                done.wait(5)
                time.sleep(0.05)
            return line

    code, lines = _run(_SlowBye(_lines({"t": "call", "id": "c1", "name": "ping", "args": [1]},
                                       {"t": "bye", "reason": "closed"}).getvalue()))
    assert code == 0
    assert {"t": "result", "id": "c1", "ok": True, "value": 2} in lines
    assert stubbed_backend == ["bye:closed"]


def test_the_apps_own_exit_tells_the_launcher(stubbed_backend, monkeypatch) -> None:
    seen: dict = {}
    real_init = _Api.__init__

    def _keep(self):
        real_init(self)
        seen["api"] = self

    monkeypatch.setattr(_Api, "__init__", _keep)

    class _ExitThenEof(io.BytesIO):
        def readline(self, size=-1):
            seen["api"].on_exit()
            return b""

    code, lines = _run(_ExitThenEof())
    assert code == 0
    assert {"t": "event", "name": "quit", "data": {"reason": "app"}} in lines
    assert stubbed_backend == ["app"]  # the EOF after it does not exit twice


# --- file dialogs go through the launcher ------------------------------------------------


class _FakeLauncher:
    """Answers each ``dialog`` event with ``answer(args)`` as a ``__dialog_result`` call."""

    def __init__(self, answer):
        self.answer = answer
        self.events: list[dict] = []
        self.replies: list[dict] = []
        self.calls: list[dict] = []
        self.inbox: list[bytes] = []
        self.ready = threading.Condition()
        self.dialogs = lifecycle.DialogBroker(self._event, timeout_s=5)
        self.window = lifecycle.BackendWindow(self.dialogs)

    def _event(self, name, data):
        self.events.append({"name": name, "data": data})
        line = json.dumps({"t": "call", "id": data["id"], "name": "__dialog_result",
                           "args": [self.answer(data["args"])]}).encode("utf-8") + b"\n"
        with self.ready:
            self.inbox += [line, b'{"t": "bye"}\n']
            self.ready.notify_all()
        return True

    def serve_one(self, extra: bytes = b"") -> threading.Thread:
        launcher = self

        class _Stdin(io.BytesIO):
            def readline(self, size=-1):
                with launcher.ready:
                    launcher.ready.wait_for(lambda: launcher.inbox, timeout=5)
                    return launcher.inbox.pop(0) if launcher.inbox else b""

        thread = threading.Thread(target=lambda: lifecycle.serve(
            _Stdin(), on_call=self.calls.append, dialogs=self.dialogs, reply=self.replies.append), daemon=True)
        if extra:
            self.inbox.append(extra)
        thread.start()
        return thread


@pytest.mark.parametrize(
    ("kwargs", "answer", "expected", "kind"),
    [
        ({"dialog_type": 10, "allow_multiple": True, "file_types": ("Images (*.png)",)},
         ["C:/a.png", "C:/b.png"], ("C:/a.png", "C:/b.png"), "open"),
        ({"dialog_type": 30, "directory": "C:/out", "save_filename": "x.json"}, "C:/out/x.json",
         ("C:/out/x.json",), "save"),
        ({"dialog_type": 20}, ["C:/folder"], ("C:/folder",), "folder"),
        ({"dialog_type": 10}, None, None, "open"),
    ],
)
def test_a_file_dialog_is_shown_by_the_launcher(kwargs, answer, expected, kind) -> None:
    launcher = _FakeLauncher(lambda args: answer)
    thread = launcher.serve_one()
    assert launcher.window.create_file_dialog(**kwargs) == expected
    args = launcher.events[0]["data"]["args"]
    assert launcher.events[0]["name"] == "dialog"
    assert args["kind"] == kind and args["dialog_type"] == kwargs["dialog_type"]
    assert args["save_filename"] == kwargs.get("save_filename", "")
    assert args["file_types"] == list(kwargs.get("file_types", ()))
    assert launcher.calls == []  # the answer never reaches the normal calls
    assert launcher.replies[0]["value"] is True
    thread.join(5)


def test_a_normal_call_still_reaches_on_call_beside_a_dialog() -> None:
    launcher = _FakeLauncher(lambda args: "C:/a.txt")
    normal = json.dumps({"t": "call", "id": "n1", "name": "ping", "args": [1]}).encode("utf-8") + b"\n"
    thread = launcher.serve_one(extra=normal)
    assert launcher.window.create_file_dialog() == ("C:/a.txt",)
    thread.join(5)
    assert [c["id"] for c in launcher.calls] == ["n1"]


def test_an_unanswered_dialog_times_out_with_none() -> None:
    sent: list = []
    broker = lifecycle.DialogBroker(lambda name, data: sent.append(data) or True, timeout_s=0.05)
    assert lifecycle.BackendWindow(broker).create_file_dialog(dialog_type=10) is None
    assert len(sent) == 1
    assert broker.answer(sent[0]["id"], ["late"]) is False  # a late answer finds nobody


def test_the_launchers_end_cancels_waiting_dialogs() -> None:
    sent: list = []
    broker = lifecycle.DialogBroker(lambda name, data: sent.append(data) or True, timeout_s=30)
    box: dict = {}
    thread = threading.Thread(target=lambda: box.update(v=broker.ask({})), daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert lifecycle.serve(_lines({"t": "bye"}), on_call=lambda m: None, dialogs=broker) == "bye"
    thread.join(5)
    assert not thread.is_alive() and box["v"] is None
    assert broker.ask({}) is None  # closed: no event, no wait
    assert len(sent) == 1


def test_backend_window_other_calls_are_no_ops() -> None:
    window = lifecycle.BackendWindow(lifecycle.DialogBroker(lambda n, d: False))
    assert window.show() is None and window.evaluate_js("1") is None
    window.events.closing += lambda: None
    assert window.create_file_dialog() is None  # event not sent: no wait


# --- the launcher (parent) dying ---------------------------------------------------------


def test_parent_death_calls_on_dead_once() -> None:
    states = iter([True, True, False, False])
    dead = threading.Event()
    calls: list[int] = []

    def _on_dead():
        calls.append(1)
        dead.set()

    thread = lifecycle.watch_parent(4242, _on_dead, alive=lambda: next(states), interval_s=0.01)
    assert thread is not None
    assert dead.wait(2)
    thread.join(2)
    assert calls == [1]


def test_no_parent_no_watch() -> None:
    assert lifecycle.watch_parent(0, lambda: pytest.fail("no parent to lose")) is None
    assert lifecycle.watch_parent(1, lambda: pytest.fail("no parent to lose")) is None


def test_parent_alive_check_follows_a_real_process() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        alive = lifecycle.parent_alive_check(child.pid)
        assert alive() is True
    finally:
        child.kill()
        child.wait(10)
    deadline = time.monotonic() + 5
    while alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert alive() is False
    assert lifecycle.parent_alive_check(-5)() is False


def test_backend_parent_death_runs_the_graceful_exit(stubbed_backend, monkeypatch) -> None:
    watched: dict = {}
    monkeypatch.setattr(lifecycle, "watch_parent", lambda pid, on_dead: watched.update(pid=pid, on_dead=on_dead))

    class _ParentGone(io.BytesIO):
        def readline(self, size=-1):
            watched["on_dead"]()
            return b""

    code, _ = _run(_ParentGone())
    assert code == 0
    assert watched["pid"] > 0
    assert stubbed_backend == ["parent_exited"]


# --- a second instance: hand off through panel.pid, never kill ---------------------------


@pytest.fixture
def no_kill(monkeypatch):
    import os

    from frontend import frozen_process

    def _fail(*_a, **_k):
        raise AssertionError("a second --backend must never kill")

    monkeypatch.setattr(frozen_process, "reclaim_stale_panel_process", _fail)
    monkeypatch.setattr(frozen_process, "reclaim_panel_port", _fail, raising=False)
    monkeypatch.setattr(os, "kill", _fail)
    try:
        import psutil

        monkeypatch.setattr(psutil.Process, "kill", _fail)
        monkeypatch.setattr(psutil.Process, "terminate", _fail)
    except ImportError:
        pass


def test_no_living_ducky_is_a_first_start(no_kill) -> None:
    assert lifecycle.second_instance([], [], read_pid=lambda: None) == (lifecycle.FIRST, None)
    assert lifecycle.second_instance([], [], read_pid=lambda: 77, own_pid=77) == (lifecycle.FIRST, None)
    assert lifecycle.second_instance([], [], read_pid=lambda: 77, alive=lambda pid: False) == (lifecycle.FIRST, None)


def test_a_living_ducky_takes_the_launch(no_kill) -> None:
    sent: list[tuple] = []

    def _handoff(paths, links, first):
        sent.append((paths, links, first))
        return True

    state = lifecycle.second_instance(
        ["C:/a.verse"], ["uefn-ducky://x"], read_pid=lambda: 900, alive=lambda pid: True, handoff=_handoff, own_pid=1
    )
    assert state == (lifecycle.HANDED_OFF, 900)
    assert sent == [(["C:/a.verse"], ["uefn-ducky://x"], True)]


def test_a_starting_ducky_is_waited_for(no_kill) -> None:
    answers = iter([False, False, True])
    state = lifecycle.second_instance(
        [], [], read_pid=lambda: 900, alive=lambda pid: True, handoff=lambda p, l, first: next(answers), own_pid=1
    )
    assert state == (lifecycle.HANDED_OFF, 900)


def test_a_silent_ducky_is_busy_and_left_running(no_kill) -> None:
    now = [0.0]

    def _clock():
        now[0] += 10.0
        return now[0]

    state = lifecycle.second_instance(
        [], [], read_pid=lambda: 900, alive=lambda pid: True, handoff=lambda p, l, first: False,
        wait_s=45.0, clock=_clock, own_pid=1,
    )
    assert state == (lifecycle.BUSY, 900)


def test_a_ducky_that_exits_while_waited_for_means_a_first_start(no_kill) -> None:
    alive = iter([True, False, False])
    state = lifecycle.second_instance(
        [], [], read_pid=lambda: 900, alive=lambda pid: next(alive), handoff=lambda p, l, first: False, own_pid=1
    )
    assert state == (lifecycle.FIRST, None)


def test_backend_hand_off_says_so_and_exits(stubbed_backend, monkeypatch, no_kill) -> None:
    from frontend import frozen_process

    monkeypatch.setattr(lifecycle, "second_instance", lambda paths, links: (lifecycle.HANDED_OFF, 900))
    monkeypatch.setattr(frozen_process, "claim_panel_process", lambda: pytest.fail("panel.pid is the other's"))
    code, lines = _run(io.BytesIO(b""))
    assert code == 0
    assert lines[0]["t"] == "hello" and lines[0]["pid"] == 900 and lines[0]["handoff"] is True
    assert lines[1] == {"t": "event", "name": "quit", "data": {"reason": "handoff", "pid": 900}}
    assert stubbed_backend == []


def test_backend_busy_instance_sends_no_hello(stubbed_backend, monkeypatch, no_kill) -> None:
    monkeypatch.setattr(lifecycle, "second_instance", lambda paths, links: (lifecycle.BUSY, 900))
    code, lines = _run(io.BytesIO(b""))
    assert code == headless.EXIT_INSTANCE_BUSY
    assert lines == []


def test_the_launcher_says_when_its_window_goes_to_the_tray() -> None:
    """``__window_hidden`` never reaches the API: it sets what the update switch reads."""
    calls: list[dict] = []
    seen: list[bool] = []
    replies: list[dict] = []
    reason = lifecycle.serve(
        _lines(
            {"t": "call", "id": "hidden-1", "name": "__window_hidden", "args": [True]},
            {"t": "call", "id": "c1", "name": "get_settings"},
            {"t": "call", "id": "hidden-0", "name": "__window_hidden", "args": [False]},
            {"t": "call", "id": "hidden-x", "name": "__window_hidden"},
            {"t": "bye"},
        ),
        on_call=calls.append,
        reply=replies.append,
        on_hidden=seen.append,
    )
    assert reason == "bye" and seen == [True, False, False]
    assert [c["id"] for c in calls] == ["c1"]
    assert replies == [{"t": "result", "id": i, "ok": True, "value": True} for i in ("hidden-1", "hidden-0", "hidden-x")]
    # A backend nobody listens on answers too, and a failing listener never ends the loop.
    replies.clear()

    def boom(hidden: bool) -> None:
        raise RuntimeError("no holder")

    again = _lines({"t": "call", "id": "h", "name": "__window_hidden", "args": [True]}, {"t": "bye"})
    assert lifecycle.serve(again, on_call=calls.append, reply=replies.append, on_hidden=boom) == "bye"
    assert replies == [{"t": "result", "id": "h", "ok": True, "value": True}]
    replies.clear()
    again = _lines({"t": "call", "id": "h", "name": "__window_hidden", "args": [True]}, {"t": "bye"})
    assert lifecycle.serve(again, on_call=calls.append, reply=replies.append) == "bye"
    assert replies == [{"t": "result", "id": "h", "ok": True, "value": False}]
