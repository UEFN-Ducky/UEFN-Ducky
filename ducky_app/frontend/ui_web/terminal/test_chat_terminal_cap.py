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
        self.closed = 0

    def isalive(self) -> bool:
        return self.alive

    def close(self, force: bool = False) -> None:
        self.alive = False
        self.closed += 1


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
    monkeypatch.setattr(TerminalManager, "ensure_budget_ticker", lambda self: None)
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
    assert mgr.close_idle_agent_terminals(now=start + 5, running={"busy-agent"}) == []  # not quiet long enough
    later = start + manager_module._AGENT_IDLE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=later, running={"busy-agent"}) == [done]
    assert {"type": "terminal_close", "session_id": done} in mgr.events  # its tab closes too
    assert set(mgr._sessions) == {mine, working, server, quiet_job}
    # Typing in it counts as use: a terminal someone is working in stays.
    mgr.get_session(working).last_activity = later
    assert mgr.close_idle_agent_terminals(now=later + 5, running=set()) == []


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


def test_a_window_terminal_opened_for_a_chat_less_agent_closes_when_idle(mgr, tmp_path, monkeypatch):
    # Nov 5 2026 soak: agent-host calls reach the window's terminal_spawn, which dropped
    # from_agent; 5 such terminals (463 MB) stayed open after their agents ended.
    from frontend.ui_web import terminal
    from frontend.ui_web.panel_api import PanelApi

    monkeypatch.setattr(terminal, "get_terminal_manager", lambda: mgr)
    opened = PanelApi().terminal_spawn(shell="powershell", cwd=str(tmp_path), from_agent=True)["session_id"]
    assert mgr.get_session(opened).agent  # also started light (shells._PS_AGENT_ARGS)
    later = mgr.get_session(opened).last_activity + manager_module._AGENT_IDLE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == [opened]


def _keep_setup(mgr, tmp_path, reason):
    """One terminal set up so close_idle_agent_terminals keeps it for ``reason``: (sid, now, running)."""
    if reason == "not_agent":
        sid = mgr.spawn(shell="powershell", cwd=str(tmp_path))["session_id"]  # the person's own
    else:
        sid = _open(mgr, tmp_path, "agent-a")["session_id"]
    shell = mgr.get_session(sid)
    now = shell.last_activity + manager_module._AGENT_IDLE_CLOSE_S + 1
    running: set[str] = set()
    if reason == "quiet<60s":
        now = shell.last_activity + 5
    elif reason == "chat_running":
        running = {"agent-a"}
    elif reason == "busy":
        shell.set_busy(True)
    elif reason == "background":
        shell.ran_background = True
        mgr.running[shell._pty.pid] = [9998]
    elif reason == "allow_card":
        mgr.request_command(sid, "npm test", conv_id="agent-a")
    elif reason == "held":
        mgr.hold(sid)
    return sid, now, running


@pytest.mark.parametrize("reason", ["quiet<60s", "chat_running", "busy", "background", "allow_card", "held", "not_agent"])
def test_each_kept_terminal_says_why_it_stays_open(mgr, tmp_path, reason):
    sid, now, running = _keep_setup(mgr, tmp_path, reason)
    assert mgr.close_idle_agent_terminals(now=now, running=running) == []
    assert mgr.kept == {sid: reason}


def test_kept_reasons_are_logged_at_most_once_a_minute(mgr, tmp_path, caplog):
    import logging

    sid, now, running = _keep_setup(mgr, tmp_path, "chat_running")
    with caplog.at_level(logging.DEBUG, logger="terminal.keep"):
        for tick in range(4):  # the reaper's 15 s ticks
            mgr.close_idle_agent_terminals(now=now + 15 * tick, running=running)
    assert [r.getMessage() for r in caplog.records] == [f"kept agent terminals: {{'{sid}': 'chat_running'}}"]


