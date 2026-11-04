"""Tests for strict Ducky model resolution (profile model → global default)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.uefn_plugins.plugin_host.testing import patch_host
from frontend.favorite_models import (
    ResolveErr,
    ResolveOk,
    allow_create_without_model,
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

    patch_host(monkeypatch, "get_coding_agent_registration", _fake_reg)


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
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda _s, **_k: {})
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
        lambda _s, **_k: {"cursor": {"composer-2.5"}},
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
        lambda _s, **_k: {"cursor": {"composer-2.5"}},
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
        lambda _s, **_k: {"cursor": {"composer-2.5"}},
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
        lambda _s, **_k: {"cursor": {"composer-2.5", "auto"}},
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
        lambda _s, **_k: {"cursor": {"auto", "composer-2.5"}},
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
        lambda _s, **_k: {"cursor": {"composer-2.5"}},
    )
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {})

    err = resolve_model_strict(["cursor:expensive-mystery"], settings)
    assert isinstance(err, ResolveErr)
    assert err.code == "model_unavailable"


def test_resolve_api_model(monkeypatch):
    settings = SimpleNamespace(default_model="")
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda _s, **_k: {})
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
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda _s, **_k: {})
    monkeypatch.setattr(
        "frontend.favorite_models._available_api_models",
        lambda: {"openai": {"gpt-4o-mini"}},
    )
    ok = resolve_model_strict(["gpt-4o-mini"], settings)
    assert isinstance(ok, ResolveOk)
    assert ok.provider == "openai"
    assert ok.model == "gpt-4o-mini"


def test_create_conversation_without_default_model(monkeypatch):
    """Empty favorites and no Default Model still create. A bad pick still errors."""
    import frontend.ui_web.panel_api as pa
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    monkeypatch.setattr(pa, "PanelSettings", SimpleNamespace(load=lambda: SimpleNamespace(default_model="")))
    monkeypatch.setattr(pa, "default_bundled_style", lambda: "artist")
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *_a, **_k: None)
    captured: dict = {}

    def _fake_create(_settings, _folder_id, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id="c1",
            title=kwargs.get("title") or "New",
            ducky_style=kwargs.get("ducky_style") or "artist",
            ducky_name=kwargs.get("ducky_name") or "",
            profile_id=kwargs.get("profile_id") or "",
            ducky_personality=kwargs.get("ducky_personality") or "",
            file_path="",
            model=kwargs.get("model") or "",
            provider=kwargs.get("provider") or "",
            coding_agent=kwargs.get("coding_agent") or "ducky",
            tts_voice="",
            tts_speed=0,
            thinking_effort="",
        )

    monkeypatch.setattr(pa, "create_conversation", _fake_create)
    api = PanelApiChatsMixin()
    created = api.create_conversation("", "artist", None, {"favorite_models": [], "ducky_name": "Ada"})
    assert created["id"] == "c1"
    assert created["model"] == ""
    assert captured["coding_agent"] == "ducky"
    assert captured["model"] == ""

    monkeypatch.setattr(
        "frontend.favorite_models._available_agent_models",
        lambda _s, **_k: {"cursor": {"composer-2.5"}},
    )
    with pytest.raises(ValueError, match="not-a-model"):
        api.create_conversation("", "artist", None, {"favorite_models": ["cursor:not-a-model"]})


def test_allow_create_without_model_keeps_bad_picks():
    err = ResolveErr(code="model_unavailable", message="missing")
    assert allow_create_without_model(err) is err
    empty = allow_create_without_model(ResolveErr(code="model_required", message="none"))
    assert isinstance(empty, ResolveOk)
    assert empty.model == ""
    assert empty.coding_agent == "ducky"


def test_resolve_api_model_when_catalog_still_warming(monkeypatch):
    """Empty cache must not block create-ducky on a live provider fetch."""
    settings = SimpleNamespace(default_model="")
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda _s, **_k: {})
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {})
    ok = resolve_model_strict(["anthropic:claude-sonnet-4-20250514"], settings)
    assert isinstance(ok, ResolveOk)
    assert ok.model == "claude-sonnet-4-20250514"
    assert ok.provider == "anthropic"


def _hang_forever_adapter(release):
    class _Hang:
        def detect(self, _settings):
            release.wait(30)
            raise RuntimeError("released")

    return _Hang()


def _claude_adapter():
    class _Claude:
        def detect(self, _settings):
            return SimpleNamespace(
                to_dict=lambda: {
                    "id": "claude_code",
                    "enabled": True,
                    "available": True,
                    "models": [{"id": "claude-sonnet-5-5"}],
                }
            )

    return _Claude()


def test_cold_cache_probes_only_the_requested_agent(monkeypatch):
    """A hung `gemini --version` must not block spawning a Claude Code ducky."""
    import threading
    import time

    from backend.agent import coding_agents
    from frontend import favorite_models

    release = threading.Event()
    adapters = {"gemini_cli": _hang_forever_adapter(release), "claude_code": _claude_adapter()}
    monkeypatch.setattr(favorite_models, "coding_agent_backends", lambda: _TEST_AGENTS)
    monkeypatch.setattr(coding_agents, "detect_all", lambda *_a: {"agents": [], "checking": True})
    monkeypatch.setattr(coding_agents, "get_adapter", adapters.get)
    try:
        t0 = time.monotonic()
        out = favorite_models._available_agent_models(SimpleNamespace(), backend="claude_code")
        assert time.monotonic() - t0 < 5
        assert out == {"claude_code": {"claude-sonnet-5-5"}}
    finally:
        release.set()


def test_a_checking_stub_in_a_partial_cache_is_probed(monkeypatch):
    """The refresh publishes per agent; an agent still on its "Checking…" stub is probed, not refused."""
    from backend.agent import coding_agents
    from frontend import favorite_models

    partial = {"agents": [{"id": "codex", "available": True, "models": [{"id": "gpt-6"}]},
                          {"id": "claude_code", "available": False, "status": "Checking…", "models": []}]}
    monkeypatch.setattr(coding_agents, "detect_all", lambda *_a: partial)
    monkeypatch.setattr(coding_agents, "get_adapter", {"claude_code": _claude_adapter()}.get)
    out = favorite_models._available_agent_models(SimpleNamespace(), backend="claude_code")
    assert out == {"claude_code": {"claude-sonnet-5-5"}}


def test_a_seat_on_the_leaders_running_agent_is_not_refused():
    from frontend.ui_web.panel_api_chats import _leader_agent_ok

    leader = SimpleNamespace(coding_agent="claude_code", model="claude-opus-5-5")
    down = ResolveErr(code="model_unavailable", message="Coding agent 'claude_code' is unavailable. …",
                      requested="claude_code:claude-opus-5-5")
    ok = _leader_agent_ok(down, leader)
    assert isinstance(ok, ResolveOk) and (ok.coding_agent, ok.model) == ("claude_code", "claude-opus-5-5")
    assert _leader_agent_ok(down, SimpleNamespace(coding_agent="codex", model="gpt-6")) is None
    unlisted = ResolveErr(code="model_unavailable", message="Model 'x' is not advertised by claude_code.",
                          requested="claude_code:x")
    assert _leader_agent_ok(unlisted, leader) is None


def test_cold_cache_probe_is_bounded(monkeypatch):
    import threading
    import time

    from backend.agent import coding_agents
    from frontend import favorite_models

    release = threading.Event()
    monkeypatch.setattr(favorite_models, "coding_agent_backends", lambda: _TEST_AGENTS)
    monkeypatch.setattr(favorite_models, "_COLD_PROBE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(coding_agents, "detect_all", lambda *_a: {"agents": [], "checking": True})
    monkeypatch.setattr(coding_agents, "get_adapter", lambda _aid: _hang_forever_adapter(release))
    try:
        t0 = time.monotonic()
        out = favorite_models._available_agent_models(SimpleNamespace(), backend="gemini_cli")
        assert time.monotonic() - t0 < 5
        assert out == {}
    finally:
        release.set()


def _catalogs(monkeypatch, *, agents: dict, apis: dict) -> None:
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda settings, backend="": agents)
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: apis)


def test_a_bare_model_a_subscription_offers_runs_there_not_on_the_api_key(monkeypatch):
    """An agent wrote "gpt-6.1-sol" and the chat billed OpenAI's API for an hour instead of Codex."""
    _catalogs(monkeypatch, agents={"codex": {"gpt-6.1-sol"}}, apis={"openai": {"gpt-6.1-sol"}})
    out = resolve_model_strict(["gpt-6.1-sol"], SimpleNamespace(default_model=""))
    assert isinstance(out, ResolveOk) and out.coding_agent == "codex" and out.provider == ""
    _catalogs(monkeypatch, agents={}, apis={"openai": {"gpt-6.1-sol"}})
    api_only = resolve_model_strict(["gpt-6.1-sol"], SimpleNamespace(default_model=""))
    assert isinstance(api_only, ResolveOk) and api_only.provider == "openai"


