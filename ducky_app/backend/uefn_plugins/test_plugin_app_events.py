"""api.emit_hook / api.set_appearance_profile: a plugin fires only its own hooks and themes."""

from __future__ import annotations

from typing import Any

import pytest

from backend.uefn_plugins import plugin_app_events as events

CONTRIB = {
    "hooks": [{"id": "card_shop.sold", "label": "Card sold", "plugin_id": "card_shop"}],
    "appearance_profiles": [{"id": "neon-night", "name": "Neon Night", "plugin_id": "neon_themes"}],
}


@pytest.fixture
def pushed(monkeypatch) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    monkeypatch.setattr("backend.uefn_plugins.host.get_ui_contributions", lambda: CONTRIB)
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", out.append)
    return out


def test_a_plugin_fires_its_own_hook(pushed) -> None:
    assert events.emit_hook("card_shop", "card_shop.sold", {"card": "pip"}) == {"ok": True, "hook": "card_shop.sold"}
    (event,) = pushed
    assert event["type"] == "plugin_hook" and event["id"] == "card_shop.sold"
    assert event["plugin_id"] == "card_shop" and event["payload"] == {"card": "pip"} and event["at"] > 0


def test_other_hooks_are_refused(pushed) -> None:
    for plugin, hook in (("card_shop", "agent.done"), ("neon_themes", "card_shop.sold"), ("card_shop", "")):
        out = events.emit_hook(plugin, hook)
        assert out["ok"] is False and "contributes.hooks" in out["error"]
    assert pushed == []


def test_a_theme_plugin_switches_to_its_own_profile_only(pushed) -> None:
    out = events.set_appearance_profile("neon_themes", "neon-night")
    assert out == {"ok": True, "profile": "__plugin__:neon_themes:neon-night"}
    assert pushed[0]["type"] == "appearance_profile_requested" and pushed[0]["id"] == out["profile"]
    assert events.set_appearance_profile("card_shop", "neon-night")["ok"] is False
    assert events.set_appearance_profile("neon_themes", "__default__")["ok"] is False
    assert len(pushed) == 1


def test_a_push_failure_is_returned_not_raised(monkeypatch) -> None:
    def broken(_event: dict[str, Any]) -> None:
        raise RuntimeError("no panel")

    monkeypatch.setattr("backend.uefn_plugins.host.get_ui_contributions", lambda: CONTRIB)
    monkeypatch.setattr("frontend.ui_web.agent_modes.push_ui_event", broken)
    out = events.emit_hook("card_shop", "card_shop.sold")
    assert out["ok"] is False and "no panel" in out["error"]