def test_a_done_agents_quiet_terminal_closes_after_10_s_and_a_working_ones_after_60(mgr, tmp_path):
    done = _open(mgr, tmp_path, "done-agent")["session_id"]
    working = _open(mgr, tmp_path, "working-agent")["session_id"]
    start = mgr.get_session(done).last_activity
    mgr.get_session(working).last_activity = start
    running = {"working-agent"}
    assert manager_module._REAP_EVERY_S <= 5
    assert mgr.close_idle_agent_terminals(now=start + 9, running=running) == []
    assert mgr.close_idle_agent_terminals(now=start + 10, running=running) == [done]
    assert mgr.close_idle_agent_terminals(now=start + 59, running=running) == []  # still working
    assert mgr.kept == {working: "quiet<60s"}


def test_a_done_agents_dev_server_terminal_closes_10_s_after_its_last_output_once_it_exits(mgr, tmp_path):
    server = _open(mgr, tmp_path, "done-agent")["session_id"]
    shell = mgr.get_session(server)
    shell.ran_background = True
    mgr.running[shell._pty.pid] = [9998]  # the dev server
    start = shell.last_activity
    assert mgr.close_idle_agent_terminals(now=start + 1, running=set()) == []
    assert mgr.close_idle_agent_terminals(now=start + 1800, running=set()) == []
    assert mgr.kept == {server: "background"}  # quiet, but its server still runs
    shell.last_activity = start + 1800  # it printed its last line
    mgr.running.pop(shell._pty.pid)  # and exited
    # Nov 6 2026: closed on the very next tick, its last output was lost to the agent reading it.
    assert mgr.close_idle_agent_terminals(now=start + 1801, running=set()) == []
    assert mgr.close_idle_agent_terminals(now=start + 1810, running=set()) == [server]


def test_a_done_agents_silent_server_exit_closes_on_the_next_tick(mgr, tmp_path):
    server = _open(mgr, tmp_path, "done-agent")["session_id"]
    shell = mgr.get_session(server)
    shell.ran_background = True
    mgr.running[shell._pty.pid] = [9998]
    start = shell.last_activity
    assert mgr.close_idle_agent_terminals(now=start + 1800, running=set()) == []
    mgr.running.pop(shell._pty.pid)  # exits without printing: quiet long past 10 s
    assert mgr.close_idle_agent_terminals(now=start + 1805, running=set()) == [server]


def test_turn_end_closes_the_chats_terminals_at_once_and_keeps_the_rest(mgr, tmp_path):
    mine = mgr.spawn(shell="powershell", cwd=str(tmp_path))["session_id"]  # the person's own
    other = _open(mgr, tmp_path, "other-chat")["session_id"]
    idle = _open(mgr, tmp_path, "agent-a")["session_id"]
    hung = _open(mgr, tmp_path, "agent-a")["session_id"]
    mgr.get_session(hung).set_busy(True)  # a timed-out command still counted as running
    asking, _n, _r = _keep_setup(mgr, tmp_path, "allow_card")
    held, _n, _r = _keep_setup(mgr, tmp_path, "held")
    server, _n, _r = _keep_setup(mgr, tmp_path, "background")
    assert sorted(mgr.close_for_turn_end("agent-a")) == sorted([idle, hung])  # no 60 s wait
    assert set(mgr._sessions) == {mine, other, asking, held, server}
    assert mgr.close_for_turn_end("") == []


def test_self_care_reports_why_terminals_stay(mgr, tmp_path, monkeypatch):
    from frontend import self_health
    from frontend.ui_web import terminal

    monkeypatch.setattr(terminal, "get_terminal_manager", lambda: mgr)
    sid, _now, _running = _keep_setup(mgr, tmp_path, "held")
    mine, _n, _r = _keep_setup(mgr, tmp_path, "not_agent")
    monkeypatch.setattr("frontend.ui_web.agent_turns.state.list_running_agents", lambda: [])
    out = self_health.self_care("close_idle_terminals")
    assert out["closed"] == [] and out["kept"] == {sid: "held", mine: "not_agent"}


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
    assert mgr.close_idle_agent_terminals(now=stale, running={"lead"}) == []


