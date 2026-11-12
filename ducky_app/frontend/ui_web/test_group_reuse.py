"""A ducky seats into the group it already has, and a workflow reuses that seat."""

from __future__ import annotations

import json
import pytest
from types import SimpleNamespace

import frontend.ui_web.panel_api as pa
from frontend.ui_web.agent_turns.testing import patch_turns
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

_PROFILE = {"id": "verse-coder", "name": "Verse Coder", "ducky_style": "classic"}


def _api() -> PanelApiChatsMixin:
    return PanelApiChatsMixin()


def test_same_name_group_is_reused(monkeypatch):
    folder = SimpleNamespace(id="f1", name="Tycoony", parent_id="", group_hub_id="hub")
    hub = SimpleNamespace(
        id="hub", title="Tycoony", is_group=True, leader_conv_id="", group_members=[], folder_id="f1"
    )
    monkeypatch.setattr(pa, "load_folders", lambda *a, **k: [folder])
    monkeypatch.setattr(pa, "load_conversation", lambda cid, *a, **k: hub if cid == "hub" else None)
    api = _api()
    api.group_create = lambda *a, **k: (_ for _ in ()).throw(AssertionError("created a duplicate"))  # type: ignore[method-assign]

    out = api.group_find_or_create("Tycoony", "")

    assert out["reused"] is True and out["id"] == "hub" and out["folder_id"] == "f1"


def test_same_name_group_is_reused_after_the_caller_moved(monkeypatch):
    # Oct 22 2026: the caller's folder changed between runs and "Deploy core to Fly" was made 3 times.
    folders = [
        SimpleNamespace(id="lead-team", name="Lead team", parent_id="", group_hub_id="hub-lead"),
        SimpleNamespace(id="f1", name="Deploy core to Fly", parent_id="", group_hub_id="hub"),
    ]
    hub = SimpleNamespace(
        id="hub", title="Deploy core to Fly", is_group=True, leader_conv_id="", group_members=[], folder_id="f1"
    )
    monkeypatch.setattr("frontend.ui_web.chats.folders.folder_project_slug", lambda fid: "")
    monkeypatch.setattr("frontend.ui_web.chats.conversations.conversation_project_slug", lambda cid: "")
    monkeypatch.setattr(pa, "load_folders", lambda *a, **k: folders)
    monkeypatch.setattr(pa, "load_conversation", lambda cid, *a, **k: hub if cid == "hub" else None)
    api = _api()
    api.group_create = lambda *a, **k: (_ for _ in ()).throw(AssertionError("created a duplicate"))  # type: ignore[method-assign]

    out = api.group_find_or_create("Deploy core to Fly", "lead-team", caller_conv_id="caller")

    assert out["reused"] is True and out["id"] == "hub" and out["folder_id"] == "f1"


def test_idle_member_is_reused(monkeypatch):
    member = {
        "member_conv_id": "m1",
        "profile_id": "verse-coder",
        "name": "Verse Coder",
        "ducky_name": "Verse Coder",
    }
    group = SimpleNamespace(id="hub", is_group=True, leader_conv_id="m1", group_members=[member], folder_id="f1")
    monkeypatch.setattr("frontend.agent_profiles.get_agent_profile", lambda pid: _PROFILE)
    monkeypatch.setattr(pa, "load_conversation", lambda cid, *a, **k: group if cid == "hub" else SimpleNamespace(id=cid))
    patch_turns(monkeypatch, "is_agent_running", lambda cid: False)
    api = _api()
    api.group_invite = lambda *a, **k: (_ for _ in ()).throw(AssertionError("invited a duplicate"))  # type: ignore[method-assign]

    out = api.group_seat_profile("hub", "verse-coder")

    assert out["reused"] is True and out["member"]["member_conv_id"] == "m1"


def test_a_just_finished_member_is_reused_once_its_thread_ends(monkeypatch):
    member = {
        "member_conv_id": "m1",
        "profile_id": "verse-coder",
        "name": "Verse Coder",
        "ducky_name": "Verse Coder",
    }
    group = SimpleNamespace(id="hub", is_group=True, leader_conv_id="m1", group_members=[member], folder_id="f1")
    busy = {"on": True}
    monkeypatch.setattr("frontend.agent_profiles.get_agent_profile", lambda pid: _PROFILE)
    monkeypatch.setattr(pa, "load_conversation", lambda cid, *a, **k: group if cid == "hub" else SimpleNamespace(id=cid))
    patch_turns(monkeypatch, "is_agent_running", lambda cid: busy["on"])

    def settle(cid, timeout=0):
        busy["on"] = False
        return True

    patch_turns(monkeypatch, "wait_for_idle", settle)
    api = _api()
    api.group_invite = lambda *a, **k: (_ for _ in ()).throw(AssertionError("invited a duplicate"))  # type: ignore[method-assign]

    out = api.group_seat_profile("hub", "verse-coder")

    assert out["reused"] is True and out["member"]["member_conv_id"] == "m1"


