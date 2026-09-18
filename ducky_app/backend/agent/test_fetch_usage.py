"""Host fetch_usage: missing hook / empty / three windows. No cache."""

from __future__ import annotations

from unittest.mock import patch

from backend.agent.model_fetch import fetch_usage, normalize_usage_windows


def test_normalize_empty_and_three_windows() -> None:
    assert normalize_usage_windows(None) == {"windows": []}
    assert normalize_usage_windows({"windows": []}) == {"windows": []}
    out = normalize_usage_windows(
        {
            "windows": [
                {"id": "hourly", "label": "Hourly", "used": 12, "limit": 40, "unit": "requests"},
                {"id": "weekly", "label": "Weekly", "used": 5, "limit": 10},
                {"id": "monthly", "label": "Monthly", "used": 1, "limit": 2, "unit": "tokens"},
                {"id": "bad", "used": 1, "limit": 0},
            ]
        }
    )
    assert [w["id"] for w in out["windows"]] == ["hourly", "weekly", "monthly"]
    assert out["windows"][0]["used"] == 12
    assert out["windows"][0]["limit"] == 40


def test_fetch_usage_missing_hook() -> None:
    with (
        patch(
            "backend.uefn_plugins.host.get_llm_provider_registration",
            return_value={},
        ),
        patch(
            "backend.uefn_plugins.host.get_coding_agent_registration",
            return_value={},
        ),
    ):
        assert fetch_usage("openai", "sk") == {"windows": []}


def test_fetch_usage_plugin_windows() -> None:
    def _fn(api_key: str, *, model: str = "") -> dict:
        assert api_key == "sk"
        assert model == "gpt-4o"
        return {
            "windows": [
                {"id": "hourly", "label": "Hourly", "used": 1, "limit": 10, "unit": "requests"},
                {"id": "weekly", "label": "Weekly", "used": 2, "limit": 20},
                {"id": "monthly", "label": "Monthly", "used": 3, "limit": 30},
            ]
        }

    with (
        patch(
            "backend.uefn_plugins.host.get_llm_provider_registration",
            return_value={"fetch_usage": _fn},
        ),
        patch(
            "backend.uefn_plugins.host.get_coding_agent_registration",
            return_value={},
        ),
    ):
        out = fetch_usage("openai", "sk", model="gpt-4o")
    assert len(out["windows"]) == 3
    assert out["windows"][2]["id"] == "monthly"
