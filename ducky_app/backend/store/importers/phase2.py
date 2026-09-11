"""Phase 2 importer: ``chats/projects/<slug>/`` (folders + conversations) and the
legacy flat layout (``chats/folders.json``, ``chats/conversations/<id>.json``).

Runs once per database (``meta.imported:chats``). Every conversation document is
written through the repo, read back and compared field by field; the source
``conversation.json`` files then move to ``legacy/chats/...``. Attachment
folders stay where they are: they are still served from disk.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.store.importers.phase1 import _move_to_legacy, _read_json, once
from backend.store.repos import chats as repo

LEGACY_FLAT_PROJECT = "_no_project"


def _canonical(doc: dict[str, Any]) -> dict[str, Any]:
    from frontend.chat_store import Conversation

    return Conversation.from_dict(doc).to_dict()


def _import_folders(project_id: str, path: Path) -> int:
    data = _read_json(path)
    raw = data.get("folders") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return 0
    rows = [f for f in raw if isinstance(f, dict) and f.get("id")]
    repo.folders_replace(project_id, rows)
    return len(rows)


def _import_conversation(project_id: str, doc_path: Path, report: dict[str, Any]) -> bool:
    data = _read_json(doc_path)
    if not isinstance(data, dict) or not data.get("id"):
        report["skipped_unreadable"] += 1
        return False
    doc = _canonical(data)
    from frontend.ui_web.session_files import session_stats_from_messages

    stats = session_stats_from_messages(doc.get("messages") or [])
    doc["tool_call_count"] = stats["tool_call_count"]
    doc["file_count"] = stats["file_count"]
    repo.conv_save(project_id, doc, messages=list(doc.get("messages") or []))
    back = repo.conv_get(doc["id"], project_id=project_id, with_messages=True)
    assert back is not None, f"import verification: {doc['id']} not readable"
    if _canonical(back) != doc:
        diff = sorted(k for k in set(doc) | set(back) if doc.get(k) != back.get(k))
        raise AssertionError(f"import verification failed for {doc['id']}: fields {diff}")
    report["conversations"] += 1
    report["messages"] += len(doc.get("messages") or [])
    return True


def import_chats(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"projects": 0, "folders": 0, "conversations": 0, "messages": 0, "skipped_unreadable": 0}
    projects_root = root / "chats" / "projects"
    if projects_root.is_dir():
        for project_dir in sorted(projects_root.iterdir()):
            if not project_dir.is_dir():
                continue
            slug = project_dir.name
            report["projects"] += 1
            folders_file = project_dir / "folders.json"
            if folders_file.is_file():
                report["folders"] += _import_folders(slug, folders_file)
                _move_to_legacy(root, folders_file, f"chats/projects/{slug}")
            conv_root = project_dir / "conversations"
            if conv_root.is_dir():
                for conv_dir in sorted(conv_root.iterdir()):
                    doc_path = conv_dir / "conversation.json"
                    if conv_dir.is_dir() and doc_path.is_file():
                        if _import_conversation(slug, doc_path, report):
                            _move_to_legacy(root, doc_path, f"chats/projects/{slug}/conversations/{conv_dir.name}")
    # Legacy flat layout (pre project scoping).
    flat_folders = root / "chats" / "folders.json"
    if flat_folders.is_file():
        report["folders"] += _import_folders(LEGACY_FLAT_PROJECT, flat_folders)
        _move_to_legacy(root, flat_folders, "chats")
    flat_convs = root / "chats" / "conversations"
    if flat_convs.is_dir():
        for doc_path in sorted(flat_convs.glob("*.json")):
            if _import_conversation(LEGACY_FLAT_PROJECT, doc_path, report):
                _move_to_legacy(root, doc_path, "chats/conversations")
    return report


def ensure() -> None:
    once("chats", import_chats)


def report() -> dict[str, Any] | None:
    from backend.store.repos import kv

    raw = kv.meta_get("imported:chats")
    return json.loads(raw) if raw else None
