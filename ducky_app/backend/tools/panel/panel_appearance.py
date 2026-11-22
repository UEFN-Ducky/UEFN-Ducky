"""Settings → Appearance for agents: read the whole look, change any part of it, switch,
save, rename and delete themes, and pick effects, skins and sounds.

Every write goes through the Ducky window (``/__panel_api``), which saves it and redraws
every window at once, the same as the Appearance tab. New themes, effects, skins and
sounds are built as plugins (``appearance.profiles`` / ``.effects`` / ``.skin`` / ``sounds``
in plugin.json, see the ducky skill's plugin_look) and then picked here.
"""

from __future__ import annotations

import json
import time
from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json
from frontend.settings import PANEL_LISTENER_PORT

FOUNDATION_KEYS = ("accent", "bg", "surface", "text", "border")
STATUS_FIELDS = ("bg", "border", "text", "dim")
# ponytail: the keys of DEFAULT_CSS_VARS in web/src/theme/defaultTokens.ts, shown as a hint;
# overrides take any CSS variable name, so a new variable needs no change here.
CORE_VARIABLES = (
    "bg", "sidebar", "card", "card-hover", "panel-header", "btn-bg", "btn-hover", "btn-pressed",
    "fg", "fg-dim", "muted", "border", "border-light", "border-focus", "select", "green", "red",
    "amber", "blue", "purple", "yellow", "accent", "accent-hover", "dropdown-bg", "radius",
    "radius-sm", "font-ui", "font-mono", "font-ui-size", "font-mono-size", "blur-md",
)
# Same as DEFAULT_SOUNDS in web/src/sfx/soundFx.ts.
APP_SOUND_HOOKS = ("tab.changed", "settings.opened", "agent.selected", "agent.done", "agent.error", "verse.errors")
PLUGIN_PROFILE_PREFIX = "__plugin__:"
_ACTIVATE_WAIT_S = 6.0


