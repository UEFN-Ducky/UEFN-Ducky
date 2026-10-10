"""Helpers for subagent reuse / recycle (handoff → fresh twin)."""

from __future__ import annotations

from backend.tools.panel.ducky_panel import build_recycle_spawn_message, next_subagent_title


def test_next_subagent_title_bumps_version():
    assert next_subagent_title("Arena Level") == "Arena Level (v2)"
    assert next_subagent_title("Arena Level (v2)") == "Arena Level (v3)"
    assert next_subagent_title("Arena Level (v10)") == "Arena Level (v11)"


def test_build_recycle_spawn_message_includes_handoff_and_continue():
    text = build_recycle_spawn_message("Built the pads.", "Wire the triggers next.")
    assert "Handoff from previous session" in text
    assert "Built the pads." in text
    assert "Wire the triggers next." in text
    assert "FRESH session" in text


def test_build_recycle_spawn_message_handoff_only():
    text = build_recycle_spawn_message("Done the mesh.")
    assert "Done the mesh." in text
    assert "Continue with this task" not in text


import copy
import json
from types import SimpleNamespace
import pytest
from backend.tools.panel import ducky_panel as panel, permission_prompt
from frontend.ui_web import agent_modes, context_control
from frontend.ui_web.test_context_session_reset import member

@pytest.mark.parametrize("wait", [False, True])
@pytest.mark.parametrize("alias", [False, True])
def test_recycle_preserves_identity_history_membership_and_assignments(member, monkeypatch, tmp_path, wait, alias):
    conv = member.conv
    conv.parent_conv_id = "group"
    conv.folder_id = "folder"
    conv.ducky_name = "Builder"
    conv.coding_agent = "codex"
    conv.is_group = False
    group = SimpleNamespace(id="group", is_group=True, leader_conv_id="builder", group_members=[{"member_conv_id":"builder"}])
    from backend.agent.coding_agents import plans
    from frontend.ui_web import project_chats
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None: {"builder":conv,"group":group,"coord":SimpleNamespace(id="coord", title="Coordinator")}.get(cid))
    monkeypatch.setattr(agent_modes, "_resolve_push", lambda _: lambda event: None)
    plans.create_plan("coord", nodes=[{"id":"section", "content":"Assigned work", "assignee":"group"}], project_root=str(tmp_path))
    assignments = plans.load_plan("coord", project_root=str(tmp_path))
    before_group = copy.deepcopy(vars(group))
    before_assignments = copy.deepcopy(assignments)
    original_messages = copy.deepcopy(conv.messages)
    monkeypatch.setattr(panel, "_project_root", lambda: "project")
    monkeypatch.setattr(panel, "load_conversation", lambda cid, project_root=None: {"builder":conv,"group":group,"coord":SimpleNamespace(id="coord", title="Coordinator")}.get(cid))
    def forbidden(*args, **kwargs):
        pytest.fail("recycle must not delete, replace, or edit membership")
    monkeypatch.setattr(panel, "delete_conversation", forbidden)
    monkeypatch.setattr(panel, "create_conversation", forbidden)
    monkeypatch.setattr(panel, "save_conversation", forbidden)
    monkeypatch.setattr(permission_prompt, "approvals_of", lambda _: {"started_by":"owner", "allow_everything":True})
    from backend.agent import a2a_client
    threads = []
    monkeypatch.setattr(panel, "_resolve_sender", lambda _: "coord")
    def open_thread(sender, receiver, **kwargs):
        threads.append((sender, receiver, kwargs))
        return "recycle-result"
    monkeypatch.setattr(a2a_client, "open_thread", open_thread)
    starts = []
    def run(cid, text, mode, *args, **kwargs):
        starts.append((cid,text,conv.upstream_session_id,kwargs))
        if len(starts) == 1:
            conv.messages.append({"role":"assistant", "content":"Handoff kept"})
            return {"status":"done", "assistant_text":"Handoff kept"}
        assert conv.upstream_session_id == ""
        conv.upstream_session_id = "new-thread"
        return {"status":"done", "assistant_text":"Continued"}
    monkeypatch.setattr(agent_modes, "run_message_and_wait", run)
    monkeypatch.setattr(agent_modes, "run_message", run)
    fn = panel.ducky_recycle_subagent if alias else panel.ducky_recycle_member
    out = json.loads(fn("builder", continue_message="Next task", wait_for_reply=wait))
    assert out["conv_id"] == out["old_conv_id"] == "builder"
    assert conv.messages == original_messages + [{"role":"assistant", "content":"Handoff kept"}]
    assert vars(group) == before_group
    assert plans.load_plan("coord", project_root=str(tmp_path)) == before_assignments
    assert plans.assigned_plan_view("builder", project_root=str(tmp_path))["assigned_from"]["assignee"] == "group"
    assert conv.parent_conv_id == "group" and conv.folder_id == "folder"
    assert conv.upstream_session_id == "new-thread" and conv.coding_agent_stats is None
    assert member.saved == ["builder"] and member.cancelled == []
    assert len(starts) == 2 and all(s[0] == "builder" for s in starts)
    assert "Handoff kept" in starts[1][1] and "Next task" in starts[1][1]
    assert starts[1][3]["started_by"] == "owner"
    assert threads == ([] if wait else [("coord", "builder", {"deliver_result":True})])
    if not wait:
        assert out["response_id"] == "recycle-result"


def test_running_handoff_cannot_be_reset_or_replaced(member, monkeypatch):
    conv = member.conv
    conv.parent_conv_id = "group"
    group = SimpleNamespace(is_group=True)
    monkeypatch.setattr(panel, "_project_root", lambda: "project")
    monkeypatch.setattr(panel, "load_conversation", lambda cid, project_root=None: conv if cid == "builder" else group)
    monkeypatch.setattr(permission_prompt, "approvals_of", lambda _: {})
    monkeypatch.setattr(agent_modes, "run_message_and_wait", lambda *a, **k: {"status":"timeout"})
    monkeypatch.setattr(agent_modes, "is_agent_running", lambda _: True)
    with pytest.raises(ValueError, match="working"):
        panel.ducky_recycle_member("builder")
    assert conv.upstream_session_id == "codex-thread-1"
    assert member.saved == [] and member.cancelled == []
