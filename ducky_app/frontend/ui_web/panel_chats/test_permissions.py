"""panel_chats.permissions: a chat's approval mode and rules change only when the change saves."""

from __future__ import annotations

import pytest

from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


@pytest.fixture
def store(monkeypatch):
    from backend.tools.panel import permission_prompt as p

    rules = {"c": ["Bash(git status)", "*"]}
    state = {"mode": "ask"}
    monkeypatch.setattr(p, "_rules", lambda cid: list(rules.get(cid, [])))
    monkeypatch.setattr(p, "_store_rules", lambda cid, kept: rules.__setitem__(cid, list(kept)))
    monkeypatch.setattr(p, "forget_rule", lambda cid, rule: rules[cid].remove(rule))
    monkeypatch.setattr(p, "set_permission_mode", lambda cid, mode: state.__setitem__("mode", mode))
    monkeypatch.setattr(p, "chat_permissions", lambda cid, agent="": {
        "agent": "claude_code", "mode": state["mode"], "rules": rules.get(cid, []),
        "modes": [{"id": "ask", "available": True}, {"id": "all", "available": False, "reason": "Needs the plugin"}]})
    return rules


def test_a_mode_its_agent_cannot_use_is_refused(store):
    api = PanelApiChatsMixin()
    with pytest.raises(ValueError, match="Needs the plugin"):
        api.set_agent_permission_mode("c", "all")
    with pytest.raises(ValueError, match="Unknown permission mode"):
        api.set_agent_permission_mode("c", "nope")
    assert api.set_agent_permission_mode("c", "ask")["mode"] == "ask"


def test_forgetting_and_revoking_rules(store):
    api = PanelApiChatsMixin()
    assert api.remove_agent_permission_rule("c", "Bash(git status)")["rules"] == ["*"]
    assert api.revoke_saved_agent_permission("c", "*") == {"ok": True}
    assert store["c"] == []
