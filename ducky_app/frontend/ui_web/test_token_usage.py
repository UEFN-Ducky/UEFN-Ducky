"""Tests for per-call and cumulative prompt-cache hit rate reporting."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web.token_usage import record_api_call, token_usage_report


def test_cumulative_cache_hit_rate_averages_across_calls():
    conv = SimpleNamespace(token_usage=None, messages=[])
    # First call: no cache hit yet (cold prefix).
    record_api_call(conv, input_tokens=1000, output_tokens=100, cache_read_tokens=0)
    # Second call: prefix now fully cached.
    record_api_call(conv, input_tokens=1000, output_tokens=100, cache_read_tokens=900)

    report = token_usage_report(conv)
    # Last-call rate reflects only the most recent call (900/1000).
    assert report["cache_hit_rate"] == 90.0
    # Cumulative rate reflects total_cache_read / total_input across both calls
    # (900 / 2000), so a single cold call doesn't make caching look broken.
    assert report["cache_hit_rate_cumulative"] == 45.0


def test_cumulative_cache_hit_rate_zero_when_no_calls():
    conv = SimpleNamespace(token_usage=None, messages=[])
    report = token_usage_report(conv)
    assert report["cache_hit_rate"] == 0.0
    assert report["cache_hit_rate_cumulative"] == 0.0


def test_cumulative_cache_hit_rate_caps_at_100():
    conv = SimpleNamespace(token_usage=None, messages=[])
    record_api_call(conv, input_tokens=100, output_tokens=10, cache_read_tokens=100)
    report = token_usage_report(conv)
    assert report["cache_hit_rate_cumulative"] == 100.0