def test_a_finished_duckys_hung_command_no_longer_keeps_its_terminal_for_good(mgr, tmp_path):
    # Nov 2026: a ducky's last command still counted as busy after its turn (queued behind another
    # chat's test run, or hung), so its shell stayed open until Ducky quit.
    hung = _open(mgr, tmp_path, "done-ducky")["session_id"]
    asking = _open(mgr, tmp_path, "done-ducky")["session_id"]
    mgr.get_session(hung).set_busy(True)
    mgr.request_command(asking, "npm test", conv_id="done-ducky")
    start = mgr.get_session(hung).last_activity
    assert mgr.close_idle_agent_terminals(now=start + manager_module._AGENT_IDLE_CLOSE_S + 1, running=set()) == []
    stale = start + manager_module._AGENT_STALE_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=stale, running=set()) == [hung]
    assert asking in mgr._sessions  # an Allow card still waits on the person


def test_a_finished_duckys_forgotten_background_command_closes_after_an_hour(mgr, tmp_path):
    server = _open(mgr, tmp_path, "done-ducky")["session_id"]
    shell = mgr.get_session(server)
    shell.ran_background = True
    mgr.running[shell._pty.pid] = [9998]  # a dev server it never stopped
    start = shell.last_activity
    assert mgr.close_idle_agent_terminals(now=start + manager_module._AGENT_STALE_CLOSE_S + 1, running=set()) == []
    later = start + manager_module._ORPHAN_BACKGROUND_CLOSE_S + 1
    assert mgr.close_idle_agent_terminals(now=later, running={"done-ducky"}) == []  # its ducky still works
    assert mgr.close_idle_agent_terminals(now=later, running=set()) == [server]


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


def test_an_archived_chats_terminals_all_close_even_a_running_one(mgr, tmp_path):
    # Nov 5 2026: an archived seat's sandbox kept a 5-minute port probe running.
    probe = _open(mgr, tmp_path, "archived-seat")["session_id"]
    server = _open(mgr, tmp_path, "archived-seat")["session_id"]
    asking = _open(mgr, tmp_path, "archived-seat")["session_id"]
    mgr.running[mgr.get_session(probe)._pty.pid] = [9999]  # still printing
    shell = mgr.get_session(server)
    shell.ran_background = True
    mgr.running[shell._pty.pid] = [9998]
    mgr.request_command(asking, "npm test", conv_id="archived-seat")
    now = mgr.get_session(probe).last_activity + 1
    assert mgr.close_chat_terminals(["archived-seat"], now=now) == []
    assert sorted(mgr.close_chat_terminals(["archived-seat"], now=now, force=True)) == sorted([probe, server])
    assert asking in mgr._sessions  # an Allow card still waits on the person


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


def test_a_terminal_whose_background_job_still_runs_is_never_closed(mgr, tmp_path):
    # Nov 3 2026: `cargo test` run with background=true (a `( … ) &` subshell the busy check
    # doesn't see) lost its terminal while it built: read_output said "session not found".
    first = [_open(mgr, tmp_path)["session_id"] for _ in range(manager_module._MAX_CHAT_SHELLS)]
    cargo = mgr.get_session(first[0])
    cargo.ran_background = True
    mgr.running[cargo._pty.pid] = [9998]  # the background job under the shell
    assert _open(mgr, tmp_path)["closed"] == [first[1]]  # the cap closes the next idle one instead
    later = cargo.last_activity + manager_module._AGENT_IDLE_CLOSE_S + 1
    assert first[0] not in mgr.close_chat_terminals(["chat-a"], now=later)
    assert first[0] in mgr._sessions
    mgr.running.pop(cargo._pty.pid)  # cargo ends: an ordinary idle terminal again
    assert mgr.close_chat_terminals(["chat-a"], now=later) == [first[0]]


