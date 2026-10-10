"""One chat's agent cannot pile up terminals without end."""

from __future__ import annotations

import itertools

import pytest

from frontend.ui_web.terminal import manager as manager_module
from frontend.ui_web.terminal import session as session_module
from frontend.ui_web.terminal.manager import TerminalManager
from frontend.ui_web.terminal.session import TerminalSession


class _Pty:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.alive = True

    def isalive(self) -> bool:
        return self.alive

    def close(self, force: bool = False) -> None:
        self.alive = False


class _Bridge:
    def __init__(self, **_kwargs) -> None:
        self.port = 1
        self.ws_url = "ws://127.0.0.1:1"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


@pytest.fixture
def mgr(monkeypatch):
    pids = itertools.count(100)
    monkeypatch.setattr(TerminalSession, "spawn", lambda self: setattr(self, "_pty", _Pty(next(pids))))
    monkeypatch.setattr(manager_module, "TerminalBridge", _Bridge)
    # The fake shells have made-up pids: never send a kill to a real process.
    monkeypatch.setattr(session_module, "kill_process_tree", lambda pid: None)
    running: dict[int, list[int]] = {}
    names = {9999: "npm.exe"}
    monkeypatch.setattr(manager_module, "_process_snapshot", lambda: (running, names))
    m = TerminalManager()
    events: list[dict] = []
    m.set_push(events.append)
    m.running = running  # type: ignore[attr-defined]
    m.events = events  # type: ignore[attr-defined]
    return m


def _open(m: TerminalManager, tmp_path, conv_id: str = "chat-a") -> dict:
    out = m.spawn(shell="powershell", cwd=str(tmp_path), conv_id=conv_id)
    assert out["ok"], out
    return out


def _chat_ids(m: TerminalManager, conv_id: str) -> list[str]:
    return [sid for sid, e in m._sessions.items() if e.chat == conv_id]


def test_opening_past_the_cap_closes_the_chats_oldest_idle_terminal(mgr, tmp_path):
    mine = mgr.spawn(shell="powershell", cwd=str(tmp_path))["session_id"]  # a person's own
    other = _open(mgr, tmp_path, "chat-b")["session_id"]
    first = [_open(mgr, tmp_path)["session_id"] for _ in range(manager_module._MAX_CHAT_SHELLS)]
    newest = _open(mgr, tmp_path)
    assert newest["closed"] == [first[0]]
    assert _chat_ids(mgr, "chat-a") == [*first[1:], newest["session_id"]]
    assert mine in mgr._sessions and other in mgr._sessions
    assert {"type": "terminal_close", "session_id": first[0]} in mgr.events


def test_busy_terminals_are_kept_and_the_next_idle_one_closes(mgr, tmp_path):
    first = [_open(mgr, tmp_path)["session_id"] for _ in range(manager_module._MAX_CHAT_SHELLS)]
    mgr.get_session(first[0]).set_busy(True)  # an agent command runs
    mgr.running[mgr.get_session(first[1])._pty.pid] = [9999]  # a typed command runs
    mgr.request_command(first[2], "npm test", conv_id="chat-a")  # waiting on Allow/Deny
    newest = _open(mgr, tmp_path)
    assert newest["closed"] == [first[3]]
    assert set(first[:3]) <= set(mgr._sessions)


def test_a_chat_whose_terminals_are_all_busy_still_gets_a_new_one(mgr, tmp_path):
    first = [_open(mgr, tmp_path)["session_id"] for _ in range(manager_module._MAX_CHAT_SHELLS)]
    for sid in first:
        mgr.get_session(sid).set_busy(True)
    newest = _open(mgr, tmp_path)
    assert "closed" not in newest
    assert len(_chat_ids(mgr, "chat-a")) == manager_module._MAX_CHAT_SHELLS + 1
    # Once some are free again, the next open brings the chat back under the cap.
    for sid in first[:3]:
        mgr.get_session(sid).set_busy(False)
    again = _open(mgr, tmp_path)
    assert again["closed"] == first[:2]
    assert len(_chat_ids(mgr, "chat-a")) == manager_module._MAX_CHAT_SHELLS


def test_an_ended_shell_goes_before_an_older_live_one(mgr, tmp_path):
    first = [_open(mgr, tmp_path)["session_id"] for _ in range(manager_module._MAX_CHAT_SHELLS)]
    mgr.get_session(first[4])._pty.alive = False
    assert _open(mgr, tmp_path)["closed"] == [first[4]]


def test_a_person_never_hits_the_cap(mgr, tmp_path):
    for _ in range(manager_module._MAX_CHAT_SHELLS + 2):
        assert "closed" not in mgr.spawn(shell="powershell", cwd=str(tmp_path))
    assert len(mgr._sessions) == manager_module._MAX_CHAT_SHELLS + 2
