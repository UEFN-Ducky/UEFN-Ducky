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
    monkeypatch.setattr(TerminalManager, "_ensure_reaper", lambda self: None)  # tests call it directly
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


def test_an_agents_terminal_it_is_done_with_closes_by_itself(mgr, tmp_path):
    # Oct 15 2026: agents never closed their terminals, so every member of a team left a shell,
    # a conhost and a socket bridge open until Ducky quit.
    mine = mgr.spawn(shell="powershell", cwd=str(tmp_path))["session_id"]  # a person's own
    done = _open(mgr, tmp_path, "builder")["session_id"]
    working = _open(mgr, tmp_path, "busy-agent")["session_id"]
    server = _open(mgr, tmp_path, "server-agent")["session_id"]
    quiet_job = _open(mgr, tmp_path, "job-agent")["session_id"]
    mgr.running[mgr.get_session(server)._pty.pid] = [9999]  # a dev server it left running
    job = mgr.get_session(quiet_job)
    job.ran_background = True  # a background job it started still runs
    mgr.running[job._pty.pid] = [9998]
    start = mgr.get_session(done).last_activity
    assert mgr.close_idle_agent_terminals(now=start + 30, running={"busy-agent"}) == []  # not quiet long enough
    later = start + manager_module._AGENT_IDLE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=later, running={"busy-agent"}) == [done]
    assert {"type": "terminal_close", "session_id": done} in mgr.events  # its tab closes too
    assert set(mgr._sessions) == {mine, working, server, quiet_job}
    # Typing in it counts as use: a terminal someone is working in stays.
    mgr.get_session(working).last_activity = later
    assert mgr.close_idle_agent_terminals(now=later + 30, running=set()) == []


def test_a_ducky_closes_its_terminal_a_minute_after_it_finishes_and_names_it(mgr, tmp_path, monkeypatch):
    # Oct 22 2026: finished duckies' shells piled up in the header for ten minutes with no
    # sign of whose they were.
    monkeypatch.setattr(manager_module, "_chat_title", lambda cid: {"chat-a": "Fix Group Leader Moves"}.get(cid, ""))
    ducky = _open(mgr, tmp_path)
    mine = mgr.spawn(shell="powershell", cwd=str(tmp_path))
    assert ducky["owner"] == "Fix Group Leader Moves" and mine["owner"] == ""
    assert manager_module._AGENT_IDLE_CLOSE_S <= 60
    later = mgr.get_session(ducky["session_id"]).last_activity + 61
    assert mgr.close_idle_agent_terminals(now=later, running={"chat-a"}) == []  # still working
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == [ducky["session_id"]]
    assert set(mgr._sessions) == {mine["session_id"]}


def test_a_running_workflows_terminal_stays_until_the_run_lets_go(mgr, tmp_path):
    # Oct 22 2026: the release workflow's terminal (its group hub's, not a running agent's)
    # sat quiet while an Agent step waited 12 minutes, the reaper closed it, and every step
    # after failed with "session not found".
    release_terminal = _open(mgr, tmp_path, "release-hub")["session_id"]
    let_go = mgr.hold(release_terminal)
    later = mgr.get_session(release_terminal).last_activity + manager_module._AGENT_IDLE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == []
    let_go()
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == [release_terminal]


def test_a_terminal_waiting_for_its_repos_test_lock_is_not_closed(mgr, tmp_path, monkeypatch):
    # Oct 28 2026: the release's "Run tests" waited for another agent's pytest in the repo;
    # quiet and not busy yet, the reaper closed it ("terminal session not running").
    from frontend.ui_web.terminal import repo_lock

    sid = _open(mgr, tmp_path, "release-hub")["session_id"]
    shell = mgr.get_session(sid)
    later = shell.last_activity + manager_module._AGENT_STALE_CLOSE_S + 1
    reaped: list[list[str]] = []

    def acquire(repo, label, on_wait=None):
        reaped.append(mgr.close_idle_agent_terminals(now=later, running=set()))

    monkeypatch.setattr(repo_lock, "acquire", acquire)
    monkeypatch.setattr(shell, "run_command", lambda *a, **k: {"ok": True, "exit_code": 0})
    assert mgr._run_command(shell, "py -3 -m pytest", timeout_s=5)["ok"]
    assert reaped == [[]] and sid in mgr._sessions
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == [sid]  # done: closes as before


def test_a_run_s_terminal_with_no_ducky_still_closes_after_the_run(mgr, tmp_path):
    # A workflow with no Agent step has no group chat to own its terminal; it piled up.
    mine = mgr.spawn(shell="powershell", cwd=str(tmp_path))["session_id"]  # a person's own
    run_terminal = mgr.spawn(shell="bash", cwd=str(tmp_path))["session_id"]
    let_go = mgr.hold(run_terminal)
    later = mgr.get_session(run_terminal).last_activity + manager_module._AGENT_IDLE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == []
    let_go()
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == [run_terminal]
    assert mine in mgr._sessions


def test_a_finished_background_command_no_longer_keeps_its_terminal(mgr, tmp_path):
    # Oct 24 2026: ran_background stayed True after the dev server ended; the shell never closed.
    server = _open(mgr, tmp_path, "server-agent")["session_id"]
    shell = mgr.get_session(server)
    shell.ran_background = True
    mgr.running[shell._pty.pid] = [9998]  # the dev server
    later = shell.last_activity + manager_module._AGENT_IDLE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == []
    mgr.running.pop(shell._pty.pid)  # it exits
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == [server]


