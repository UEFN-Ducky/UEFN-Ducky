"""An agent's terminal command: the Allow/Deny pop-up, or none when its chat allows everything."""

from __future__ import annotations

from frontend.ui_web.terminal.manager import TerminalManager


class _Session:
    shell = "bash"
    cwd = "C:/repo"

    def __init__(self) -> None:
        self.ran: list[str] = []

    def is_busy(self) -> bool:
        return False

    def run_command(self, command: str, **_kw) -> dict:
        self.ran.append(command)
        return {"ok": True}

    def read_output_tail(self, **_kw) -> str:
        return "done"


def _manager(monkeypatch) -> tuple[TerminalManager, _Session, list[dict]]:
    mgr, session, events = TerminalManager(), _Session(), []
    monkeypatch.setattr(mgr, "get_session", lambda _sid: session)
    mgr.set_push(events.append)
    return mgr, session, events


def test_allow_everything_runs_without_the_popup(monkeypatch) -> None:
    mgr, session, events = _manager(monkeypatch)
    out = mgr.run_agent_command("s1", "npm test", conv_id="c1", auto_approve=True, command_timeout_s=5)
    assert out["ok"] is True and out["output_tail"] == "done"
    for _ in range(50):  # it runs on its own thread
        if session.ran:
            break
        __import__("time").sleep(0.02)
    assert session.ran == ["npm test"]
    assert not [e for e in events if e.get("type") == "terminal_command_pending"]


def test_without_it_the_popup_asks(monkeypatch) -> None:
    mgr, session, events = _manager(monkeypatch)
    out = mgr.run_agent_command("s1", "npm test", conv_id="c1", approval_timeout_s=1)
    assert out == {"ok": False, "error": "command not approved (timed out)"}
    # The timed-out question closes its pop-up in every window.
    assert [e["type"] for e in events] == ["terminal_command_pending", "terminal_command_decided"]
    assert events[1]["request_id"] == events[0]["request_id"] and session.ran == []


class _Exits(_Session):
    """A command that takes a while and ends with an exit code."""

    def __init__(self, code: int) -> None:
        super().__init__()
        self.code = code
        self.timeouts: list[float] = []

    def run_command(self, command: str, **kw) -> dict:
        __import__("time").sleep(0.2)
        self.ran.append(command)
        self.timeouts.append(kw.get("timeout_s"))
        return {"ok": True, "exit_code": self.code, "output_tail": f"{command} finished"}


def test_a_waited_command_returns_when_it_ends_with_its_exit_code(monkeypatch) -> None:
    """A workflow step used to come back before its command even started."""
    mgr, _session, _events = _manager(monkeypatch)
    session = _Exits(0)
    monkeypatch.setattr(mgr, "get_session", lambda _sid: session)
    out = mgr.run_agent_command("s1", "py release/publish_app.py", auto_approve=True, command_timeout_s=3600)
    assert session.ran == ["py release/publish_app.py"] and session.timeouts == [3600]
    assert out["ok"] is True and out["exit_code"] == 0 and out["output_tail"] == "py release/publish_app.py finished"


def test_a_failing_command_is_not_ok(monkeypatch) -> None:
    mgr, _session, _events = _manager(monkeypatch)
    session = _Exits(2)
    monkeypatch.setattr(mgr, "get_session", lambda _sid: session)
    out = mgr.run_agent_command("s1", "npm test", auto_approve=True, command_timeout_s=5)
    assert out["ok"] is False and out["exit_code"] == 2 and "exit code 2" in out["error"]
    assert out["output_tail"] == "npm test finished"


def test_allow_on_the_popup_runs_it_once_for_the_waiting_agent(monkeypatch) -> None:
    import threading

    mgr, _session, events = _manager(monkeypatch)
    session = _Exits(0)
    monkeypatch.setattr(mgr, "get_session", lambda _sid: session)
    result: dict = {}
    worker = threading.Thread(target=lambda: result.update(mgr.run_agent_command("s1", "npm test", approval_timeout_s=5)))
    worker.start()
    for _ in range(100):
        if events:
            break
        __import__("time").sleep(0.02)
    assert mgr.approve_command(events[0]["request_id"])["ok"]
    worker.join(5)
    assert result["ok"] is True and session.ran == ["npm test"]
    decided = [e for e in events if e.get("type") == "terminal_command_decided"]
    assert [e["request_id"] for e in decided] == [events[0]["request_id"]]
    # A second answer (another window, or a pop-up replayed after a reload) changes nothing.
    assert mgr.approve_command(events[0]["request_id"])["ok"] is False
    assert len([e for e in events if e.get("type") == "terminal_command_decided"]) == 1


def test_the_done_marker_is_found_when_split_across_two_reads() -> None:
    import threading

    from frontend.ui_web.terminal.session import TerminalSession

    session = TerminalSession(shell="bash", cwd="C:/repo")
    chunks = [b"Uploaded.\r\n__DUCKY_DO", b"NE__0__\r\n$ "]

    class _Pty:
        exitstatus = 0

        def isalive(self) -> bool:
            return True

        def read(self, _n: int) -> bytes:
            if chunks:
                return chunks.pop(0)
            session._stop.set()
            return b""

    session._pty = _Pty()
    session._pending_done = threading.Event()
    session._read_loop()
    assert session._pending_done.is_set() and session._pending_exit_code == 0


def test_a_tab_shown_again_keeps_its_history_through_a_stream_of_progress_codes() -> None:
    """pytest prints a taskbar progress code per test; 400 tiny chunks used to push the history out."""
    from frontend.ui_web.terminal.session import TerminalSession

    session = TerminalSession(shell="bash", cwd="C:/repo")
    chunks = [b"=== security gate ===\r\n", b"npm-audit: ok\r\n"]
    chunks += [b"\x1b]9;4;2;%d\x1b\." % (n % 100) for n in range(5000)]

    class _Pty:
        exitstatus = 0

        def isalive(self) -> bool:
            return True

        def read(self, _n: int) -> bytes:
            if chunks:
                return chunks.pop(0)
            session._stop.set()
            return b""

    session._pty = _Pty()
    session._read_loop()
    history = session.read_output_tail(512 * 1024)
    assert history.startswith("=== security gate ===") and "npm-audit: ok" in history
    assert "\x1b]9;4" not in history and history.count(".") == 5000


def test_progress_codes_split_across_reads_stay_out_of_the_history() -> None:
    """The terminal often hands over half a progress code per read."""
    from frontend.ui_web.terminal.session import TerminalSession

    session = TerminalSession(shell="bash", cwd="C:/repo")
    chunks = [b"HEADER\r\n"]
    for n in range(500):
        chunks += [b"\x1b]9;4;2;", b"%d\x1b" % (n % 100), b"\."]

    class _Pty:
        exitstatus = 0

        def isalive(self) -> bool:
            return True

        def read(self, _n: int) -> bytes:
            if chunks:
                return chunks.pop(0)
            session._stop.set()
            return b""

    session._pty = _Pty()
    session._read_loop()
    kept = "".join(session._output_ring)
    assert "\x1b]9;4" not in kept and kept == "HEADER\r\n" + "." * 500
    assert session._output_chars == len(kept)
