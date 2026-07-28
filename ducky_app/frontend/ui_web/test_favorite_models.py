"""Tests for strict Ducky model resolution (profile model → global default)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend.favorite_models import (
    ResolveErr,
    ResolveOk,
    first_favorite,
    is_legacy_agent_only,
    parse_selection,
    qualify,
    resolve_model_strict,
)

_TEST_AGENTS = frozenset({"claude_code", "codex", "cursor", "gemini_cli"})
_TEST_APIS = frozenset({"anthropic", "openai", "gemini", "ollama"})


@pytest.fixture(autouse=True)
def _soft_backends(monkeypatch):
    monkeypatch.setattr(
        "frontend.favorite_models.coding_agent_backends", lambda: _TEST_AGENTS
    )
    monkeypatch.setattr("frontend.favorite_models.api_backends", lambda: _TEST_APIS)
    monkeypatch.setattr(
        "frontend.favorite_models.known_backends", lambda: _TEST_AGENTS | _TEST_APIS
    )

    def _norm_cursor(model: str) -> str:
        mid = (model or "").strip()
        return "auto" if not mid or mid.lower() == "default" else mid

    def _fake_reg(agent_id: str):
        if (agent_id or "").strip().lower() == "cursor":
            return {"normalize_model": _norm_cursor}
        return None

    monkeypatch.setattr(
        "backend.uefn_plugins.host.get_coding_agent_registration", _fake_reg
    )


def test_qualify_and_parse_round_trip():
    assert qualify("cursor", "composer-2.5") == "cursor:composer-2.5"
    sel = parse_selection("cursor:composer-2.5")
    assert sel is not None
    assert sel.backend == "cursor"
    assert sel.model_id == "composer-2.5"
    assert sel.is_coding_agent
    assert sel.coding_agent == "cursor"


def test_parse_accepts_cursor_auto_but_rejects_other_default_and_bare_agent():
    assert parse_selection("cursor") is None
    cursor_auto = parse_selection("cursor:default")
    assert cursor_auto is not None
    assert cursor_auto.backend == "cursor"
    assert cursor_auto.model_id == "auto"
    assert parse_selection("anthropic:default") is None
    assert parse_selection("") is None
    assert is_legacy_agent_only("cursor")
    assert is_legacy_agent_only("claude_code")
    assert not is_legacy_agent_only("cursor:composer-2.5")


def test_first_favorite_ignores_empty_slots():
    assert first_favorite(["", "cursor:composer-2.5", "anthropic:x"]) == "cursor:composer-2.5"
    assert first_favorite([]) == ""
    assert first_favorite(None) == ""


def test_resolve_errors_without_model_or_default():
    settings = SimpleNamespace(default_model="")
    err = resolve_model_strict([], settings)
    assert isinstance(err, ResolveErr)
    assert err.code == "model_required"
    assert "Default Model" in err.message

    err2 = resolve_model_strict(["", ""], settings)
    assert isinstance(err2, ResolveErr)
    assert err2.code == "model_required"


def test_resolve_falls_back_to_settings_default(monkeypatch):
    settings = SimpleNamespace(default_model="anthropic:claude-sonnet-4-20250514")
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda _s: {})
    monkeypatch.setattr(
        "frontend.favorite_models._available_api_models",
        lambda: {"anthropic": {"claude-sonnet-4-20250514"}},
    )

    ok = resolve_model_strict([], settings)
    assert isinstance(ok, ResolveOk)
    assert ok.coding_agent == "ducky"
    assert ok.model == "claude-sonnet-4-20250514"
    assert ok.provider == "anthropic"


def test_profile_model_overrides_default(monkeypatch):
    settings = SimpleNamespace(default_model="anthropic:claude-sonnet-4-20250514")
    monkeypatch.setattr(
        "frontend.favorite_models._available_agent_models",
        lambda _s: {"cursor": {"composer-2.5"}},
    )
    monkeypatch.setattr(
        "frontend.favorite_models._available_api_models",
        lambda: {"anthropic": {"claude-sonnet-4-20250514"}},
    )

    ok = resolve_model_strict(["cursor:composer-2.5"], settings)
    assert isinstance(ok, ResolveOk)
    assert ok.coding_agent == "cursor"
    assert ok.model == "composer-2.5"


def test_unavailable_profile_model_never_falls_back_to_default(monkeypatch):
    settings = SimpleNamespace(default_model="anthropic:claude-sonnet-4-20250514")
    monkeypatch.setattr(
        "frontend.favorite_models._available_agent_models",
        lambda _s: {"cursor": {"composer-2.5"}},
    )
    monkeypatch.setattr(
        "frontend.favorite_models._available_api_models",
        lambda: {"anthropic": {"claude-sonnet-4-20250514"}},
    )

    err = resolve_model_strict(["cursor:gone"], settings)
    assert isinstance(err, ResolveErr)
    assert err.code == "model_unavailable"


def test_resolve_default_can_be_coding_agent(monkeypatch):
    settings = SimpleNamespace(default_model="cursor:composer-2.5")
    monkeypatch.setattr(
        "frontend.favorite_models._available_agent_models",
        lambda _s: {"cursor": {"composer-2.5"}},
    )
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {})

    ok = resolve_model_strict(None, settings)
    assert isinstance(ok, ResolveOk)
    assert ok.coding_agent == "cursor"
    assert ok.model == "composer-2.5"


def test_resolve_legacy_agent_only_needs_repick():
    settings = SimpleNamespace(default_model="")
    err = resolve_model_strict(["cursor"], settings)
    assert isinstance(err, ResolveErr)
    assert err.code == "model_needs_repick"
    assert "cursor" in err.message.lower() or "agent name" in err.message.lower()


def test_resolve_coding_agent_exact_model(monkeypatch):
    settings = SimpleNamespace(default_model="")
    monkeypatch.setattr(
        "frontend.favorite_models._available_agent_models",
        lambda _s: {"cursor": {"composer-2.5", "auto"}},
    )
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {})

    ok = resolve_model_strict(["cursor:composer-2.5"], settings)
    assert isinstance(ok, ResolveOk)
    assert ok.coding_agent == "cursor"
    assert ok.model == "composer-2.5"
    assert ok.provider == ""


def test_resolve_cursor_auto_model(monkeypatch):
    settings = SimpleNamespace(default_model="")
    monkeypatch.setattr(
        "frontend.favorite_models._available_agent_models",
        lambda _s: {"cursor": {"auto", "composer-2.5"}},
    )
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {})

    ok = resolve_model_strict(["cursor:default"], settings)
    assert isinstance(ok, ResolveOk)
    assert ok.coding_agent == "cursor"
    assert ok.model == "auto"
    assert ok.selection.qualified == "cursor:auto"


def test_resolve_coding_agent_missing_model_errors(monkeypatch):
    settings = SimpleNamespace(default_model="")
    monkeypatch.setattr(
        "frontend.favorite_models._available_agent_models",
        lambda _s: {"cursor": {"composer-2.5"}},
    )
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {})

    err = resolve_model_strict(["cursor:expensive-mystery"], settings)
    assert isinstance(err, ResolveErr)
    assert err.code == "model_unavailable"


def test_resolve_api_model(monkeypatch):
    settings = SimpleNamespace(default_model="")
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda _s: {})
    monkeypatch.setattr(
        "frontend.favorite_models._available_api_models",
        lambda: {"anthropic": {"claude-sonnet-4-20250514"}},
    )

    ok = resolve_model_strict(["anthropic:claude-sonnet-4-20250514"], settings)
    assert isinstance(ok, ResolveOk)
    assert ok.coding_agent == "ducky"
    assert ok.model == "claude-sonnet-4-20250514"
    assert ok.provider == "anthropic"


def test_legacy_bare_api_id_unique_match(monkeypatch):
    settings = SimpleNamespace(default_model="")
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda _s: {})
    monkeypatch.setattr(
        "frontend.favorite_models._available_api_models",
        lambda: {"openai": {"gpt-4o-mini"}},
    )
    ok = resolve_model_strict(["gpt-4o-mini"], settings)
    assert isinstance(ok, ResolveOk)
    assert ok.provider == "openai"
    assert ok.model == "gpt-4o-mini"
