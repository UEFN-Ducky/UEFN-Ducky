"""Plan ticks made in a turn whose reply invents files are undone at the end of that turn."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import plans, tick_claim_guard as guard, tick_gate

REPLY = """The map is built.

## Inventory
- **Verse device** / `game_manager.verse`, `pet_manager.verse` — compiled, 0 errors.
"""


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: None)
    monkeypatch.setattr(plans, "_push_assignment_invalidated", lambda *args: None)
    monkeypatch.setattr(plans, "push_plan_updated", lambda plan: None)
    yield str(tmp_path)
    plans.drain_after_save()


def _rec(name, args, status="success", result=None):
    return SimpleNamespace(name=name, arguments=args, status=status, result=result)


def _journal(monkeypatch, run):
    """The change journal's answer for this run: a dict, or an exception to raise."""
    def get_run(run_id, *, project_root):
        if isinstance(run, BaseException):
            raise run
        return run

    monkeypatch.setattr("backend.workspace.runtime.get_writer",
                        lambda: SimpleNamespace(journal=SimpleNamespace(get_run=get_run)))


def _plan(root: str) -> None:
    plans.create_plan("lead", nodes=[
        {"id": "verse", "content": "Write the game manager", "body_markdown": "Done when it compiles."},
        {"id": "pets", "content": "Write the pet manager"},
        {"id": "old", "content": "Pick the art style"},
        {"id": "ui", "content": "Build the HUD"},
    ], project_root=root)
    plans.update_node("lead", "old", status="completed", project_root=root)  # an earlier turn's work


def _status(root: str, node_id: str) -> str:
    return plans._walk_find(plans.load_plan("lead", root)["nodes"], node_id)[2]["status"]


def test_reads_the_steps_a_plan_tool_call_ticked() -> None:
    assert guard.ticked_steps(_rec("ducky_plan_update_node", {"node_id": "a", "status": "completed"})) == ["a"]
    assert guard.ticked_steps(_rec("ducky_plan_update_node", {"node_id": "a", "status": "in_progress"})) == []
    # A refused tick answers ok:false, and a failed call ticked nothing.
    assert guard.ticked_steps(_rec("ducky_plan_update_node", {"node_id": "a", "status": "completed"},
                                   result='{"ok": false, "error": "node not found"}')) == []
    assert guard.ticked_steps(_rec("ducky_plan_update_node", {"node_id": "a", "status": "completed"}, status="error")) == []
    whole = _rec("ducky_update_plan", {"todos": [{"id": "t1", "status": "completed"}, {"id": "t2", "status": "pending"}],
                                       "nodes": [{"id": "s", "status": "pending", "children": [{"id": "s1", "status": "completed"}]}]})
    assert guard.ticked_steps(whole) == ["t1", "s1"]
    wrapped = _rec("ducky_call_tool", {"name": "ducky_plan_update_node", "arguments": {"node_id": "w", "status": "completed"}})
    assert guard.is_tick_tool(wrapped) and guard.ticked_steps(wrapped) == ["w"]
    assert not guard.is_tick_tool(_rec("workspace_write_file", {"relative_path": "Verse/a.verse"}))
    assert guard.ticked_steps(_rec("workspace_write_file", {"relative_path": "Verse/a.verse"})) == []


def test_a_listed_file_is_invented_only_when_the_project_has_no_such_file(tmp_path) -> None:
    (tmp_path / "Content" / "Verse").mkdir(parents=True)
    (tmp_path / "Content" / "Verse" / "pet_manager.verse").write_text("# real", encoding="utf-8")
    assert guard.invented_files(REPLY, set(), str(tmp_path)) == ["game_manager.verse"]
    assert guard.invented_files(REPLY, {"game_manager.verse"}, str(tmp_path)) == []  # written this turn
    by_path = "## Inventory\n- `Content/Verse/pet_manager.verse`\n- `Content/Verse/ghost.verse`\n"
    assert guard.invented_files(by_path, set(), str(tmp_path)) == ["Content/Verse/ghost.verse"]
    assert guard.invented_files(REPLY, set(), "") == [], "no project folder: nothing can be told"
    assert guard.invented_files("All good, nothing to report.", set(), str(tmp_path)) == []


