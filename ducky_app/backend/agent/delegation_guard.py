"""Detect coordinator replies that claim delegation without calling swarm tools."""

from __future__ import annotations

import re
from typing import Any

from backend.agent.builtin_toolsets import BUILTIN_DUCKY, BUILTIN_UEFN

DELEGATION_TOOL_NAMES = frozenset(
    {
        "ducky_spawn_chat",
        "ducky_send_chat_message",
        "ducky_group_create",
        "ducky_group_invite",
        "ducky_group_add_member",
        "ducky_recycle_member",
        "ducky_recycle_subagent",
    }
)

_DELEGATION_CLAIM_RE = re.compile(
    r"\b(delegat|specialist|initiated|sending.{0,40}task"
    r"|spawn_chat|group_invite|underway|routing|handing\s+off)\b",
    re.I,
)

_FAKE_DELEGATION_WARNING = (
    "This reply talks about delegating but never called a swarm tool "
    "(ducky_group_create / ducky_group_invite / ducky_spawn_chat / "
    "ducky_send_chat_message) — no specialist was seated. "
    'Say "delegate now" or switch to a specialist ducky.'
)


def is_delegator_conv(conv: Any) -> bool:
    """True when this chat should be treated as a swarm coordinator.

    Decided by user-written details (personality/when_to_use mentioning a
    swarm/delegation tool) or by a Ducky-app-only toolset config
    (builtin_ducky without builtin_uefn). No profile-name hardcoding —
    rename/delete Producer freely; write the tools into any custom ducky's
    details and it qualifies the same way.
    """
    from backend.agent.profile_details import tools_named_in_profile

    if tools_named_in_profile(conv, DELEGATION_TOOL_NAMES):
        return True
    toolsets = getattr(conv, "builtin_toolsets", None)
    if toolsets is None:
        return False
    ids = {str(x).strip() for x in toolsets if str(x).strip()}
    if not ids:
        return False
    # Legacy chats may still list builtin_uefn — those are not Ducky-only coordinators.
    return BUILTIN_DUCKY in ids and BUILTIN_UEFN not in ids


def _message_claims_delegation(assistant_message: dict[str, Any]) -> bool:
    content = str(assistant_message.get("content") or "")
    if _DELEGATION_CLAIM_RE.search(content):
        return True
    for block in assistant_message.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text = str(block.get("text") or block.get("content") or "")
            if _DELEGATION_CLAIM_RE.search(text):
                return True
    return False


def _message_spawned(assistant_message: dict[str, Any], tools_called: set[str]) -> bool:
    if DELEGATION_TOOL_NAMES & tools_called:
        return True
    for block in assistant_message.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_call" and str(block.get("name") or "") in DELEGATION_TOOL_NAMES:
            return True
    return False


def fake_delegation_warning(
    conv: Any,
    assistant_message: dict[str, Any] | None,
    tools_called: set[str],
) -> str | None:
    """Return a user-visible warning when a coordinator narrated delegation without spawning."""
    if not assistant_message or not is_delegator_conv(conv):
        return None
    if _message_spawned(assistant_message, tools_called):
        return None
    if not _message_claims_delegation(assistant_message):
        return None
    return _FAKE_DELEGATION_WARNING


def append_delegation_warning(assistant_message: dict[str, Any], warning: str) -> dict[str, Any]:
    """Copy assistant message with a trailing delegation warning."""
    msg = dict(assistant_message)
    content = str(msg.get("content") or "").rstrip()
    banner = f"\n\n---\n\n⚠ **Delegation not executed:** {warning}"
    msg["content"] = (content + banner) if content else f"⚠ **Delegation not executed:** {warning}"
    msg["delegation_warning"] = warning
    return msg
