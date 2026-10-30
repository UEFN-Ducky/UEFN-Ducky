"""A just-installed / just-connected gateway must count without an app restart."""

from __future__ import annotations

import threading
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


def test_key_status_rebuilds_plugin_contributions_at_most_twice():
    # Oct 10 2026: the 15 s key-status poll rebuilt every plugin's contributions 2 + K
    # times (K = gateways without a saved key), once more per keyless gateway.
    rows = [{"id": f"gw{i}", "label": f"GW {i}", "secret_key": f"gw{i}", "kind": "secret"} for i in range(6)]
    keys = {"gw0": "sk-0", "gw1": "sk-1"}
    builds: list[int] = []
    me = threading.get_ident()

    def _ui_contributions():
        # Plugin-load threads left by earlier tests may call this too; count only the poll's own.
        if threading.get_ident() == me:
            builds.append(1)
        return {"llm_providers": rows}

    with (
        patch("backend.agent.secrets.has_key", lambda name: bool(keys.get(name))),
        patch("backend.agent.secrets.get_key", lambda name: keys.get(name, "")),
        patch("backend.uefn_plugins.host.plugins_ready", return_value=True),
        patch("backend.uefn_plugins.host.ensure_plugins_loaded", return_value=True),
        patch("backend.uefn_plugins.host.get_ui_contributions", _ui_contributions),
        patch("backend.uefn_plugins.host.get_llm_provider_registration", return_value={"factory": object()}),
    ):
        status = PanelApi.__new__(PanelApi).get_key_status()
    assert status["gw0"] is True and status["gw5"] is False
    assert len(builds) <= 2


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
            return_value={"llm_providers": [{"id": "hosted", "kind": "secret"}]},
        ),
        patch("backend.agent.secrets.get_key", return_value=None),
    ):
        assert resolve_gateway_credential("hosted") == "dky_v1_device"


def test_warm_model_cache_fetches_contrib_key_optional_without_factory():
    from frontend.ui_web import panel_api as pa
    from backend.agent.model_fetch import ModelInfo

    pa._model_cache.clear()
    fake = [{"id": "hosted", "label": "Hosted", "key_optional": True, "kind": "secret"}]

    def _fetch(provider: str, _cred: str):
        assert provider == "hosted"
        return [ModelInfo(id="hosted-ai", display_name="Hosted")]

    with (
        patch("backend.uefn_plugins.host.get_contributions", return_value={"llm_providers": fake}),
        patch("backend.uefn_plugins.host.get_llm_provider_registration", return_value={}),
        patch("backend.uefn_plugins.host.resolve_gateway_credential", return_value=""),
        patch("frontend.ui_web.panel_api._contributed_provider_ids", return_value={"hosted"}),
        patch("frontend.ui_web.panel_api.all_providers", return_value=()),
        patch("backend.agent.model_fetch.fetch_models", _fetch),
        patch("frontend.ui_web.panel_api._save_model_cache_to_disk"),
    ):
        pa._warm_model_cache()
    assert pa._model_cache["hosted"][0].id == "hosted-ai"


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


def test_resolve_gateway_credential_uses_the_picked_account_key(tmp_path):
    from backend.agent import accounts
    from backend.uefn_plugins.host import resolve_gateway_credential

    keys: dict[str, str] = {}
    with (
        patch.object(accounts, "_data_root", lambda: tmp_path),
        patch("backend.agent.secrets.get_key", keys.get),
        patch("backend.agent.secrets.set_key", keys.__setitem__),
        patch("backend.uefn_plugins.host.get_llm_provider_registration", return_value={}),
    ):
        keys["openai"] = "sk-personal"
        work = accounts.add_account("openai", "Work", "key")
        assert resolve_gateway_credential("openai") == "sk-personal"
        accounts.set_active_account("openai", work)
        assert resolve_gateway_credential("openai") == "sk-personal"  # Work has no key yet
        accounts.set_account_key("openai", work, "sk-work")
        assert resolve_gateway_credential("openai") == "sk-work"
        accounts.mark_exhausted("openai", work)
        assert resolve_gateway_credential("openai") == "sk-personal"  # failover to the next
        assert resolve_gateway_credential("openai", "sk-typed") == "sk-typed"  # Settings test


def test_a_gateway_s_own_model_ids_find_it():
    from unittest.mock import patch

    from backend.agent.model_pricing import infer_provider
    from backend.uefn_plugins import host

    reg = {"factory": object, "model_ids": ("hosted-ai",), "model_aliases": {"hosted-old": "hosted-ai"}}
    with patch.dict(host._LLM_PROVIDER_FACTORIES, {"hosted": reg}):
        for mid in ("hosted-ai", "hosted:hosted-ai", "claude_code:hosted-old", "hosted-old"):
            assert infer_provider(mid) == "hosted", mid
        assert host.current_model_id("hosted", "hosted-old") == "hosted-ai"
        assert host.current_model_id("hosted", "hosted-ai") == "hosted-ai"
    assert infer_provider("hosted-ai") != "hosted"  # unregistered: no gateway owns it
