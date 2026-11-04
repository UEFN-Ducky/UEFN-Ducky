"""What loaded plugins contribute to the UI: dedupe, chat references, enabled-only rows."""

from __future__ import annotations

from backend.uefn_plugins.plugin_host import state, ui_registry


def test_dedupe_keeps_the_first_row_per_plugin_and_key() -> None:
    rows = [{"id": "a", "plugin_id": "p", "n": 1}, {"id": "a", "plugin_id": "P", "n": 2}, {"id": "a", "plugin_id": "q"}, "junk"]
    assert ui_registry._dedupe_contrib_rows(rows) == [rows[0], rows[2]]


def test_chat_reference_needs_a_known_scheme() -> None:
    good = {"trigger": "/", "id": "x", "label": "X", "href": "skill:verse"}
    assert ui_registry.normalize_chat_reference(good, "p")["group"] == "Plugins"
    assert ui_registry.normalize_chat_reference({**good, "href": "https://x"}, "p") is None
    assert ui_registry.normalize_chat_reference({**good, "trigger": "#"}, "p") is None


def test_project_sources_list_only_enabled_plugins(monkeypatch) -> None:
    monkeypatch.setattr(ui_registry, "is_plugin_enabled", lambda pid: pid == "on")
    with state._LOCK:
        saved = list(state._CONTRIBUTIONS["project_sources"])
        state._CONTRIBUTIONS["project_sources"] = [{"id": "a", "plugin_id": "on"}, {"id": "b", "plugin_id": "off"}]
    try:
        assert [r["id"] for r in ui_registry.get_project_sources()] == ["a"]
    finally:
        with state._LOCK:
            state._CONTRIBUTIONS["project_sources"] = saved


def test_ui_contributions_hide_a_gateway_its_plugin_hides(monkeypatch) -> None:
    monkeypatch.setattr(ui_registry, "get_enabled_plugin_ids", lambda: ["gw"])
    monkeypatch.setattr(ui_registry, "provider_visible", lambda pid: pid != "hidden")
    with state._LOCK:
        saved = list(state._CONTRIBUTIONS["llm_providers"])
        state._CONTRIBUTIONS["llm_providers"] = [
            {"id": "shown", "label": "Shown", "plugin_id": "gw"},
            {"id": "hidden", "label": "Hidden", "plugin_id": "gw"},
        ]
    try:
        out = ui_registry.get_ui_contributions()
        assert [r["id"] for r in out["llm_providers"]] == ["shown"]
        assert out["enabled_ids"] == ["gw"]
    finally:
        with state._LOCK:
            state._CONTRIBUTIONS["llm_providers"] = saved
