"""Ducky profile pathway: read/create/edit the ducky templates in Settings → Duckies.

Wraps :mod:`frontend.agent_profiles`. A profile's `style` is validated against
the available ducky styles; an unknown style returns the valid options so the
agent can spotlight the Appearance picker instead of guessing.
"""

from __future__ import annotations

from typing import Any

from backend.util.json_util import tool_json
from backend.server import mcp

# Patch keys accepted from callers → canonical PROFILE_FIELDS names.
_PATCH_ALIASES = {
    "display_name": "name",
    "name": "name",
    "style": "ducky_style",
    "ducky_style": "ducky_style",
    "personality": "ducky_personality",
    "ducky_personality": "ducky_personality",
    "when_to_use": "when_to_use",
    "disabled_packs": "disabled_packs",
    "enabled_subskills": "enabled_subskills",
    "disabled_tool_ids": "disabled_tool_ids",
    "favorite_model": "favorite_models",
    "favorite_models": "favorite_models",
}


def _valid_styles() -> list[str]:
    from frontend.ducky_assets import list_bundled_duckies

    return [d.id for d in list_bundled_duckies()]


USE_EXISTING = (
    "Not created: a team seats the existing ducky profiles. Pick one with ducky_list_duckies and "
    "seat it with ducky_group_invite or ducky_spawn_chat(ducky=<name>); give it its job in the plan. "
    "New profiles are the person's to make in Settings → Duckies."
)


def _team_caller() -> str:
    """The calling agent's chat when it sits in a group (a lead or a member), else ''.

    Leads made a new profile for every member they seated ("Island Kit Gateway …, Parallel
    specialist for …"), so the person's Duckies page filled with one-off copies."""
    try:
        from backend.agent.coding_agents.plans import _chat_and_group_ids
        from backend.workspace.identity import resolve_context

        context = resolve_context()
        cid = str(getattr(context, "conv_id", "") or "").strip() if context is not None else ""
        return cid if cid and len(_chat_and_group_ids(cid, None)) > 1 else ""
    except Exception:
        return ""


def _notify_changed() -> None:
    try:
        from frontend.ui_web.agent_turns.push import push_ui_event

        push_ui_event({"type": "duckies_changed"})
    except Exception:
        pass


def _normalize_patch(patch: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        canonical = _PATCH_ALIASES.get(str(key))
        if not canonical:
            continue
        if canonical == "favorite_models" and not isinstance(value, list):
            out[canonical] = [str(value)] if value else []
        else:
            out[canonical] = value
    if out.get("favorite_models"):
        out["favorite_models"] = [_exact_model(str(m)) for m in out["favorite_models"]]
    return out


def _exact_model(raw: str) -> str:
    """A bare model id is saved as the exact pick it resolves to ("gpt-6.1-sol" →
    "codex:gpt-6.1-sol" when Codex offers it), so the profile shows how it will run."""
    from frontend.favorite_models import ResolveOk, parse_selection, resolve_model_strict
    from frontend.settings import PanelSettings

    text = (raw or "").strip()
    if not text or parse_selection(text) is not None:
        return text
    try:
        result = resolve_model_strict([text], PanelSettings.load())
    except Exception:
        return text
    return result.selection.qualified if isinstance(result, ResolveOk) else text


def set_profile(ducky_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Patch a ducky profile (bundled or custom). Shared with the guide composer."""
    from frontend.agent_profiles import (
        bundled_profile_ids,
        get_agent_profile,
        save_agent_profile,
        save_agent_profile_override,
    )

    pid = (ducky_id or "").strip()
    if not pid:
        return {"error": "ducky_id required"}
    existing = get_agent_profile(pid)
    if not existing:
        return {"error": f"profile not found: {pid}"}
    norm = _normalize_patch(patch)
    if not norm:
        return {"error": "no recognized patch fields"}
    style = norm.get("ducky_style")
    if style is not None:
        style_str = str(style).strip()
        if not style_str.startswith("custom:") and style_str not in _valid_styles():
            return {"error": f"unknown style: {style_str}", "valid_styles": _valid_styles()}
    try:
        if pid in bundled_profile_ids():
            saved = save_agent_profile_override(pid, norm)
        else:
            saved = save_agent_profile({**{k: existing.get(k) for k in existing}, **norm, "id": pid})
    except ValueError as exc:
        return {"error": str(exc)}
    _notify_changed()
    return {"ok": True, "profile": saved}


@mcp.tool()
def ducky_profile_list(pretty: bool = False) -> str:
    """List ducky profiles (bundled + custom): {id, name, ducky_style, when_to_use, ...}."""
    from frontend.agent_profiles import list_agent_profiles_available

    return tool_json({"profiles": list_agent_profiles_available()}, pretty=pretty)


@mcp.tool()
def ducky_profile_get(ducky_id: str, pretty: bool = False) -> str:
    """Get one ducky profile by id (packs, tools, style, personality, favorite model)."""
    from frontend.agent_profiles import get_agent_profile

    profile = get_agent_profile((ducky_id or "").strip())
    if not profile:
        return tool_json({"error": f"profile not found: {ducky_id}"}, pretty=pretty)
    return tool_json({"profile": profile}, pretty=pretty)


@mcp.tool()
def ducky_profile_set(ducky_id: str, patch: dict[str, Any], pretty: bool = False) -> str:
    """Edit a ducky profile: its name, look, personality, when to use it, model, packs or tools.

    patch keys: display_name, style, personality, when_to_use, favorite_model,
    enabled_packs, tool_ids. An unknown `style` returns the valid options.
    Example: ducky_profile_set("producer", {"style": "wizard"}).
    """
    return tool_json(set_profile(ducky_id, patch or {}), pretty=pretty)


@mcp.tool()
def ducky_profile_duplicate(ducky_id: str, pretty: bool = False) -> str:
    """Clone a ducky profile into a new editable custom profile (never from inside a team)."""
    from frontend.agent_profiles import duplicate_agent_profile

    if _team_caller():
        return tool_json({"ok": False, "error": USE_EXISTING}, pretty=pretty)

    try:
        created = duplicate_agent_profile((ducky_id or "").strip())
    except ValueError as exc:
        return tool_json({"error": str(exc)}, pretty=pretty)
    _notify_changed()
    return tool_json({"ok": True, "profile": created}, pretty=pretty)


@mcp.tool()
def ducky_profile_create(name: str, patch: dict[str, Any] | None = None, pretty: bool = False) -> str:
    """Create a new custom ducky profile.

    name: display name. patch: optional style/personality/when_to_use/favorite_model/
    enabled_packs/tool_ids. An unknown `style` returns the valid options. A team never makes
    profiles: it seats the existing ones (ducky_list_duckies).
    """
    from frontend.agent_profiles import save_agent_profile

    if _team_caller():
        return tool_json({"ok": False, "error": USE_EXISTING}, pretty=pretty)

    norm = _normalize_patch(patch or {})
    style = norm.get("ducky_style")
    if style is not None:
        style_str = str(style).strip()
        if not style_str.startswith("custom:") and style_str not in _valid_styles():
            return tool_json({"error": f"unknown style: {style_str}", "valid_styles": _valid_styles()}, pretty=pretty)
    try:
        created = save_agent_profile({**norm, "name": (name or "").strip() or "Untitled"})
    except ValueError as exc:
        return tool_json({"error": str(exc)}, pretty=pretty)
    _notify_changed()
    return tool_json({"ok": True, "profile": created}, pretty=pretty)
