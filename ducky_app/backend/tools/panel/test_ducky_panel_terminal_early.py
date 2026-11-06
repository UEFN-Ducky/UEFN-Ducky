"""ducky_terminal_run and sandbox_run hand back the manager's early return with a read hint."""

from __future__ import annotations

import json

from backend.tools.panel import ducky_panel_terminal as term

EARLY = {"ok": True, "still_running": True, "session_id": "t3", "request_id": "r1", "command_id": "r1",
         "output_so_far": "collected 900 items", "note": "The person sent a message. Read it in this result."}


class _Mgr:
    def __init__(self, answer: dict) -> None:
        self.answer, self.calls = answer, []

    def run_agent_command(self, session_id: str, command: str, **kw) -> dict:
        self.calls.append((session_id, command, kw))
        return dict(self.answer)


def _run(monkeypatch, answer: dict) -> tuple[dict, _Mgr]:
    mgr = _Mgr(answer)
    monkeypatch.setattr(term, "_terminal_manager", lambda: mgr)
    monkeypatch.setattr(term, "_terminal_chat", lambda _c: "chat-1")
    monkeypatch.setattr("backend.tools.panel.permission_prompt.allows_everything", lambda _c: True)
    return json.loads(term.ducky_terminal_run("t3", "pytest")), mgr


def test_still_running_passes_through_with_the_read_call(monkeypatch) -> None:
    out, mgr = _run(monkeypatch, EARLY)
    assert {k: out[k] for k in EARLY} == EARLY  # unchanged
    assert out["read_with"] == 'ducky_terminal_read_output(session_id="t3")'
    assert mgr.calls[0][2]["conv_id"] == "chat-1"  # the manager's steer check needs the chat


def test_waiting_for_approval_gets_the_hint_too(monkeypatch) -> None:
    out, _ = _run(monkeypatch, {"ok": True, "waiting_for_approval": True, "session_id": "t3", "note": "card"})
    assert out["waiting_for_approval"] is True and "t3" in out["read_with"]


def test_a_finished_run_is_unchanged(monkeypatch) -> None:
    done = {"ok": True, "session_id": "t3", "request_id": "r1", "output_tail": "900 passed", "exit_code": 0}
    out, _ = _run(monkeypatch, done)
    assert out == done


class _Spawner:
    def __init__(self) -> None:
        self.kw: dict = {}

    def spawn(self, **kw) -> dict:
        self.kw = kw
        return {"ok": True, "session_id": "t9"}


def _open(monkeypatch, bound: str, env: str = "") -> dict:
    from types import SimpleNamespace

    mgr = _Spawner()
    monkeypatch.setattr(term, "_terminal_manager", lambda: mgr)
    monkeypatch.setattr("backend.workspace.identity.current",
                        lambda: SimpleNamespace(conv_id=bound) if bound else None)
    if env:
        monkeypatch.setenv("DUCKY_CONV_ID", env)
    else:
        monkeypatch.delenv("DUCKY_CONV_ID", raising=False)
    term.ducky_terminal_open(shell="powershell")
    return mgr.kw


def test_open_without_conv_id_takes_the_callers_chat(monkeypatch) -> None:
    kw = _open(monkeypatch, "chat-7")
    assert kw["conv_id"] == "chat-7" and kw["from_agent"] is True
    assert _open(monkeypatch, "", env="chat-8")["conv_id"] == "chat-8"  # dedicated tool server


def test_open_with_no_identity_owns_nothing_and_is_run_owned(monkeypatch) -> None:
    kw = _open(monkeypatch, "")
    assert kw["conv_id"] == "" and kw["from_agent"] is True  # the manager marks it run_owned


def test_the_window_proxy_passes_from_agent(monkeypatch) -> None:
    # The window must see from_agent, or a chat-less agent's terminal counts as the person's
    # own and the reaper never closes it (Nov 5 2026 soak).
    sent = []
    proxy = term._PanelTerminalManager()
    monkeypatch.setattr(proxy, "_call", lambda method, args, timeout=30.0: sent.append((method, args)) or {"ok": True})
    proxy.spawn(shell="bash", cwd=None, conv_id="", from_agent=True)
    method, args = sent[0]
    assert method == "terminal_spawn"
    assert args["from_agent"] is True and args["conv_id"] == "" and args["cwd"] == ""


def test_sandbox_run_carries_the_early_return(monkeypatch) -> None:
    from backend.tools.core import sandbox_tools

    monkeypatch.setattr(sandbox_tools, "_refused", lambda _c: "")
    monkeypatch.setattr(sandbox_tools, "_approved", lambda *_a: "")
    monkeypatch.setattr(sandbox_tools, "_forward", lambda call, **_a: dict(EARLY))
    out = json.loads(sandbox_tools.sandbox_run("pytest"))
    assert out["still_running"] is True and out["note"] == EARLY["note"]
    assert out["read_with"] == 'ducky_terminal_read_output(session_id="t3")'
    monkeypatch.setattr(sandbox_tools, "_forward", lambda call, **_a: {"ok": True, "exit_code": 0, "session_id": "t3"})
    assert "read_with" not in json.loads(sandbox_tools.sandbox_run("pytest"))
