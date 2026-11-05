"""A seat's task text keeps each point whole: its Do, Tests and Done when are never cut."""

from __future__ import annotations

import pytest

from frontend.ui_web import team_keeper


@pytest.fixture(autouse=True)
def _no_memory(monkeypatch):
    from backend.memory import project as memory

    monkeypatch.setattr(memory, "list_entries", lambda project_root="": [])
    monkeypatch.setattr(memory, "suggest_entries", lambda text, project_root="", *, limit=3: [])


def _big_step(tmp_path) -> tuple[dict, str]:
    full = ("**Why:** " + "the seat brief was cut. " * 260 + "\n**Do:**\n1. Keep the body whole.\n"
            "**Tests:** a 6k step keeps its parts.\n**Done when:** Seat H's task text ends with this line.")
    assert len(full) > 6_000
    link = ".ducky/plans/p1/notes/h1.md"
    (tmp_path / ".ducky" / "plans" / "p1" / "notes").mkdir(parents=True)
    (tmp_path / link).write_text(full, encoding="utf-8")
    # What the plan stores: ~2 KB of it, then the link (plans._cap_notes).
    stored = full[:1900] + f"\n\n…full notes: {link}"
    return {"id": "h1", "content": "Keep it whole", "status": "pending", "body_markdown": stored}, full


def test_a_6k_step_keeps_its_do_tests_and_done_when(tmp_path) -> None:
    point, full = _big_step(tmp_path)
    spec = "## Decisions\n" + "Keep every rule. " * 200  # a spec section that always stays whole
    plan = {"chat_id": "lead", "title": "Release", "project_root": str(tmp_path), "body_markdown": spec}
    work = {"section": {"id": "h", "content": "Seat H", "children": [point]}, "points": [point]}
    text = team_keeper._task_text(plan, work)
    for part in ("**Do:**", "**Tests:**", "**Done when:** Seat H's task text ends with this line."):
        assert part in text
    assert "…full notes" not in text
    assert "Keep every rule." in text  # under the budget the spec stays
    # Three such points run past it: the spec gives way to a pointer, the points stay whole.
    work = {"section": {"id": "h", "content": "Seat H", "children": [point]}, "points": [point] * 3}
    text = team_keeper._task_text(plan, work)
    assert 'read it with ducky_get_plan(chat_id="lead")' in text and "Keep every rule." not in text
    assert text.count("**Done when:** Seat H's task text ends with this line.") == 3


def test_a_short_task_keeps_the_spec(tmp_path) -> None:
    point = {"id": "p", "content": "Small", "status": "pending", "body_markdown": "**Done when:** pushed."}
    plan = {"chat_id": "lead", "title": "T", "project_root": str(tmp_path), "body_markdown": "Use the repo layout."}
    text = team_keeper._task_text(plan, {"section": {}, "points": [point]})
    assert "Use the repo layout." in text and "**Done when:** pushed." in text
