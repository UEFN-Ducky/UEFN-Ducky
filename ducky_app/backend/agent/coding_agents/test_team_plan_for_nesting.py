"""A group leader's plan binds a chat only when that plan belongs to the chat's team."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import plans
from frontend.ui_web.chats import conversations as chat_convs

LEAD, GROUP, CHAT = "5d789e53", "0cadd26f", "88390a68"


def _conv(parent: str = "", folder: str = "", leader: str = "") -> SimpleNamespace:
    return SimpleNamespace(parent_conv_id=parent, folder_id=folder, leader_conv_id=leader)


@pytest.fixture()
def convs(tmp_path, monkeypatch):
    from frontend.ui_web.agent_turns.testing import patch_turns
    patch_turns(monkeypatch, "_resolve_push", lambda _: lambda event: None)
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PLANS", "files")
    table = {
        LEAD: _conv(folder="431c7fe8"),               # the 1.2.469 lead, in its own folder
        GROUP: _conv(folder="f47f242a", leader=LEAD),  # a stray group naming that lead
        CHAT: _conv(parent=GROUP, folder="f47f242a"),
        "builder": _conv(parent=GROUP, folder="f47f242a"),
    }
    monkeypatch.setattr(chat_convs, "load_conversation_meta",
                        lambda cid, project_root=None: table.get(cid))
    root = str(tmp_path)
    plans.create_plan(LEAD, title="Release 1.2.469", nodes=[
        {"id": "s1", "content": "Release", "children": [{"id": "s1a", "content": "Build"}]},
        {"id": "s2", "content": "Docs"},
    ], project_root=root, team_check=False)
    plans.update_node(LEAD, "s1a", status="in_progress", project_root=root)
    plans.drain_after_save()
    yield root, table
    plans.drain_after_save()


def test_a_stray_leaders_plan_does_not_bind_the_chat(convs) -> None:
    root, _ = convs
    assert plans.team_plan_for(CHAT, root) is None
    mine = plans.create_plan(CHAT, title="Memory fix", nodes=[{"id": "m", "content": "Fix"}], project_root=root)
    assert mine["chat_id"] == CHAT


def test_a_member_with_an_assigned_section_is_still_refused(convs) -> None:
    root, _ = convs
    plans.update_node(LEAD, "s1", assignee=CHAT, project_root=root)
    assert plans.team_plan_for(CHAT, root)["chat_id"] == LEAD
    with pytest.raises(ValueError, match="team's plan"):
        plans.create_plan(CHAT, title="Mine", nodes=[{"id": "m", "content": "Fix"}], project_root=root)


def test_a_section_for_the_group_binds_every_member(convs) -> None:
    root, _ = convs
    plans.update_node(LEAD, "s1", assignee=GROUP, project_root=root)
    assert plans.team_plan_for("builder", root)["chat_id"] == LEAD
    with pytest.raises(ValueError, match="team's plan"):
        plans.create_plan("builder", title="Mine", nodes=[{"id": "m", "content": "Fix"}], project_root=root)


def test_a_lead_in_the_same_folder_still_binds(convs) -> None:
    root, table = convs
    table[LEAD].folder_id = "f47f242a"
    assert plans.team_plan_for(CHAT, root)["chat_id"] == LEAD
