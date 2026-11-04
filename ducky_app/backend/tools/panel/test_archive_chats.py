"""ducky_archive_chats: nothing is archived or deleted until the person says yes, and a
chat never archives itself (or a group it sits in)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.tools.panel import ducky_panel


class _Api:
    def __init__(self):
        self.calls: list[tuple] = []

    def move_conversation(self, conv_id, folder_id):
        self.calls.append(("move", conv_id, folder_id))

    def delete_conversation(self, conv_id):
        self.calls.append(("delete", conv_id))

    def delete_folder(self, folder_id, archive_members=False):
        self.calls.append(("folder", folder_id, archive_members))
        return []


@pytest.fixture
def env(monkeypatch):
    chats = {
        "team": SimpleNamespace(id="team", title="Team: jobs", is_group=True, folder_id=""),
        "solo": SimpleNamespace(id="solo", title="Old chat", is_group=False, folder_id=""),
    }
    api = _Api()
    asked: list[str] = []
    state = {"answer": True, "inside": {"team": {"team", "member"}, "solo": {"solo"}}}
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: chats.get(cid))
    monkeypatch.setattr(ducky_panel, "_panel_api", lambda: api)
    monkeypatch.setattr(ducky_panel, "_chats_leaving_with", lambda conv, root: state["inside"][conv.id])
    monkeypatch.setattr("backend.tools.panel.panel_ui._resolve_ask_user_conv_id", lambda: "me")

    def confirm(prompt, title=""):
        asked.append(prompt)
        return state["answer"]

    monkeypatch.setattr("backend.tools.panel.panel_plugin_data._confirm", confirm)
    return SimpleNamespace(api=api, asked=asked, state=state)


def test_no_means_nothing_changes(env):
    env.state["answer"] = False
    out = json.loads(ducky_panel.ducky_archive_chats(["team", "solo"]))
    assert not out["ok"] and env.api.calls == []
    assert "“Team: jobs” (group, with its duckies)" in env.asked[0] and "“Old chat”" in env.asked[0]


def test_yes_archives_and_delete_deletes(env):
    assert json.loads(ducky_panel.ducky_archive_chats(["team"]))["archived"] == ["team"]
    assert env.api.calls == [("move", "team", "archive")]
    json.loads(ducky_panel.ducky_archive_chats(["solo"], delete=True))
    assert env.api.calls[-1] == ("delete", "solo") and "can't be undone" in env.asked[-1]


def test_never_archives_the_calling_chat(env):
    env.state["inside"]["team"].add("me")
    out = json.loads(ducky_panel.ducky_archive_chats(["team"]))
    assert not out["ok"] and "holds this chat" in out["error"]
    assert env.asked == [] and env.api.calls == []


def test_folder_goes_to_archive_with_everything_inside(env, monkeypatch):
    folder = SimpleNamespace(id="f1", name="Engine", parent_id="")
    monkeypatch.setattr(ducky_panel, "load_folders", lambda root=None: [folder])
    monkeypatch.setattr("frontend.ui_web.chats.folders.folder_subtree_ids", lambda fid, root=None: [fid])
    monkeypatch.setattr(ducky_panel, "list_conversations",
                        lambda project_root=None: [SimpleNamespace(id="c1", folder_id="f1")])
    out = json.loads(ducky_panel.ducky_archive_chats(folder_ids=["f1"]))
    assert out["ok"] and out["folders"] == ["f1"]
    assert env.api.calls == [("folder", "f1", True)]
    assert "folder “Engine” (with everything in it)" in env.asked[0]


def test_move_chat_refuses_archive():
    with pytest.raises(ValueError, match="ducky_archive_chats"):
        ducky_panel.ducky_move_chat("solo", "archive")
