"""Immutable saved workflow snapshots, retained without a rolling limit.

Saved copies are per PC and never sync. In the database they are sealed for the
account that saved them, so another account on this PC can't list or open them.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any


def snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    return {key: doc.get(key) for key in ("id", "name", "description", "enabled", "folder", "graph", "updated")}


def summary(version_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    return {"id": version_id, "name": doc.get("name", ""), "saved_at": doc.get("updated", 0),
            "node_count": len((doc.get("graph") or {}).get("nodes") or []), "note": str(doc.get("note") or "")}


def directory(workflow_id: str):
    from backend.automations.store import _files_dir
    # IDs are opaque; never interpolate a caller-supplied ID into a path.
    path = _files_dir() / "versions" / hashlib.sha256(workflow_id.encode()).hexdigest()
    path.mkdir(parents=True, exist_ok=True)
    return path


def archive_file(doc: dict[str, Any]) -> None:
    path = directory(doc["id"]) / f"{uuid.uuid4()}.json"
    from frontend.atomic_json import write_json_plain

    write_json_plain(path, snapshot(doc), ensure_ascii=False)


def list_versions(workflow_id: str) -> list[dict[str, Any]]:
    from backend.automations.store import use_db
    if use_db("automations"):
        from backend.automations.owned import account, open_version
        from backend.store.repos.automations import version_rows

        aid = account()
        rows = []
        for version_id, stored in version_rows(workflow_id):
            doc = open_version(stored, aid)
            if doc is not None:
                rows.append(summary(version_id, doc))
        return rows
    rows = []
    for path in directory(workflow_id).glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        rows.append(summary(path.stem, doc))
    return sorted(rows, key=lambda row: (row["saved_at"], row["id"]), reverse=True)


def get_version(workflow_id: str, version_id: str) -> dict[str, Any] | None:
    from backend.automations.store import use_db
    if use_db("automations"):
        from backend.automations.owned import account, open_version
        from backend.store.repos.automations import version_row

        stored = version_row(workflow_id, version_id)
        return open_version(stored, account()) if stored is not None else None
    try:
        version_id = str(uuid.UUID(version_id))
    except (ValueError, AttributeError):
        return None
    path = directory(workflow_id) / f"{version_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
