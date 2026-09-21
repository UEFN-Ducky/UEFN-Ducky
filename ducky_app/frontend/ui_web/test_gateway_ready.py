"""A just-installed / just-connected gateway must count without an app restart."""

from __future__ import annotations

from unittest.mock import patch

from frontend.favorite_models import api_backends, parse_selection
from frontend.ui_web.panel_api import PanelApi


def test_api_backends_include_contributed_before_factory():
    fake = [{"id": "openai", "label": "OpenAI", "plugin_id": "openai"}]
    with (
        patch(
            "backend.uefn_plugins.host.get_ui_contributions",
            return_value={"llm_providers": fake},
        ),
        patch(
            "backend.uefn_plugins.host.get_contributions",
            return_value={"llm_providers": fake},
        ),
        patch("backend.agent.providers.gateway_providers", return_value=()),
    ):
        assert "openai" in api_backends()
        sel = parse_selection("openai:gpt-4o")
        assert sel is not None
        assert sel.backend == "openai"
        assert sel.model_id == "gpt-4o"


def test_has_any_api_key_sees_contributed_key_before_factory():
    fake = [{"id": "openai", "label": "OpenAI", "secret_key": "openai", "kind": "secret"}]
    keys = {"openai": True}

    def _has_key(name: str) -> bool:
        return bool(keys.get(str(name).strip().lower()))

    with (
        patch("backend.agent.secrets.has_key", _has_key),
        patch("frontend.ui_web.panel_api.all_providers", return_value=()),
        patch("backend.uefn_plugins.host.plugins_ready", return_value=False),
        patch("backend.uefn_plugins.host.ensure_plugins_loaded_async"),
        patch(
            "backend.uefn_plugins.host.get_ui_contributions",
            return_value={"llm_providers": fake},
        ),
    ):
        api = PanelApi()
        assert api.get_key_status().get("openai") is True
        assert api.has_any_api_key() is True


def test_resolve_gateway_credential_uses_normalize_not_localhost():
    from backend.uefn_plugins.host import resolve_gateway_credential

    with (
        patch(
            "backend.uefn_plugins.host.get_llm_provider_registration",
            return_value={
                "key_optional": True,
                "normalize_secret": lambda raw: "dky_v1_device",
            },
        ),
        patch(
            "backend.uefn_plugins.host.get_contributions",
            return_value={"llm_providers": [{"id": "uefn_ducky", "kind": "secret"}]},
        ),
        patch("backend.agent.secrets.get_key", return_value=None),
    ):
        assert resolve_gateway_credential("uefn_ducky") == "dky_v1_device"


def test_warm_model_cache_fetches_contrib_key_optional_without_factory():
    from frontend.ui_web import panel_api as pa
    from backend.agent.model_fetch import ModelInfo

    pa._model_cache.clear()
    fake = [{"id": "uefn_ducky", "label": "UEFN Ducky", "key_optional": True, "kind": "secret"}]

    def _fetch(provider: str, _cred: str):
        assert provider == "uefn_ducky"
        return [ModelInfo(id="ducky-brain", display_name="UEFN Ducky")]

    with (
        patch("backend.uefn_plugins.host.get_contributions", return_value={"llm_providers": fake}),
        patch("backend.uefn_plugins.host.get_llm_provider_registration", return_value={}),
        patch("backend.uefn_plugins.host.resolve_gateway_credential", return_value=""),
        patch("frontend.ui_web.panel_api._contributed_provider_ids", return_value={"uefn_ducky"}),
        patch("frontend.ui_web.panel_api.all_providers", return_value=()),
        patch("backend.agent.model_fetch.fetch_models", _fetch),
        patch("frontend.ui_web.panel_api._save_model_cache_to_disk"),
    ):
        pa._warm_model_cache()
    assert pa._model_cache["uefn_ducky"][0].id == "ducky-brain"


def test_resolve_gateway_credential_url_defaults_localhost():
    from backend.uefn_plugins.host import resolve_gateway_credential

    with (
        patch(
            "backend.uefn_plugins.host.get_llm_provider_registration",
            return_value={"key_optional": True},
        ),
        patch(
            "backend.uefn_plugins.host.get_contributions",
            return_value={"llm_providers": [{"id": "ollama", "kind": "url"}]},
        ),
        patch("backend.agent.secrets.get_key", return_value=None),
    ):
        assert resolve_gateway_credential("ollama") == "http://localhost:11434"
