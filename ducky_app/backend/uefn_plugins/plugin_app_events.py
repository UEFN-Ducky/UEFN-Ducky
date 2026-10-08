"""What a plugin can ask of the app window (``api.emit_hook`` / ``api.set_appearance_profile``):
fire one of its own sound hooks, or switch Appearance to one of its own themes. Both go
to every window over the panel push bus and the main window acts on them, so a sound
plays once. Each event carries ``at`` so a window that reloads doesn't replay old ones.
"""

from __future__ import annotations

import time
from typing import Any

# Same id shape as the web app's pluginProfileId().
PROFILE_PREFIX = "__plugin__:"


def _declared(plugin_id: str, kind: str, local_id: str) -> bool:
    from backend.uefn_plugins.host import get_ui_contributions

    rows = get_ui_contributions().get(kind) or []
    return any(isinstance(r, dict) and r.get("plugin_id") == plugin_id and str(r.get("id") or "") == local_id for r in rows)


def _push(event: dict[str, Any]) -> dict[str, Any] | None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({**event, "at": time.time()})
    except Exception as exc:  # noqa: BLE001 — told to the plugin, never raised into it
        return {"ok": False, "error": f"Couldn't reach the app window: {exc}"}
    return None


def emit_hook(plugin_id: str, hook_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Fire one of the plugin's ``contributes.hooks``: the sound the user put on it in
    Settings → Appearance → Sounds plays (and ``ducky:hook`` listeners see it)."""
    hid = str(hook_id or "").strip()
    if not _declared(plugin_id, "hooks", hid):
        return {"ok": False, "error": f"{hid or 'That hook'} isn't one of this plugin's hooks (contributes.hooks)."}
    failed = _push({"type": "plugin_hook", "id": hid, "plugin_id": plugin_id, "payload": dict(payload or {})})
    return failed or {"ok": True, "hook": hid}


def set_appearance_profile(plugin_id: str, profile_id: str) -> dict[str, Any]:
    """Switch Appearance to one of the plugin's own ``appearance.profiles`` (its ``id`` there),
    the same as picking it in Settings → Appearance."""
    local = str(profile_id or "").strip()
    if not _declared(plugin_id, "appearance_profiles", local):
        return {"ok": False, "error": f"{local or 'That theme'} isn't one of this plugin's appearance.profiles."}
    full = f"{PROFILE_PREFIX}{plugin_id.strip().lower()}:{local}"
    failed = _push({"type": "appearance_profile_requested", "id": full, "plugin_id": plugin_id})
    return failed or {"ok": True, "profile": full}
