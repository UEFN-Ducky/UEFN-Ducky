"""panel_chats.groups: seat names, leaders and refusals on chats that are not groups."""

from __future__ import annotations

from types import SimpleNamespace

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from frontend.ui_web.panel_chats import groups


def test_a_second_seat_gets_the_next_free_copy_name():
    members = [{"name": "Verse Coder"}, {"ducky_name": "Verse Coder 2"}]
    assert groups._copy_name({"name": "Verse Coder"}, members) == "Verse Coder 3"
    assert groups._copy_name({}, []) == "Ducky 2"


def test_group_rpcs_refuse_a_chat_that_is_not_a_group(monkeypatch):
    monkeypatch.setattr(_pa, "load_conversation", lambda *a, **kw: SimpleNamespace(is_group=False))
    api = PanelApiChatsMixin()
    for call in (lambda: api.group_set_leader("g", "m"), lambda: api.group_remove("g", "m"),
                 lambda: api.group_get_lanes("g"), lambda: api.group_add_member("g", "m")):
        assert call() == {"ok": False, "error": "Not a group chat"}
    assert api.group_check_lane("g", "m", None)["errors"] == ["Not a group chat"]
    assert api.group_lane_authorities("g") == []


def test_a_member_counts_where_it_is_filed(monkeypatch):
    hub = SimpleNamespace(is_group=True)
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: hub if cid == "other-team" else None)
    assert groups._filed_with_group(SimpleNamespace(parent_conv_id="g"), "g")
    assert groups._filed_with_group(SimpleNamespace(parent_conv_id="other-team"), "g")
    assert not groups._filed_with_group(SimpleNamespace(parent_conv_id="gone"), "g")


def test_group_stop_cancels_the_members_but_not_the_lead(monkeypatch) -> None:
    """Stop team stops the members, never the team's lead."""
    from frontend.ui_web import group_orchestrator
    from frontend.ui_web.agent_turns.testing import patch_turns
    from frontend.ui_web.chats import conversations as chat_convs

    team = SimpleNamespace(id="team", is_group=True, leader_conv_id="lead")
    rows = [{"member_conv_id": "lead"}, {"member_conv_id": "a"}, {"member_conv_id": "b"}]
    monkeypatch.setattr(chat_convs, "load_conversation", lambda cid, **kw: team if cid == "team" else None)
    monkeypatch.setattr(group_orchestrator, "is_group_conversation", lambda conv: conv.is_group)
    monkeypatch.setattr(group_orchestrator, "group_and_nested_ids", lambda gid: [gid])
    monkeypatch.setattr(group_orchestrator, "group_members", lambda conv: rows)
    monkeypatch.setattr(group_orchestrator, "cancel_group_run", lambda gid: None)
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    api = PanelApiChatsMixin()
    cancelled: list[str] = []
    api.cancel_agent = cancelled.append
    res = api.group_stop("team")
    assert cancelled == ["a", "b"]
    assert {k: res[k] for k in ("ok", "stopped", "groups", "stopped_ids")} == {
        "ok": True, "stopped": 2, "groups": 1, "stopped_ids": ["a", "b"]}
    assert api.group_stop("a")["ok"] is False  # not a group


def test_an_api_key_chat_is_refused_to_agents(monkeypatch):
    from frontend import favorite_models

    conv = SimpleNamespace(title="Paid", coding_agent="ducky", provider="openai", model="gpt")
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: conv)
    monkeypatch.setattr(favorite_models, "billing_kind", lambda *a: "api_key")
    monkeypatch.setattr(_pa.PanelSettings, "load", staticmethod(lambda: SimpleNamespace(agent_api_keys=[])))
    assert "API key" in groups._agent_api_key_refusal("c")
    monkeypatch.setattr(_pa.PanelSettings, "load", staticmethod(lambda: SimpleNamespace(agent_api_keys=["OpenAI"])))
    assert groups._agent_api_key_refusal("c") == ""


def _fake_group_store(monkeypatch, *, lands: bool):
    """A store where a group save lands (or never does), recording each save's wait."""
    folders: list = []
    stored: dict = {}
    waits: list = []

    def create_folder(name, parent, root, group_hub_id=""):
        folder = SimpleNamespace(id="f-new", name=name, parent_id=parent, group_hub_id=group_hub_id)
        folders.append(folder)
        return folder

    def create_conversation(settings, folder_id, **kw):
        return SimpleNamespace(id=kw["conv_id"], title=kw["title"], folder_id=folder_id,
                               is_group=False, parent_conv_id="", leader_conv_id="", group_members=[])

    def save_conversation(conv, root=None, *, wait=False, full=False):
        waits.append(wait)
        if lands:
            stored[conv.id] = SimpleNamespace(**vars(conv))

    monkeypatch.setattr(_pa, "create_folder", create_folder)
    monkeypatch.setattr(_pa, "create_conversation", create_conversation)
    monkeypatch.setattr(_pa, "save_conversation", save_conversation)
    monkeypatch.setattr(_pa, "load_conversation", lambda cid, project_root=None: stored.get(cid))
    monkeypatch.setattr(_pa, "load_folders", lambda root=None: list(folders))
    monkeypatch.setattr(_pa, "default_bundled_style", lambda: "classic")
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **kw: None)
    from frontend.ui_web.chats import folders as folder_store

    monkeypatch.setattr(folder_store, "save_folders", lambda fs, root=None: folders.__setitem__(slice(None), fs))
    return folders, stored, waits


def test_a_new_group_is_written_before_it_answers_so_a_seat_call_finds_it(monkeypatch):
    # Nov 5 2026: ducky_group_create said ok, then ducky_spawn_chat into it said "not a group hub".
    folders, stored, waits = _fake_group_store(monkeypatch, lands=True)
    res = groups._group_create_locked(PanelApiChatsMixin(), "Release X", "", SimpleNamespace(), None, False)
    assert res["ok"] and stored[res["id"]].is_group and waits[-1] is True
    assert [f.id for f in folders] == ["f-new"]


def test_a_group_whose_save_never_lands_answers_not_ok_and_leaves_no_folder(monkeypatch):
    folders, stored, _ = _fake_group_store(monkeypatch, lands=False)
    res = groups._group_create_locked(PanelApiChatsMixin(), "Release X", "", SimpleNamespace(), None, False)
    assert not res["ok"] and "not saved" in res["error"] and folders == [] and stored == {}


def test_a_seat_call_right_after_create_finds_the_group_by_its_id_and_by_its_folder(monkeypatch):
    from backend.tools.panel import ducky_panel
    from frontend.ui_web.chats import saves

    folders, stored, _ = _fake_group_store(monkeypatch, lands=True)
    res = groups._group_create_locked(PanelApiChatsMixin(), "Release X", "", SimpleNamespace(), None, False)
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: stored.get(cid))
    monkeypatch.setattr(ducky_panel, "load_folders", lambda root=None: list(folders))
    monkeypatch.setattr(saves, "flush_chat_saves", lambda *a, **kw: True)
    assert ducky_panel._resolve_group_hub_id(group_id=res["id"]) == res["id"]
    assert ducky_panel._resolve_group_hub_id(folder_id=res["folder_id"]) == res["id"]