def test_only_a_change_to_the_project_counts_as_one(monkeypatch) -> None:
    _journal(monkeypatch, ValueError("Changeset run not found: r1"))
    assert guard.run_changed_project("r1", "P:/x") is False
    # Seating a team and sending messages are Ducky's own rows, not changes to the project.
    _journal(monkeypatch, {"entries": [{"editor": {"program": "ducky"}}, {"editor": {"program": "ducky"}}]})
    assert guard.run_changed_project("r1", "P:/x") is False
    _journal(monkeypatch, {"entries": [{"editor": {"program": "uefn"}, "outcome": "failed"}]})
    assert guard.run_changed_project("r1", "P:/x") is False
    _journal(monkeypatch, {"entries": [{"editor": {"program": "ducky"}}, {"path": "Content/Verse/a.verse"}]})
    assert guard.run_changed_project("r1", "P:/x") is True
    _journal(monkeypatch, {"entries": [{"editor": {"program": "blender"}}]})
    assert guard.run_changed_project("r1", "P:/x") is True
    # Unknown never undoes a tick.
    _journal(monkeypatch, RuntimeError("journal offline"))
    assert guard.run_changed_project("r1", "P:/x") is True
    assert guard.run_changed_project("", "P:/x") is True


def test_ticks_of_a_turn_that_invented_files_and_changed_nothing_are_undone(root, monkeypatch) -> None:
    _plan(root)
    _journal(monkeypatch, {"entries": [{"editor": {"program": "ducky"}}]})
    before = guard.completed_steps("lead", root)
    assert before == {"old"}
    for step in ("verse", "pets", "ui"):
        plans.update_node("lead", step, status="completed", project_root=root)
    ticked = [s for s in ("verse", "pets", "ui", "old") if s not in before]

    line = guard.undo_unearned_ticks("lead", "r1", ticked, REPLY, set(), root)
    assert line and line.startswith("3 plan steps ticked this turn were reopened (1, 2, 4)")
    assert "game_manager.verse, pet_manager.verse" in line and "changed nothing" in line
    assert [_status(root, s) for s in ("verse", "pets", "ui")] == ["pending"] * 3
    assert _status(root, "old") == "completed", "work from an earlier turn stays ticked"
    notes = plans._walk_find(plans.load_plan("lead", root)["nodes"], "verse")[2]["body_markdown"]
    assert notes.startswith("Done when it compiles.") and notes.endswith(guard.REOPEN_NOTE)
    assert plans.todo_progress(plans.load_plan("lead", root))["completed"] == 1


def test_ticks_stand_when_the_files_are_there_or_the_turn_changed_something(root, monkeypatch, tmp_path) -> None:
    _plan(root)
    for step in ("verse", "pets"):
        plans.update_node("lead", step, status="completed", project_root=root)
    # The turn placed things in the editor: its ticks stand, whatever the reply lists.
    _journal(monkeypatch, {"entries": [{"editor": {"program": "uefn"}}]})
    assert guard.undo_unearned_ticks("lead", "r1", ["verse", "pets"], REPLY, set(), root) is None
    # The listed files are in the project (an earlier turn wrote them): the ticks stand.
    _journal(monkeypatch, ValueError("Changeset run not found"))
    for name in ("game_manager.verse", "pet_manager.verse"):
        (tmp_path / name).write_text("# real", encoding="utf-8")
    assert guard.undo_unearned_ticks("lead", "r1", ["verse", "pets"], REPLY, set(), root) is None
    # A reply that lists no files proves nothing, and a turn that ticked nothing has nothing to undo.
    assert guard.undo_unearned_ticks("lead", "r1", ["verse"], "Read the code; the style is voxel.", set(), root) is None
    assert guard.undo_unearned_ticks("lead", "r1", [], REPLY, set(), root) is None
    assert [_status(root, s) for s in ("verse", "pets")] == ["completed", "completed"]
