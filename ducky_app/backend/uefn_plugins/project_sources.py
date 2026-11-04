"""Projects-window tabs plugins add (a Roblox plugin lists its Rojo projects).

A plugin declares ``contributes['project.sources'] = [{id, label, icon?, rpc}]`` and
registers that panel RPC; it returns ``[{path, name, thumbnail_url?}]``. Rows land in
the plugin's tab; a folder Ducky already lists moves to that tab.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _row(item: Any, source_id: str) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    raw = str(item.get("path") or "").strip()
    if not raw:
        return None
    try:
        path = str(Path(raw).resolve())
    except OSError:
        return None
    from frontend.project_kind import project_flavor
    from frontend.ui_web.project_chats import project_slug

    return {
        "path": path,
        "name": str(item.get("name") or Path(path).name),
        "slug": project_slug(path),
        "kind": "folder",
        "flavor": str(item.get("flavor") or "") or project_flavor(path),
        "mount": "",
        "source": source_id,
        "last_opened": 0.0,
        "has_thumbnail": False,
        "thumbnail_url": str(item.get("thumbnail_url") or ""),
        "active": False,
    }


def list_plugin_projects() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(tabs, rows)`` from every enabled plugin's project sources. A plugin that fails
    keeps its tab with no rows: one broken plugin never empties the window."""
    from backend.uefn_plugins.plugin_host.panel_rpc import call_panel_rpc
    from backend.uefn_plugins.plugin_host.ui_registry import get_project_sources

    tabs: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for source in get_project_sources():
        sid = f"{source['plugin_id']}:{source['id']}"
        tabs.append({"id": sid, "label": str(source.get("label") or source["id"]), "icon": source.get("icon") or ""})
        try:
            listed = call_panel_rpc(str(source["plugin_id"]), str(source["rpc"]), {})
        except Exception:  # noqa: BLE001 - the tab stays, empty
            continue
        for item in listed if isinstance(listed, list) else []:
            row = _row(item, sid)
            if row:
                rows.append(row)
    return tabs, rows
