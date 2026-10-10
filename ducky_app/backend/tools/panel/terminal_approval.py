"""What an agent's terminal command card offers, and what its answer remembers for the chat.

The card sits in the chat that asked (Allow once / Always allow this command in this chat /
Allow everything in this chat / Deny). "Always allow" saves a ``Terminal:<command>`` rule on
the chat, next to the rules Claude Code's approval cards save, and only for plain commands:
a chained command, a push, a delete or a publish always asks (the same list
``permission_prompt`` uses). "Allow everything" is the chat's own switch, never offered for a
push or publish of a local-only AI plugin.
"""

from __future__ import annotations

from typing import Any

RULE_PREFIX = "Terminal:"
_SCOPES = ("once", "always", "all")


def _prompts():
    from backend.tools.panel import permission_prompt

    return permission_prompt


def describe_command(command: str, cwd: str = "") -> dict[str, Any]:
    """``rule_label``: what "Always allow ... in this chat" names ('' = never remembered).
    ``local_only``: pushes or publishes a local-only AI plugin (no "Allow everything")."""
    cmd = (command or "").strip()
    if not cmd:
        return {"rule_label": "", "local_only": False}
    try:
        prompts = _prompts()
        card = prompts.describe("Bash", {"command": cmd}, project_root=cwd or prompts._project_root())
    except Exception:
        return {"rule_label": "", "local_only": False}
    local_only = bool(card.get("local_only"))
    label = str(card.get("rule_label") or "") if card.get("rule") else ""
    # A push in any form (git -C <folder> push too) always asks.
    if local_only or prompts._REMOTE_CHANGE_RE.search(cmd):
        label = ""
    return {"rule_label": label, "local_only": local_only}


def rule_for(command: str, cwd: str = "") -> str:
    label = describe_command(command, cwd)["rule_label"]
    return f"{RULE_PREFIX}{label}" if label else ""


def allows(conv_id: str, command: str, cwd: str = "") -> bool:
    """This chat said "Always allow" for this command."""
    conv_id = (conv_id or "").strip()
    if not conv_id:
        return False
    rule = rule_for(command, cwd)
    if not rule:
        return False
    try:
        return rule in _prompts()._rules(conv_id)
    except Exception:
        return False


def remember(conv_id: str, command: str, scope: str, *, cwd: str = "", local_only: bool = False) -> str:
    """Save the chat's answer. Returns what was saved: once, always or all."""
    conv_id = (conv_id or "").strip()
    scope = scope if scope in _SCOPES else "once"
    if not conv_id or scope == "once":
        return "once"
    try:
        prompts = _prompts()
        if scope == "all":
            if local_only:
                return "once"
            prompts.set_allow_everything(conv_id, True)
            return "all"
        rule = rule_for(command, cwd)
        if not rule:
            return "once"
        prompts._remember(conv_id, rule)
        return "always"
    except Exception:
        return "once"
