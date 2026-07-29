"""Tests for per-LLM context estimate helpers (limits, cache badges, images)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend.ui_web.context_tokens import (
    _cache_mode_for_provider,
    _coding_agent_context_limit,
    _message_image_tokens,
    context_limit_for_model,
)

_CACHE_MODES = {
    "anthropic": "cached",
    "openai": "cached",
    "gemini": "implicit",
    "ollama": "local",
}


@pytest.fixture(autouse=True)
def _soft_gateway_regs(monkeypatch):
    def _llm_reg(provider_id: str):
        mode = _CACHE_MODES.get((provider_id or "").strip().lower())
        return {"cache_mode": mode} if mode else None

    def _agent_reg(agent_id: str):
        if (agent_id or "").strip().lower() == "cursor":
            return {"token_provider": "cursor"}
        return None

    monkeypatch.setattr(
        "backend.uefn_plugins.host.get_llm_provider_registration", _llm_reg
    )
    monkeypatch.setattr(
        "backend.uefn_plugins.host.get_coding_agent_registration", _agent_reg
    )
    # Host gates markers on plugin-owned promptCaching; unit tests have no plugins.
    monkeypatch.setattr(
        "backend.agent.providers.cache_utils.provider_cache_markers_enabled",
        lambda _p, *, fallback=True: bool(fallback),
    )


def _settings(*, caching: bool = True, freeze: bool = True) -> SimpleNamespace:
    return SimpleNamespace(prompt_caching_enabled=caching, freeze_prompt_prefix=freeze)


def test_coding_agent_context_limit_unknown_is_none():
    # No catalog entry → None (never invent a provider default).
    assert _coding_agent_context_limit("cursor", "made-up-model") is None


def test_cache_mode_for_provider_requires_freeze():
    assert _cache_mode_for_provider("anthropic", _settings(freeze=False)) is None
    assert _cache_mode_for_provider("ollama", _settings(freeze=False)) is None


def test_cache_mode_for_provider_explicit_cache_providers():
    assert _cache_mode_for_provider("anthropic", _settings()) == "cached"
    assert _cache_mode_for_provider("openai", _settings()) == "cached"


def test_cache_mode_for_provider_gemini_is_implicit():
    assert _cache_mode_for_provider("gemini", _settings()) == "implicit"


def test_cache_mode_for_provider_gemini_uses_freeze_only():
    # Gemini's implicit cache needs a stable prefix (freeze), not Anthropic/OpenAI markers.
    assert _cache_mode_for_provider("gemini", _settings(caching=False)) == "implicit"


def test_cache_mode_for_provider_ollama_is_local_regardless_of_caching_toggle():
    # Ollama's warm model / KV cache is independent of the provider-cache setting.
    assert _cache_mode_for_provider("ollama", _settings(caching=False)) == "local"
    assert _cache_mode_for_provider("ollama", _settings(caching=True)) == "local"


def test_cache_mode_for_provider_unknown_provider():
    assert _cache_mode_for_provider("mystery", _settings()) is None


def test_message_image_tokens_counts_only_images():
    message = {
        "attachments": [
            {"kind": "image", "name": "a.png"},
            {"kind": "image", "name": "b.png"},
            {"kind": "file", "name": "notes.txt"},
        ]
    }
    assert _message_image_tokens(message) == 2 * 1600


def test_message_image_tokens_no_attachments():
    assert _message_image_tokens({}) == 0
    assert _message_image_tokens({"attachments": []}) == 0
    assert _message_image_tokens({"attachments": "not-a-list"}) == 0


def test_context_limit_for_model_unknown_returns_none():
    assert context_limit_for_model("totally-unknown-model-xyz", "anthropic") is None