def test_an_agents_terminal_with_no_chat_closes_when_idle(mgr, tmp_path):
    # An MCP call with no chat id used to count as the person's own terminal.
    mine = mgr.spawn(shell="powershell", cwd=str(tmp_path))["session_id"]
    agents = mgr.spawn(shell="powershell", cwd=str(tmp_path), from_agent=True)["session_id"]
    later = mgr.get_session(agents).last_activity + manager_module._AGENT_IDLE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == [agents]
    assert mgr.close_idle_agent_terminals(now=later + 3600, running=set()) == []
    assert mine in mgr._sessions


def test_a_running_leads_long_idle_shell_closes(mgr, tmp_path):
    lead = _open(mgr, tmp_path, "lead")["session_id"]
    stuck = _open(mgr, tmp_path, "member")["session_id"]
    mgr.running[mgr.get_session(stuck)._pty.pid] = [9999]  # a hung pager
    start = mgr.get_session(lead).last_activity
    assert mgr.close_idle_agent_terminals(now=start + 120, running={"lead"}) == []
    stale = start + manager_module._AGENT_STALE_CLOSE_S + 1
    assert sorted(mgr.close_idle_agent_terminals(now=stale, running={"lead"})) == sorted([lead, stuck])


def test_an_agent_command_or_allow_card_is_never_closed_as_stale(mgr, tmp_path):
    waiting = _open(mgr, tmp_path, "lead")["session_id"]
    asking = _open(mgr, tmp_path, "lead")["session_id"]
    mgr.get_session(waiting).set_busy(True)
    mgr.request_command(asking, "npm test", conv_id="lead")
    stale = mgr.get_session(waiting).last_activity + manager_module._AGENT_STALE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=stale, running=set()) == []


def test_close_chat_terminals_kills_a_hung_child_and_keeps_the_rest(mgr, tmp_path, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(session_module, "kill_process_tree", killed.append)
    mine = mgr.spawn(shell="powershell", cwd=str(tmp_path))["session_id"]  # a person's own
    other = _open(mgr, tmp_path, "other-team")["session_id"]
    hung = _open(mgr, tmp_path, "member-a")["session_id"]
    idle = _open(mgr, tmp_path, "member-b")["session_id"]
    asking = _open(mgr, tmp_path, "member-b")["session_id"]
    printing = _open(mgr, tmp_path, "member-b")["session_id"]
    hung_pid = mgr.get_session(hung)._pty.pid
    mgr.running[hung_pid] = [9999]  # a hung git, quiet for minutes
    mgr.running[mgr.get_session(printing)._pty.pid] = [9999]  # a test run still printing (a named child)
    mgr.request_command(asking, "npm test", conv_id="member-b")
    now = mgr.get_session(hung).last_activity + manager_module._AGENT_IDLE_CLOSE_S + 1
    mgr.get_session(printing).last_activity = now - 5
    closed = mgr.close_chat_terminals(["member-a", "member-b"], now=now)
    assert sorted(closed) == sorted([hung, idle])
    assert hung_pid in killed
    assert {"type": "terminal_close", "session_id": hung} in mgr.events
    assert {mine, other, asking, printing} <= set(mgr._sessions)
    assert mgr.close_chat_terminals([]) == []


def test_a_sandbox_reuses_its_own_shell_and_never_a_plain_one(mgr, tmp_path):
    # Oct 23 2026: shells opened with their own environment were never reused, so each
    # sandbox_run started a new PowerShell (5 of them, 555 MB, after one live test).
    env = {"USERPROFILE": str(tmp_path / "home"), "PATH": "a;b"}
    plain = mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", reuse_idle=True)
    first = mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", env_extra=env, reuse_idle=True)
    again = mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", env_extra=dict(env), reuse_idle=True)
    assert first["session_id"] != plain["session_id"]
    assert again["session_id"] == first["session_id"] and again.get("reused")
    # A runtime installed in between changes PATH: that needs a fresh shell.
    newer = mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a",
                      env_extra={**env, "PATH": "node;a;b"}, reuse_idle=True)
    assert newer["session_id"] not in (first["session_id"], plain["session_id"])
    assert mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", reuse_idle=True)["session_id"] == plain["session_id"]


def test_a_terminal_whose_timed_out_command_still_runs_is_not_handed_out_again(mgr, tmp_path):
    # Oct 31 2026: a timed-out sandbox_run left only a bash subshell, which the busy check
    # reads as Git's shim; the next sandbox_run got that terminal and failed at once.
    env = {"PATH": "a"}
    first = mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", env_extra=env, reuse_idle=True)
    shell = mgr.get_session(first["session_id"])
    left = {4242: "bash.exe"}
    shell._timed_out = dict(left)
    shell._descendants = lambda snapshot=None: dict(left)
    again = mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", env_extra=env, reuse_idle=True)
    assert again["session_id"] != first["session_id"]
    left.clear()  # it ended: that terminal is free again
    assert mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", env_extra=env,
                     reuse_idle=True)["session_id"] == first["session_id"]


def test_a_shell_handed_out_again_is_not_closed_as_idle(mgr, tmp_path):
    # Oct 23 2026: a sandbox_run picked the chat's quiet shell just as the idle cleanup
    # closed it ("session not found").
    import time as _time

    env = {"PATH": "a"}
    first = mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", env_extra=env, reuse_idle=True)
    shell = mgr.get_session(first["session_id"])
    shell.last_activity = _time.time() - manager_module._AGENT_IDLE_CLOSE_S - 5
    again = mgr.spawn(shell="powershell", cwd=str(tmp_path), conv_id="chat-a", env_extra=env, reuse_idle=True)
    assert again["session_id"] == first["session_id"]
    assert mgr.close_idle_agent_terminals(now=_time.time() + 1, running=set()) == []
