"""Focused Creative device tools — ToyOptions / native UEFN devices (not Verse @editable)."""

from __future__ import annotations

from typing import Any, Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


def _resolve_actor_path(actor_path: str = "", label: str = "") -> str:
    return (actor_path or label or "").strip()


def _filter_settings(result: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    """Keep only requested ToyOptions keys (case-sensitive first, then casefold)."""
    wanted = [k for k in keys if isinstance(k, str) and k.strip()]
    if not wanted:
        return result
    settings = result.get("settings")
    if not isinstance(settings, dict):
        result["keys_filtered"] = True
        return result
    by_fold = {str(k).casefold(): k for k in settings}
    out: dict[str, Any] = {}
    missing: list[str] = []
    for key in wanted:
        if key in settings:
            out[key] = settings[key]
        elif key.casefold() in by_fold:
            real = by_fold[key.casefold()]
            out[real] = settings[real]
        else:
            missing.append(key)
    result = {**result, "settings": out, "keys_filtered": True}
    if missing:
        result["keys_missing"] = missing
    return result


@plugin_mcp_tool("uefn")
def find_devices(
    label_filter: str = "",
    class_filter: str = "",
    limit: int = 200,
    pretty: bool = False,
) -> str:
    """Find devices in the level. Result ``kind`` routes the next call:

    verse_script → inspect_verse_device; creative_device → inspect_creative_device.
    Prefer this over get_all_actors for "find my wallet / granter / button".
    """
    result = send_command(
        "list_creative_devices",
        {"class_filter": class_filter, "label_filter": label_filter, "limit": limit},
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def inspect_creative_device(
    actor_path: str = "",
    label: str = "",
    keys: Optional[list[str]] = None,
    include_events: bool = False,
    pretty: bool = False,
) -> str:
    """READ settings on a native Creative device (granter, button, spawner, …).

    ``actor_path`` may be the Outliner label (preferred). ``label`` is an alias for
    ``actor_path``. Pass ``keys=["MaxPlayers", …]`` for Island Settings — full dumps
    are huge. For kind: verse_script devices use inspect_verse_device instead.
    """
    path = _resolve_actor_path(actor_path, label)
    if not path:
        return tool_json(
            {"error": "actor_path required (Outliner label works; label= is accepted as alias)"},
            pretty=pretty,
        )
    key_list = [k for k in (keys or []) if isinstance(k, str) and k.strip()]
    params: dict[str, Any] = {"actor_path": path, "include_events": include_events}
    if key_list:
        params["keys"] = key_list
    result = send_command("get_device_settings", params)
    if isinstance(result, dict) and key_list and not result.get("keys_filtered"):
        result = _filter_settings(result, key_list)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def set_creative_device_fields(
    actor_path: str = "",
    fields: Optional[dict[str, Any]] = None,
    label: str = "",
    save_level: bool = False,
    pretty: bool = False,
) -> str:
    """WRITE ToyOptions settings on a native Creative device.

    inspect_creative_device first — only use property keys from that response.
    e.g. ``{"PlayAudio": "YES", "PlayerTeam": {"team_index": 2, "team_type": "Any"}}``.
    ``label`` aliases ``actor_path``. For Verse @editable fields use
    wire_verse_device_ref or set_verse_editable (one field per call).
    """
    path = _resolve_actor_path(actor_path, label)
    if not path:
        return tool_json(
            {"error": "actor_path required (Outliner label works; label= is accepted as alias)"},
            pretty=pretty,
        )
    props = fields if isinstance(fields, dict) else {}
    result = send_command(
        "set_device_settings",
        {"actor_path": path, "properties": props, "save_level": save_level},
    )
    return tool_json(result, pretty=pretty)