def test_agents_never_start_chats_on_an_api_key_the_person_did_not_open(monkeypatch):
    from frontend.favorite_models import billing_kind, refuse_paid_api_for_agent

    _catalogs(monkeypatch, agents={"codex": {"gpt-6.1-sol"}}, apis={"openai": {"gpt-6.1-sol"}, "ollama": {"qwen3"}})
    monkeypatch.setattr("backend.agent.model_fetch.get_model_info", lambda p, m: None)
    shut = SimpleNamespace(default_model="", agent_api_keys=[])
    api = resolve_model_strict(["openai:gpt-6.1-sol"], shut)
    refused = refuse_paid_api_for_agent(api, shut)
    assert isinstance(refused, ResolveErr) and refused.code == "api_key_blocked"
    assert "codex:gpt-6.1-sol" in refused.message
    opened = SimpleNamespace(default_model="", agent_api_keys=["openai"])
    assert refuse_paid_api_for_agent(api, opened) is api
    local = resolve_model_strict(["ollama:qwen3"], shut)
    assert refuse_paid_api_for_agent(local, shut) is local
    assert billing_kind("codex", "", "gpt-6.1-sol") == "subscription"
    assert billing_kind("ducky", "openai", "gpt-6.1-sol") == "api_key"
    from unittest.mock import patch

    from backend.uefn_plugins.plugin_host import state

    with patch.dict(state._LLM_PROVIDER_FACTORIES, {"hosted": {"factory": object, "billing": "account"}}):
        assert billing_kind("ducky", "hosted", "hosted-ai") == "account"


