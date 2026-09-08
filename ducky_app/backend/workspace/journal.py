"""Changeset journal: a per-run ledger of applied project writes, with revert.

Storage (per project, under the app's data directory, never inside the project):

    <storage>/runs/<run_id>.json   one document per run, schema changeset_run/1
    <storage>/blobs/<hash>.txt     content-addressed before/after bodies
    <storage>/index.json           last writer per path (conflict detection)

Revert restores blobs through the write pipeline, so a revert is itself an
attributed, journaled write (``source: "revert"``). Conflicts are flagged, never
denied: the pipeline's lane policy is the only thing that refuses a write.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol, runtime_checkable

from backend.workspace import events, identity
from backend.workspace.paths import content_hash, normalize_rel

if TYPE_CHECKING:
    from backend.workspace.writer import WriteRecord

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
EXPORT_FORMAT = "ducky.changeset/1"

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"
STATUS_REVERTED = "reverted"
STATUS_PARTIALLY_REVERTED = "partially_reverted"
_OPEN_STATUSES = frozenset({STATUS_RUNNING})

CONFLICT_STALE_BASE = "stale_base"
CONFLICT_CONCURRENT = "concurrent_writer"

_TEXT_OPS = frozenset({"write", "create"})
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


@runtime_checkable
class ChangeJournal(Protocol):
    def record(self, record: WriteRecord) -> dict[str, Any]:
        """Persist one applied write; return ``{run_id, seq, in_lane, conflict, warning}``."""
        ...

    def note_read(self, path: str, content_hash: str, project_root: str = "") -> None:
        """Remember the base a run last saw for *path* (conflict detection)."""
        ...


class NullJournal:
    """No-op journal: attribution still reaches observers, nothing is ledgered."""

    def record(self, record: WriteRecord) -> dict[str, Any]:
        return {}

    def note_read(self, path: str, content_hash: str, project_root: str = "") -> None:
        return None


StorageLocator = Callable[[str], Path]


class FileChangeJournal:
    def __init__(
        self,
        storage_for_root: StorageLocator,
        *,
        enabled: Callable[[], bool] = lambda: True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._storage_for_root = storage_for_root
        self._enabled = enabled
        self._clock = clock
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        # Reads seen by a run before its first write (no run document yet).
        self._pending_seen: dict[str, dict[str, dict[str, Any]]] = {}

    # ------------------------------------------------------------ recording

    def note_read(self, path: str, content_hash_value: str, project_root: str = "") -> None:
        ctx = identity.resolve_context()
        if ctx is None or not ctx.run_id or not self._enabled():
            return
        rel = normalize_rel(path)
        seen = {"hash": content_hash_value, "ts": self._clock()}
        try:
            storage = self._storage(project_root)
        except Exception:  # noqa: BLE001 - no root configured: keep it in memory only
            storage = None
        with self._lock(storage):
            run = self._load_run(storage, ctx.run_id) if storage else None
            if run is None:
                self._pending_seen.setdefault(ctx.run_id, {})[rel] = seen
                return
            run["seen"][rel] = seen
            self._save_run(storage, run)

    def record(self, record: WriteRecord) -> dict[str, Any]:
        if not self._enabled():
            return {}
        writer = dict(record.writer)
        run_id = str(writer.get("run_id") or "")
        storage = self._storage(record.project_root)
        with self._lock(storage):
            index = self._load_index(storage)
            if not run_id:
                # Human or unattributed write: only the last-writer index moves.
                self._apply_index(index, record, run_id="")
                self._save_index(storage, index)
                return {}
            run = self._load_run(storage, run_id)
            if run is None:
                run = self._new_run(run_id, record)
                run["seen"].update(self._pending_seen.pop(run_id, {}))
            conflict = self._detect_conflict(storage, index, run, record)
            entry = self._make_entry(storage, run, record, conflict)
            run["entries"].append(entry)
            if record.op in _TEXT_OPS:
                run["seen"][record.path] = {"hash": record.after_hash, "ts": entry["ts"]}
            self._apply_index(index, record, run_id=run_id)
            self._save_run(storage, run)
            self._save_index(storage, index)
        warning = self._conflict_warning(record, conflict)
        if conflict:
            ctx = record.ctx
            events.emit(
                events.FileGuardEvent(
                    kind=events.GUARD_CONFLICT,
                    path=record.path,
                    text=warning,
                    conv_id=ctx.conv_id if ctx else str(writer.get("conv_id") or ""),
                    run_id=run_id,
                    tool=record.tool,
                    details=dict(conflict),
                ).to_dict()
            )
        return {
            "run_id": run_id,
            "seq": entry["seq"],
            "in_lane": entry["in_lane"],
            "conflict": conflict,
            "warning": warning,
        }

    def end_run(self, run_id: str, status: str, *, project_root: str) -> None:
        self._pending_seen.pop(run_id, None)
        storage = self._storage(project_root)
        with self._lock(storage):
            run = self._load_run(storage, run_id)
            if run is None:
                return
            if run["status"] in _OPEN_STATUSES:
                run["status"] = status if status in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED) else STATUS_DONE
            run["ended"] = self._clock()
            self._save_run(storage, run)
        try:
            self.prune(project_root=project_root)
        except Exception:  # noqa: BLE001 - housekeeping
            log.debug("changeset prune failed", exc_info=True)

    # --------------------------------------------------------------- reading

    def list_runs(
        self,
        *,
        project_root: str,
        conv_id: str = "",
        group_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        storage = self._storage(project_root)
        runs: list[dict[str, Any]] = []
        with self._lock(storage):
            for path in (storage / "runs").glob("*.json"):
                run = self._read_json(path)
                if not isinstance(run, dict):
                    continue
                if conv_id and run.get("conv_id") != conv_id:
                    continue
                if group_id and run.get("group_id") != group_id:
                    continue
                runs.append(run)
        runs.sort(key=lambda r: float(r.get("started") or 0.0), reverse=True)
        return runs[: max(1, int(limit))]

    def get_run(self, run_id: str, *, project_root: str) -> dict[str, Any]:
        storage = self._storage(project_root)
        with self._lock(storage):
            run = self._load_run(storage, run_id)
        if run is None:
            raise ValueError(f"Changeset run not found: {run_id}")
        return run

    def entry_contents(self, run_id: str, seq: int, *, project_root: str) -> dict[str, str]:
        run = self.get_run(run_id, project_root=project_root)
        entry = self._entry(run, seq)
        storage = self._storage(project_root)
        return {
            "path": entry["path"],
            "before": self._read_blob(storage, entry.get("before_blob")),
            "after": self._read_blob(storage, entry.get("after_blob")),
        }

    def export_run(self, run_id: str, *, project_root: str) -> dict[str, Any]:
        run = self.get_run(run_id, project_root=project_root)
        return {
            "schema_version": SCHEMA_VERSION,
            "format": EXPORT_FORMAT,
            "run": {
                key: run.get(key, "")
                for key in ("run_id", "conv_id", "profile_id", "ducky_name", "model", "coding_agent", "group_id")
            }
            | {"started": run.get("started", 0.0), "ended": run.get("ended"), "status": run.get("status", "")},
            "author": {
                "id": run.get("profile_id") or run.get("conv_id") or "",
                "name": run.get("ducky_name") or "",
                "model": run.get("model") or "",
            },
            "lane": run.get("lane"),
            "files": [
                {
                    "path": e["path"],
                    "op": e["op"],
                    "from_path": e.get("from_path"),
                    "before_hash": e.get("before_hash", ""),
                    "after_hash": e.get("after_hash", ""),
                    "lines_added": int(e.get("lines_added") or 0),
                    "lines_removed": int(e.get("lines_removed") or 0),
                    "in_lane": e.get("in_lane"),
                    "conflict": ({"kind": e["conflict"]["kind"]} if e.get("conflict") else None),
                }
                for e in run.get("entries", [])
            ],
        }

    # -------------------------------------------------------------- reverting

    def revert_run(
        self,
        run_id: str,
        *,
        project_root: str,
        force: bool = False,
        actor: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Undo every entry of a run, newest first, one restore per path."""
        run = self.get_run(run_id, project_root=project_root)
        entries = [e for e in run.get("entries", []) if not e.get("reverted")]
        return self._revert_entries(run, entries, project_root=project_root, force=force, actor=actor)

    def revert_entry(
        self,
        run_id: str,
        seq: int,
        *,
        project_root: str,
        force: bool = False,
        actor: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Undo one file within a run: every not-yet-reverted entry for *seq*'s path."""
        run = self.get_run(run_id, project_root=project_root)
        entry = self._entry(run, seq)
        same_path = [e for e in run.get("entries", []) if e["path"] == entry["path"] and not e.get("reverted")]
        if not same_path:
            return {"ok": True, "reverted": [], "skipped_modified": [], "errors": [f"seq {seq} already reverted"]}
        return self._revert_entries(run, same_path, project_root=project_root, force=force, actor=actor)

    def _revert_entries(
        self,
        run: dict[str, Any],
        entries: list[dict[str, Any]],
        *,
        project_root: str,
        force: bool,
        actor: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        from backend.workspace.runtime import get_writer

        storage = self._storage(project_root)
        writer = get_writer()
        revert_run_id = f"revert:{uuid.uuid4().hex}"
        meta = {**identity.user_writer(tool="changeset_revert"), **dict(actor or {})}
        meta["source"] = identity.SOURCE_REVERT
        meta["run_id"] = revert_run_id
        meta["tool"] = "changeset_revert"

        reverted: list[int] = []
        skipped: list[dict[str, Any]] = []
        errors: list[str] = []
        touched: list[str] = []
        # Newest first; one restore per path, to the state before that path's earliest entry.
        by_path: dict[str, list[dict[str, Any]]] = {}
        for entry in sorted(entries, key=lambda e: int(e["seq"]), reverse=True):
            by_path.setdefault(entry["path"], []).append(entry)
        for path, group in by_path.items():
            newest, earliest = group[0], group[-1]
            try:
                outcome = self._revert_path(writer, storage, project_root, path, newest, earliest, force=force, meta=meta)
            except Exception as exc:  # noqa: BLE001 - report per path, keep going
                errors.append(f"{path}: {exc}")
                continue
            if outcome == "skipped":
                skipped.append({"seq": int(newest["seq"]), "path": path})
                continue
            reverted.extend(int(e["seq"]) for e in group)
            touched.append(path)
        if reverted:
            with self._lock(storage):
                fresh = self._load_run(storage, run["run_id"]) or run
                for entry in fresh.get("entries", []):
                    if int(entry["seq"]) in reverted:
                        entry["reverted"] = True
                        entry["reverted_by_run"] = revert_run_id
                remaining = [e for e in fresh.get("entries", []) if not e.get("reverted")]
                fresh["status"] = STATUS_REVERTED if not remaining else STATUS_PARTIALLY_REVERTED
                self._save_run(storage, fresh)
            events.emit(
                events.FilesRevertedEvent(
                    paths=tuple(touched), run_id=run["run_id"], conv_id=str(run.get("conv_id") or "")
                ).to_dict()
            )
        return {
            "ok": not errors,
            "run_id": run["run_id"],
            "revert_run_id": revert_run_id if reverted else "",
            "reverted": sorted(reverted),
            "skipped_modified": skipped,
            "errors": errors,
        }

    def _revert_path(
        self,
        writer: Any,
        storage: Path,
        project_root: str,
        path: str,
        newest: dict[str, Any],
        earliest: dict[str, Any],
        *,
        force: bool,
        meta: dict[str, Any],
    ) -> str:
        full = os.path.join(project_root, path)
        op_new = newest["op"]
        if op_new in _TEXT_OPS or op_new == "delete":
            current = self._disk_hash(full)
            expected = "" if op_new == "delete" else str(newest.get("after_hash") or "")
            if current != expected and not force:
                return "skipped"
        op_old = earliest["op"]
        if op_old in _TEXT_OPS and not earliest.get("before_hash"):
            # The file did not exist before this run touched it.
            self._unlink_through_pipeline(writer, path, meta)
            return "reverted"
        if op_old in _TEXT_OPS:
            before = self._read_blob(storage, earliest.get("before_blob"))
            if before is None:
                before = ""  # an empty file has a hash but no blob
            writer.write_text(path, before, tool="changeset_revert", writer=meta)
            return "reverted"
        if op_old == "delete":
            before = self._read_blob(storage, earliest.get("before_blob"))
            if before is None:
                raise ValueError("deleted content was not captured (binary or folder)")
            writer.write_text(path, before, op="create", tool="changeset_revert", writer=meta)
            return "reverted"
        if op_old in ("rename", "move"):
            src = str(earliest.get("from_path") or "")
            if not src:
                raise ValueError("no source recorded for the rename/move")
            self._move_back_through_pipeline(writer, project_root, path, src, meta)
            return "reverted"
        if op_old in ("copy", "import", "restore"):
            if os.path.isdir(full):
                raise ValueError("folder operations are not auto-reverted")
            self._unlink_through_pipeline(writer, path, meta)
            return "reverted"
        raise ValueError(f"unknown op {op_old!r}")

    @staticmethod
    def _unlink_through_pipeline(writer: Any, path: str, meta: dict[str, Any]) -> None:
        def _unlink() -> None:
            abs_path = os.path.join(writer.root(), path)
            if os.path.isfile(abs_path):
                os.remove(abs_path)

        writer.path_op("delete", path, tool="changeset_revert", perform=_unlink, writer=meta)

    @staticmethod
    def _move_back_through_pipeline(writer: Any, project_root: str, current: str, original: str, meta: dict[str, Any]) -> None:
        def _move() -> None:
            src = os.path.join(project_root, current)
            dst = os.path.join(project_root, original)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)

        writer.path_op("move", original, source=current, tool="changeset_revert", perform=_move, writer=meta)

    # ------------------------------------------------------------- retention

    def prune(self, *, project_root: str, max_age_days: int = 30, keep_runs: int = 300) -> dict[str, int]:
        storage = self._storage(project_root)
        cutoff = self._clock() - max(1, int(max_age_days)) * 86400.0
        removed_runs = 0
        removed_blobs = 0
        with self._lock(storage):
            runs: list[tuple[float, Path, dict[str, Any]]] = []
            for path in (storage / "runs").glob("*.json"):
                run = self._read_json(path)
                if not isinstance(run, dict):
                    continue
                runs.append((float(run.get("started") or 0.0), path, run))
            runs.sort(key=lambda t: t[0], reverse=True)
            keep: list[dict[str, Any]] = []
            for i, (started, path, run) in enumerate(runs):
                open_run = run.get("status") in _OPEN_STATUSES
                if not open_run and (i >= keep_runs or started < cutoff):
                    path.unlink(missing_ok=True)
                    removed_runs += 1
                    continue
                keep.append(run)
            referenced: set[str] = set()
            for run in keep:
                for e in run.get("entries", []):
                    for key in ("before_blob", "after_blob"):
                        if e.get(key):
                            referenced.add(str(e[key]))
            blobs = storage / "blobs"
            if blobs.is_dir():
                for blob in blobs.glob("*.txt"):
                    if blob.stem not in referenced:
                        blob.unlink(missing_ok=True)
                        removed_blobs += 1
        return {"removed_runs": removed_runs, "removed_blobs": removed_blobs}

    # --------------------------------------------------------------- helpers

    def _storage(self, project_root: str) -> Path:
        if not project_root:
            raise ValueError("project_root is required")
        storage = self._storage_for_root(project_root)
        (storage / "runs").mkdir(parents=True, exist_ok=True)
        (storage / "blobs").mkdir(parents=True, exist_ok=True)
        return storage

    def _lock(self, storage: Path | None) -> threading.RLock:
        key = str(storage) if storage else ""
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._locks[key] = lock
            return lock

    @staticmethod
    def _run_path(storage: Path, run_id: str) -> Path:
        return storage / "runs" / f"{_SAFE_ID.sub('_', run_id)}.json"

    def _load_run(self, storage: Path, run_id: str) -> dict[str, Any] | None:
        data = self._read_json(self._run_path(storage, run_id))
        return data if isinstance(data, dict) else None

    def _save_run(self, storage: Path, run: dict[str, Any]) -> None:
        self._write_json(self._run_path(storage, run["run_id"]), run)

    def _load_index(self, storage: Path) -> dict[str, Any]:
        data = self._read_json(storage / "index.json")
        return data if isinstance(data, dict) else {}

    def _save_index(self, storage: Path, index: dict[str, Any]) -> None:
        self._write_json(storage / "index.json", index)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _store_blob(storage: Path, content: str) -> str:
        digest = content_hash(content)
        target = storage / "blobs" / f"{digest}.txt"
        if not target.exists():
            tmp = target.with_suffix(".tmp")
            tmp.write_text(content, encoding="utf-8", newline="")
            tmp.replace(target)
        return digest

    @staticmethod
    def _read_blob(storage: Path, digest: Any) -> str | None:
        if not digest:
            return None
        target = storage / "blobs" / f"{digest}.txt"
        try:
            return target.read_text(encoding="utf-8", newline="")
        except OSError:
            return None

    @staticmethod
    def _disk_hash(full: str) -> str:
        if not os.path.isfile(full):
            return ""
        with open(full, encoding="utf-8", errors="replace", newline="") as f:
            return content_hash(f.read())

    def _new_run(self, run_id: str, record: WriteRecord) -> dict[str, Any]:
        writer = dict(record.writer)
        ctx = record.ctx
        lane = None if ctx is None or ctx.lane is None else {"write_allowed": list(ctx.lane)}
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "conv_id": str(writer.get("conv_id") or ""),
            "profile_id": str(writer.get("profile_id") or ""),
            "ducky_name": str(writer.get("ducky_name") or ""),
            "model": str(writer.get("model") or ""),
            "coding_agent": str(writer.get("coding_agent") or ""),
            "group_id": str(writer.get("group_id") or ""),
            "leader_conv_id": ctx.leader_conv_id if ctx else "",
            "source": str(writer.get("source") or identity.SOURCE_AGENT),
            "lane": lane,
            "started": self._clock(),
            "ended": None,
            "status": STATUS_RUNNING,
            "seen": {},
            "entries": [],
        }

    def _make_entry(
        self, storage: Path, run: dict[str, Any], record: WriteRecord, conflict: dict[str, Any] | None
    ) -> dict[str, Any]:
        before_blob = self._store_blob(storage, record.before) if record.before else None
        after_blob = self._store_blob(storage, record.after) if record.op in _TEXT_OPS else None
        return {
            "seq": len(run["entries"]) + 1,
            "ts": record.ts or self._clock(),
            "path": record.path,
            "op": record.op,
            "from_path": record.from_path or None,
            "trash_token": record.trash_token or None,
            "tool": record.tool,
            "before_hash": record.before_hash,
            "after_hash": record.after_hash,
            "before_entry_id": None,
            "after_entry_id": None,
            "before_blob": before_blob,
            "after_blob": after_blob,
            "lines_added": int(record.lines_added),
            "lines_removed": int(record.lines_removed),
            "in_lane": record.in_lane,
            "conflict": conflict,
            "reverted": False,
            "reverted_by_run": None,
        }

    @staticmethod
    def _entry(run: dict[str, Any], seq: int) -> dict[str, Any]:
        for entry in run.get("entries", []):
            if int(entry.get("seq", 0)) == int(seq):
                return entry
        raise ValueError(f"Changeset entry not found: seq {seq}")

    def _detect_conflict(
        self, storage: Path, index: dict[str, Any], run: dict[str, Any], record: WriteRecord
    ) -> dict[str, Any] | None:
        if record.op not in _TEXT_OPS:
            return None
        prev = index.get(record.path)
        if not isinstance(prev, dict):
            return None
        prev_run = str(prev.get("run_id") or "")
        if prev_run == run["run_id"]:
            return None
        other = {
            "other_run_id": prev_run,
            "other_conv_id": str(prev.get("conv_id") or ""),
            "other_ducky": str(prev.get("ducky_name") or ("You" if not prev_run else "")),
        }
        base = run["seen"].get(record.path, {}).get("hash")
        if base is not None and base != record.before_hash:
            return {"kind": CONFLICT_STALE_BASE, "expected_hash": base, "found_hash": record.before_hash, **other}
        if prev_run:
            other_run = self._load_run(storage, prev_run)
            if other_run is not None and other_run.get("status") in _OPEN_STATUSES:
                return {"kind": CONFLICT_CONCURRENT, "expected_hash": "", "found_hash": record.before_hash, **other}
        return None

    @staticmethod
    def _conflict_warning(record: WriteRecord, conflict: dict[str, Any] | None) -> str:
        if not conflict:
            return ""
        who = conflict.get("other_ducky") or conflict.get("other_conv_id") or "another run"
        name = os.path.basename(record.path)
        if conflict["kind"] == CONFLICT_STALE_BASE:
            return f"{name} was changed by {who} after you read it; your write replaced their version. Re-read it before compiling."
        return f"{name} is also being edited by {who} right now; coordinate before compiling."

    def _apply_index(self, index: dict[str, Any], record: WriteRecord, *, run_id: str) -> None:
        writer = dict(record.writer)
        stamp = {
            "hash": record.after_hash,
            "run_id": run_id,
            "conv_id": str(writer.get("conv_id") or ""),
            "ducky_name": str(writer.get("ducky_name") or ""),
            "ts": record.ts or self._clock(),
            "source": str(writer.get("source") or ""),
        }
        if record.op == "delete":
            index.pop(record.path, None)
            return
        if record.op in ("rename", "move") and record.from_path:
            index.pop(record.from_path, None)
        index[record.path] = stamp
