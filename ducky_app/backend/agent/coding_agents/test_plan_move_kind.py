"""Regression for dragging a child into a leaf step."""
import os
from unittest.mock import patch
from backend.agent.coding_agents import plans


def test_move_promotes_destination_and_persists(tmp_path):
    with patch.dict(os.environ, {"DUCKY_STORE_BACKEND_PLANS": "files"}):
        plans.create_plan("move-kind", title="Move", nodes=[
            {"id": "parent", "content": "Parent", "status": "pending", "kind": "step"},
            {"id": "child", "content": "Child", "status": "pending", "kind": "step"},
        ], project_root=str(tmp_path))
        result = plans.move_node("move-kind", "child", parent_id="parent", project_root=str(tmp_path))
        assert result["nodes"][0]["kind"] == "subplan"
        saved = plans.load_plan("move-kind", project_root=str(tmp_path))
        assert saved["nodes"][0]["kind"] == "subplan"
        assert saved["nodes"][0]["children"][0]["id"] == "child"