@pytest.mark.parametrize("busy, excluded", [(True, ""), (False, "m1")])
def test_busy_member_or_caller_gets_a_numbered_copy(monkeypatch, busy, excluded):
    member = {
        "member_conv_id": "m1",
        "profile_id": "verse-coder",
        "name": "Verse Coder",
        "ducky_name": "Verse Coder",
    }
    group = SimpleNamespace(id="hub", is_group=True, leader_conv_id="m1", group_members=[member], folder_id="f1")
    monkeypatch.setattr("frontend.agent_profiles.get_agent_profile", lambda pid: _PROFILE)
    monkeypatch.setattr(pa, "load_conversation", lambda cid, *a, **k: group if cid == "hub" else SimpleNamespace(id=cid))
    patch_turns(monkeypatch, "is_agent_running", lambda cid: busy)
    patch_turns(monkeypatch, "wait_for_idle", lambda *a: False)
    seated: dict[str, str] = {}

    def invite(group_id, profile_id, model="", write_allowed=None, name="", for_agent=False):
        seated["group_id"] = group_id
        seated["name"] = name
        return {"ok": True, "member": {"member_conv_id": "m2", "name": name}}

    api = _api()
    api.group_invite = invite  # type: ignore[method-assign]

    out = api.group_seat_profile("hub", "verse-coder", exclude_conv_id=excluded)

    assert seated == {"group_id": "hub", "name": "Verse Coder 2"}
    assert out["reused"] is False and out["member"]["member_conv_id"] == "m2"


def test_an_agent_inviting_a_second_ducky_of_a_profile_gets_a_numbered_copy(monkeypatch):
    # Oct 21 2026: a lead asked for 2 Verse Coders; the invite refused the second one.
    member = {"member_conv_id": "m1", "profile_id": "verse-coder", "name": "Verse Coder", "ducky_name": "Verse Coder"}
    group = SimpleNamespace(id="hub", is_group=True, leader_conv_id="m1", group_members=[member], folder_id="f1")
    monkeypatch.setattr("frontend.agent_profiles.get_agent_profile", lambda pid: _PROFILE)
    monkeypatch.setattr(pa, "load_conversation", lambda cid, *a, **k: group)
    monkeypatch.setattr(pa.PanelSettings, "load", staticmethod(lambda: SimpleNamespace(agent_api_keys=[])))
    monkeypatch.setattr(pa, "resolve_model_selection", lambda favorites, settings: None)
    chosen = SimpleNamespace(model="gpt-6-luna", provider="", coding_agent="codex")
    monkeypatch.setattr("frontend.favorite_models.allow_create_without_model", lambda result: chosen)
    monkeypatch.setattr("frontend.favorite_models.refuse_paid_api_for_agent", lambda result, settings, *_a: chosen)
    monkeypatch.setattr("frontend.ui_web.group_orchestrator._group_folder_id", lambda gid: "f1")
    titles: list[str] = []

    class Made(Exception):
        pass

    def create(settings, folder_id, **kw):
        titles.append(kw["title"])
        raise Made

    monkeypatch.setattr(pa, "create_conversation", create)
    api = _api()
    assert api.group_invite("hub", "verse-coder")["error"] == "That ducky is already in this group"  # the UI
    with pytest.raises(Made):
        api.group_invite("hub", "verse-coder", for_agent=True)
    assert titles == ["Verse Coder 2"]


def _seat_env(monkeypatch, members):
    group = SimpleNamespace(id="hub", is_group=True, leader_conv_id="m1", group_members=members, folder_id="f1")
    monkeypatch.setattr("frontend.agent_profiles.get_agent_profile", lambda pid: _PROFILE)
    monkeypatch.setattr(pa, "load_conversation", lambda cid, *a, **k: group if cid == "hub" else SimpleNamespace(id=cid))
    patch_turns(monkeypatch, "is_agent_running", lambda cid: False)
    return group


