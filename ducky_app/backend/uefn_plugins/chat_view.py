"""What a tool's card in chat shows beside the call: the plugin it belongs to (with an Open
button for that plugin's panel) and the pictures, videos and sounds the call made, played
right in the chat instead of in a tab that jumps to the front.

Every plugin gets this without doing anything: the card finds media files in the result.
A plugin says more with a ``chat_view`` object in its result (skill reference
``ai_plugins.md``, "In the chat"): ``title``, ``media`` (file paths, or ``{path}``),
``blocks`` (rich blocks), ``open`` (``{panel, label}``). The chat reads it from the result;
this module only names owners and signs links, so a link is only ever made for a media
file that exists.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_MAX_MEDIA = 12
_MAX_TOOLS = 64


def bare_tool_name(name: str) -> str:
    """``mcp__uefn__imk_render_get`` / ``uefn.imk_render_get`` → ``imk_render_get``."""
    text = str(name or "").strip()
    for sep in ("__", "/", "."):
        cut = text.rfind(sep)
        if cut >= 0:
            text = text[cut + len(sep):]
    return text


def tool_owner(tool_name: str) -> dict[str, Any]:
    """``{plugin_id, label, panel_id, panel_title}`` for a Store plugin's tool, ``{}`` for
    Ducky's own tools. ``panel_id`` is empty when the plugin has no panel to open."""
    from backend.uefn_plugins.host import get_ui_contributions, plugin_for_tool

    pid = plugin_for_tool(bare_tool_name(tool_name))
    if not pid or pid == "ducky":
        return {}
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        label = str((load_plugin_manifest(pid) or {}).get("label") or pid)
    except Exception:
        label = pid
    panels = [row for row in (get_ui_contributions().get("ui_panels") or [])
              if isinstance(row, dict) and row.get("plugin_id") == pid and row.get("id")]
    first = panels[0] if panels else {}
    return {
        "plugin_id": pid,
        "label": label,
        "panel_id": str(first.get("id") or ""),
        "panel_title": str(first.get("title") or label),
    }


def media_link(path: Any) -> dict[str, str] | None:
    """``{path, url, kind, name}`` for one media file on this PC, else None."""
    from backend.automations.files import kind_of, media_url

    text = os.path.expandvars(os.path.expanduser(str(path or "").strip()))
    if not text or len(text) > 1024:
        return None
    target = Path(text)
    if not target.is_absolute():
        return None
    try:
        if not target.is_file():
            return None
    except OSError:
        return None
    url = media_url(target)
    if not url:
        return None
    return {"path": str(target), "url": url, "kind": kind_of(target), "name": target.name}


def chat_tool_view(tool_names: list[str] | None = None, paths: list[str] | None = None) -> dict[str, Any]:
    """Owners for ``tool_names`` and signed links for ``paths`` (media files only)."""
    tools: dict[str, Any] = {}
    for name in list(tool_names or [])[:_MAX_TOOLS]:
        key = str(name or "").strip()
        if key and key not in tools:
            try:
                tools[key] = tool_owner(key)
            except Exception:
                tools[key] = {}
    media: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in list(paths or []):
        if len(media) >= _MAX_MEDIA:
            break
        link = media_link(raw)
        if link and link["path"].lower() not in seen:
            seen.add(link["path"].lower())
            media.append({**link, "asked": str(raw)})
    return {"tools": tools, "media": media}
