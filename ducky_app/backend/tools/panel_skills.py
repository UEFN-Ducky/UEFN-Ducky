"""Skills pathway: list/create packs, additive subskills, enablement, studio nav.

Wraps :mod:`backend.skill`. Store packs are additive-only (origin=user subskills
survive Store updates). Reading content uses skill_read_subskill.
"""

from __future__ import annotations

from typing import Any

from backend.json_util import tool_json
from backend.server import mcp


def _notify_changed() -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "skills_changed"})
    except Exception:
        pass


def create_pack_tool(pack_id: str, label: str = "", description: str = "") -> dict[str, Any]:
    from backend.skill import create_skill_pack, normalize_pack_id

    try:
        pid = normalize_pack_id(pack_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        dest = create_skill_pack(pid, label or "", description or "")
    except FileExistsError as exc:
        return {"ok": False, "error": str(exc)}
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    _notify_changed()
    return {"ok": True, "pack_id": pid, "path": str(dest)}


def _pack_is_user_owned(man: dict[str, Any]) -> bool:
    """True when the pack itself is user-authored (not store/bundled/plugin)."""
    from backend.skill import ORIGIN_USER, SOURCE_STORE

    kind = str(man.get("kind") or "").strip().lower()
    if kind in ("store", "bundled", "plugin"):
        return False
    if str(man.get("source") or "").strip().lower() == SOURCE_STORE:
        return False
    if str(man.get("origin") or "").strip().lower() == ORIGIN_USER:
        return True
    # Custom AppData packs without store source are user-owned.
    return kind in ("custom", "user", "") or not kind


def write_subskill_tool(
    pack_id: str,
    subskill_id: str,
    body: str,
    label: str = "",
    description: str = "",
) -> dict[str, Any]:
    """Create or update a user-origin subskill. Additive-only on Store packs."""
    from backend.skill import (
        CORE_ID,
        ORIGIN_USER,
        create_subskill,
        load_pack_manifest,
        normalize_pack_id,
        normalize_subskill_id,
        save_subskill,
    )

    try:
        pid = normalize_pack_id(pack_id)
        sid = normalize_subskill_id(subskill_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    man = load_pack_manifest(pid)
    if man is None:
        return {"ok": False, "error": f"skill pack not found: {pid}"}
    if sid == CORE_ID:
        if not _pack_is_user_owned(man):
            return {
                "ok": False,
                "error": (
                    "Cannot overwrite Store/bundled/plugin SKILL.md — "
                    "add a user subskill instead (survives Store updates)"
                ),
            }
        try:
            path = save_subskill(pid, CORE_ID, body if isinstance(body, str) else str(body))
        except (FileNotFoundError, ValueError, OSError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        _notify_changed()
        return {"ok": True, "pack_id": pid, "subskill_id": sid, "path": str(path), "origin": ORIGIN_USER}

    existing = None
    for sub in man.get("subskills") or []:
        if isinstance(sub, dict) and str(sub.get("id") or "") == sid:
            existing = sub
            break
    if existing is not None:
        sub_origin = str(existing.get("origin") or "").strip().lower()
        # Missing origin on a non-user pack → treat as store/bundled (refuse overwrite).
        if sub_origin != ORIGIN_USER:
            return {
                "ok": False,
                "error": (
                    f"Subskill {sid!r} is store/bundled content — "
                    "add a new user subskill instead (additive; survives Store updates)"
                ),
            }
        try:
            path = save_subskill(pid, sid, body if isinstance(body, str) else str(body))
        except (FileNotFoundError, ValueError, OSError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        _notify_changed()
        return {
            "ok": True,
            "pack_id": pid,
            "subskill_id": sid,
            "path": str(path),
            "origin": ORIGIN_USER,
            "updated": True,
        }

    # Create new user subskill (works on Store packs — additive).
    clean_label = (label or "").strip() or sid.replace("_", " ").title()
    try:
        path = create_subskill(pid, sid, clean_label, description or clean_label)
        if body and str(body).strip():
            path = save_subskill(pid, sid, str(body))
    except FileExistsError as exc:
        return {"ok": False, "error": str(exc)}
    except (FileNotFoundError, ValueError, OSError, PermissionError) as exc:
        return {"ok": False, "error": str(exc)}
    _notify_changed()
    return {
        "ok": True,
        "pack_id": pid,
        "subskill_id": sid,
        "path": str(path),
        "origin": ORIGIN_USER,
        "created": True,
    }


def set_pack_scoped(pack_id: str, enabled: bool, *, scope: str = "global", chat_id: str = "") -> dict[str, Any]:
    """Allow/deny a skill pack globally or for one chat (deny-list). Shared with the guide."""
    from backend.skill import list_pack_ids

    pid = (pack_id or "").strip()
    if pid not in list_pack_ids():
        return {"error": f"skill pack not found: {pid}"}
    scope = (scope or "global").strip().lower()
    if scope == "global":
        from frontend.settings import PanelSettings

        settings = PanelSettings.load()
        denied = list(settings.default_disabled_packs or [])
        if enabled:
            denied = [p for p in denied if p != pid]
        elif pid not in denied:
            denied.append(pid)
        settings.default_disabled_packs = denied
        settings.validate()
        settings.save()
        _notify_changed()
        return {
            "ok": True,
            "pack_id": pid,
            "enabled": bool(enabled),
            "scope": "global",
            "default_disabled_packs": denied,
        }
    if scope != "chat":
        return {"error": "scope must be global or chat"}
    cid = (chat_id or "").strip()
    if not cid:
        return {"error": "chat_id required for scope=chat"}
    from frontend.ui_web.project_chats import load_conversation, set_conversation_disabled_packs

    conv = load_conversation(cid)
    if not conv:
        return {"error": f"conversation not found: {cid}"}
    denied = list(getattr(conv, "disabled_packs", None) or [])
    if enabled:
        denied = [p for p in denied if p != pid]
    elif pid not in denied:
        denied.append(pid)
    out = set_conversation_disabled_packs(cid, denied)
    _notify_changed()
    return {
        "ok": True,
        "pack_id": pid,
        "enabled": bool(enabled),
        "scope": "chat",
        "chat_id": cid,
        "disabled_packs": out,
    }


@mcp.tool()
def ducky_skills_list_packs(pretty: bool = False) -> str:
    """List installed skill packs: {id, label, kind, version, default_enabled, subskills}.

    `default_enabled` is True when the pack is not on the global deny-list.
    """
    from backend.skill import list_skill_packs
    from frontend.settings import PanelSettings

    denied = set(PanelSettings.load().default_disabled_packs or [])
    packs = list_skill_packs()
    for pack in packs:
        pack["default_enabled"] = pack.get("id") not in denied
    return tool_json({"packs": packs}, pretty=pretty)


@mcp.tool()
def ducky_skills_set_pack(
    pack_id: str, enabled: bool, scope: str = "global", chat_id: str = "", pretty: bool = False
) -> str:
    """Enable or disable a skill pack.

    scope: "global" (default for new chats) or "chat" (requires chat_id).
    Example: ducky_skills_set_pack("materials", true).
    """
    return tool_json(set_pack_scoped(pack_id, bool(enabled), scope=scope, chat_id=chat_id), pretty=pretty)


@mcp.tool()
def ducky_skills_open_in_studio(pack_id: str = "", pretty: bool = False) -> str:
    """Open the Skills studio (optionally on a pack) so the user can edit it. Needs an open panel."""
    from backend.panel_rpc import panel_rpc

    return tool_json(panel_rpc("navigate", {"route": "skills_studio", "item_id": (pack_id or "").strip()}), pretty=pretty)


@mcp.tool()
def ducky_skills_create_pack(
    pack_id: str,
    label: str = "",
    description: str = "",
    pretty: bool = False,
) -> str:
    """Create a new user-owned skill pack in AppData (origin=user, fully editable)."""
    return tool_json(create_pack_tool(pack_id, label=label, description=description), pretty=pretty)


@mcp.tool()
def ducky_skills_write_subskill(
    pack_id: str,
    subskill_id: str,
    body: str,
    label: str = "",
    description: str = "",
    pretty: bool = False,
) -> str:
    """Create or update a user-origin subskill (additive on Store packs).

    Store/bundled/plugin SKILL.md and store-origin refs cannot be overwritten —
    add a new subskill_id instead so future Store updates keep your changes.
    User-created packs can edit core (subskill_id=\"core\").
    """
    return tool_json(
        write_subskill_tool(
            pack_id,
            subskill_id,
            body,
            label=label,
            description=description,
        ),
        pretty=pretty,
    )
