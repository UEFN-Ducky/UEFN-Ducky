"""Chat context follows persisted ownership, including All-projects lookups."""

from __future__ import annotations

import json

import pytest

from backend.tools.panel import ducky_panel as panel
from frontend.settings import PanelSettings
from frontend.ui_web import project_chats as pc
from frontend.ui_web.recent_projects import add_recent_project, remove_recent_project


@pytest.fixture(params=["db", "files"])
def projects(request, monkeypatch, tmp_path):
    monkeypatch.setenv("DUCKY_STORE_BACKEND", request.param)
    monkeypatch.setenv("DUCKY_STORE_BACKEND_CHATS", request.param)
    monkeypatch.setenv("DUCKY_STORE_BACKEND_PROJECTS", request.param)
    roots = {}
    for name in ("ActiveIsland", "HomeIsland", "ThirdIsland"):
        root = tmp_path / name
        (root / "Content").mkdir(parents=True)
        roots[name] = str(root)
        add_recent_project(str(root))
    settings = PanelSettings.load()
    settings.uefn_project_root = roots["ActiveIsland"]
    settings.save()
    return roots


def _chat(root):
    conv = pc.create_conversation(project_root=root, title="Home chat", skill_snapshot="test")
    pc.append_message(conv, {"role": "assistant", "content": "Home knowledge", "ts": 1.0}, root)
    return conv


@pytest.fixture
def live_context(monkeypatch):
    from backend.agent import context_memory
    from frontend.ui_web import context_tokens

    calls = []

    def compute(conv_id, model, **kwargs):
        calls.append((conv_id, model, kwargs))
        return {"used_tokens": 123}

    monkeypatch.setattr(context_tokens, "compute_context_usage", compute)
    monkeypatch.setattr(context_memory, "chat_context_memory_status", lambda *a, **k: {})
    monkeypatch.setattr(context_memory, "should_compress", lambda *a, **k: False)
    return calls


@pytest.mark.parametrize("requested", ["", "ActiveIsland", "HomeIsland", "ThirdIsland", "path", "slug"])
def test_foreign_chat_context_uses_its_home_project(projects, live_context, requested):
    home = projects["HomeIsland"]
    conv = _chat(home)
    project = home if requested == "path" else pc.project_slug(home) if requested == "slug" else requested

    result = json.loads(panel.ducky_get_chat_context(conv.id, project=project))

    assert result["scope"] == "cross_project_summary"
    assert result["project"] == "HomeIsland"
    assert result["conv_id"] == conv.id
    assert result["last_messages"][0]["content"] == "Home knowledge"
    assert result["message_count"] == 1
    assert live_context == []
    assert PanelSettings.load().uefn_project_root == projects["ActiveIsland"]


@pytest.mark.parametrize("requested", ["", "HomeIsland", "ThirdIsland"])
def test_active_chat_keeps_live_context_with_another_project_hint(projects, live_context, requested):
    conv = _chat(projects["ActiveIsland"])
    result = json.loads(panel.ducky_get_chat_context(conv.id, project=requested, model="test-model", mode="ask"))

    assert result["used_tokens"] == 123
    assert result["conv_id"] == conv.id
    assert result.get("scope") != "cross_project_summary"
    assert live_context == [(conv.id, "test-model", {"mode": "ask"})]
    assert PanelSettings.load().uefn_project_root == projects["ActiveIsland"]


def test_chat_owner_does_not_depend_on_recent_project_roster(projects, live_context):
    home = projects["HomeIsland"]
    conv = _chat(home)
    remove_recent_project(home)

    result = json.loads(panel.ducky_get_chat_context(conv.id, project="ThirdIsland"))

    assert result["scope"] == "cross_project_summary"
    assert result["project"] == pc.project_slug(home)
    assert live_context == []


def test_projectless_chat_is_not_labeled_with_the_selected_project(projects, live_context):
    conv = _chat("")
    result = json.loads(panel.ducky_get_chat_context(conv.id))

    assert result["scope"] == "cross_project_summary"
    assert result["project"] == "No project"
    assert live_context == []


@pytest.mark.parametrize("projectless", [False, True])
def test_context_with_no_selected_project(projects, live_context, projectless):
    settings = PanelSettings.load()
    settings.uefn_project_root = ""
    settings.save()
    conv = _chat("" if projectless else projects["HomeIsland"])

    result = json.loads(panel.ducky_get_chat_context(conv.id))

    if projectless:
        assert result["used_tokens"] == 123
        assert len(live_context) == 1
    else:
        assert result["scope"] == "cross_project_summary"
        assert result["project"] == "HomeIsland"
        assert live_context == []


def test_missing_chat_does_not_compute_context(projects, live_context):
    with pytest.raises(ValueError, match="Conversation not found"):
        panel.ducky_get_chat_context("missing-chat", project="ThirdIsland")
    assert live_context == []


def test_unknown_project_hint_still_raises(projects, live_context):
    conv = _chat(projects["HomeIsland"])
    with pytest.raises(ValueError, match="Unknown project"):
        panel.ducky_get_chat_context(conv.id, project="UnknownIsland")
    assert live_context == []
