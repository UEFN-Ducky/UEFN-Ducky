"""Phase 3 importer: ``changesets/<slug>/`` (runs, index, blobs, human_index) and
``file_history/<slug>/<path>/<id>.json``. Once per database; every run document
is read back and compared; sources move under ``legacy/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.store.importers.phase1 import _move_to_legacy, _read_json, once
from backend.store.repos import history, ledger


def _import_ledger_dir(root: Path, project_dir: Path, report: dict[str, Any]) -> None:
    slug = project_dir.name
    report["ledgers"] += 1
    blobs_dir = project_dir / "blobs"
    if blobs_dir.is_dir():
        for blob in sorted(blobs_dir.glob("*.txt")):
            try:
                ledger.blob_put(blob.read_text(encoding="utf-8", newline=""), digest=blob.stem)
                report["blobs"] += 1
            except OSError:
                report["skipped_unreadable"] += 1
    runs_dir = project_dir / "runs"
    if runs_dir.is_dir():
        for run_path in sorted(runs_dir.glob("*.json")):
            run = _read_json(run_path)
            if not isinstance(run, dict) or not run.get("run_id"):
                report["skipped_unreadable"] += 1
                continue
            rid = str(run["run_id"])
            if ledger.run_get(slug, rid) is not None:
                report["run_id_collisions"].append(f"{slug}/{rid}")
            ledger.run_put(slug, run)
            back = ledger.run_get(slug, rid)
            assert back is not None
            for key in ("entries", "seen", "status", "conv_id", "archived"):
                assert back.get(key) == run.get(key), f"ledger import verification failed: {slug}/{rid} {key}"
            report["runs"] += 1
            report["entries"] += len(run.get("entries") or [])
    index = _read_json(project_dir / "index.json")
    if isinstance(index, dict):
        ledger.index_replace(slug, {str(k): v for k, v in index.items() if isinstance(v, dict)})
        report["index_paths"] += len(index)
    human = _read_json(project_dir / "human_index.json")
    if isinstance(human, dict):
        seen = {}
        for key, value in human.items():
            digest = str(value.get("hash") or "") if isinstance(value, dict) else str(value or "")
            if key and digest:
                seen[str(key)] = digest
        ledger.watch_replace(slug, seen)
        report["watch_paths"] += len(seen)
    _move_to_legacy(root, project_dir, "changesets")


def _import_history_dir(root: Path, project_dir: Path, report: dict[str, Any]) -> None:
    slug = project_dir.name
    for entry_path in sorted(project_dir.rglob("*.json")):
        data = _read_json(entry_path)
        if not isinstance(data, dict) or not isinstance(data.get("content"), str):
            report["skipped_unreadable"] += 1
            continue
        rel = str(data.get("path") or entry_path.parent.relative_to(project_dir).as_posix())
        entry_id = str(data.get("id") or entry_path.stem)
        content = data["content"]
        attribution = {k: data.get(k) for k in history.ATTRIBUTION if isinstance(data.get(k), str)}
        history.put(
            slug,
            rel,
            entry_id,
            content,
            preview=str(data.get("preview") or ""),
            saved_at=int(data.get("saved_at") or 0),
            attribution=attribution,
            schema_version=int(data.get("schema_version") or 1),
        )
        assert history.content(slug, rel, entry_id) == content, f"history import verification failed: {slug}/{rel}/{entry_id}"
        report["versions"] += 1
    _move_to_legacy(root, project_dir, "file_history")


def import_ledger_and_history(root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "ledgers": 0, "runs": 0, "entries": 0, "blobs": 0, "index_paths": 0, "watch_paths": 0,
        "versions": 0, "skipped_unreadable": 0, "run_id_collisions": [],
    }
    ledgers = root / "changesets"
    if ledgers.is_dir():
        for project_dir in sorted(ledgers.iterdir()):
            if project_dir.is_dir():
                _import_ledger_dir(root, project_dir, report)
    histories = root / "file_history"
    if histories.is_dir():
        for project_dir in sorted(histories.iterdir()):
            if project_dir.is_dir():
                _import_history_dir(root, project_dir, report)
    return report


def ensure() -> None:
    once("ledger", import_ledger_and_history)


def report() -> dict[str, Any] | None:
    from backend.store.repos import kv

    raw = kv.meta_get("imported:ledger")
    return json.loads(raw) if raw else None
