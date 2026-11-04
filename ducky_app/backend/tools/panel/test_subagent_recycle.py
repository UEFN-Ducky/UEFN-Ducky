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
from frontend.ui_web.agent_turns.testing import patch_turns
from frontend.ui_web.test_context_session_reset import member  # noqa: F401  (fixture)

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
    from frontend.ui_web.chats import conversations as chat_convs
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    monkeypatch.setattr(chat_convs, "load_conversation", lambda cid, project_root=None: {"builder":conv,"group":group,"coord":SimpleNamespace(id="coord", title="Coordinator")}.get(cid))
    monkeypatch.setattr(chat_convs, "load_conversation_meta", chat_convs.load_conversation)
    patch_turns(monkeypatch, "_resolve_push", lambda _: lambda event: None)
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
    patch_turns(monkeypatch, "run_message_and_wait", run)
    patch_turns(monkeypatch, "run_message", run)
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
    patch_turns(monkeypatch, "run_message_and_wait", lambda *a, **k: {"status":"timeout"})
    patch_turns(monkeypatch, "is_agent_running", lambda _: True)
    clock = [0.0]
    monkeypatch.setattr(panel.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(panel.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    with pytest.raises(ValueError, match="working"):
        panel.ducky_recycle_member("builder")
    assert conv.upstream_session_id == "codex-thread-1"
    assert member.saved == [] and member.cancelled == []


@pytest.mark.parametrize("running", [False, True])
def test_stop_marks_only_a_running_chat_stopped(monkeypatch, running):
    # Oct 25: stopping an idle member marked it stopped, which blocks every automatic wake
    # (plan assignments included) until someone types in it, so it never took its next task.
    from backend.agent import a2a_client

    marked = []
    monkeypatch.setattr(panel, "_project_root", lambda: "project")
    monkeypatch.setattr(panel, "load_conversation", lambda cid, project_root=None: SimpleNamespace(id=cid))
    patch_turns(monkeypatch, "is_agent_running", lambda _: running)
    patch_turns(monkeypatch, "cancel_agent", lambda cid: None)
    monkeypatch.setattr(a2a_client, "on_agent_cancelled_by_user", marked.append)
    out = json.loads(panel.ducky_agent_stop("member"))
    assert out["stopped"] == (["member"] if running else [])
    assert marked == (["member"] if running else [])


def test_recycle_waits_for_the_handoff_turn_to_go_idle(member, monkeypatch):
    # Oct 25: the handoff reply came back while its run was still marked running, so the
    # session reset refused ("agent is working") and every recycle failed after its handoff.
    conv = member.conv
    conv.parent_conv_id = "group"
    conv.folder_id = "folder"
    conv.ducky_name = "Builder"
    conv.is_group = False
    group = SimpleNamespace(is_group=True)
    monkeypatch.setattr(panel, "_project_root", lambda: "project")
    monkeypatch.setattr(panel, "load_conversation", lambda cid, project_root=None: conv if cid == "builder" else group)
    monkeypatch.setattr(panel, "_resolve_sender", lambda _: "")
    monkeypatch.setattr(permission_prompt, "approvals_of", lambda _: {})
    patch_turns(monkeypatch, "run_message_and_wait", lambda *a, **k: {"status": "done", "assistant_text": "Handoff"})
    patch_turns(monkeypatch, "run_message", lambda *a, **k: "started")
    still_running = [True, True, True]
    patch_turns(monkeypatch, "is_agent_running", lambda _: bool(still_running and still_running.pop()))
    monkeypatch.setattr(panel.time, "sleep", lambda s: None)
    out = json.loads(panel.ducky_recycle_member("builder", wait_for_reply=False))
    assert out["status"] == "running" and not still_running
    assert conv.upstream_session_id == ""
