"""Which desktop plugin owns which skill pack.

One map ``{pack_id: (plugin_id, path, active)}`` built from
``uefn_plugins.store.list_plugin_owned_skills()``. ``active`` is True when the owning
plugin is enabled and the folder holds a SKILL.md, so agents may load it.

Cached on (plugin-tree fingerprint, enabled ids): ``_pack_roots()`` asks for this on
every pack lookup, and the uncached walk re-stat'd every plugin skill dir and reloaded
panel_settings.json per entry. Inside ``store.pass_memo()`` even the fingerprint and
settings read run once per pass. Install / uninstall / enable call :func:`invalidate`.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

OwnedMap = dict[str, tuple[str, Path, bool]]

_CACHE: dict[Any, OwnedMap] = {}
_LOCK = threading.Lock()
_MEMO_KEY = "owned_map"


def invalidate() -> None:
    """Drop the cached ownership map (plugin installed, removed, enabled or disabled)."""
    with _LOCK:
        _CACHE.clear()


def _build() -> OwnedMap:
    from backend.skills.store import PACK_FILE
    from backend.uefn_plugins.host import is_plugin_enabled
    from backend.uefn_plugins.store import (
        appdata_uefn_plugins_dir,
        get_enabled_plugin_ids,
        list_plugin_owned_skills,
        plugin_tree_fingerprint,
    )

    key = (
        plugin_tree_fingerprint(appdata_uefn_plugins_dir()),
        tuple(sorted(get_enabled_plugin_ids())),
    )
    with _LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        return dict(cached)

    out: OwnedMap = {}
    for entry in list_plugin_owned_skills():
        pack_id = str(entry.get("pack_id") or "").strip()
        if not pack_id:
            continue
        owner = str(entry.get("plugin_id") or "").strip()
        path = Path(str(entry.get("path") or ""))
        # First entry names the owner (installs refuse a second claim on the same id).
        first_owner, first_path, active = out.get(pack_id, (owner, path, False))
        enabled = not owner or is_plugin_enabled(owner)
        if enabled and path.is_dir() and (path / PACK_FILE).is_file():
            out[pack_id] = (first_owner, path, True)
        else:
            out[pack_id] = (first_owner, first_path, active)
    with _LOCK:
        _CACHE.clear()
        _CACHE[key] = dict(out)
    return out


def owned_map() -> OwnedMap:
    """Every plugin-owned pack, enabled or not. Empty when plugins can't be read."""
    from backend.skills.store import _PASS_MEMO

    memo = _PASS_MEMO.get()
    if memo is not None:
        if _MEMO_KEY not in memo:
            memo[_MEMO_KEY] = _safe_build()
        return dict(memo[_MEMO_KEY])
    return _safe_build()


def _safe_build() -> OwnedMap:
    try:
        return _build()
    except Exception:  # noqa: BLE001 — a broken plugin tree means no plugin skills
        return {}


def _plugin_owned_skill_map() -> dict[str, Path]:
    """pack_id → skill dir for skills bundled inside *enabled* desktop plugins."""
    return {pid: path for pid, (_owner, path, active) in owned_map().items() if active}


def plugin_owner_for_skill(pack_id: str) -> str | None:
    """Return owning desktop plugin id when ``pack_id`` is plugin-bundled (enabled or not)."""
    row = owned_map().get(pack_id)
    if row is None:
        return None
    return row[0] or None


def _is_plugin_owned_pack_id(pack_id: str) -> bool:
    return plugin_owner_for_skill(pack_id) is not None