def test_a_named_seat_is_made_with_its_role_name(monkeypatch):
    # Oct 23 2026: a lead seated 5 members and could only get "Verse Coder 2..6".
    _seat_env(monkeypatch, [{"member_conv_id": "m1", "profile_id": "verse-coder", "name": "Verse Coder"}])
    seated: dict[str, str] = {}

    def invite(group_id, profile_id, model="", write_allowed=None, name="", for_agent=False):
        seated["name"] = name
        return {"ok": True, "member": {"member_conv_id": "m2", "name": name}}

    api = _api()
    api.group_invite = invite  # type: ignore[method-assign]
    out = api.group_seat_profile("hub", "verse-coder", name="Search lead")
    assert seated == {"name": "Search lead"} and out["reused"] is False


def test_an_idle_seat_with_that_role_name_is_reused(monkeypatch):
    _seat_env(monkeypatch, [
        {"member_conv_id": "m1", "profile_id": "verse-coder", "name": "Verse Coder"},
        {"member_conv_id": "m2", "profile_id": "verse-coder", "name": "Search lead"},
    ])
    api = _api()
    api.group_invite = lambda *a, **k: (_ for _ in ()).throw(AssertionError("made a new seat"))  # type: ignore[method-assign]
    out = api.group_seat_profile("hub", "verse-coder", name="Search lead")
    assert out["reused"] is True and out["member"]["member_conv_id"] == "m2"


def test_two_seats_never_share_a_name(monkeypatch):
    _seat_env(monkeypatch, [{"member_conv_id": "m1", "profile_id": "verse-coder", "name": "Search lead"}])
    out = _api().group_invite("hub", "verse-coder", name="Search lead", for_agent=True)
    assert out["ok"] is False and "already called 'Search lead'" in out["error"]


def test_profile_rename_keeps_role_names(monkeypatch):
    chats = {
        "a": SimpleNamespace(id="a", profile_id="verse-coder", ducky_name="Verse Coder", title="a", folder_id=""),
        "b": SimpleNamespace(id="b", profile_id="verse-coder", ducky_name="Verse Coder 2", title="b", folder_id=""),
        "c": SimpleNamespace(id="c", profile_id="verse-coder", ducky_name="Search lead", title="c", folder_id=""),
    }
    monkeypatch.setattr(pa, "list_all_conversation_metadata", lambda: list(chats.values()))
    monkeypatch.setattr(pa, "load_conversation", lambda cid, *a, **k: chats[cid])
    monkeypatch.setattr(pa, "save_conversation", lambda conv, **k: None)
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: None)
    _api()._sync_chats_for_profile({"id": "verse-coder", "name": "Code Ducky"}, "Verse Coder")
    assert [c.ducky_name for c in chats.values()] == ["Code Ducky", "Code Ducky 2", "Search lead"]


def test_spawn_from_a_group_member_joins_that_group(monkeypatch):
    from backend.tools.panel import ducky_panel

    member = SimpleNamespace(
        id="mem", title="Verse Coder", is_group=False, parent_conv_id="hub",
        folder_id="f1", model="", provider="", coding_agent="ducky",
    )
    hub = SimpleNamespace(id="hub", is_group=True, title="Swarm", folder_id="f1", leader_conv_id="mem")

    def load(cid, project_root=None):
        return {"mem": member, "hub": hub}.get(cid)

    monkeypatch.setattr(ducky_panel, "load_conversation", load)
    monkeypatch.setattr(ducky_panel, "_resolve_ducky_profile", lambda ducky: _PROFILE)
    seated: dict[str, str] = {}

    class Api:
        def group_seat_profile(self, group_id, profile_id, model="", write_allowed=None, for_agent=False, exclude_conv_id=""):
            seated["group_id"] = group_id
            assert exclude_conv_id == "mem"
            return {
                "ok": True,
                "member": {
                    "member_conv_id": "worker",
                    "name": "Verse Coder",
                    "ducky_name": "Verse Coder",
                    "coding_agent": "ducky",
                    "model": "",
                },
            }

    monkeypatch.setattr(ducky_panel, "_panel_api", lambda: Api())
    monkeypatch.setattr(
        "frontend.ui_web.agent_turns.routing.run_message_and_wait",
        lambda *a, **k: {"status": "done", "assistant_text": "ok"},
    )

    data = json.loads(ducky_panel.ducky_spawn_chat("hello", ducky="Verse Coder", sender="mem"))

    assert seated["group_id"] == "hub"
    assert data["group_id"] == "hub" and data["conv_id"] == "worker"
