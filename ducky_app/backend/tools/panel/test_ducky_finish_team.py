"""ducky_finish_team: the Finish team card lists each seat's commits and dirty files; a yes
archives every member but the lead and closes their terminals; a swapped seat retires."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import tick_gate
from backend.tools.panel import ducky_finish_team as ft
from backend.tools.panel import ducky_panel

PLAN = {
    "id": "p1", "title": "Speed", "chat_id": "lead", "created_at": 1000.0,
    "nodes": [
        {"id": "s1", "assignee": "a", "children": [{"id": "x", "status": "completed"}]},
        {"id": "s2", "assignee": "team", "children": [
            {"id": "s3", "assignee": "lead", "children": []},  # the lead's own section: never archived
        ]},
    ],
}


@pytest.fixture
def env(monkeypatch):
    chats = {
        "lead": SimpleNamespace(id="lead", title="Lead", is_group=False, parent_conv_id="team"),
        "a": SimpleNamespace(id="a", ducky_name="Builder", is_group=False, parent_conv_id="team"),
        "team": SimpleNamespace(id="team", title="Team", is_group=True, parent_conv_id=""),
        "b": SimpleNamespace(id="b", ducky_name="Tester", is_group=False, parent_conv_id="team"),
    }
    monkeypatch.setattr("frontend.ui_web.chats.conversations.load_conversation", lambda cid, project_root=None: chats.get(cid))
    monkeypatch.setattr("frontend.ui_web.chats.conversations.list_conversations", lambda project_root=None: list(chats.values()))
    lanes = {"a": ["src/a/**"], "b": ["src/b/**"]}
    monkeypatch.setattr("backend.workspace.lanes.resolve_lane", lambda cid: lanes.get(cid, []))
    monkeypatch.setattr(ft, "_commits", lambda lane, since: {"src/a/**": ["c1", "c2"], "src/b/**": ["c3"]}[lane[0]])
    monkeypatch.setattr(tick_gate, "lane_dirty_files", lambda lane: ["src/b/x.py"] if lane == ["src/b/**"] else [])
    closed: list[list[str]] = []
    terms = SimpleNamespace(close_chat_terminals=lambda ids: closed.append(list(ids)) or [f"t-{i}" for i in ids])
    monkeypatch.setattr("backend.tools.panel.ducky_panel_terminal._terminal_manager", lambda: terms)
    archived: list[list[str]] = []
    state = {"yes": True}

    def archive(conv_ids=None, **_kw):
        if not state["yes"]:
            return json.dumps({"ok": False, "error": "The person said no: nothing was archived."})
        archived.append(list(conv_ids))
        return json.dumps({"ok": True, "archived": list(conv_ids)})

    monkeypatch.setattr(ducky_panel, "ducky_archive_chats", archive)
    return SimpleNamespace(closed=closed, archived=archived, state=state)


def test_card_lists_commits_and_dirty_files_per_seat(env):
    card = ft.finish_card(PLAN)
    assert card["ui"] == "finish_team" and card["lead"] == "lead"
    by = {s["conv_id"]: s for s in card["seats"]}
    assert set(by) == {"a", "b"}  # the group's members, never the lead
    assert by["a"]["name"] == "Builder" and by["a"]["commits"] == ["c1", "c2"] and by["a"]["dirty"] == []
    assert by["b"]["commits"] == ["c3"] and by["b"]["dirty"] == ["src/b/x.py"]
    assert card["totals"] == {"seats": 2, "commits": 3, "uncommitted": 1}
    assert card["text"] == "Finish team: 2 seats · 3 commits · 1 uncommitted · Archive team"


def test_yes_archives_every_member_but_the_lead_and_closes_their_terminals(env):
    out = ft.archive_team(PLAN)
    assert out["status"] == "done" and sorted(out["archived"]) == ["a", "b"]
    assert env.archived == [out["archived"]] and "lead" not in env.archived[0]
    assert env.closed == [env.archived[0]] and len(out["terminals_closed"]) == 2


def test_no_keeps_the_team_and_its_terminals(env):
    env.state["yes"] = False
    out = ft.archive_team(PLAN)
    assert not out["ok"] and env.closed == [] and env.archived == []
    # The card stays whole after a no: same seats, still asking.
    assert out["status"] == "needs_confirm" and {s["conv_id"] for s in out["seats"]} == {"a", "b"}
    assert "said no" in out["error"]


def test_a_swapped_seat_archives_the_old_chat(env, monkeypatch):
    moves: list[tuple[str, str]] = []
    monkeypatch.setattr(ducky_panel, "_panel_api", lambda: SimpleNamespace(move_conversation=lambda c, f: moves.append((c, f))))
    assert ft.retire_swapped_seat("old", "new")
    assert moves == [("old", "archive")] and env.closed == [["old"]]
    assert not ft.retire_swapped_seat("same", "same")  # a recycle keeps its chat
    assert moves == [("old", "archive")]


def test_commits_reads_git_log_and_keeps_lane_commits(monkeypatch):
    log = b"\x01abc1\n\nsrc/a/x.py\x00src/z.py\x00\x01abc2\n\nsrc/z.py\x00"
    seen: list[tuple] = []
    monkeypatch.setattr(tick_gate, "_bases", lambda lane: [Path("/r")])
    monkeypatch.setattr(tick_gate, "_toplevel", lambda p: Path("/r"))
    monkeypatch.setattr(tick_gate, "_git", lambda top, *a: seen.append(a) or log)
    monkeypatch.setattr(tick_gate, "_in_lane", lambda lane, full: full.startswith("/r/src/a/"))
    assert ft._commits(["src/a/**"], 1000.0) == ["abc1"]
    assert "--since=@1000" in seen[0]
