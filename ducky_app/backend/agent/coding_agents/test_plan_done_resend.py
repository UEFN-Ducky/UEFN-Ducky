"""Resending what a completed step already holds is a no-op; a real change is still refused."""

from __future__ import annotations

import pytest

from backend.agent.coding_agents import plans, tick_gate


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    monkeypatch.setattr(tick_gate, "check_tick", lambda cid, node: None)
    monkeypatch.setattr(plans, "_push_assignment_invalidated", lambda *args: None)
    yield str(tmp_path)
    plans.drain_after_save()


def test_identical_resend_on_done_step_is_ok(root):
    plans.create_plan("lead", nodes=[{"id": "s1", "content": "Step"}, {"id": "s2", "content": "Next"}],
                      project_root=root)
    plans.update_node("lead", "s1", status="in_progress", project_root=root)
    plans.update_node("lead", "s1", status="completed", body_markdown="done", project_root=root)
    # The reply was lost; the agent sends the same tick again.
    plans.update_node("lead", "s1", status="completed", body_markdown="done", project_root=root)
    node = next(n for n in plans._flatten_nodes(plans.load_plan("lead", root)["nodes"]) if n["id"] == "s1")
    assert (node["status"], node["body_markdown"]) == ("completed", "done")
    with pytest.raises(ValueError, match="can't be edited"):
        plans.update_node("lead", "s1", body_markdown="something else", project_root=root)
