"""Gateway providers / coding agents are Store-plugin gated (not host builtins)."""

from __future__ import annotations

from unittest.mock import patch


def test_no_builtin_cloud_providers():
    from backend.agent.providers import builtin_providers, gateway_providers

    assert builtin_providers() == ()
    with patch(
        "backend.uefn_plugins.host.get_contributions",
        return_value={"llm_providers": []},
    ):
        assert gateway_providers() == ()


def test_gateway_coding_agents_not_listed_without_contribution():
    from backend.agent.coding_agents.base import listed_external_coding_agents

    with patch(
        "backend.agent.coding_agents.base.contributed_coding_agents",
        return_value=(),
    ):
        listed = listed_external_coding_agents()
        assert "claude_code" not in listed
        assert "codex" not in listed
        assert "gemini_cli" not in listed
        assert "cursor" not in listed


def test_cursor_listed_when_contributed():
    from backend.agent.coding_agents.base import listed_external_coding_agents

    with patch(
        "backend.agent.coding_agents.base.contributed_coding_agents",
        return_value=("cursor",),
    ):
        assert "cursor" in listed_external_coding_agents()


def test_gemini_cli_listed_when_contributed():
    from backend.agent.coding_agents.base import listed_external_coding_agents

    with patch(
        "backend.agent.coding_agents.base.contributed_coding_agents",
        return_value=("gemini_cli",),
    ):
        assert "gemini_cli" in listed_external_coding_agents()


def test_gemini_gateway_when_contributed_and_registered():
    from backend.agent.providers import all_providers, gateway_providers

    fake = [{"id": "gemini", "label": "Google", "plugin_id": "google"}]
    with (
        patch(
            "backend.uefn_plugins.host.get_contributions",
            return_value={"llm_providers": fake},
        ),
        patch(
            "backend.uefn_plugins.host.get_llm_provider_registration",
            return_value={"factory": lambda *a, **k: None},
        ),
    ):
        assert "gemini" in gateway_providers()
        assert "gemini" in all_providers()


def test_make_provider_gemini_requires_gateway():
    from backend.agent.providers import make_provider

    with patch(
        "backend.uefn_plugins.host.get_llm_provider_registration",
        return_value=None,
    ):
        try:
            make_provider("gemini", "key", "gemini-2.0-flash")
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "gateway" in str(exc).lower() or "store" in str(exc).lower()


def test_registry_unregistered_adapter_is_none():
    """Self-check: no factory → get_adapter returns None (boundary)."""
    from backend.agent.coding_agents.base import get_adapter

    with patch(
        "backend.uefn_plugins.host.get_coding_agent_registration",
        return_value=None,
    ):
        assert get_adapter("cursor") is None
