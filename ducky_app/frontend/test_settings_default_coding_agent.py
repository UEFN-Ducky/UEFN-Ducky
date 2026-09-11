"""The default coding agent must survive moments when its gateway is not registered."""

from __future__ import annotations

from frontend.settings import PanelSettings


def test_validate_keeps_unregistered_default_coding_agent(monkeypatch) -> None:
    monkeypatch.setattr("backend.uefn_plugins.host.plugins_ready", lambda: True)
    monkeypatch.setattr("backend.agent.coding_agents.base.contributed_coding_agents", lambda: ())
    s = PanelSettings(default_coding_agent="claude_code")
    s.validate()
    assert s.default_coding_agent == "claude_code"
    s.save()
    assert PanelSettings.load().default_coding_agent == "claude_code"


def test_validate_normalises_registered_alias_and_defaults_blank(monkeypatch) -> None:
    monkeypatch.setattr("backend.uefn_plugins.host.plugins_ready", lambda: True)
    monkeypatch.setattr("backend.agent.coding_agents.base.contributed_coding_agents", lambda: ("claude_code",))
    monkeypatch.setattr("backend.agent.coding_agents.base.normalize_coding_agent", lambda v: "ducky" if (v or "").lower() in ("", "ducky") else "claude_code")
    s = PanelSettings(default_coding_agent="Claude-Code")
    s.validate()
    assert s.default_coding_agent == "claude_code"
    s = PanelSettings(default_coding_agent="")
    s.validate()
    assert s.default_coding_agent == "ducky"
