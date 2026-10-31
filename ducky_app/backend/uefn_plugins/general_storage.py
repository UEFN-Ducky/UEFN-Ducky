"""General storage: one place for anything, not tied to a plugin.

The account keeps it under the core data id ``ducky.storage``, beside Workflows
(``ducky.automations``), so it is plugin data like any other: Local or the team picked in
Your team (``plugin_scope_set_all`` moves it with every plugin), synced, with history,
undo, locks and conflicts, and listed in the Ducky Account's Storage under Workflows.

Each item is one doc ``item.<id>`` (``name``, ``folder``, ``kind``, ``by``, ``updated``,
and a note's ``text`` or ``value``); a file's bytes sit at its own path, ``<folder>/<name>``,
written in the same call as its doc (one call, one change). The agents' tools
(``ducky_storage_*``) and every plugin (``api.storage``) go through here.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import time
from typing import Any

from backend.uefn_plugins import scopes

GENERAL = "ducky.storage"
LABEL = "General storage"
_ITEM = "item."
_MAX_FILE = 100 * 1024 * 1024


def _slug(text: str, limit: int) -> str:
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9._-]+", "-", str(text or "").strip().lower())).strip("-.")[:limit]


def _folder(folder: str) -> str:
    """``Promo Art/2026`` → ``promo-art/2026``: each part a safe path part, no ``..``."""
    parts = [_slug(p, 60) for p in re.split(r"[\\/]+", str(folder or ""))]
    return "/".join(p for p in parts if p and p not in (".", ".."))


def _item_id(folder: str, name: str) -> str:
    """One id per folder + name: saving the same name again replaces it (its history keeps the old)."""
    base = _slug(f"{folder}-{name}" if folder else name, 100) or "item"
    digest = hashlib.sha1(f"{folder}/{name}".encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"[:120]


def _data() -> scopes.PluginData:
    return scopes.PluginData(GENERAL)


def where() -> dict[str, Any]:
    """Whose General storage this PC writes to now: ``{kind, label, spaceId, readOnly}``."""
    return scopes.scope_view(scopes.active_scope(GENERAL))


def _public(doc: dict[str, Any], item_id: str) -> dict[str, Any]:
    out = {k: doc.get(k) for k in ("name", "folder", "kind", "path", "size", "mime", "by", "updated")}
    return {"id": item_id, **{k: v for k, v in out.items() if v not in (None, "")}}


def put_file(name: str, data: bytes, *, folder: str = "", by: str = "you") -> dict[str, Any]:
    """Keep a file: its bytes at ``<folder>/<name>`` and its doc, in one change."""
    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("A file's contents must be bytes.")
    if len(data) > _MAX_FILE:
        raise ValueError("A file in General storage can be up to 100 MB.")
    name = str(name or "").strip()
    if not name:
        raise ValueError("Give the file a name.")
    folder = _folder(folder)
    stem, dot, ext = name.rpartition(".")
    file_name = f"{_slug(stem, 80) or 'file'}.{_slug(ext, 12)}" if dot and stem else _slug(name, 90) or "file"
    path = f"{folder}/{file_name}" if folder else file_name
    item_id = _item_id(folder, name)
    store = _data()
    old = store.get(_ITEM + item_id)
    if isinstance(old, dict) and old.get("path") and old["path"] != path:
        store.delete_file(old["path"])
    store.put_file(path, bytes(data))
    doc = {"name": name, "folder": folder, "kind": "file", "path": path, "size": len(data),
           "mime": mimetypes.guess_type(name)[0] or "application/octet-stream", "by": str(by or "you"),
           "updated": time.time()}
    store.put(_ITEM + item_id, doc)
    return {"ok": True, "item": _public(doc, item_id), "scope": where()}


def put_value(name: str, value: Any, *, folder: str = "", by: str = "you") -> dict[str, Any]:
    """Keep a note (text) or any JSON value under a name."""
    name = str(name or "").strip()
    if not name:
        raise ValueError("Give it a name.")
    folder = _folder(folder)
    item_id = _item_id(folder, name)
    store = _data()
    old = store.get(_ITEM + item_id)
    if isinstance(old, dict) and old.get("path"):
        store.delete_file(old["path"])  # a file saved under this name before
    doc: dict[str, Any] = {"name": name, "folder": folder, "by": str(by or "you"), "updated": time.time()}
    if isinstance(value, str):
        doc.update(kind="note", text=value)
    else:
        doc.update(kind="json", value=value)
    store.put(_ITEM + item_id, doc)
    return {"ok": True, "item": _public(doc, item_id), "scope": where()}


def items(folder: str = "", query: str = "") -> list[dict[str, Any]]:
    """Everything kept, newest first; ``folder`` keeps that folder and the ones in it,
    ``query`` keeps the ones whose name or folder has it."""
    folder, q = _folder(folder), str(query or "").strip().lower()
    out = []
    for key, doc in _data().items(_ITEM).items():
        if not isinstance(doc, dict):
            continue
        f = str(doc.get("folder") or "")
        if folder and not (f == folder or f.startswith(folder + "/")):
            continue
        if q and q not in f"{doc.get('name', '')} {f}".lower():
            continue
        out.append(_public(doc, key[len(_ITEM):]))
    out.sort(key=lambda r: float(r.get("updated") or 0), reverse=True)
    return out


def get(item_id: str, *, with_data: bool = False) -> dict[str, Any] | None:
    """One item: its doc, a note's ``text`` / ``value``, and with ``with_data`` a file's ``data`` (bytes)."""
    store = _data()
    doc = store.get(_ITEM + str(item_id or ""))
    if not isinstance(doc, dict):
        return None
    out = _public(doc, str(item_id))
    if doc.get("kind") == "note":
        out["text"] = doc.get("text", "")
    elif doc.get("kind") == "json":
        out["value"] = doc.get("value")
    elif with_data and doc.get("path"):
        out["data"] = store.get_file(doc["path"])
    return out


def find(name: str, folder: str = "") -> dict[str, Any] | None:
    """The item saved under this name (and folder)."""
    return get(_item_id(_folder(folder), str(name or "").strip()))


def delete(item_id: str) -> dict[str, Any]:
    store = _data()
    doc = store.get(_ITEM + str(item_id or ""))
    if not isinstance(doc, dict):
        return {"ok": False, "error": "Nothing in General storage has that id."}
    if doc.get("path"):
        store.delete_file(doc["path"])
    store.delete(_ITEM + str(item_id))
    return {"ok": True, "deleted": str(item_id), "scope": where()}


class StorageApi:
    """``api.storage`` for plugins: the account's General storage, shared with every plugin and
    the person. What a plugin keeps here is marked as its own (``by``) and stays when the plugin
    goes. Its own records still belong in ``api.data``."""

    def __init__(self, plugin_id: str) -> None:
        self._by = plugin_id

    def put_file(self, name: str, data: bytes, folder: str = "") -> dict[str, Any]:
        return put_file(name, data, folder=folder, by=self._by)

    def put(self, name: str, value: Any, folder: str = "") -> dict[str, Any]:
        return put_value(name, value, folder=folder, by=self._by)

    def items(self, folder: str = "", query: str = "") -> list[dict[str, Any]]:
        return items(folder, query)

    def get(self, item_id: str, with_data: bool = False) -> dict[str, Any] | None:
        return get(item_id, with_data=with_data)

    def find(self, name: str, folder: str = "") -> dict[str, Any] | None:
        return find(name, folder)

    def delete(self, item_id: str) -> dict[str, Any]:
        return delete(item_id)

    def where(self) -> dict[str, Any]:
        return where()
