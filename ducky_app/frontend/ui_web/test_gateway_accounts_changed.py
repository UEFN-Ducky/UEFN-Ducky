"""A deleted key takes its models with it, and account changes reach every open picker.

The model picker went on listing a gateway's API models after its key was deleted, and the
old account after a switch or sign-out, until the app restarted (Nov 2026).
"""

from __future__ import annotations

from unittest.mock import patch

from backend.agent.model_fetch import ModelInfo
from backend.uefn_plugins.plugin_host.testing import host_patched
from frontend.ui_web import call_pools, gateway_accounts_changed, panel_api as pa
from frontend.ui_web.panel_api_settings import _accounts_call


def _warm(*, registration: dict, credential: str = "", fetched=None) -> list[str]:
    cleared: list[str] = []
    with (
        host_patched("get_contributions", return_value={"llm_providers": []}),
        host_patched("get_llm_provider_registration", return_value=registration),
        host_patched("resolve_gateway_credential", return_value=credential),
        patch("frontend.ui_web.panel_api._contributed_provider_ids", return_value=set()),
        patch("frontend.ui_web.panel_api.all_providers", return_value=("anthropic",)),
        patch("backend.agent.model_fetch.fetch_models", lambda provider, cred: fetched or []),
        patch("backend.agent.model_fetch.clear_model_cache", cleared.append),
        patch("frontend.ui_web.panel_api._save_model_cache_to_disk"),
    ):
        pa._warm_model_cache()
    return cleared


def test_a_gateway_whose_key_is_gone_loses_its_models(monkeypatch) -> None:
    monkeypatch.setattr(pa, "_model_cache", {"anthropic": [ModelInfo(id="claude-x", display_name="Claude X")]})
    assert _warm(registration={"id": "anthropic"}) == ["anthropic"]
    assert "anthropic" not in pa._model_cache


def test_models_stay_until_the_gateway_is_loaded_and_while_its_key_is_there(monkeypatch) -> None:
    last_session = [ModelInfo(id="claude-x", display_name="Claude X")]
    # At boot, before the gateway's plugin registers, nobody knows whether it has a key.
    monkeypatch.setattr(pa, "_model_cache", {"anthropic": list(last_session)})
    assert _warm(registration={}) == [] and pa._model_cache["anthropic"] == last_session
    # A gateway that needs no key keeps its list too.
    assert _warm(registration={"key_optional": True}) == [] and pa._model_cache["anthropic"] == last_session
    # With its key, an empty answer (a network blip) keeps the list; a real one replaces it.
    assert _warm(registration={"id": "anthropic"}, credential="sk-test") == []
    assert pa._model_cache["anthropic"] == last_session
    fresh = [ModelInfo(id="claude-y", display_name="Claude Y")]
    _warm(registration={"id": "anthropic"}, credential="sk-test", fetched=fresh)
    assert pa._model_cache["anthropic"] == fresh


class _Api:
    def __init__(self) -> None:
        self.pushed: list[dict] = []

    def _push_panel(self, event: dict) -> None:
        self.pushed.append(event)


def test_a_change_to_accounts_refreshes_the_lists_and_tells_every_window(monkeypatch) -> None:
    kicks: list[str] = []
    monkeypatch.setattr(pa, "kick_model_refresh", lambda: kicks.append("models"))
    monkeypatch.setattr("backend.agent.coding_agents.base.kick_detect_refresh", lambda: kicks.append("logins"))
    api = _Api()
    assert _accounts_call(lambda p: None, "Anthropic", api) == {"ok": True}
    assert sorted(kicks) == ["logins", "models"]
    assert api.pushed == [{"type": "gateway_accounts_changed", "provider": "anthropic"}]


def test_a_read_or_a_refused_change_refreshes_nothing(monkeypatch) -> None:
    kicks: list[str] = []
    monkeypatch.setattr(pa, "kick_model_refresh", lambda: kicks.append("models"))
    api = _Api()
    assert _accounts_call(lambda p: {"accounts": []}, "anthropic") == {"ok": True, "accounts": []}  # a read

    def refuse(provider: str) -> None:
        raise ValueError("no such account")

    assert _accounts_call(refuse, "anthropic", api) == {"ok": False, "error": "no such account"}
    assert gateway_accounts_changed.accounts_changed(api, {"ok": False, "error": "x"}, "anthropic")["ok"] is False
    assert kicks == [] and api.pushed == []


def test_the_change_drops_the_cached_login_list(monkeypatch) -> None:
    """list_coding_agents answers from a one-second cache: the picker's re-read must not get it."""
    calls: list[int] = []

    def agents() -> dict:
        calls.append(1)
        return {"agents": []}

    call_pools.call_shared("list_coding_agents", agents, [])
    call_pools.call_shared("list_coding_agents", agents, [])
    assert len(calls) == 1
    call_pools.on_push("gateway_accounts_changed")
    call_pools.call_shared("list_coding_agents", agents, [])
    assert len(calls) == 2
