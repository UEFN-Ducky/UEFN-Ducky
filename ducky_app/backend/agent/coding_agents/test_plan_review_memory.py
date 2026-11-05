"""Plan review suggests the project memory a team step should read first."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from backend.agent.coding_agents import plan_review

BODY = ("**Why:** members share one checkout. **Files:** `backend/a.py` **Do:** 1. work in a detached "
        "git worktree of origin/main, then pull --rebase and push. **Tests:** test_a.py. **Done when:** "
        "pushed. **Example:** one worktree per seat, never the person's checkout.")


@pytest.fixture()
def memory(monkeypatch):
    project = ModuleType("backend.memory.project")
    scope = ModuleType("backend.memory.scope")
    seen: list[tuple[str, str]] = []

    def suggest_entries(text, project_root="", *, limit=3):
        seen.append((text, project_root))
        return ["team-workflow/git-and-worktrees"] if "worktree" in text.lower() else []

    project.suggest_entries = suggest_entries
    scope.memory_root = lambda conv_id="", project_root="", plan=None: "C:/repo"
    monkeypatch.setitem(sys.modules, "backend.memory.project", project)
    monkeypatch.setitem(sys.modules, "backend.memory.scope", scope)
    return seen


def _plan(body: str) -> dict:
    return {"nodes": [{"id": "s", "content": "Seat B", "assignee": "chat-b",
                       "body_markdown": "Files: `backend/`", "children": [
                           {"id": "s1", "content": "Use a worktree per seat", "body_markdown": body}]}]}


def _read_first(issues: list[dict]) -> list[dict]:
    return [i for i in issues if "Read first" in i["issue"]]


def test_a_worktree_step_gets_the_git_lesson(memory) -> None:
    hits = _read_first(plan_review.plan_review(_plan(BODY)))
    assert hits == [{"n": "1.1", "issue": "Add `Read first: team-workflow/git-and-worktrees`"}]
    assert memory and memory[0][1] == "C:/repo"


def test_a_step_that_names_its_lessons_gets_nothing(memory) -> None:
    body = BODY + "\nRead first: team-workflow/git-and-worktrees"
    assert _read_first(plan_review.plan_review(_plan(body))) == []


def test_without_memory_there_is_no_advice(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "backend.memory.project", None)  # import fails
    assert _read_first(plan_review.plan_review(_plan(BODY))) == []
