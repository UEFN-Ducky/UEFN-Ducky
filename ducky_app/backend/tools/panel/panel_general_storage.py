"""MCP tools for General storage: the account's place for anything, not tied to a plugin.

It follows Your team like plugin data (Local or the team), so what a ducky keeps here syncs
with history and undo, and shows in the Ducky Account's Storage under Workflows. See
``backend.uefn_plugins.general_storage``.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from backend.server import mcp
from backend.util.json_util import tool_json


def _fail(exc: Exception, pretty: bool) -> str:
    return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)


@mcp.tool()
def ducky_storage_list(folder: str = "", query: str = "", pretty: bool = False) -> str:
    """List what's in General storage (the account's storage for anything: files, notes, JSON),
    newest first. ``folder`` keeps that folder and the ones in it; ``query`` matches names and
    folders. Says whose storage it is (Local or the team picked in Your team)."""
    from backend.uefn_plugins import general_storage as gs

    try:
        rows = gs.items(folder, query)
        return tool_json({"ok": True, "scope": gs.where(), "count": len(rows), "items": rows[:500]}, pretty=pretty)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, pretty)


@mcp.tool()
def ducky_storage_get(id: str = "", name: str = "", folder: str = "", save_to: str = "", pretty: bool = False) -> str:
    """Read one item of General storage by ``id`` (or by ``name`` + ``folder``). A note comes back
    as ``text``, JSON as ``value``. A file comes back as metadata; pass ``save_to`` (a folder or a
    file path) to write its bytes there."""
    from backend.uefn_plugins import general_storage as gs

    try:
        item = gs.get(id, with_data=bool(save_to)) if id else gs.find(name, folder)
        if item is None:
            return tool_json({"ok": False, "error": "Nothing in General storage by that id or name."}, pretty=pretty)
        if save_to and item.get("kind") == "file":
            if item.get("data") is None and not id:
                item = gs.get(item["id"], with_data=True) or item
            data = item.pop("data", None)
            if data is None:
                return tool_json({"ok": False, "error": "That file hasn't reached this PC yet. Try again after a sync."},
                                 pretty=pretty)
            dest = Path(save_to).expanduser()
            if dest.is_dir() or str(save_to).endswith(("/", "\\")):
                dest.mkdir(parents=True, exist_ok=True)
                dest = dest / item["name"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            item["saved"] = str(dest)
        item.pop("data", None)
        return tool_json({"ok": True, "item": item, "scope": gs.where()}, pretty=pretty)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, pretty)


@mcp.tool()
def ducky_storage_put(name: str, folder: str = "", text: str = "", json_value: str = "", source_path: str = "",
                      b64: str = "", pretty: bool = False) -> str:
    """Keep anything in General storage under ``name`` (in ``folder``, e.g. ``Promo/2026``): a
    note (``text``), any JSON (``json_value``, a JSON string), a file from this PC
    (``source_path``) or file bytes (``b64``). Saving the same name again replaces it; the
    older version stays in its history. Synced with the team picked in Your team."""
    from backend.uefn_plugins import general_storage as gs

    try:
        if source_path:
            src = Path(source_path).expanduser()
            if not src.is_file():
                return tool_json({"ok": False, "error": f"No file at {source_path}."}, pretty=pretty)
            out = gs.put_file(name or src.name, src.read_bytes(), folder=folder, by="ducky")
        elif b64:
            out = gs.put_file(name, base64.b64decode(b64, validate=True), folder=folder, by="ducky")
        elif json_value:
            out = gs.put_value(name, json.loads(json_value), folder=folder, by="ducky")
        else:
            out = gs.put_value(name, text, folder=folder, by="ducky")
        return tool_json(out, pretty=pretty)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, pretty)


@mcp.tool()
def ducky_storage_delete(id: str, pretty: bool = False) -> str:
    """Remove one item from General storage (its history keeps it, so it can be restored)."""
    from backend.uefn_plugins import general_storage as gs

    try:
        return tool_json(gs.delete(id), pretty=pretty)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, pretty)
