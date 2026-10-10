"""A Ducky chat with no model takes the Default Model the way a new chat does."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import frontend.ui_web.panel_api as pa
from frontend.favorite_models import FavoriteSelection, ResolveErr, ResolveOk
from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin


@pytest.fixture
def chat(monkeypatch):
    conv = SimpleNamespace(id="c1", title="Builder", folder_id="", coding_agent="ducky", model="",
                           provider="", is_group=False)
    saved: list[tuple[str, str, str]] = []
    monkeypatch.setattr(pa, "load_conversation", lambda _cid: conv)
    monkeypatch.setattr(pa, "save_conversation", lambda c: saved.append((c.coding_agent, c.model, c.provider)))
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: None)
    monkeypatch.setattr(pa, "_first_available_api_model", lambda: ("anthropic", "claude-opus-5-5"))
    conv.saved = saved
    return conv


def _default(monkeypatch, agent: str, model: str, provider: str = "") -> None:
    backend = agent if agent != "ducky" else provider
    result = ResolveOk(coding_agent=agent, model=model, provider=provider,
                       selection=FavoriteSelection(backend=backend, model_id=model))
    monkeypatch.setattr(pa, "resolve_model_selection", lambda _fav, _settings: result)


def _api() -> PanelApiSettingsMixin:
    api = PanelApiSettingsMixin()
    api._push = lambda _e: None  # type: ignore[attr-defined]
    return api


def test_a_chat_with_no_model_takes_a_coding_agent_default(monkeypatch, chat):
    _default(monkeypatch, "codex", "gpt-6-astra")
    out = _api().adopt_default_model("c1")
    assert out == {"ok": True, "coding_agent": "codex", "model": "gpt-6-astra", "provider": ""}
    assert chat.saved == [("codex", "gpt-6-astra", "")]


def test_a_chat_with_no_model_takes_an_api_default(monkeypatch, chat):
    _default(monkeypatch, "ducky", "claude-opus-5-5", provider="anthropic")
    out = _api().adopt_default_model("c1")
    assert out == {"ok": True, "coding_agent": "ducky", "model": "claude-opus-5-5", "provider": "anthropic"}


def test_a_branded_chat_stays_on_ducky_with_an_api_model(monkeypatch, chat):
    _default(monkeypatch, "codex", "gpt-6-astra")
    out = _api().adopt_default_model("c1", True)
    assert out == {"ok": True, "coding_agent": "ducky", "model": "claude-opus-5-5", "provider": "anthropic"}


def test_without_a_default_model_nothing_changes(monkeypatch, chat):
    monkeypatch.setattr(pa, "resolve_model_selection",
                        lambda _fav, _settings: ResolveErr(code="model_required", message="No model selected."))
    out = _api().adopt_default_model("c1")
    assert out["ok"] is False and out["error"] == "No model selected."
    assert chat.saved == [] and chat.model == ""


def test_a_chat_that_has_a_model_keeps_it(monkeypatch, chat):
    _default(monkeypatch, "codex", "gpt-6-astra")
    chat.model, chat.provider = "claude-opus-5-5", "anthropic"
    out = _api().adopt_default_model("c1")
    assert out == {"ok": True, "coding_agent": "ducky", "model": "claude-opus-5-5", "provider": "anthropic"}
    assert chat.saved == []
