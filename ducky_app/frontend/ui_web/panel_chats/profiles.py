"""Ducky profiles (the Duckies library): list, save, archive, copy and delete them, rename
the chats a renamed profile still names, and the profile editor's catalog."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


def list_agent_profiles(self) -> dict[str, Any]:
    profiles = _pa.list_agent_profiles()
    return {
        "profiles": profiles,
        "archived_profiles": _pa.list_archived_agent_profiles(),
        "template_profiles": _pa.list_bundled_agent_profile_templates(),
        "blank_profile_id": _pa.BLANK_PROFILE_ID,
    }


def archive_agent_profile(self, profile_id: str) -> dict[str, Any]:
    saved = _pa.archive_agent_profile(profile_id)
    return {"ok": True, "profile": saved}


def unarchive_agent_profile(self, profile_id: str) -> dict[str, Any]:
    saved = _pa.unarchive_agent_profile(profile_id)
    return {"ok": True, "profile": saved}


def save_agent_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")
    old = self._profile_name(str(profile.get("id") or ""))
    saved = _pa.save_agent_profile(profile)
    self._sync_chats_for_profile(saved, old)
    return {"ok": True, "profile": saved}


def save_agent_profile_override(self, bundled_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(patch, dict):
        raise ValueError("patch must be an object")
    old = self._profile_name(bundled_id)
    saved = _pa.save_agent_profile_override(bundled_id, patch)
    self._sync_chats_for_profile(saved, old)
    return {"ok": True, "profile": saved}


def _profile_name(profile_id: str) -> str:
    from frontend.agent_profiles import get_agent_profile

    try:
        return str((get_agent_profile(profile_id) or {}).get("name") or "").strip() if profile_id else ""
    except Exception:  # noqa: BLE001 - a new profile has no old name
        return ""


def _sync_chats_for_profile(self, profile: dict[str, Any], old_name: str = "") -> None:
    """A renamed library profile renames the chats still called by its old name (and its
    numbered copies, "Verse Coder 2"). Seats named by role ("Search lead") keep their name."""
    pid = str(profile.get("id") or "").strip()
    name = str(profile.get("name") or "").strip()
    old = (old_name or "").strip()
    if not pid or not name or not old or old == name:
        return
    try:

        for meta in _pa.list_all_conversation_metadata():
            if str(getattr(meta, "profile_id", "") or "").strip() != pid:
                continue
            current = str(getattr(meta, "ducky_name", "") or "").strip()
            suffix = current[len(old):]
            if not current.startswith(old) or (suffix and not suffix.strip().isdigit()):
                continue
            conv = _pa.load_conversation(meta.id)
            if conv is None:
                continue
            conv.ducky_name = name + suffix
            _pa.save_conversation(conv, touch_updated=False)
            _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=False)
    except Exception:
        _pa.logging.getLogger(__name__).exception("sync chats for profile %s failed", pid)


def delete_agent_profile(self, profile_id: str) -> dict[str, Any]:
    _pa.delete_agent_profile(profile_id)
    return {"ok": True}


def duplicate_agent_profile(self, profile_id: str) -> dict[str, Any]:
    duplicated = _pa.duplicate_agent_profile(profile_id)
    return {"ok": True, "profile": duplicated}


def get_agent_profile_editor_catalog(self) -> dict[str, Any]:
    """Duckies editor catalog — never sync-wait on plugin register().

    Same non-blocking pattern as get_uefn_plugin_contributions: return packs/
    builtins/MCP immediately; fill UEFN agent tool rows once plugins_ready.
    """
    from backend.agent.builtin_toolsets import (
        builtin_group_rows,
        get_enabled_builtin_group_ids,
    )
    from backend.mcp_plugins.store import (
        get_enabled_plugin_ids,
        list_mcp_plugins,
        seed_mcp_plugins,
    )
    from backend.skills.manifest import list_skill_packs
    from backend.skills.seeding import seed_skill_packs
    from backend.skills.selection import default_selection_from_settings
    from backend.uefn_plugins.host import (
        ensure_plugins_loaded_async,
        plugins_ready,
        uefn_agent_tool_rows,
    )
    from frontend.ui_web.project_chats import all_available_tool_ids

    seed_skill_packs()
    seed_mcp_plugins()
    settings = _pa.PanelSettings.load()
    sel = default_selection_from_settings(settings)
    base = {
        "packs": list_skill_packs(),
        "default_disabled_packs": list(sel.disabled_packs),
        "default_disabled_tool_ids": [],
        "default_enabled_packs": sel.enabled_packs,  # derived (all − denied)
        "default_enabled_subskills": {},
    }
    if plugins_ready():
        return {
            **base,
            "tools": builtin_group_rows() + list_mcp_plugins() + uefn_agent_tool_rows(),
            "default_tool_ids": all_available_tool_ids(),
            "plugins_loading": False,
        }
    # Kick background load; UI refreshes on uefn_plugins_changed.
    ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
    return {
        **base,
        "tools": builtin_group_rows() + list_mcp_plugins(),
        "default_tool_ids": list(get_enabled_builtin_group_ids())
        + list(get_enabled_plugin_ids()),
        "plugins_loading": True,
    }
