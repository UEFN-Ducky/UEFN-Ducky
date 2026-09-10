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
