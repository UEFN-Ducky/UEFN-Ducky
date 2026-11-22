"""ducky_appearance_*: an agent reads, changes and switches the whole Appearance."""

from __future__ import annotations

import json
from typing import Any

import pytest

from backend.tools.panel import panel_appearance as pa

CONTRIB = {
    "enabled_ids": ["galaxy"],
    "appearance_profiles": [
        {"id": "nebula", "name": "Nebula", "plugin_id": "galaxy"},
        {"id": "off", "name": "Off Theme", "plugin_id": "disabled_one"},
    ],
    "appearance_effects": [{"id": "planets", "label": "Planets", "plugin_id": "galaxy"}],
    "appearance_skins": [{"id": "nebula", "label": "Nebula chrome", "plugin_id": "galaxy"}],
    "sounds": [{"id": "whoosh", "label": "Whoosh", "plugin_id": "galaxy"}],
    "hooks": [{"id": "galaxy.warp", "plugin_id": "galaxy"}],
}


def _appearance(**extra: Any) -> dict[str, Any]:
    base = {
        "foundation": {"accent": "#f8a800"},
        "overrides": {"radius": "12px"},
        "status_overrides": {},
        "profiles": [{"id": "u1", "name": "My Look"}],
        "active_profile_id": "__default__",
        "effect_id": "",
        "effects_enabled": False,
        "skin_id": "",
        "profile_patches": {},
        "sounds": {"enabled": False, "volume": 0.5, "mapping": {"agent.done": "builtin:ding"}},
    }
    base.update(extra)
    return base


class FakeWindow:
    def __init__(self, appearance: dict[str, Any]) -> None:
        self.appearance = appearance
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, method: str, args: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
        self.calls.append((method, dict(args or {})))
        if method == "get_appearance":
            return json.loads(json.dumps(self.appearance))
        if method == "get_uefn_plugin_contributions":
            return CONTRIB
        if method == "save_appearance":
            self.appearance = args["patch"]
            return "Appearance saved"
        return {"ok": True}


@pytest.fixture
def window(monkeypatch) -> FakeWindow:
    fake = FakeWindow(_appearance())
    monkeypatch.setattr(pa, "_window", fake)
    monkeypatch.setattr(pa, "_writes_blocked", lambda: None)
    return fake


def test_catalog_lists_every_kind_of_pick_from_enabled_plugins_only() -> None:
    cat = pa.build_catalog(CONTRIB, _appearance())
    kinds = {(p["kind"], p["id"]) for p in cat["profiles"]}
    assert ("built_in", "__default__") in kinds
    assert ("plugin", "__plugin__:galaxy:nebula") in kinds
    assert ("user", "u1") in kinds
    assert not any("disabled_one" in p["id"] for p in cat["profiles"])
    assert cat["effects"][0]["id"] == "plugin:galaxy:planets"
    assert cat["skins"][0]["id"] == "plugin:galaxy:nebula"
    assert cat["sounds"][0]["ref"] == "plugin:galaxy:whoosh"
    assert "agent.done" in cat["sound_hooks"] and "galaxy.warp" in cat["sound_hooks"]


def test_merge_changes_only_what_is_passed_and_empty_resets() -> None:
    cat = pa.build_catalog(CONTRIB, _appearance())
    out = pa.merge_appearance(
        _appearance(), cat,
        foundation={"bg": "#000000"},
        overrides={"--radius": "", "font-ui-size": "15px"},
        status_overrides={"error": {"bg": "#300", "text": "#f88"}},
        effect_id="plugin:galaxy:planets",
        sounds={"enabled": True, "volume": 3, "mapping": {"agent.error": "", "galaxy.warp": "plugin:galaxy:whoosh"}},
    )
    assert out["foundation"] == {"accent": "#f8a800", "bg": "#000000"}
    assert out["overrides"] == {"font-ui-size": "15px"}
    assert out["status_overrides"] == {"error": {"bg": "#300", "text": "#f88"}}
    assert out["effect_id"] == "plugin:galaxy:planets" and out["effects_enabled"] is True
    assert out["sounds"]["enabled"] is True and out["sounds"]["volume"] == 1.0
    assert out["sounds"]["mapping"] == {"agent.done": "builtin:ding", "agent.error": "", "galaxy.warp": "plugin:galaxy:whoosh"}


