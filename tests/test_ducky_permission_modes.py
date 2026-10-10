"""The embedded Ducky agent follows the chat's approval mode (the composer's permissions button)."""
import json
import threading
import time

import pytest

import test_ducky_ask_readonly as harness
from backend.tools.panel import permission_prompt as permissions

public = harness.public  # the shared embedded-turn fixture
tool = harness.tool
EDIT = {"relative_path": "Verse/game.verse", "content": "x"}


@pytest.fixture
def cards(monkeypatch):
    shown: list[dict] = []
    answer = {"selected": ["deny"]}

    def fake_ask(questions, title=""):
        shown.append({"questions": questions, "title": title})
        return json.dumps({"ok": True, "answers": {"agent_permission": {**answer, "text": "", "skipped": False}}})

    monkeypatch.setattr("backend.tools.panel.panel_ui.ducky_ask_user", fake_ask)
    return shown, answer


def test_accept_edits_writes_files_without_a_card(public, cards):
    shown, _ = cards
    public.run("workspace_write_file", EDIT, mode="agent")
    assert public.executed == [("workspace_write_file", EDIT)]
    assert shown == []


@pytest.mark.parametrize("choice,runs", [("deny", False), ("once", True)])
def test_ask_before_changes_shows_a_card_before_a_file_edit(public, cards, choice, runs):
    shown, answer = cards
    answer["selected"] = [choice]
    permissions.set_permission_mode(public.conv.id, "ask")
    public.run("workspace_write_file", EDIT, mode="agent")
    assert len(shown) == 1 and shown[0]["questions"][0]["detail"] == "Verse/game.verse"
    assert public.executed == ([("workspace_write_file", EDIT)] if runs else [])
    assert public.conv.messages[-1]["content"] == "Evidence answer"


def test_stop_works_while_an_approval_card_waits(public, monkeypatch):
    release = threading.Event()

    def stop_instead_of_answering(questions, title=""):
        public.session._cancel.set()  # the person presses Stop with the card still up
        release.wait(10)
        return json.dumps({"ok": True, "answers": {"agent_permission": {"selected": ["once"], "text": "", "skipped": False}}})

    monkeypatch.setattr("backend.tools.panel.panel_ui.ducky_ask_user", stop_instead_of_answering)
    permissions.set_permission_mode(public.conv.id, "ask")
    started = time.monotonic()
    try:
        public.run("workspace_write_file", EDIT, mode="agent")
        assert time.monotonic() - started < 8
        assert public.executed == []
        assert any(e["type"] == "agent_stopped" and e["reason"] == "cancelled" for e in public.events)
    finally:
        release.set()


def test_a_destructive_tool_is_still_refused_without_a_card_by_default(public, cards):
    shown, _ = cards
    public.catalog.append(tool("destroy_entity"))
    public.run("destroy_entity", {}, mode="agent")
    assert public.executed == [] and shown == []


def test_allow_everything_runs_a_destructive_tool_without_a_card(public, cards):
    shown, _ = cards
    public.catalog.append(tool("destroy_entity"))
    permissions.set_permission_mode(public.conv.id, "all")
    public.run("destroy_entity", {}, mode="agent")
    assert public.executed == [("destroy_entity", {})] and shown == []
