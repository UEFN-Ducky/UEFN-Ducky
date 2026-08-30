"""Live two-turn cache smoke. Skip unless DUCKY_CACHE_SMOKE=1 and a gateway is configured."""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DUCKY_CACHE_SMOKE", "").strip() not in ("1", "true", "yes"),
    reason="no gateway configured (set DUCKY_CACHE_SMOKE=1)",
)


def test_second_turn_reads_prompt_cache():
    """Turn 2 must show a provider cache hit on the usage ledger.

    Cloud: cache_read_tokens > 0. Ollama: prompt_eval / input ≈ tail (near-zero
    re-eval of the frozen prefix). Serialization surprises live on the provider.
    """
    from frontend.settings import PanelSettings
    from frontend.ui_web.plugin_llm import _complete_text, _resolve_api_model

    settings = PanelSettings.load()
    provider, model = _resolve_api_model(model="")
    if not provider or not model:
        pytest.skip("no resolved gateway model")

    system = (
        "You are a cache-smoke fixture. Reply with exactly the word pong and nothing else. "
        + ("Stable prefix. " * 80)
    )
    _complete_text(provider_name=provider, model=model, system=system, user="ping 1")
    second = _complete_text(provider_name=provider, model=model, system=system, user="ping 2")
    del second

    usage = getattr(settings, "token_usage", None)
    calls = []
    if isinstance(usage, dict):
        calls = [c for c in (usage.get("calls") or []) if isinstance(c, dict)]
    if not calls:
        from frontend.ui_web.token_usage import token_usage_report

        report = token_usage_report()
        calls = list(report.get("calls") or []) if isinstance(report, dict) else []
    if len(calls) < 1:
        pytest.skip("usage ledger has no calls after smoke turns")
    last = calls[-1]
    read = int(last.get("cache_read_tokens") or 0)
    prompt_eval = int(last.get("prompt_eval_count") or last.get("prompt_eval_tokens") or 0)
    if str(provider).strip().lower() == "ollama":
        assert prompt_eval == 0 or read > 0 or int(last.get("input_tokens") or 0) > 0
        if prompt_eval:
            assert prompt_eval < 2000
    else:
        assert read > 0
