"""Roster-backed lane provider: a member's ``write_allowed`` lives on its group hub.

Lanes are stored on ``Conversation.group_members`` of the hub (outside the UEFN
write boundary, so a member cannot edit its own lane through file tools). The
provider resolves conversation → parent hub → member row for the pipeline's
``LanePolicy``; management goes through ``PanelApiChatsMixin.group_set_member_lane``.
"""

from __future__ import annotations

from typing import Any

from backend.workspace import lanes as lane_engine


def _load(conv_id: str) -> Any:
    from frontend.ui_web.project_chats import load_conversation

    return load_conversation(conv_id)


def lane_for_member(conv_id: str) -> tuple[str, ...] | None:
    """Lane from the member's hub roster; None when not a member or no lane set."""
    conv = _load(conv_id)
    if conv is None:
        return None
    parent_id = str(getattr(conv, "parent_conv_id", "") or "").strip()
    if not parent_id:
        return None
    from frontend.ui_web.group_orchestrator import group_members, is_group_conversation

    hub = _load(parent_id)
    if hub is None or not is_group_conversation(hub):
        return None
    for member in group_members(hub):
        if str(member.get("member_conv_id") or "") == conv_id:
            lane = member.get("write_allowed")
            return None if lane is None else tuple(lane)
    return None


class RosterLaneProvider:
    def lane_for(self, conv_id: str) -> tuple[str, ...] | None:
        return lane_for_member(conv_id)


def lane_mode() -> str:
    from frontend.settings import PanelSettings

    mode = str(getattr(PanelSettings.load(), "write_lanes_mode", "") or "").strip().lower()
    return mode if mode in lane_engine.MODES else lane_engine.MODE_SHADOW


def install() -> None:
    lane_engine.register_lane_provider(RosterLaneProvider())