def test_a_stale_default_model_still_creates_the_chat_but_a_profile_pick_does_not(monkeypatch):
    # Oct 16 2026: Codex stopped offering gpt-6-astra, the Default Model. Every spawn and
    # seat with no profile model raised "Model 'gpt-6-astra' is not advertised by codex".
    settings = SimpleNamespace(default_model="codex:gpt-6-astra")
    monkeypatch.setattr("frontend.favorite_models._available_agent_models",
                        lambda _s, **_k: {"codex": {"gpt-6-luna"}})
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {})
    stale = resolve_model_strict(None, settings)
    assert isinstance(stale, ResolveErr) and stale.code == "model_unavailable" and stale.from_default
    created = allow_create_without_model(stale)
    assert isinstance(created, ResolveOk) and created.model == "" and created.coding_agent == "ducky"
    own = resolve_model_strict(["codex:gpt-6-astra"], settings)
    assert isinstance(own, ResolveErr) and not own.from_default
    assert allow_create_without_model(own) is own


def test_a_gateway_s_fallback_model_is_offered_only_while_its_plugin_shows_it(monkeypatch):
    from unittest.mock import patch

    import frontend.favorite_models as fm
    from backend.uefn_plugins.plugin_host import state

    allowed = {"on": True}
    reg = {"factory": object, "fallback_model": "hosted-ai", "visible": lambda: allowed["on"]}
    monkeypatch.setattr("frontend.favorite_models.api_backends", lambda: _TEST_APIS | {"hosted"})
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {"hosted": {"hosted-ai"}})
    with patch.dict(state._LLM_PROVIDER_FACTORIES, {"hosted": reg}):
        pick = fm.fallback_selection()
        assert (pick.coding_agent, pick.model, pick.provider) == ("ducky", "hosted-ai", "hosted")
        allowed["on"] = False
        assert fm.fallback_selection() is None


def test_chat_model_choice_prefers_the_default_then_a_gateway_fallback(monkeypatch):
    import frontend.favorite_models as fm

    fallback = ResolveOk(coding_agent="ducky", model="hosted-ai", provider="hosted",
                         selection=fm.FavoriteSelection(backend="hosted", model_id="hosted-ai"))
    monkeypatch.setattr(fm, "fallback_selection", lambda: fallback)
    monkeypatch.setattr("frontend.favorite_models._available_agent_models",
                        lambda _s, **_k: {"codex": {"gpt-6-luna"}})
    monkeypatch.setattr("frontend.favorite_models._available_api_models", lambda: {})

    choice, error = fm.chat_model_choice(SimpleNamespace(default_model="codex:gpt-6-luna"))
    assert (choice.coding_agent, choice.model, error) == ("codex", "gpt-6-luna", "")

    # Oct 21 2026: Codex stopped listing gpt-6-astra and new chats quietly moved to another
    # gateway. A Default Model its agent no longer offers stays on that agent: Auto, else
    # the first model it offers.
    choice, error = fm.chat_model_choice(SimpleNamespace(default_model="codex:gpt-6-astra"))
    assert (choice.coding_agent, choice.model, error) == ("codex", "gpt-6-luna", "")
    monkeypatch.setattr("frontend.favorite_models._available_agent_models",
                        lambda _s, **_k: {"codex": {"gpt-6-luna", "auto"}})
    choice, _ = fm.chat_model_choice(SimpleNamespace(default_model="codex:gpt-6-astra"))
    assert (choice.coding_agent, choice.model) == ("codex", "auto")

    # Codex itself gone: a gateway's fallback model.
    monkeypatch.setattr("frontend.favorite_models._available_agent_models", lambda _s, **_k: {})
    choice, error = fm.chat_model_choice(SimpleNamespace(default_model="codex:gpt-6-astra"))
    assert choice is fallback and error == ""

    monkeypatch.setattr(fm, "fallback_selection", lambda: None)
    choice, error = fm.chat_model_choice(SimpleNamespace(default_model=""))
    assert choice is None
    assert error == "No Default Model is set. Pick a model below and this ducky uses it."
    choice, error = fm.chat_model_choice(SimpleNamespace(default_model="codex:gpt-6-astra"))
    assert choice is None
    assert error == "Your Default Model codex:gpt-6-astra isn't available. Pick a model below and this ducky uses it."
