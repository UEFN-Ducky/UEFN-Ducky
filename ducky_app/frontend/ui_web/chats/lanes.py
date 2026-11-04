"""Group roster write lanes on save: an older copy never drops a lane set since."""

from __future__ import annotations

from typing import Any

from frontend.chat_store import Conversation


def _keep_stored_lanes(conv: Conversation, stored: dict[str, Any] | None) -> None:
    """A group roster's write lanes never go back to an older copy's (Oct 24 2026)."""
    if not getattr(conv, "is_group", False) or not isinstance(stored, dict):
        return
    from backend.workspace.lanes import keep_stored_lanes

    conv.group_members = keep_stored_lanes(
        getattr(conv, "group_members", None) or [], stored.get("group_members") or []
    )
