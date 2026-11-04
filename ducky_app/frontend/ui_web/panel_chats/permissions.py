"""A chat's agent approvals: Allow everything, the approval mode, its "always allow" rules,
updating the agent's plugin, and the saved approvals list in Settings."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


def set_agent_allow_everything(self, conv_id: str, on: bool) -> dict[str, Any]:
    """The chat's own "Allow everything" (approval cards) on or off."""
    from backend.tools.panel.permission_prompt import allow_state, set_allow_everything

    set_allow_everything(str(conv_id or ""), bool(on))
    return {"ok": True, **allow_state(str(conv_id or ""))}


def get_agent_permissions(self, conv_id: str, agent: str = "") -> dict[str, Any]:
    """The chat's approval mode, the modes its agent can use and the rules it allowed."""
    from backend.tools.panel.permission_prompt import chat_permissions

    return chat_permissions(str(conv_id or ""), str(agent or ""))


def update_agent_plugin(self, conv_id: str, agent: str = "") -> dict[str, Any]:
    """The pop-up's Update: install the Store's newest plugin for this agent, then its new modes."""
    from backend.tools.panel import permission_prompt as permissions

    conv_id = str(conv_id or "")
    agent_id = str(agent or "") or permissions.chat_permissions(conv_id)["agent"]
    permissions.update_agent_plugin(agent_id)
    return permissions.chat_permissions(conv_id, agent_id)


def set_agent_permission_mode(self, conv_id: str, mode: str, agent: str = "") -> dict[str, Any]:
    """Pick "ask", "edits" or "all" for this chat; its next turn uses it."""
    from backend.tools.panel import permission_prompt as permissions

    conv_id, agent = str(conv_id or ""), str(agent or "")
    row = next((m for m in permissions.chat_permissions(conv_id, agent)["modes"] if m["id"] == mode), None)
    if row is None:
        raise ValueError(f"Unknown permission mode: {mode!r}")
    if not row["available"]:
        raise ValueError(row["reason"])
    permissions.set_permission_mode(conv_id, mode)
    state = permissions.chat_permissions(conv_id, agent)
    if state["mode"] != mode:
        raise ValueError("Could not save the permission change.")
    return state


def remove_agent_permission_rule(self, conv_id: str, rule: str, agent: str = "") -> dict[str, Any]:
    """Forget one "always allow" rule of this chat."""
    from backend.tools.panel import permission_prompt as permissions

    conv_id = str(conv_id or "")
    permissions.forget_rule(conv_id, str(rule or ""))
    if rule in permissions._rules(conv_id):
        raise ValueError("Could not save the permission change.")
    return permissions.chat_permissions(conv_id, str(agent or ""))


def clear_agent_permission_rules(self, conv_id: str, agent: str = "") -> dict[str, Any]:
    """Forget every "always allow" rule of this chat; its mode stays."""
    from backend.tools.panel import permission_prompt as permissions

    conv_id = str(conv_id or "")
    permissions.clear_rules(conv_id)
    state = permissions.chat_permissions(conv_id, str(agent or ""))
    if state["rules"]:
        raise ValueError("Could not save the permission change.")
    return state


def list_saved_agent_permissions(self) -> list[dict[str, str]]:
    """All remembered chat approvals, including individual command/file rules."""
    from backend.tools.panel import permission_prompt as permissions

    if permissions._rules_in_db():
        from backend.store.repos import chats, kv

        docs = kv.list_docs("workspace_state", "agent_allow:")
        entries = []
        for key, rules in docs.items():
            conv_id = key.removeprefix("agent_allow:")
            conv = chats.conv_get(conv_id, with_messages=False)
            if conv and isinstance(rules, list):
                entries.append((conv_id, str(conv.get("title") or "Chat"), rules))
    else:
        entries = [
            (conv.id, conv.title or "Chat", list(getattr(conv, "agent_allow_rules", None) or []))
            for _, conv in _pa.iter_conversations_by_project()
        ]
    return [
        {"conv_id": conv_id, "title": title, "rule": rule,
         "label": "Allow everything in this chat" if rule == "*" else rule}
        for conv_id, title, rules in entries for rule in rules if isinstance(rule, str) and rule
    ]


def revoke_saved_agent_permission(self, conv_id: str, rule: str) -> dict[str, bool]:
    """Human Settings action: remove one existing approval, never grant access."""
    from backend.tools.panel import permission_prompt as permissions

    rules = permissions._rules(conv_id)
    remaining = [saved for saved in rules if saved != rule]
    permissions._store_rules(conv_id, remaining)
    if permissions._rules(conv_id) != remaining:
        raise ValueError("Could not save the permission change.")
    return {"ok": True}
