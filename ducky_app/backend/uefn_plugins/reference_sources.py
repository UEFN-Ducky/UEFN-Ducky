"""Live @ / / rows plugins add (a Kanban plugin lists its boards).

A plugin declares ``contributes.chat.reference_sources = [{id, rpc, panel, label?, group?,
trigger?, icon?, refresh_on?}]`` and registers that panel RPC; ``refresh_on`` names the
plugin's own push events (``kanban_changed``) that make Ducky ask again at once; it returns ``[{id, label, description?,
icon?}]``. Each item becomes a reference ``plugin:<plugin>/<panel>:<item>`` that opens the
plugin's panel on that item. Ducky asks again every time the catalog reloads.
"""

from __future__ import annotations

import re
from typing import Any

_ITEM_ID = re.compile(r"[A-Za-z0-9_.-]+")
_MAX_ROWS = 200  # per source; a plugin with thousands of items lists the ones that matter


def _icon(raw: Any, fallback: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("data:image/") or re.match(r"https?://", text, re.I):
        return text
    if text and len(text) <= 4:  # an emoji
        return text
    return fallback


def _row(item: Any, source: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    item_id = str(item.get("id") or "").strip()
    label = str(item.get("label") or item.get("name") or "").strip()
    if not label or not _ITEM_ID.fullmatch(item_id):
        return None
    pid = str(source["plugin_id"])
    panel = str(source["panel"]).strip().lower()
    out: dict[str, Any] = {
        "trigger": "@" if source.get("trigger") == "@" else "/",
        "id": f"plugin:{pid}/{panel}:{item_id}",
        "label": label,
        "group": str(source.get("group") or source.get("label") or pid).strip() or "Plugins",
        "href": f"plugin:{pid}/{panel}:{item_id}",
        "plugin_id": pid,
    }
    description = str(item.get("description") or "").strip()
    if description:
        out["description"] = description[:200]
    icon = _icon(item.get("icon"), str(source.get("icon") or ""))
    if icon:
        out["icon_url"] = icon
    return out


def refresh_events() -> list[str]:
    """Push event types after which the menu asks the plugins again."""
    from backend.uefn_plugins.host import get_reference_sources

    out: set[str] = set()
    for source in get_reference_sources():
        raw = source.get("refresh_on")
        for name in raw if isinstance(raw, list) else [raw]:
            if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.:-]+", name):
                out.add(name)
    return sorted(out)


def list_plugin_references() -> list[dict[str, Any]]:
    """Every enabled plugin's live rows. A plugin that fails adds nothing; the rest still list."""
    from backend.uefn_plugins.host import call_panel_rpc, get_reference_sources

    rows: list[dict[str, Any]] = []
    for source in get_reference_sources():
        try:
            listed = call_panel_rpc(str(source["plugin_id"]), str(source["rpc"]), {})
        except Exception:  # noqa: BLE001 - one broken plugin never empties the menu
            continue
        if isinstance(listed, dict):
            listed = listed.get("items") or listed.get("rows") or []
        for item in (listed if isinstance(listed, list) else [])[:_MAX_ROWS]:
            row = _row(item, source)
            if row:
                rows.append(row)
    return rows
