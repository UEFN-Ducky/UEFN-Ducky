"""Spawned agents and groups always land inside the spawner's own group (Nov 5 2026: a lead's
new group landed at the top level and the lead took the lead of the team it spawned)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import frontend.ui_web.panel_api as _pa
from backend.tools.panel import ducky_panel
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from frontend.ui_web.panel_chats import groups


class _Store:
    """Folders and chats shared by the panel api and ducky_panel."""

    def __init__(self) -> None:
        self.folders: list = []
        self.convs: dict = {}
        self.n = 0

    def folder(self, fid, parent="", hub=""):
        self.folders.append(SimpleNamespace(id=fid, name=fid, parent_id=parent, group_hub_id=hub))

    def group(self, hub, folder, parent_folder="", parent_conv="", leader=""):
        self.folder(folder, parent_folder, hub)
        self.convs[hub] = SimpleNamespace(id=hub, title=hub, folder_id=folder, is_group=True,
                                          parent_conv_id=parent_conv, leader_conv_id=leader, group_members=[])

    def chat(self, cid, folder="", parent=""):
        self.convs[cid] = SimpleNamespace(id=cid, title=cid, folder_id=folder, is_group=False, parent_conv_id=parent,
                                          model="", provider="", coding_agent="ducky")


@pytest.fixture
def store(monkeypatch):
    s = _Store()

    def create_folder(name, parent, root, group_hub_id=""):
        s.n += 1
        s.folder(f"f-{name}", parent, group_hub_id)
        return s.folders[-1]

    def create_conversation(settings, folder_id, **kw):
        return SimpleNamespace(id=kw["conv_id"], title=kw["title"], folder_id=folder_id, is_group=False,
                               parent_conv_id="", leader_conv_id="", group_members=[])

    def save(conv, root=None, **kw):
        s.convs[conv.id] = conv

    load = lambda cid, project_root=None, **kw: s.convs.get(cid)  # noqa: E731
    folders = lambda root=None: list(s.folders)  # noqa: E731
    for mod in (_pa, ducky_panel):
        monkeypatch.setattr(mod, "load_conversation", load)
        monkeypatch.setattr(mod, "load_folders", folders)
        monkeypatch.setattr(mod, "save_conversation", save)
    monkeypatch.setattr(_pa, "create_folder", create_folder)
    monkeypatch.setattr(_pa, "create_conversation", create_conversation)
    monkeypatch.setattr(_pa, "default_bundled_style", lambda: "classic")
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **kw: None)
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "flush_chat_saves", lambda *a, **kw: True)

    class Api:
        def group_find_or_create(self, name="", folder_id="", open_tab=False, caller_conv_id=""):
            return groups._group_create_locked(PanelApiChatsMixin(), name, folder_id, SimpleNamespace(), None, False)

        def group_seat_profile(self, group_id, profile_id, model="", write_allowed=None, for_agent=False,
                               exclude_conv_id="", name=""):
            hub = s.convs[group_id]
            s.chat("y", hub.folder_id, group_id)
            hub.group_members = [*hub.group_members, {"member_conv_id": "y", "name": name}]
            return {"ok": True, "member": {"member_conv_id": "y", "name": name, "ducky_name": name}}

    monkeypatch.setattr(ducky_panel, "_panel_api", lambda: Api())
    return s


def _as(monkeypatch, caller):
    monkeypatch.setattr(ducky_panel, "_resolve_sender", lambda sender: sender or caller)


def test_a_group_made_by_a_member_of_g_is_nested_in_g(monkeypatch, store):
    store.group("G", "fG", leader="lead")
    store.chat("lead", "fG", "G")
    _as(monkeypatch, "lead")
    res = json.loads(ducky_panel.ducky_group_create("X"))
    x = store.convs[res["id"]]
    folder = next(f for f in store.folders if f.id == res["folder_id"])
    assert folder.parent_id == "fG" and x.parent_conv_id == "G"


def test_a_parent_folder_outside_the_callers_group_is_refused(monkeypatch, store):
    store.group("G", "fG", leader="lead")
    store.group("H", "fH")
    store.chat("lead", "fG", "G")
    _as(monkeypatch, "lead")
    with pytest.raises(ValueError, match="Spawn inside your own group: fG"):
        ducky_panel.ducky_group_create("X", parent_folder_id="fH")
    with pytest.raises(ValueError, match="Spawn inside your own group: G"):
        ducky_panel.ducky_spawn_chat("hi", ducky="Developer", group_id="H", sender="lead")


def test_a_lead_seat_leads_the_new_group_and_the_caller_stays_outside_it(monkeypatch, store):
    store.group("G", "fG", leader="lead")
    store.chat("lead", "fG", "G")
    _as(monkeypatch, "lead")
    x_id = json.loads(ducky_panel.ducky_group_create("X"))["id"]
    monkeypatch.setattr(ducky_panel, "_resolve_ducky_profile", lambda d: {"id": "developer", "name": "Developer"})
    monkeypatch.setattr(ducky_panel, "_spawn_needs_plan", lambda *a: False)
    monkeypatch.setattr(ducky_panel, "_from_another_ducky", lambda cid, text, root, sender="": text)
    monkeypatch.setattr("frontend.ui_web.agent_turns.routing.run_message_and_wait",
                        lambda *a, **k: {"status": "done", "assistant_text": "ok"})

    data = json.loads(ducky_panel.ducky_spawn_chat("go", ducky="Developer", group_id=x_id, name="Lead: Y",
                                                   sender="lead"))

    x, g = store.convs[x_id], store.convs["G"]
    assert data["group_id"] == x_id and x.leader_conv_id == "y"
    assert g.leader_conv_id == "lead"
    assert "lead" not in {m["member_conv_id"] for m in x.group_members}


def test_a_caller_in_no_group_still_makes_a_top_level_group(monkeypatch, store):
    store.chat("solo")
    _as(monkeypatch, "solo")
    res = json.loads(ducky_panel.ducky_group_create("X"))
    folder = next(f for f in store.folders if f.id == res["folder_id"])
    assert folder.parent_id == "" and store.convs[res["id"]].parent_conv_id == ""
