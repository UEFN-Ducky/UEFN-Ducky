"""An agent's terminal command card in its chat: what it offers and what each answer saves."""

from __future__ import annotations

import threading
import time

import pytest

from backend.tools.panel import permission_prompt
from frontend.ui_web.terminal.manager import TerminalManager


class _Session:
    shell = "powershell"
    cwd = "C:/repo"

    def __init__(self) -> None:
        self.ran: list[str] = []

    def is_busy(self) -> bool:
        return False

    def run_command(self, command: str, **_kw) -> dict:
        self.ran.append(command)
        return {"ok": True, "exit_code": 0, "output_tail": "done"}

    def read_output_tail(self, **_kw) -> str:
        return "done"


@pytest.fixture
def chat_rules(monkeypatch) -> dict[str, list[str]]:
    """The chats' remembered approvals, in memory."""
    rules: dict[str, list[str]] = {}
    monkeypatch.setattr(permission_prompt, "_rules", lambda conv: list(rules.get(conv, [])))
    monkeypatch.setattr(permission_prompt, "_remember", lambda conv, rule: rules.setdefault(conv, []).append(rule))
    monkeypatch.setattr(
        permission_prompt,
        "set_allow_everything",
        lambda conv, on: rules.setdefault(conv, []).append("*") if on else None,
    )
    monkeypatch.setattr(permission_prompt, "_project_root", lambda: "")
    return rules


def _manager(monkeypatch) -> tuple[TerminalManager, _Session, list[dict]]:
    mgr, session, events = TerminalManager(), _Session(), []
    monkeypatch.setattr(mgr, "get_session", lambda _sid: session)
    mgr.set_push(events.append)
    return mgr, session, events


def _ask(mgr: TerminalManager, events: list[dict], command: str, conv_id: str = "c1") -> tuple[dict, threading.Thread]:
    """Start an agent's command and wait for its card; returns (card event, agent thread)."""
    result: dict = {}
    before = len(events)
    worker = threading.Thread(
        target=lambda: result.update(mgr.run_agent_command("s1", command, conv_id=conv_id, approval_timeout_s=5, command_timeout_s=5)),
        daemon=True,
    )
    worker.start()
    for _ in range(200):
        if len(events) > before:
            break
        time.sleep(0.01)
    worker.result = result  # type: ignore[attr-defined]
    return events[before], worker


def test_the_card_names_its_chat_folder_and_what_always_allow_would_save(monkeypatch, chat_rules) -> None:
    mgr, _session, events = _manager(monkeypatch)
    card, worker = _ask(mgr, events, "npm test")
    assert card["type"] == "terminal_command_pending"
    assert card["conv_id"] == "c1" and card["cwd"] == "C:/repo" and card["shell"] == "powershell"
    assert card["rule_label"] == "npm test"
    # A window that reloads gets the same card back.
    assert [row["request_id"] for row in mgr.list_pending()] == [card["request_id"]]
    assert mgr.reject_command(card["request_id"])["ok"]
    worker.join(5)
    assert mgr.list_pending() == []


def test_always_allow_runs_that_command_again_without_a_card_in_that_chat_only(monkeypatch, chat_rules) -> None:
    mgr, session, events = _manager(monkeypatch)
    card, worker = _ask(mgr, events, "npm test")
    out = mgr.approve_command(card["request_id"], scope="always")
    worker.join(5)
    assert out["ok"] and out["saved"] == "always"
    assert chat_rules == {"c1": ["Terminal:npm test"]}
    assert worker.result["ok"] is True  # type: ignore[attr-defined]

    asked = len([e for e in events if e["type"] == "terminal_command_pending"])
    again = mgr.run_agent_command("s1", "npm test", conv_id="c1", approval_timeout_s=1, command_timeout_s=5)
    assert again["ok"] is True
    assert len([e for e in events if e["type"] == "terminal_command_pending"]) == asked
    assert session.ran.count("npm test") == 2

    # Another chat, or another command, still asks.
    other = mgr.run_agent_command("s1", "npm test", conv_id="c2", approval_timeout_s=1, command_timeout_s=5)
    assert other == {"ok": False, "error": "command not approved (timed out)", "fail": "timeout"}
    different = mgr.run_agent_command("s1", "npm run build", conv_id="c1", approval_timeout_s=1, command_timeout_s=5)
    assert different["ok"] is False


def test_allow_everything_turns_on_the_chats_switch(monkeypatch, chat_rules) -> None:
    mgr, _session, events = _manager(monkeypatch)
    card, worker = _ask(mgr, events, "npm test")
    assert mgr.approve_command(card["request_id"], scope="all")["saved"] == "all"
    worker.join(5)
    assert chat_rules == {"c1": ["*"]}


