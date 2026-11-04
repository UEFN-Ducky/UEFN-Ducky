"""A Ducky chat with no model takes the Default Model the way a new chat does, else
UEFN Ducky's own model (Ducky AI); with neither, the person picks one."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import frontend.favorite_models as fm
from frontend.ui_web.agent_turns.testing import patch_turns
import frontend.ui_web.panel_api as pa
from frontend.favorite_models import FavoriteSelection, ResolveErr, ResolveOk
from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin

_FALLBACK = ResolveOk(coding_agent="ducky", model="hosted-ai", provider="hosted",
                      selection=FavoriteSelection(backend="hosted", model_id="hosted-ai"))


@pytest.fixture
def chat(monkeypatch):
    conv = SimpleNamespace(id="c1", title="Builder", folder_id="", coding_agent="ducky", model="",
                           provider="", is_group=False)
    saved: list[tuple[str, str, str]] = []
    released: list[str] = []
    monkeypatch.setattr(pa, "load_conversation", lambda _cid: conv)
    monkeypatch.setattr(pa, "save_conversation", lambda c: saved.append((c.coding_agent, c.model, c.provider)))
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: None)
    monkeypatch.setattr(pa.PanelSettings, "load", staticmethod(lambda: SimpleNamespace(default_model="")))
    monkeypatch.setattr(fm, "fallback_selection", lambda: _FALLBACK)
    monkeypatch.setattr(fm, "_first_api_model", lambda: ResolveOk(
        coding_agent="ducky", model="claude-opus-5-5", provider="anthropic",
        selection=FavoriteSelection(backend="anthropic", model_id="claude-opus-5-5")))
    patch_turns(monkeypatch, "release_held_for_model", lambda cid: released.append(cid) or 0)
    conv.saved, conv.released = saved, released
    return conv


def _default(monkeypatch, agent: str, model: str, provider: str = "") -> None:
    backend = agent if agent != "ducky" else provider
    result = ResolveOk(coding_agent=agent, model=model, provider=provider,
                       selection=FavoriteSelection(backend=backend, model_id=model))
    monkeypatch.setattr(fm, "resolve_model_strict", lambda _fav, _settings: result)


def _no_default(monkeypatch, code: str = "model_required", requested: str = "") -> None:
    monkeypatch.setattr(fm, "resolve_model_strict", lambda _fav, _settings: ResolveErr(
        code=code, message="whatever the strict resolver says", requested=requested))


def _api() -> PanelApiSettingsMixin:
    api = PanelApiSettingsMixin()
    api._push = lambda _e: None  # type: ignore[attr-defined]
    return api


def test_a_chat_with_no_model_takes_a_coding_agent_default(monkeypatch, chat):
    _default(monkeypatch, "codex", "gpt-6-luna")
    out = _api().adopt_default_model("c1")
    assert out == {"ok": True, "coding_agent": "codex", "model": "gpt-6-luna", "provider": ""}
    assert chat.saved == [("codex", "gpt-6-luna", "")]
    assert chat.released == ["c1"]


def test_a_chat_with_no_model_takes_an_api_default(monkeypatch, chat):
    _default(monkeypatch, "ducky", "claude-opus-5-5", provider="anthropic")
    out = _api().adopt_default_model("c1")
    assert out == {"ok": True, "coding_agent": "ducky", "model": "claude-opus-5-5", "provider": "anthropic"}


def test_without_a_default_model_the_chat_takes_the_fallback_model(monkeypatch, chat):
    _no_default(monkeypatch)
    out = _api().adopt_default_model("c1")
    assert out == {"ok": True, "coding_agent": "ducky", "model": "hosted-ai", "provider": "hosted"}
    assert chat.saved == [("ducky", "hosted-ai", "hosted")]


def test_an_unavailable_default_model_falls_back_quietly(monkeypatch, chat):
    # Oct 16 2026: the Default Model codex:gpt-6-astra left Codex's list, and every new
    # ducky opened on "This ducky has no model. Set a Default Model." and stopped there.
    # A chat on Ducky AI then said why in a yellow note; the owner: never mention the
    # Default Model to someone using Ducky AI.
    _no_default(monkeypatch, code="model_unavailable", requested="codex:gpt-6-astra")
    out = _api().adopt_default_model("c1")
    assert out == {"ok": True, "coding_agent": "ducky", "model": "hosted-ai", "provider": "hosted"}


def test_a_branded_chat_stays_on_ducky_and_takes_the_fallback_model(monkeypatch, chat):
    _default(monkeypatch, "codex", "gpt-6-luna")
    out = _api().adopt_default_model("c1", True)
    assert (out["coding_agent"], out["model"], out["provider"]) == ("ducky", "hosted-ai", "hosted")


def test_a_branded_chat_without_a_fallback_takes_the_first_api_model(monkeypatch, chat):
    _default(monkeypatch, "codex", "gpt-6-luna")
    monkeypatch.setattr(fm, "fallback_selection", lambda: None)
    out = _api().adopt_default_model("c1", True)
    assert (out["coding_agent"], out["model"], out["provider"]) == ("ducky", "claude-opus-5-5", "anthropic")


def test_with_no_model_at_all_the_person_picks_one(monkeypatch, chat):
    _no_default(monkeypatch, code="model_unavailable", requested="codex:gpt-6-astra")
    monkeypatch.setattr(fm, "fallback_selection", lambda: None)
    patch_turns(monkeypatch, "held_for_model", lambda _cid: 2)
    out = _api().adopt_default_model("c1")
    assert out["ok"] is False and out["held"] == 2
    assert out["error"] == ("Your Default Model codex:gpt-6-astra isn't available. "
                            "Pick a model below and this ducky uses it.")
    assert chat.saved == [] and chat.model == "" and chat.released == []


def test_a_chat_that_has_a_model_keeps_it_and_runs_what_waited(monkeypatch, chat):
    _default(monkeypatch, "codex", "gpt-6-luna")
    chat.model, chat.provider = "claude-opus-5-5", "anthropic"
    out = _api().adopt_default_model("c1")
    assert out == {"ok": True, "coding_agent": "ducky", "model": "claude-opus-5-5", "provider": "anthropic"}
    assert chat.saved == [] and chat.released == ["c1"]
