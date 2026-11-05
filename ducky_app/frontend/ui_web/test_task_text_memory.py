"""A seat's first message names the project memory entries to pull before it builds."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from frontend.ui_web import team_keeper

PLAN = {"title": "Memory fix", "body_markdown": "", "nodes": []}


def _work(section_body: str = "", point_body: str = "") -> dict:
    point = {"id": "p1", "content": "Push the fix", "status": "pending", "body_markdown": point_body}
    section = {"id": "s", "content": "Seat H", "assignee": "h", "body_markdown": section_body, "children": [point]}
    return {"section": section, "points": [point]}


def _memory(monkeypatch, entries: list[dict], suggested: list[str]) -> None:
    project = ModuleType("backend.memory.project")
    project.list_entries = lambda project_root="": entries
    project.suggest_entries = lambda text, project_root="", *, limit=3: list(suggested)
    scope = ModuleType("backend.memory.scope")
    scope.memory_root = lambda conv_id="", project_root="", plan=None: "C:/repo"
    monkeypatch.setitem(sys.modules, "backend.memory.project", project)
    monkeypatch.setitem(sys.modules, "backend.memory.scope", scope)


TEAM = {"name": "team-workflow", "subs": [{"name": "team-workflow/testing-and-release"},
                                          {"name": "team-workflow/git-and-worktrees"}]}


def test_the_sections_read_first_line_comes_first(monkeypatch) -> None:
    _memory(monkeypatch, [TEAM], ["bug/ws-reconnect"])
    work = _work(section_body="Read first: `team-workflow/git-and-worktrees`")
    assert team_keeper._read_first_names(PLAN, work) == [
        "team-workflow/git-and-worktrees", "bug/ws-reconnect", "team-workflow/testing-and-release"]
    text = team_keeper._task_text(PLAN, work)
    first, second = text.split("\n", 2)[:2]
    assert first.startswith("[Ducky] Your task: the sub plan “Seat H”")
    assert second == ("Before you build, project_memory_get each of these: team-workflow/git-and-worktrees, "
                      "bug/ws-reconnect, team-workflow/testing-and-release.")


def test_an_empty_memory_adds_no_line(monkeypatch) -> None:
    _memory(monkeypatch, [], [])
    text = team_keeper._task_text(PLAN, _work())
    assert "project_memory_get" not in text


def test_without_the_memory_modules_there_is_no_line(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "backend.memory.project", None)
    monkeypatch.setitem(sys.modules, "backend.memory.scope", None)
    assert "project_memory_get" not in team_keeper._task_text(PLAN, _work())


def test_the_list_is_capped_at_five(monkeypatch) -> None:
    subs = [{"name": f"team-workflow/rule-{i}"} for i in range(6)]
    _memory(monkeypatch, [{"name": "team-workflow", "subs": subs}], ["bug/a", "bug/b"])
    names = team_keeper._read_first_names(PLAN, _work(point_body="Read first: decision/x, bug/a"))
    assert names == ["decision/x", "bug/a", "bug/b", "team-workflow/rule-0", "team-workflow/rule-1"]


@pytest.mark.parametrize("private", [True])
def test_private_entries_are_left_out(monkeypatch, private) -> None:
    _memory(monkeypatch, [{**TEAM, "private": private}], [])
    assert team_keeper._read_first_names(PLAN, _work()) == []
