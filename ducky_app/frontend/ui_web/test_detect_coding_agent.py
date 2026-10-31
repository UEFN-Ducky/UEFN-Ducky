"""Detect reloads only the plugin that adds the agent, and only when it is installed
(Oct 30 2026: a full reload of every plugin per agent checked, three in 21 s)."""

from __future__ import annotations

import frontend.ui_web.panel_api  # noqa: F401 - loads the mixins in the app's order
from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin


def _api(monkeypatch, plugin_of: dict[str, str]):
    calls: list[str] = []
    monkeypatch.setattr("backend.uefn_plugins.host.ensure_plugins_loaded", lambda timeout=0: True)
    monkeypatch.setattr("backend.uefn_plugins.host.reload_plugins", lambda: calls.append("FULL"))
    monkeypatch.setattr("backend.uefn_plugins.host.reload_single_plugin", lambda pid: calls.append(pid))
    monkeypatch.setattr("backend.uefn_plugins.host.wait_plugin_toggles", lambda timeout=0: None)
    monkeypatch.setattr("backend.agent.coding_agents.base.normalize_coding_agent", lambda aid: aid)
    monkeypatch.setattr("backend.agent.coding_agents.base.get_adapter", lambda aid: None)
    monkeypatch.setattr("backend.agent.coding_agents.base.invalidate_detect_cache", lambda: None)
    monkeypatch.setattr("backend.agent.coding_agents.base.coding_agent_plugin_id", lambda aid: plugin_of.get(aid, ""))
    api = PanelApiSettingsMixin()
    api._coding_agent_reload_tried = set()
    return api, calls


def test_only_the_agents_own_plugin_reloads_once(monkeypatch) -> None:
    api, calls = _api(monkeypatch, {"codex": "openai"})
    for _ in range(3):
        assert api.detect_coding_agent_cli("codex")["ok"] is False
    assert calls == ["openai"]


def test_an_agent_whose_plugin_is_not_installed_reloads_nothing(monkeypatch) -> None:
    api, calls = _api(monkeypatch, {})
    for aid in ("cursor", "gemini", "kimi"):
        api.detect_coding_agent_cli(aid)
    assert calls == []