def test_merge_refuses_bad_keys_unknown_picks_and_a_skin_without_a_plugin_theme() -> None:
    cat = pa.build_catalog(CONTRIB, _appearance())
    for kwargs in (
        {"foundation": {"accentt": "#fff"}},
        {"status_overrides": {"error": {"glow": "#fff"}}},
        {"effect_id": "plugin:nope:x"},
        {"skin_id": "plugin:galaxy:nebula"},  # Default theme active
    ):
        with pytest.raises(ValueError):
            pa.merge_appearance(_appearance(), cat, **kwargs)
    on_plugin = _appearance(active_profile_id="__plugin__:galaxy:nebula")
    out = pa.merge_appearance(on_plugin, cat, skin_id="plugin:galaxy:nebula", foundation={"accent": "#0ff"})
    patch = out["profile_patches"]["__plugin__:galaxy:nebula"]
    assert patch["skin_id"] == "plugin:galaxy:nebula" and patch["foundation"]["accent"] == "#0ff"


def test_resolve_profile_by_id_name_or_plugin_local_id() -> None:
    profiles = pa.build_catalog(CONTRIB, _appearance())["profiles"]
    assert pa.resolve_profile_id("u1", profiles) == "u1"
    assert pa.resolve_profile_id("my look", profiles) == "u1"
    assert pa.resolve_profile_id("nebula", profiles) == "__plugin__:galaxy:nebula"
    assert pa.resolve_profile_id("nothing", profiles) is None


def test_set_saves_through_the_window_and_updates_the_active_user_theme(window) -> None:
    window.appearance = _appearance(active_profile_id="u1")
    out = json.loads(pa.ducky_appearance_set(foundation={"accent": "#22c55e"}))
    assert out["ok"] is True and out["appearance"]["foundation"]["accent"] == "#22c55e"
    methods = [m for m, _ in window.calls]
    assert "save_appearance" in methods and "update_active_appearance_profile" in methods


def test_set_dry_run_saves_nothing_and_errors_come_back_as_json(window) -> None:
    out = json.loads(pa.ducky_appearance_set(overrides={"radius": "2px"}, dry_run=True))
    assert out["dry_run"] is True and out["after"]["overrides"]["radius"] == "2px"
    bad = json.loads(pa.ducky_appearance_set(effect_id="plugin:nope:x"))
    assert bad["ok"] is False and "effect" in bad["error"]
    assert "save_appearance" not in [m for m, _ in window.calls]


def test_writes_are_refused_while_change_settings_is_off(window, monkeypatch) -> None:
    monkeypatch.setattr(pa, "_writes_blocked", lambda: "off")
    assert json.loads(pa.ducky_appearance_set(foundation={"bg": "#000"}))["ok"] is False
    assert json.loads(pa.ducky_appearance_profile("save_as", name="x"))["ok"] is False
    assert json.loads(pa.ducky_appearance_profile("list"))["ok"] is True


def test_activate_asks_the_window_and_waits_until_it_switched(window, monkeypatch) -> None:
    pushed: list[dict[str, Any]] = []

    def push(event: dict[str, Any]) -> None:
        pushed.append(event)
        window.appearance["active_profile_id"] = event["id"]

    monkeypatch.setattr("frontend.ui_web.agent_turns.push.push_ui_event", push)
    monkeypatch.setattr(pa.time, "sleep", lambda _s: None)
    out = json.loads(pa.ducky_appearance_profile("activate", profile="Nebula"))
    assert out["ok"] is True and out["active"] == "__plugin__:galaxy:nebula"
    assert pushed[0]["type"] == "appearance_profile_requested" and pushed[0]["at"] > 0


def test_save_as_rename_delete_go_to_the_window(window) -> None:
    assert json.loads(pa.ducky_appearance_profile("save_as", name="Sunset"))["ok"] is True
    assert json.loads(pa.ducky_appearance_profile("rename", profile="My Look", name="Old"))["ok"] is True
    assert json.loads(pa.ducky_appearance_profile("delete", profile="u1"))["ok"] is True
    sent = {m: a for m, a in window.calls}
    assert sent["save_appearance_profile"] == {"name": "Sunset"}
    assert sent["rename_appearance_profile"] == {"profile_id": "u1", "name": "Old"}
    assert sent["delete_appearance_profile"] == {"profile_id": "u1"}
    assert json.loads(pa.ducky_appearance_profile("explode", profile="u1"))["ok"] is False