def test_a_push_never_offers_always_and_any_plugin_push_can_allow_everything(monkeypatch, chat_rules) -> None:
    mgr, _session, events = _manager(monkeypatch)
    card, worker = _ask(mgr, events, "git -C uefn-plugin-openai push")
    assert card["rule_label"] == "" and "local_only" not in card
    out = mgr.approve_command(card["request_id"], scope="all")
    worker.join(5)
    assert out["ok"] and out["saved"] == "all" and chat_rules == {"c1": ["*"]}
    chat_rules.clear()

    card, worker = _ask(mgr, events, "npm test && git push")
    assert card["rule_label"] == ""
    assert mgr.approve_command(card["request_id"], scope="always")["saved"] == "once"
    worker.join(5)
    assert chat_rules == {}


def test_a_command_from_no_chat_saves_nothing(monkeypatch, chat_rules) -> None:
    mgr, _session, events = _manager(monkeypatch)
    card, worker = _ask(mgr, events, "npm test", conv_id="")
    assert card["conv_id"] == ""
    assert mgr.approve_command(card["request_id"], scope="all")["saved"] == "once"
    worker.join(5)
    assert chat_rules == {}


def test_the_panel_api_passes_the_answer_and_lists_waiting_cards(monkeypatch, chat_rules) -> None:
    from frontend.ui_web import terminal
    from frontend.ui_web.panel_api import PanelApi

    mgr, _session, events = _manager(monkeypatch)
    monkeypatch.setattr(terminal, "get_terminal_manager", lambda: mgr)
    api = PanelApi()
    card, worker = _ask(mgr, events, "npm test")
    assert [row["request_id"] for row in api.terminal_pending_commands()] == [card["request_id"]]
    assert api.terminal_approve_command(f" {card['request_id']} ", "always")["saved"] == "always"
    worker.join(5)
    assert api.terminal_pending_commands() == []
    decided = [e for e in events if e["type"] == "terminal_command_decided"]
    assert decided == [{"type": "terminal_command_decided", "request_id": card["request_id"], "conv_id": "c1"}]


def test_an_answer_before_the_agent_waits_still_reaches_it(monkeypatch, chat_rules) -> None:
    mgr, _session, events = _manager(monkeypatch)
    req = mgr.request_command("s1", "npm test", conv_id="c1", runner_waits=True)
    assert mgr.approve_command(req["request_id"])["ok"]
    assert mgr.wait_for_approval(req["request_id"], timeout_s=1) == {"ok": True, "approved": True}
    assert mgr.wait_for_approval(req["request_id"], timeout_s=1)["error"] == "request not found"


def test_a_members_card_also_shows_in_its_leads_chat(monkeypatch, chat_rules) -> None:
    # Oct 15 2026: a member's card waited 120 s in its own chat, nobody saw it, and the team
    # stopped. It now also shows in the lead's chat, named after the member, and Allow
    # everything picked there is saved on the lead (which covers every member it starts).
    from types import SimpleNamespace

    monkeypatch.setattr(permission_prompt, "starter_root", lambda cid: "lead" if cid == "member" else "")
    monkeypatch.setattr("frontend.ui_web.project_chats.load_conversation",
                        lambda cid, **_k: SimpleNamespace(ducky_name="Verse Coder", title="Verse Coder"))
    alerts: list[tuple] = []
    monkeypatch.setattr("frontend.ui_web.terminal.manager._alert_command", lambda *a: alerts.append(a))
    mgr, _session, events = _manager(monkeypatch)
    card, worker = _ask(mgr, events, "py -3 -m unittest", conv_id="member")
    assert card["conv_id"] == "member" and card["lead_conv_id"] == "lead" and card["asked_by"] == "Verse Coder"
    assert alerts == [("lead", "py -3 -m unittest", "Verse Coder")]
    assert mgr.approve_command(card["request_id"], scope="all", answered_in="lead")["saved"] == "all"
    worker.join(5)
    assert chat_rules == {"lead": ["*"]}
    # A chat nobody started keeps its card, and its answers, to itself.
    card, worker = _ask(mgr, events, "npm test", conv_id="c1")
    assert card["lead_conv_id"] == "" and card["asked_by"] == ""
    assert mgr.approve_command(card["request_id"], scope="all", answered_in="lead")["saved"] == "all"
    worker.join(5)
    assert chat_rules == {"lead": ["*"], "c1": ["*"]}
