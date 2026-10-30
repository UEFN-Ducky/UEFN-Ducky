"""Host fetch_usage: missing hook / empty / three windows. 2 min cache."""

from __future__ import annotations

from unittest.mock import patch

from backend.agent.model_fetch import fetch_usage, normalize_usage_windows, reset_usage_cache


def test_normalize_empty_and_three_windows() -> None:
    assert normalize_usage_windows(None) == {"windows": []}
    assert normalize_usage_windows({"windows": []}) == {"windows": []}
    out = normalize_usage_windows(
        {
            "windows": [
                {"id": "hourly", "label": "Hourly", "used": 12, "limit": 40, "unit": "requests", "reset": "Resets in 2 hr"},
                {"id": "weekly", "label": "Weekly", "used": 5, "limit": 10},
                {"id": "monthly", "label": "Monthly", "used": 1, "limit": 2, "unit": "tokens"},
                {"id": "bad", "used": 1, "limit": 0},
            ]
        }
    )
    assert [w["id"] for w in out["windows"]] == ["hourly", "weekly", "monthly"]
    assert out["windows"][0]["used"] == 12
    assert out["windows"][0]["limit"] == 40
    assert out["windows"][0]["reset"] == "Resets in 2 hr"


def test_fetch_usage_without_key_still_calls_plugin() -> None:
    reset_usage_cache()
    def _fn(api_key: str, *, model: str = "") -> dict:
        assert api_key == ""
        return {
            "windows": [
                {
                    "id": "hourly",
                    "label": "5-hour limit",
                    "used": 100,
                    "limit": 100,
                    "reset": "Resets in 2 hr 41 min",
                    "readout": "100%",
                }
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
        out = fetch_usage("anthropic", "")
    assert out["windows"][0]["label"] == "5-hour limit"
    assert out["windows"][0]["readout"] == "100%"
    assert "notice" not in out


def test_normalize_notice_when_windows_empty() -> None:
    out = normalize_usage_windows(
        {
            "windows": [],
            "notice": {
                "message": "Claude login expired.",
                "action": "login",
                "action_label": "Log in",
                "provider_id": "anthropic",
                "agent_id": "claude_code",
            },
        }
    )
    assert out["windows"] == []
    assert out["notice"]["action"] == "login"
    assert out["notice"]["provider_id"] == "anthropic"


def test_normalize_keeps_a_valid_account_switch() -> None:
    switch = {
        "plugin_id": "anthropic",
        "rpc": "set_claude_account",
        "value": "2",
        "options": [{"id": "1", "label": "Account 1"}, {"id": "2", "label": "Account 2"}, {"label": "no id"}],
    }
    out = normalize_usage_windows({"windows": [{"id": "w", "used": 1, "limit": 100}], "switch": switch})
    assert out["switch"] == {**switch, "options": switch["options"][:2]}
    assert normalize_usage_windows({"windows": [], "switch": switch})["switch"]["value"] == "2"
    # One option, no RPC, or junk: no toggle.
    assert "switch" not in normalize_usage_windows({"windows": [], "switch": {**switch, "options": switch["options"][:1]}})
    assert "switch" not in normalize_usage_windows({"windows": [], "switch": {**switch, "rpc": ""}})
    assert "switch" not in normalize_usage_windows({"windows": [], "switch": "junk"})


def test_fetch_usage_missing_hook() -> None:
    reset_usage_cache()
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
    reset_usage_cache()
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


def test_fetch_usage_caches_until_force() -> None:
    reset_usage_cache()
    n = {"calls": 0}

    def _fn(api_key: str, *, model: str = "") -> dict:
        n["calls"] += 1
        return {"windows": [{"id": "hourly", "label": "Hourly", "used": n["calls"], "limit": 10}]}

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
        a = fetch_usage("openai", "sk")
        b = fetch_usage("openai", "sk")
        c = fetch_usage("openai", "sk", force=True)
    assert n["calls"] == 2
    assert a["windows"][0]["used"] == 1
    assert b["windows"][0]["used"] == 1
    assert c["windows"][0]["used"] == 2


def test_fetch_usage_adds_core_account_switch(tmp_path) -> None:
    from backend.agent import accounts

    reset_usage_cache()
    seen: list[str] = []

    def _fn(api_key: str, *, model: str = "", account: str = "") -> dict:
        seen.append(account)
        return {"windows": [{"id": "h", "used": len(seen), "limit": 10}]}

    def _old_fn(api_key: str, *, model: str = "") -> dict:  # a plugin that knows no accounts
        return {"windows": [{"id": "h", "used": 1, "limit": 10}]}

    plugin_switch = {"plugin_id": "openai", "rpc": "own", "value": "a",
                     "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}

    def _own_fn(api_key: str, *, model: str = "") -> dict:
        return {"windows": [{"id": "h", "used": 1, "limit": 10}], "switch": plugin_switch}

    reg = {"fetch_usage": _fn}
    with (
        patch.object(accounts, "_data_root", lambda: tmp_path),
        patch("backend.uefn_plugins.host.get_llm_provider_registration", side_effect=lambda _p: reg),
        patch("backend.uefn_plugins.host.get_coding_agent_registration", return_value={}),
    ):
        assert "switch" not in fetch_usage("openai", "sk")  # 1 account: no toggle
        assert seen == [""]

        work = accounts.add_account("openai", "Work", "key")
        team = accounts.add_account("openai", "Team", "key")
        out = fetch_usage("openai", "sk")
        assert out["switch"] == {
            "plugin_id": "", "rpc": "set_gateway_account", "provider": "openai", "value": "default",
            "options": [{"id": "default", "label": "Default"}, {"id": work, "label": "Work"},
                        {"id": team, "label": "Team"}],
        }
        assert normalize_usage_windows(out)["switch"] == out["switch"]  # survives the clamp
        assert seen == ["", "default"]

        accounts.set_active_account("openai", work)
        out = fetch_usage("openai", "sk")  # own cache entry per account
        assert seen[-1] == work and out["switch"]["value"] == work
        assert out["windows"][0]["used"] == 3

        reg["fetch_usage"] = _old_fn
        reset_usage_cache("openai")
        assert len(fetch_usage("openai", "sk")["switch"]["options"]) == 3  # TypeError fallback

        reg["fetch_usage"] = _own_fn
        reset_usage_cache("openai")
        assert fetch_usage("openai", "sk")["switch"]["rpc"] == "own"  # plugin's switch wins


def test_fetch_usage_keeps_windows_on_empty_retry() -> None:
    reset_usage_cache()
    n = {"calls": 0}

    def _fn(api_key: str, *, model: str = "") -> dict:
        n["calls"] += 1
        if n["calls"] == 1:
            return {"windows": [{"id": "hourly", "label": "Hourly", "used": 4, "limit": 10}]}
        return {
            "windows": [],
            "notice": {"message": "Claude rate-limited this check.", "action": "retry"},
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
        first = fetch_usage("anthropic", "")
        second = fetch_usage("anthropic", "", force=True)
    assert first["windows"][0]["used"] == 4
    assert second["windows"][0]["used"] == 4
    assert "notice" not in second

