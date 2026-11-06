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
    monkeypatch.setattr(
        webview_app, "start_panel_server", lambda: ("http://127.0.0.1:4567/", False, "http://127.0.0.1:4567/")
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
