"""Which gateways get the slim prompt."""

from __future__ import annotations

from backend.agent.local_slim import provider_wants_local_slim


def test_slim_for_ollama_local_cache_and_gateways_that_ask(monkeypatch):
    from backend.uefn_plugins import host

    regs = {"uefn_ducky": {"slim_prompt": True}, "lmstudio": {"cache_mode": "local"}, "anthropic": {"cache_mode": "cached"}}
    monkeypatch.setattr(host, "get_llm_provider_registration", lambda name: regs.get(name))
    assert provider_wants_local_slim("ollama")
    assert provider_wants_local_slim("uefn_ducky"), "Ducky AI's 128k window can't take the full prompt"
    assert provider_wants_local_slim("lmstudio")
    assert not provider_wants_local_slim("anthropic")
    assert not provider_wants_local_slim("unknown")
    assert not provider_wants_local_slim("")