def _hosts(monkeypatch, running: dict, names: dict) -> list[int]:
    """Console hosts under this process are made up; terminations are only recorded."""
    import os as _os

    ended: list[int] = []
    monkeypatch.setattr(session_module, "_process_snapshot", lambda: (running, names))
    monkeypatch.setattr(session_module, "terminate_pid", ended.append)
    monkeypatch.setattr(manager_module, "terminate_pid", ended.append)
    running.setdefault(_os.getpid(), [])
    return ended


def test_kill_closes_a_pty_whose_shell_already_exited(mgr, tmp_path):
    shell = mgr.get_session(_open(mgr, tmp_path)["session_id"])
    pty = shell._pty
    pty.alive = False  # the shell exited by itself; its console host still runs
    mgr.kill(shell.id)
    assert pty.closed == 1


def test_the_recorded_console_host_ends_with_its_terminal(mgr, tmp_path, monkeypatch):
    import os as _os

    names = {555: "openconsole.exe", 556: "conhost.exe"}
    ended = _hosts(monkeypatch, {_os.getpid(): [555, 556]}, names)
    shell = mgr.get_session(_open(mgr, tmp_path)["session_id"])
    shell.console_hosts = {555}
    mgr.kill(shell.id)
    assert ended == [555]  # the other terminal's host stays


def test_the_reaper_ends_orphaned_hosts_and_spares_live_ones(mgr, tmp_path, monkeypatch):
    import os as _os

    mine = _os.getpid()
    mgr.running[mine] = [703]  # Ducky's own console, there before any terminal
    names = {700: "conhost.exe", 701: "openconsole.exe", 702: "python.exe", 703: "conhost.exe"}
    monkeypatch.setattr(manager_module, "_process_snapshot", lambda: (mgr.running, names))
    ended = _hosts(monkeypatch, mgr.running, names)
    live = mgr.get_session(_open(mgr, tmp_path)["session_id"])
    live.console_hosts = {700}
    mgr.running[mine] = [700, 701, 702, 703]
    assert mgr.reap_console_hosts() == [701]
    assert ended == [701]


def test_another_managers_reaper_never_ends_a_live_terminals_console_host(mgr, tmp_path, monkeypatch):
    # Nov 6 2026: a second manager in the process (its baseline empty, its sessions fakes) took
    # a live terminal's conhost for a stray and ended it; the shell died mid-command and the
    # 60 s loopback test read "session not found" whenever it ran after other terminal tests.
    import os as _os

    names = {800: "conhost.exe", 801: "conhost.exe"}
    monkeypatch.setattr(manager_module, "_process_snapshot", lambda: (mgr.running, names))
    ended = _hosts(monkeypatch, mgr.running, names)
    other = TerminalManager()
    other._host_baseline = set()  # it opened a terminal before any of ours existed
    live = mgr.get_session(_open(mgr, tmp_path)["session_id"])
    live.console_hosts = {800}
    mgr.running[_os.getpid()] = [800, 801]  # 801: a host no terminal owns
    assert other.reap_console_hosts() == [801]
    assert ended == [801]


def test_no_reaper_ends_a_bare_sessions_console_host(mgr, monkeypatch):
    # Nov 6 2026: test_shell_memory's bare TerminalSession (in no manager) failed under -n 4: a
    # leaked manager in the same worker took its conhost for a stray. Live sessions own theirs.
    import os as _os

    from frontend.ui_web.terminal import session as session_module

    names = {900: "conhost.exe", 901: "conhost.exe"}
    monkeypatch.setattr(manager_module, "_process_snapshot", lambda: (mgr.running, names))
    ended = _hosts(monkeypatch, mgr.running, names)
    monkeypatch.setattr(session_module, "_live_hosts", {1: {900}})  # a bare session spawned it
    other = TerminalManager()
    other._host_baseline = set()
    mgr.running[_os.getpid()] = [900, 901]
    monkeypatch.setattr(session_module, "_spawning", 1)  # a session is starting: its host unknown
    assert other.reap_console_hosts() == [] and ended == []
    monkeypatch.setattr(session_module, "_spawning", 0)
    assert other.reap_console_hosts() == [901]
    assert ended == [901]