def _window(method: str, args: dict[str, Any] | None = None, timeout: float = 15.0) -> Any:
    """Run one PanelApi method in the Ducky window; raise RuntimeError with its error."""
    import urllib.request

    request = urllib.request.Request(
        f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_api/{method}",
        data=json.dumps({"args": args or {}}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"The Ducky window isn't answering: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("ok", True):
        raise RuntimeError(str((payload or {}).get("error") or f"{method} failed"))
    return payload.get("result", payload)


def _writes_blocked() -> str | None:
    from frontend.duckyos_account import effective_allow_settings_write
    from frontend.settings import PanelSettings

    if effective_allow_settings_write(PanelSettings.load().allow_settings_write):
        return None
    return "Appearance changes are off: the person turned off 'Change Ducky Settings' (Settings → Account)."


def _enabled_rows(contrib: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    enabled = {str(i).strip().lower() for i in contrib.get("enabled_ids") or []}
    return [
        r for r in contrib.get(kind) or []
        if isinstance(r, dict) and str(r.get("plugin_id") or "").strip().lower() in enabled
    ]


def _ref(plugin_id: Any, local_id: Any) -> str:
    return f"plugin:{str(plugin_id).strip().lower()}:{str(local_id).strip()}"


def build_catalog(contrib: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Everything an agent can pick: themes, effects, skins, sounds and the keys it can set."""
    from frontend.appearance_builtin_profiles import (
        built_in_appearance_profile_ids,
        get_built_in_appearance_profile,
    )

    profiles: list[dict[str, Any]] = []
    for pid in sorted(built_in_appearance_profile_ids()):
        row = get_built_in_appearance_profile(pid) or {}
        profiles.append({"id": pid, "name": row.get("name") or pid, "kind": "built_in"})
    for r in _enabled_rows(contrib, "appearance_profiles"):
        profiles.append({
            "id": f"{PLUGIN_PROFILE_PREFIX}{str(r['plugin_id']).strip().lower()}:{r.get('id')}",
            "name": r.get("name") or r.get("id"), "kind": "plugin", "plugin_id": r["plugin_id"],
        })
    for p in current.get("profiles") or []:
        profiles.append({"id": p.get("id"), "name": p.get("name"), "kind": "user"})

    def picks(kind: str) -> list[dict[str, Any]]:
        return [
            {"id": _ref(r["plugin_id"], r.get("id")), "label": r.get("label") or r.get("id"), "plugin_id": r["plugin_id"]}
            for r in _enabled_rows(contrib, kind)
        ]

    sounds = [
        {"ref": _ref(r["plugin_id"], r.get("id")), "label": r.get("label") or r.get("id")}
        for r in _enabled_rows(contrib, "sounds")
    ]
    hooks = list(APP_SOUND_HOOKS) + [
        str(r.get("id")) for r in _enabled_rows(contrib, "hooks") if r.get("id")
    ]
    return {
        "profiles": profiles,
        "effects": picks("appearance_effects"),
        "skins": picks("appearance_skins"),
        "sounds": sounds,
        "sound_hooks": hooks,
        "foundation_keys": list(FOUNDATION_KEYS),
        "core_variables": list(CORE_VARIABLES),
        "status_fields": list(STATUS_FIELDS),
        "sound_ref_forms": ["builtin:<name> (click, pop, ding, error-buzz, ...)", "plugin:<plugin>:<sound>", "file:<name>", "\"\" = silent"],
    }


def _contributions() -> dict[str, Any]:
    try:
        out = _window("get_uefn_plugin_contributions")
        return out if isinstance(out, dict) else {}
    except RuntimeError:
        return {}


def _merge_flat(base: dict[str, Any], patch: dict[str, Any], *, allowed: tuple[str, ...] | None, label: str) -> dict[str, str]:
    out = {str(k): str(v) for k, v in (base or {}).items() if v}
    for raw_key, value in patch.items():
        key = str(raw_key).strip().removeprefix("--")
        if allowed is not None and key not in allowed:
            raise ValueError(f"{label}: '{key}' isn't one of {list(allowed)}")
        if value in (None, ""):
            out.pop(key, None)
        else:
            out[key] = str(value)
    return out


def merge_appearance(
    current: dict[str, Any],
    catalog: dict[str, Any],
    *,
    foundation: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    status_overrides: dict[str, Any] | None = None,
    effect_id: str | None = None,
    effects_enabled: bool | None = None,
    skin_id: str | None = None,
    sounds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The full appearance after a patch. ``""`` / None values remove a key. Raises ValueError."""
    nxt = json.loads(json.dumps(current))
    if foundation:
        nxt["foundation"] = _merge_flat(nxt.get("foundation") or {}, foundation, allowed=FOUNDATION_KEYS, label="foundation")
    if overrides:
        nxt["overrides"] = _merge_flat(nxt.get("overrides") or {}, overrides, allowed=None, label="overrides")
    if status_overrides:
        rows = {str(k): dict(v) for k, v in (nxt.get("status_overrides") or {}).items() if isinstance(v, dict)}
        for status_id, fields in status_overrides.items():
            if not isinstance(fields, dict):
                raise ValueError(f"status_overrides.{status_id} must be an object of {list(STATUS_FIELDS)}")
            row = _merge_flat(rows.get(str(status_id)) or {}, fields, allowed=STATUS_FIELDS, label=f"status_overrides.{status_id}")
            if row:
                rows[str(status_id)] = row
            else:
                rows.pop(str(status_id), None)
        nxt["status_overrides"] = rows
    if effect_id is not None:
        eid = str(effect_id).strip()
        known = [e["id"] for e in catalog.get("effects") or []]
        if eid and eid not in known:
            raise ValueError(f"effect_id '{eid}' isn't an installed effect; pick one of {known} or build one as a plugin")
        nxt["effect_id"] = eid
        if effects_enabled is None:
            effects_enabled = bool(eid)
    if effects_enabled is not None:
        nxt["effects_enabled"] = bool(effects_enabled)
    if skin_id is not None:
        sid = str(skin_id).strip()
        known = [s["id"] for s in catalog.get("skins") or []]
        if sid and sid not in known:
            raise ValueError(f"skin_id '{sid}' isn't an installed skin; pick one of {known} or build one as a plugin")
        if sid and not str(nxt.get("active_profile_id") or "").startswith(PLUGIN_PROFILE_PREFIX):
            # The window drops a skin on Default and the person's own themes (AppearanceContext.tsx).
            raise ValueError("A skin only stays on with a plugin theme: switch to one first (ducky_appearance_profile action=activate)")
        nxt["skin_id"] = sid
    if sounds:
        cur = dict(nxt.get("sounds") or {})
        mapping = dict(cur.get("mapping") or {})
        for hook, ref in (sounds.get("mapping") or {}).items():
            mapping[str(hook)] = "" if ref is None else str(ref)
        cur["mapping"] = mapping
        if "enabled" in sounds:
            cur["enabled"] = bool(sounds["enabled"])
        if "volume" in sounds:
            cur["volume"] = max(0.0, min(1.0, float(sounds["volume"])))
        nxt["sounds"] = cur
    active = str(nxt.get("active_profile_id") or "")
    if active.startswith(PLUGIN_PROFILE_PREFIX):
        # Same snapshot the window keeps per plugin theme (syncPluginPatch), so switching away and back keeps it.
        patches = dict(nxt.get("profile_patches") or {})
        patches[active] = {
            "foundation": nxt.get("foundation") or {},
            "overrides": nxt.get("overrides") or {},
            "status_overrides": nxt.get("status_overrides") or {},
            "effect_id": nxt.get("effect_id") or "",
            "effects_enabled": bool(nxt.get("effects_enabled")),
            "skin_id": nxt.get("skin_id") or "",
        }
        nxt["profile_patches"] = patches
    return nxt


def resolve_profile_id(wanted: str, profiles: list[dict[str, Any]]) -> str | None:
    """A theme's id from its id, its name or a plugin theme's own id (case-insensitive)."""
    w = str(wanted or "").strip()
    if not w:
        return None
    for match in (
        lambda p: p.get("id") == w,
        lambda p: str(p.get("name") or "").strip().lower() == w.lower(),
        lambda p: str(p.get("id") or "").lower().endswith(f":{w.lower()}"),
    ):
        hits = [p for p in profiles if match(p)]
        if len(hits) == 1:
            return str(hits[0]["id"])
    return None


@mcp.tool()
def ducky_appearance_get(pretty: bool = False) -> str:
    """Everything in Settings → Appearance: the current look and what can be picked.

    appearance: foundation (accent / bg / surface / text / border), overrides (any CSS
    variable without --, colors, radius, fonts, sizes), status_overrides, active_profile_id,
    effect_id + effects_enabled, skin_id, sounds {enabled, volume, mapping hook → ref}.
    catalog: every theme (built_in / plugin / user, with ids), installed effects, skins,
    sounds and sound hooks. Change it with ducky_appearance_set / ducky_appearance_profile.
    """
    try:
        current = _window("get_appearance")
    except RuntimeError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json({"ok": True, "appearance": current, "catalog": build_catalog(_contributions(), current)}, pretty=pretty)


@mcp.tool()
def ducky_appearance_set(
    foundation: dict[str, Any] | None = None,
    overrides: dict[str, Any] | None = None,
    status_overrides: dict[str, Any] | None = None,
    effect_id: str | None = None,
    effects_enabled: bool | None = None,
    skin_id: str | None = None,
    sounds: dict[str, Any] | None = None,
    dry_run: bool = False,
    pretty: bool = False,
) -> str:
    """Change any part of Ducky's look; it saves and shows in every window at once.

    Only what you pass changes; "" (or null) removes a key, back to the theme's value.
    foundation: {accent, bg, surface, text, border} → CSS colors (the five base colors).
    overrides: {"<css variable without -->": value}, e.g. {"radius": "4px", "font-ui":
      "\"Figtree\", sans-serif", "font-ui-size": "14px", "green": "#4ade80"}.
    status_overrides: {"<status id>": {bg, border, text, dim}}.
    effect_id: an installed effect's id ("plugin:<plugin>:<effect>", see catalog) or "" for
      none; effects_enabled turns it on/off and defaults to on when you pick one.
    skin_id: an installed skin ("plugin:<plugin>:<skin>") or ""; needs a plugin theme active.
    sounds: {enabled?, volume? 0-1, mapping?: {hook: "builtin:ding" | "plugin:<p>:<s>" | ""}}.
    The active user theme is updated too, like Save in the Appearance tab. dry_run previews.
    """
    blocked = _writes_blocked()
    if blocked:
        return tool_json({"ok": False, "error": blocked}, pretty=pretty)
    try:
        current = _window("get_appearance")
        catalog = build_catalog(_contributions(), current) if (effect_id or skin_id) else {}
        nxt = merge_appearance(
            current, catalog, foundation=foundation, overrides=overrides,
            status_overrides=status_overrides, effect_id=effect_id,
            effects_enabled=effects_enabled, skin_id=skin_id, sounds=sounds,
        )
        if dry_run:
            return tool_json({"ok": True, "dry_run": True, "before": current, "after": nxt}, pretty=pretty)
        _window("save_appearance", {"patch": nxt})
        active = str(nxt.get("active_profile_id") or "")
        names = [p.get("id") for p in current.get("profiles") or []]
        if active in names:
            _window("update_active_appearance_profile")
        after = _window("get_appearance")
    except (RuntimeError, ValueError, TypeError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json({"ok": True, "appearance": after}, pretty=pretty)


@mcp.tool()
def ducky_appearance_profile(action: str, profile: str = "", name: str = "", pretty: bool = False) -> str:
    """Themes in Settings → Appearance: list, activate, save_as, rename, delete.

    activate: profile = a theme's id, name or a plugin theme's own id; works for Default,
      plugin themes (turns on their skin and effect too) and the person's own themes.
    save_as: saves the current look as a new theme called `name` and makes it active.
    rename (profile + name) / delete (profile): the person's own themes only.
    New themes with their own effect or skin are plugins: build one with ducky_plugin_*
    (appearance.profiles / .effects / .skin), then activate it here.
    """
    act = str(action or "").strip().lower()
    try:
        current = _window("get_appearance")
        profiles = build_catalog(_contributions(), current)["profiles"]
        if act == "list":
            return tool_json({"ok": True, "active": current.get("active_profile_id"), "profiles": profiles}, pretty=pretty)
        blocked = _writes_blocked()
        if blocked:
            return tool_json({"ok": False, "error": blocked}, pretty=pretty)
        if act == "save_as":
            if not str(name or "").strip():
                return tool_json({"ok": False, "error": "save_as needs a name"}, pretty=pretty)
            saved = _window("save_appearance_profile", {"name": name})
            _window("push_appearance_live", {"data": _window("get_appearance")})
            return tool_json({"ok": True, "profile": saved}, pretty=pretty)
        pid = resolve_profile_id(profile, profiles)
        if not pid:
            return tool_json({"ok": False, "error": f"No single theme matches '{profile}'", "profiles": profiles}, pretty=pretty)
        if act == "activate":
            from frontend.ui_web.agent_turns.push import push_ui_event

            # The main window loads it as if picked in Settings (AppearanceContext loadProfile).
            push_ui_event({"type": "appearance_profile_requested", "id": pid, "at": time.time()})
            deadline = time.monotonic() + _ACTIVATE_WAIT_S
            while time.monotonic() < deadline:
                time.sleep(0.3)
                now = _window("get_appearance")
                if now.get("active_profile_id") == pid:
                    return tool_json({"ok": True, "active": pid, "appearance": now}, pretty=pretty)
            return tool_json({"ok": False, "error": f"The Ducky window didn't switch to {pid} within {_ACTIVATE_WAIT_S:g} s"}, pretty=pretty)
        if act == "rename":
            if not str(name or "").strip():
                return tool_json({"ok": False, "error": "rename needs a name"}, pretty=pretty)
            out = _window("rename_appearance_profile", {"profile_id": pid, "name": name})
        elif act == "delete":
            out = _window("delete_appearance_profile", {"profile_id": pid})
        else:
            return tool_json({"ok": False, "error": "action must be list, activate, save_as, rename or delete"}, pretty=pretty)
        _window("push_appearance_live", {"data": _window("get_appearance")})
    except (RuntimeError, ValueError, TypeError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json({"ok": True, "result": out}, pretty=pretty)
