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
from backend.workspace.editor_ops import usable_ident
from backend.workspace.paths import content_hash, normalize_rel, same_project_roots

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
#: Editor mutations (actors, assets, devices, Verse wiring). Their "path" is a
#: target slot such as ``uefn://actor/<guid>/transform``, not a file on disk.
OP_EDITOR = "editor"
#: Ops whose after-state is stored as a blob. Editor entries keep JSON there so
#: the panel can diff what a command changed.
_BLOB_OPS = _TEXT_OPS | {OP_EDITOR}

OUTCOME_OK = "ok"
OUTCOME_BLOCKED = "blocked"
OUTCOME_FAILED = "failed"
#: Entries that changed nothing: recorded so the history is honest, but never
#: reverted, never indexed, never counted as a file.
_NO_EFFECT_OUTCOMES = frozenset({OUTCOME_BLOCKED, OUTCOME_FAILED})
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")
_VERSE_DEVICE_CLASS = re.compile(r"(?m)^class\s+(\w+_device)\b")


_KEEP_UNDER_CONTENT = frozenset({"verse", "python"})


def prune_empty_parents(abs_file: str, project_root: str) -> None:
    """Remove empty folders left after deleting a created file.

    Stops at Content/ and never removes Content/Verse or Content/Python.
    """
    from backend.workspace.paths import island_root

    parent = os.path.dirname(os.path.realpath(abs_file))
    island = os.path.realpath(island_root(project_root))
    content = os.path.realpath(os.path.join(island, "Content"))
    stop = content if os.path.isdir(content) else island
    while parent:
        here = os.path.realpath(parent)
        if here in (stop, island):
            break
        if os.path.dirname(here) == stop and os.path.basename(here).lower() in _KEEP_UNDER_CONTENT:
            break
        try:
            if os.listdir(parent):
                break
            os.rmdir(parent)
        except OSError:
            break
        parent = os.path.dirname(parent)


def _verse_device_names(source: str) -> list[str]:
    return _VERSE_DEVICE_CLASS.findall(source or "")


def _revert_uefn_for_deleted_verse(rel_path: str, source: str, writer_root: str) -> None:
    """Drop the generated Verse class / placed device after the .verse file is gone.

    Offline is fine — the file (and empty folders) already match disk. UEFN is
    best-effort so Content Browser does not keep a ghost class.
    """
    names = _verse_device_names(source)
    if not names:
        return
    from backend.workspace.paths import island_root

    island = os.path.basename(island_root(writer_root).rstrip("\\/"))
    rel = rel_path.replace("\\", "/").lstrip("/")
    if rel.lower().startswith("content/"):
        rel = rel[8:]
    folder = rel.rsplit("/", 1)[0] if "/" in rel else ""
    try:
        from backend.workspace.plugin_revert import post_inverse_step
    except Exception:
        return
    for name in names:
        asset = f"/{island}/{folder}/{name}" if folder else f"/{island}/{name}"
        try:
            post_inverse_step(
                "ducky_revert_creation",
                {"kind": "asset", "id": asset, "guid": ""},
                program="uefn",
            )
        except Exception:
            pass
        for ident in (name, name.replace("_", "")):
            try:
                post_inverse_step(
                    "ducky_revert_creation",
                    {"kind": "actor", "id": ident, "guid": ""},
                    program="uefn",
                )
            except Exception:
                pass


def kick_verse_compile() -> None:
    """Rebuild so UEFN drops generated classes for deleted .verse files. Never blocks revert."""
    import threading

    def _run() -> None:
        try:
            from backend.tools.verse.verse_diagnostics import workspace_compile_verse

            workspace_compile_verse()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="changeset-verse-compile").start()


def entry_revert_key(entry: Mapping[str, Any]) -> str:
    """Identity used to group one restore. Files use the path.

    Older editor rows stored an all-zero GUID in the slot, so every label or
    folder write shared ``uefn://actor/0000…/label``. Revert must still key
    those by the Unreal path on the target, the same way the Changes list does.

    Old Verse wires share ``uefn://verse/<device>/editable`` with no targets —
    key those by actor_path + field from the recorded params (or after-blob).
    """
    path = str(entry.get("path") or "")
    spec = entry.get("editor") if isinstance(entry.get("editor"), Mapping) else {}
    params = spec.get("params") if isinstance(spec, Mapping) else None
    cmd = str((spec or {}).get("command") or entry.get("tool") or "")
    if cmd == "ducky_revert_creation" and isinstance(params, Mapping):
        deleted = str(params.get("id") or "").strip()
        if deleted:
            return f"uefn://actor/{deleted}/exists"
    if isinstance(params, Mapping):
        field = str(params.get("field") or "").strip()
        actor = str(params.get("actor_path") or "").strip()
        if field:
            prog = path.split("://", 1)[0] if "://" in path else "uefn"
            return f"{prog}://verse/{actor or 'device'}/{field}"
    if "://" not in path:
        return _file_revert_key(path)
    targets = spec.get("targets") if isinstance(spec, Mapping) else None
    target = targets[0] if isinstance(targets, list) and targets else {}
    actor_path = str(target.get("path") or "").strip() if isinstance(target, Mapping) else ""
    if not actor_path:
        return path
    rest = path.split("://", 1)[-1]
    parts = rest.split("/")
    slot_id = parts[1] if len(parts) > 1 else ""
    if usable_ident(slot_id):
        return path
    kind = str((spec or {}).get("kind") or (parts[0] if parts else "actor"))
    facet = str((spec or {}).get("facet") or (parts[2] if len(parts) > 2 else ""))
    prog = path.split("://", 1)[0] or "uefn"
    return f"{prog}://{kind}/{actor_path}/{facet}"


def _is_creation_entry(entry: Mapping[str, Any]) -> bool:
    spec = entry.get("editor") if isinstance(entry.get("editor"), Mapping) else {}
    cmd = str((spec or {}).get("command") or entry.get("tool") or "")
    return cmd == "ducky_revert_creation"


def _remap_params(params: Mapping[str, Any], remap: Mapping[str, str]) -> dict[str, Any]:
    """Rewrite recorded actor paths after a redo spawn minted a new UAID."""
    if not remap:
        return dict(params)
    out = dict(params)
    for key in ("actor_path", "target_path", "child_path", "parent_path", "id"):
        val = out.get(key)
        if isinstance(val, str) and val in remap:
            out[key] = remap[val]
    for key in ("target_paths", "actor_paths"):
        val = out.get(key)
        if isinstance(val, list):
            out[key] = [remap.get(item, item) if isinstance(item, str) else item for item in val]
    return out


def _spawned_ident(result: Any) -> str:
    if not isinstance(result, Mapping):
        return ""
    actor = result.get("actor")
    if isinstance(actor, Mapping):
        return str(actor.get("path") or actor.get("actor_path") or actor.get("id") or "")
    if isinstance(actor, str) and actor:
        return actor
    path = result.get("path") or result.get("actor_path")
    return str(path) if path else ""


def _compile_verse_now() -> None:
    """Redo just restored a .verse file — the class must exist before we respawn its device."""
    try:
        from backend.tools.verse.verse_diagnostics import workspace_compile_verse

        workspace_compile_verse()
    except Exception:
        pass


def _synthesize_editor_inverse(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Old wire rows have no sidecar inverse; the after-blob still has actor+field.

    ponytail: clearing the field is the undo when no before-target was captured.
    A later write of the same field still blocks via entry_revert_key.
    """
    spec = entry.get("editor") if isinstance(entry.get("editor"), Mapping) else {}
    params = spec.get("params") if isinstance(spec, Mapping) else None
    if not isinstance(params, Mapping):
        return []
    command = str((spec or {}).get("command") or entry.get("tool") or "")
    actor = str(params.get("actor_path") or "").strip()
    field = str(params.get("field") or "").strip()
    if not actor or not field:
        return []
    if command == "wire_verse_device_array":
        return [{"command": "wire_verse_device_array",
                 "params": {"actor_path": actor, "field": field, "target_paths": [], "replace": True}}]
    if command == "wire_verse_prop_assets":
        return [{"command": "wire_verse_prop_assets",
                 "params": {"actor_path": actor, "field": field, "asset_paths": []}}]
    if command in ("wire_verse_device_ref", "set_verse_editable"):
        return [{"command": "set_verse_editable",
                 "params": {"actor_path": actor, "field": field, "value": None}}]
    return []


def _actor_missing(exc: BaseException) -> bool:
    return "actor not found" in str(exc).lower()


def _file_revert_key(path: str) -> str:
    """Same island file whether the ledger stored Content/Verse/x or Verse/x."""
    p = normalize_rel(path)
    if "://" in p:
        return p
    if p.lower().startswith("content/"):
        return p[8:]
    return p


def _path_aliases(path: str) -> tuple[str, ...]:
    p = normalize_rel(path)
    if not p or "://" in p:
        return (p,) if p else ()
    if p.lower().startswith("content/"):
        return (p, p[8:])
    return (p, f"Content/{p}")


def _use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("ledger")


def _repo():
    """The ledger repo, with the one-time legacy import done."""
    from backend.store.importers import phase3
    from backend.store.repos import ledger as repo

    phase3.ensure()
    return repo


def _pid(storage: Path) -> str:
    """Rows are keyed by the ledger folder name (the project slug)."""
    return storage.name


def _disk_rel(newest: Mapping[str, Any], earliest: Mapping[str, Any], key: str) -> str:
    """Prefer the ledger's stored path so Content-rooted and project-rooted writers both resolve."""
    cands = [str(newest.get("path") or ""), str(earliest.get("path") or "")]
    for p in cands:
        if p.lower().startswith("content/"):
            return p
    for p in cands:
        if p and "://" not in p:
            return p
    return key


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
        if not run_id and str(writer.get("source") or "") == identity.SOURCE_USER:
            run_id = self._human_run_id()
            writer["run_id"] = run_id
            writer["ducky_name"] = str(writer.get("ducky_name") or "You")
        storage = self._storage(record.project_root)
        with self._lock(storage):
            index = self._load_index(storage)
            if not run_id:
                # Unattributed write: only the last-writer index moves.
                self._apply_index(index, record, run_id="", writer=writer)
                self._save_index(storage, index)
                return {}
            run = self._load_run(storage, run_id)
            if run is None:
                run = self._new_run(run_id, record, writer=writer)
                run["seen"].update(self._pending_seen.pop(run_id, {}))
            conflict = self._detect_conflict(storage, index, run, record)
            entry = self._make_entry(storage, run, record, conflict)
            if record.outcome == OUTCOME_OK:
                for prev in run["entries"]:
                    if prev.get("path") == record.path and prev.get("reverted"):
                        prev["reverted"] = False
                        prev["reverted_by_run"] = ""
                if run.get("status") in (STATUS_REVERTED, STATUS_PARTIALLY_REVERTED):
                    run["status"] = STATUS_RUNNING if run.get("ended") is None else STATUS_DONE
            run["entries"].append(entry)
            if record.op in _TEXT_OPS and record.outcome == OUTCOME_OK:
                run["seen"][record.path] = {"hash": record.after_hash, "ts": entry["ts"]}
            self._apply_index(index, record, run_id=run_id, writer=writer)
            self._save_run(storage, run)
            self._save_index(storage, index)
        warning = self._conflict_warning(record, conflict)
        ctx = record.ctx
        conv_id = ctx.conv_id if ctx else str(writer.get("conv_id") or "")
        events.emit(
            {
                "type": events.CHANGESET_ROW,
                "run_id": run_id,
                "conv_id": conv_id,
                "path": record.path,
                "seq": entry["seq"],
            }
        )
        if conflict:
            events.emit(
                events.FileGuardEvent(
                    kind=events.GUARD_CONFLICT,
                    path=record.path,
                    text=warning,
                    conv_id=conv_id,
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
            "outcome": entry["outcome"],
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

    def close_orphan_runs(
        self, *, project_root: str, live_run_ids: set[str] | frozenset[str]
    ) -> list[dict[str, Any]]:
        """Close ledger rows still marked running after their agent thread died.

        Cursor / follow-up sends used to skip ``end_run`` when ``session.run_id``
        had already moved on, which left Revert disabled forever.

        Returns ``{run_id, conv_id}`` for each closed row so callers can persist
        an interrupted assistant when the turn never reached disk.
        """
        closed: list[dict[str, Any]] = []
        live = set(live_run_ids)
        for run in self.list_runs(project_root=project_root, limit=500):
            rid = str(run.get("run_id") or "")
            if run.get("status") in _OPEN_STATUSES and rid and rid not in live:
                self.end_run(rid, STATUS_CANCELLED, project_root=project_root)
                closed.append({"run_id": rid, "conv_id": str(run.get("conv_id") or "")})
        return closed

    # --------------------------------------------------------------- reading

    def list_runs(
        self,
        *,
        project_root: str,
        conv_id: str = "",
        group_id: str = "",
        limit: int = 50,
        archived: bool | None = False,
    ) -> list[dict[str, Any]]:
        storage = self._storage_for_root(project_root)
        runs_dir = storage / "runs"
        if not _use_db() and not runs_dir.is_dir():
            return []
        cap = max(1, int(limit))
        conv_id = (conv_id or "").strip()
        group_id = (group_id or "").strip()
        want_archived = None if archived is None else bool(archived)
        runs: list[dict[str, Any]] = []
        with self._lock(storage):
            catalog = self._load_catalog(storage)
            if catalog is None:
                catalog = self._rebuild_catalog(storage)
            ranked: list[tuple[float, str]] = []
            for rid, meta in catalog.items():
                if conv_id and str(meta.get("conv_id") or "") != conv_id:
                    continue
                if group_id and str(meta.get("group_id") or "") != group_id:
                    continue
                if want_archived is not None and bool(meta.get("archived")) != want_archived:
                    continue
                ranked.append((float(meta.get("started") or 0.0), rid))
            ranked.sort(key=lambda t: t[0], reverse=True)
            for _, rid in ranked[:cap]:
                run = self._load_run(storage, rid)
                if isinstance(run, dict):
                    runs.append(run)
        return runs

    def get_run(self, run_id: str, *, project_root: str) -> dict[str, Any]:
        storage = self._storage(project_root)
        with self._lock(storage):
            run = self._load_run(storage, run_id)
        if run is None:
            raise ValueError(f"Changeset run not found: {run_id}")
        self._hydrate_editor_params(storage, run.get("entries") or [])
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
            | {
                "started": run.get("started", 0.0),
                "ended": run.get("ended"),
                "status": run.get("status", ""),
                "archived": bool(run.get("archived")),
            },
            "author": {
                "id": run.get("profile_id") or run.get("conv_id") or "",
                "name": run.get("ducky_name") or "",
                "model": run.get("model") or "",
            },
            "lane": run.get("lane"),
            "files": [
                {
                    "seq": int(e.get("seq") or 0),
                    "path": e["path"],
                    "op": e["op"],
                    "from_path": e.get("from_path"),
                    "before_hash": e.get("before_hash", ""),
                    "after_hash": e.get("after_hash", ""),
                    "lines_added": int(e.get("lines_added") or 0),
                    "lines_removed": int(e.get("lines_removed") or 0),
                    "in_lane": e.get("in_lane"),
                    "outcome": e.get("outcome", OUTCOME_OK),
                    "conflict": ({"kind": e["conflict"]["kind"]} if e.get("conflict") else None),
                }
                for e in run.get("entries", [])
                if e.get("op") != OP_EDITOR
            ],
            "editor": [
                {
                    "seq": int(e.get("seq") or 0),
                    "slot": e["path"],
                    "command": (e.get("editor") or {}).get("command", e.get("tool", "")),
                    "kind": (e.get("editor") or {}).get("kind", ""),
                    "program": (e.get("editor") or {}).get("program", ""),
                    "summary": (e.get("editor") or {}).get("summary", ""),
                    "reason": (e.get("editor") or {}).get("reason", ""),
                    "label": self._export_editor_label(e.get("editor") or {}),
                    "targets": (e.get("editor") or {}).get("targets", []),
                    "revertable": (e.get("editor") or {}).get("revertable", "manual"),
                    "outcome": e.get("outcome", OUTCOME_OK),
                    "reverted": bool(e.get("reverted")),
                }
                for e in run.get("entries", [])
                if e.get("op") == OP_EDITOR
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
        self._require_revertable(run)
        entries = [
            e for e in run.get("entries", [])
            if not e.get("reverted") and e.get("outcome", OUTCOME_OK) == OUTCOME_OK
        ]
        if not entries and str(run.get("source") or "") != identity.SOURCE_REVERT:
            applied = [
                e for e in run.get("entries", [])
                if e.get("outcome", OUTCOME_OK) == OUTCOME_OK
            ]
            if applied and all(e.get("reverted") for e in applied):
                return self._replay_run(run, project_root=project_root, actor=actor)
        return self._revert_entries(run, entries, project_root=project_root, force=force, actor=actor)

    def revert_entry(
        self,
        run_id: str,
        seq: int,
        *,
        project_root: str,
        force: bool = False,
        actor: Mapping[str, Any] | None = None,
        step: bool = False,
    ) -> dict[str, Any]:
        """Undo one file within a run.

        Default: every not-yet-reverted entry for *seq*'s path (pre-run state).
        ``step=True``: that write only. A later unreverted write on the same
        path blocks — walk newest → oldest.
        """
        run = self.get_run(run_id, project_root=project_root)
        self._require_revertable(run)
        entry = self._entry(run, seq)
        if step:
            if entry.get("reverted"):
                return {"ok": True, "reverted": [], "skipped_modified": [], "errors": [f"seq {seq} already reverted"]}
            return self._revert_entries(
                run, [entry], project_root=project_root, force=force, actor=actor, step=True,
            )
        key = entry_revert_key(entry)
        same_path = [
            e for e in run.get("entries", [])
            if entry_revert_key(e) == key and not e.get("reverted")
        ]
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
        step: bool = False,
    ) -> dict[str, Any]:
        from backend.workspace.runtime import get_writer

        storage = self._storage(project_root)
        self._hydrate_editor_params(storage, entries)
        writer = get_writer()
        try:
            writer_root = os.path.realpath(writer.root())
        except Exception as exc:  # noqa: BLE001 - revert must not claim success
            return {
                "ok": False,
                "run_id": run["run_id"],
                "revert_run_id": "",
                "reverted": [],
                "skipped_modified": [],
                "manual": [],
                "errors": [str(exc)],
            }
        if not same_project_roots(writer_root, project_root):
            return {
                "ok": False,
                "run_id": run["run_id"],
                "revert_run_id": "",
                "reverted": [],
                "skipped_modified": [],
                "manual": [],
                "errors": [f"writer root {writer_root} is not this project {project_root}"],
            }
        from backend.workspace.plugin_revert import ensure_programs_ready, programs_of_entries

        try:
            ensure_programs_ready(programs_of_entries(entries))
        except ConnectionError as exc:
            return {
                "ok": False,
                "run_id": run["run_id"],
                "revert_run_id": "",
                "reverted": [],
                "skipped_modified": [],
                "manual": [],
                "errors": [str(exc)],
            }
        revert_run_id = f"revert:{uuid.uuid4().hex}"
        meta = {**identity.user_writer(tool="changeset_revert"), **dict(actor or {})}
        meta["source"] = identity.SOURCE_REVERT
        meta["run_id"] = revert_run_id
        meta["tool"] = "changeset_revert"
        # Stay in the same chat as the work we undo, or the per-chat drawer
        # filters the compensating run out and Redo never appears.
        meta["conv_id"] = str(meta.get("conv_id") or run.get("conv_id") or "")
        meta["group_id"] = str(meta.get("group_id") or run.get("group_id") or "")
        meta["profile_id"] = str(meta.get("profile_id") or run.get("profile_id") or "")
        meta["ducky_name"] = str(meta.get("ducky_name") or "Revert")

        reverted: list[int] = []
        skipped: list[dict[str, Any]] = []
        errors: list[str] = []
        touched: list[str] = []
        # Changes with no computable inverse. Not reverted, not an error — the
        # caller shows them as a 'remove these by hand' list.
        manual: list[dict[str, Any]] = []
        blocked_by: list[dict[str, Any]] = []
        # Newest first; one restore per path, to the state before that path's earliest entry.
        by_path: dict[str, list[dict[str, Any]]] = {}
        for entry in sorted(entries, key=lambda e: int(e["seq"]), reverse=True):
            by_path.setdefault(entry_revert_key(entry), []).append(entry)
        files: list[tuple[str, list[dict[str, Any]]]] = []
        creates: list[tuple[str, list[dict[str, Any]]]] = []
        rest: list[tuple[str, list[dict[str, Any]]]] = []
        for path, group in by_path.items():
            if "://" not in path:
                files.append((path, group))
            elif _is_creation_entry(group[-1]):
                creates.append((path, group))
            else:
                rest.append((path, group))
        remap: dict[str, str] = {}
        compiled = False
        # Redo (revert-of-revert): files, then respawns, then wires. Undo stays newest-first.
        ordered = files + creates + rest if run.get("source") == identity.SOURCE_REVERT else list(by_path.items())
        for path, group in ordered:
            if (
                not compiled
                and run.get("source") == identity.SOURCE_REVERT
                and creates
                and path == creates[0][0]
                and any(str(_disk_rel(g[0], g[-1], p)).replace("\\", "/").endswith(".verse") for p, g in files)
            ):
                compiled = True
                _compile_verse_now()
            newest, earliest = group[0], group[-1]
            blocker = self._later_other_writer(storage, path, run, earliest)
            if not blocker and step:
                blocker = self._later_step_in_run(run, earliest)
            if blocker:
                blocked_by.append(blocker)
                continue
            try:
                outcome = self._revert_path(
                    writer, storage, project_root, path, newest, earliest,
                    force=force, meta=meta, origin=run, remap=remap,
                )
            except ConnectionError as exc:
                errors.append(f"{path}: {exc}")
                break
            except Exception as exc:  # noqa: BLE001 - report per path, keep going
                errors.append(f"{path}: {exc}")
                continue
            if outcome == "skipped":
                skipped.append({"seq": int(newest["seq"]), "path": str(newest.get("path") or path)})
                continue
            if outcome == "manual":
                manual.append(self._manual_row(path, newest, earliest))
                continue
            if "://" not in path:
                try:
                    self._confirm_file_restored(writer, _disk_rel(newest, earliest, path), earliest)
                except Exception as exc:  # noqa: BLE001 - do not mark reverted
                    errors.append(f"{path}: {exc}")
                    continue
            reverted.extend(int(e["seq"]) for e in group)
            touched.append(path if "://" in path else _disk_rel(newest, earliest, path))
        if reverted:
            with self._lock(storage):
                fresh = self._load_run(storage, run["run_id"]) or run
                for entry in fresh.get("entries", []):
                    if int(entry["seq"]) in reverted:
                        entry["reverted"] = True
                        entry["reverted_by_run"] = revert_run_id
                remaining = [
                    e
                    for e in fresh.get("entries", [])
                    if not e.get("reverted") and e.get("outcome", OUTCOME_OK) == OUTCOME_OK
                ]
                fresh["status"] = STATUS_REVERTED if not remaining else STATUS_PARTIALLY_REVERTED
                self._save_run(storage, fresh)
                revert_doc = self._load_run(storage, revert_run_id)
                if revert_doc is not None:
                    revert_doc["reverts_run_id"] = fresh["run_id"]
                    self._save_run(storage, revert_doc)
                if fresh.get("source") == identity.SOURCE_REVERT:
                    self._restore_after_redo(
                        storage,
                        revert_run_id=fresh["run_id"],
                        hint_id=str(fresh.get("reverts_run_id") or ""),
                        touched=set(touched),
                    )
            # Only real file paths belong here: consumers reload them from disk.
            events.emit(
                events.FilesRevertedEvent(
                    paths=tuple(p for p in touched if "://" not in p),
                    run_id=run["run_id"],
                    conv_id=str(run.get("conv_id") or ""),
                ).to_dict()
            )
            if any(str(p).replace("\\", "/").endswith(".verse") for p in touched):
                kick_verse_compile()
        return {
            "ok": not errors and not skipped and not blocked_by,
            "run_id": run["run_id"],
            "revert_run_id": revert_run_id if reverted else "",
            "reverted": sorted(reverted),
            "skipped_modified": skipped,
            "manual": manual,
            "blocked_by": blocked_by,
            "errors": errors,
        }

    def _restore_after_redo(
        self,
        storage: Path,
        *,
        revert_run_id: str,
        hint_id: str,
        touched: set[str],
    ) -> None:
        """Reverting a revert puts the original work back — clear its reverted flags."""
        targets: list[dict[str, Any]] = []
        if hint_id:
            hinted = self._load_run(storage, hint_id)
            if hinted is not None:
                targets.append(hinted)
        if not targets:
            for path in (storage / "runs").glob("*.json"):
                other = self._read_json(path)
                if not isinstance(other, dict):
                    continue
                if str(other.get("run_id") or "") == revert_run_id:
                    continue
                if any(e.get("reverted_by_run") == revert_run_id for e in other.get("entries", [])):
                    targets.append(other)
        slots = set(touched) | {_file_revert_key(p) for p in touched}
        for orig in targets:
            changed = False
            for entry in orig.get("entries", []):
                if entry.get("reverted_by_run") == revert_run_id and (
                    entry.get("path") in slots or entry_revert_key(entry) in slots
                ):
                    entry["reverted"] = False
                    entry["reverted_by_run"] = None
                    changed = True
            if not changed:
                continue
            orig["status"] = self._status_from_applied(orig.get("entries", []))
            self._save_run(storage, orig)

    def _later_other_writer(
        self,
        storage: Path,
        path: str,
        run: dict[str, Any],
        earliest: dict[str, Any],
    ) -> dict[str, Any] | None:
        """A later unreverted write of *path* from a different chat, if any.

        Same-chat later edits of one slot still collapse to one restore. A
        compensating revert-run is not a writer — it put the earlier state back.
        """
        my_conv = str(run.get("conv_id") or "")
        my_run = str(run.get("run_id") or "")
        my_ts = float(earliest.get("ts") or 0)
        my_started = float(run.get("started") or 0)
        best: dict[str, Any] | None = None
        for other in self._iter_run_docs(storage):
            other_id = str(other.get("run_id") or "")
            if other_id == my_run:
                continue
            if str(other.get("source") or "") == identity.SOURCE_REVERT:
                continue
            # You-edits are not another chat — hash mismatch asks "Revert anyway".
            if str(other.get("source") or "") == identity.SOURCE_USER:
                continue
            other_conv = str(other.get("conv_id") or "")
            if other_conv == my_conv:
                continue
            other_started = float(other.get("started") or 0)
            for entry in other.get("entries") or []:
                if entry_revert_key(entry) != path:
                    continue
                if entry.get("reverted"):
                    continue
                if entry.get("outcome", OUTCOME_OK) != OUTCOME_OK:
                    continue
                ets = float(entry.get("ts") or 0)
                if ets < my_ts:
                    continue
                if ets == my_ts and other_started <= my_started:
                    continue
                row = {
                    "other_conv_id": other_conv,
                    "other_ducky": str(other.get("ducky_name") or ""),
                    "other_run_id": other_id,
                    "other_seq": int(entry.get("seq") or 0),
                    "path": path,
                    "ts": ets,
                }
                if best is None or ets > float(best.get("ts") or 0):
                    best = row
        if best:
            best.pop("ts", None)
        return best

    @staticmethod
    def _later_step_in_run(run: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any] | None:
        """Newest unreverted later write of the same path in this run, if any.

        Step revert walks newest → oldest. Punching a hole in the middle would
        restore an intermediate blob on top of later writes.
        """
        path = entry_revert_key(entry)
        seq = int(entry.get("seq") or 0)
        later: dict[str, Any] | None = None
        for other in run.get("entries") or []:
            if other is entry:
                continue
            if entry_revert_key(other) != path:
                continue
            if other.get("reverted"):
                continue
            if other.get("outcome", OUTCOME_OK) != OUTCOME_OK:
                continue
            other_seq = int(other.get("seq") or 0)
            if other_seq <= seq:
                continue
            if later is None or other_seq > int(later["other_seq"]):
                later = {
                    "other_conv_id": str(run.get("conv_id") or ""),
                    "other_ducky": str(run.get("ducky_name") or ""),
                    "other_run_id": str(run.get("run_id") or ""),
                    "other_seq": other_seq,
                    "path": path,
                    "same_run": True,
                }
        return later

    def _iter_run_docs(self, storage: Path) -> list[dict[str, Any]]:
        if _use_db():
            return _repo().runs_docs(_pid(storage))
        docs: list[dict[str, Any]] = []
        for path in (storage / "runs").glob("*.json"):
            run = self._read_json(path)
            if isinstance(run, dict) and run.get("run_id"):
                docs.append(run)
        return docs

    @staticmethod
    def _status_from_applied(entries: list[dict[str, Any]]) -> str:
        applied = [e for e in entries if e.get("outcome", OUTCOME_OK) == OUTCOME_OK]
        reverted = [e for e in applied if e.get("reverted")]
        if not reverted:
            return STATUS_DONE
        if len(reverted) == len(applied):
            return STATUS_REVERTED
        return STATUS_PARTIALLY_REVERTED

    @staticmethod
    def _manual_row(path: str, newest: dict[str, Any], earliest: dict[str, Any]) -> dict[str, Any]:
        spec = earliest.get("editor") or {}
        target = (spec.get("targets") or [{}])[0]
        return {
            "seq": int(newest["seq"]),
            "path": path,
            "command": spec.get("command") or earliest.get("tool", ""),
            "label": target.get("label") or target.get("path") or target.get("id") or "",
            "target": target.get("id") or target.get("path") or "",
            "reason": spec.get("reason") or earliest.get("reason") or "",
            "summary": spec.get("summary") or "",
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
        origin: Mapping[str, Any] | None = None,
        remap: dict[str, str] | None = None,
    ) -> str:
        if earliest["op"] == OP_EDITOR:
            return self._revert_editor(
                earliest, meta=meta, origin=origin, storage=storage, remap=remap,
            )
        path = _disk_rel(newest, earliest, path)
        full = writer.abs_path(path)
        op_new = newest["op"]
        if op_new in _TEXT_OPS or op_new == "delete":
            current = self._disk_hash(full)
            expected = "" if op_new == "delete" else str(newest.get("after_hash") or "")
            if current != expected and not force:
                return "skipped"
        op_old = earliest["op"]
        if op_old in _TEXT_OPS and not earliest.get("before_hash"):
            # The file did not exist before this run touched it.
            after = self._read_blob(storage, newest.get("after_blob")) or ""
            unlinked = self._unlink_through_pipeline(writer, path, meta)
            if unlinked == "skipped":
                return "skipped"
            if str(path).replace("\\", "/").endswith(".verse"):
                _revert_uefn_for_deleted_verse(path, after, writer.root())
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
            unlinked = self._unlink_through_pipeline(writer, path, meta)
            return "skipped" if unlinked == "skipped" else "reverted"
        raise ValueError(f"unknown op {op_old!r}")

    def _confirm_file_restored(self, writer: Any, path: str, earliest: dict[str, Any]) -> None:
        """Disk must match the restore. A journal flag is not enough."""
        full = writer.abs_path(path)
        op = earliest["op"]
        if op in _TEXT_OPS and not earliest.get("before_hash"):
            if os.path.isfile(full):
                raise ValueError("is still on disk")
            return
        if op in ("copy", "import", "restore"):
            if os.path.isfile(full) or os.path.isdir(full):
                raise ValueError("is still on disk")
            return
        if op in ("rename", "move"):
            src = str(earliest.get("from_path") or "")
            if os.path.isfile(full):
                raise ValueError("is still at the new location")
            if src and not os.path.isfile(writer.abs_path(src)):
                raise ValueError(f"{src} did not come back")
            return
        want = str(earliest.get("before_hash") or "")
        got = self._disk_hash(full)
        if got != want:
            raise ValueError("did not restore")

    def _creation_restore_step(
        self,
        earliest: Mapping[str, Any],
        origin: Mapping[str, Any] | None,
        storage: Path | None,
    ) -> dict[str, Any] | None:
        """How to put back an actor/asset this revert deleted."""
        spec = earliest.get("editor") if isinstance(earliest.get("editor"), Mapping) else {}
        params = spec.get("params") if isinstance(spec, Mapping) else None
        if not isinstance(params, Mapping):
            params = {}
        cmd = str(params.get("restore_command") or "").strip()
        rest = params.get("restore_params")
        if cmd and isinstance(rest, Mapping):
            return {"command": cmd, "params": dict(rest)}
        hint = str((origin or {}).get("reverts_run_id") or "")
        deleted = str(params.get("id") or "").strip()
        if not hint or storage is None or not deleted:
            return None
        orig = self._load_run(storage, hint)
        if not orig:
            return None
        for entry in orig.get("entries") or []:
            ed = entry.get("editor") if isinstance(entry.get("editor"), Mapping) else {}
            for target in list(ed.get("created") or []) + list(ed.get("targets") or []):
                if not isinstance(target, Mapping):
                    continue
                ident = str(target.get("path") or target.get("id") or "")
                label = str(target.get("label") or "")
                if ident != deleted and label != deleted and not ident.endswith(deleted):
                    continue
                p = ed.get("params") if isinstance(ed.get("params"), Mapping) else {}
                c = str(ed.get("command") or entry.get("tool") or "").strip()
                if c and p:
                    return {"command": c, "params": dict(p)}
        return None

    def _revert_editor(
        self,
        earliest: dict[str, Any],
        *,
        meta: dict[str, Any],
        origin: Mapping[str, Any] | None = None,
        storage: Path | None = None,
        remap: dict[str, str] | None = None,
    ) -> str:
        """Undo one editor change by posting its inverse, or removing what it created.

        The revert runs as its own attributed run, so undoing a move is itself a
        recorded change — symmetric with file reverts. There is no rollback here:
        UEFN has already saved, so every undo is a compensating forward edit.
        """
        spec = earliest.get("editor") or {}
        tool = str(spec.get("command") or earliest.get("tool") or "")
        steps = list(spec.get("inverse") or [])
        created = list(spec.get("created") or [])
        deleted_id = ""
        aliases = remap if remap is not None else {}
        if tool == "ducky_revert_creation":
            deleted_id = str((spec.get("params") or {}).get("id") or "")
            restore = self._creation_restore_step(earliest, origin, storage)
            if restore:
                steps = [restore]
                created = []
            elif not steps:
                return "manual"
        if not steps and not created:
            steps = list(_synthesize_editor_inverse(earliest) or [])
        if not steps and not created:
            # "none" means there is nothing to undo (a script whose bracketing
            # snapshots matched, an empty folder): done, not homework.
            return "reverted" if spec.get("revertable") == "none" else "manual"
        from backend.workspace.plugin_revert import PROGRAM_UEFN, post_inverse_step, program_of_slot

        program = str(spec.get("program") or "") or program_of_slot(str(earliest.get("path") or ""))
        if program in ("", "file"):
            program = PROGRAM_UEFN
        restore_cmd = str(spec.get("command") or earliest.get("tool") or "")
        restore_params = spec.get("params") if isinstance(spec.get("params"), Mapping) else {}
        # ducky_revert_creation is a UEFN listener command. Plugin programs
        # (blender, …) already put the delete in inverse — appending this
        # looks up a FastMCP tool that does not exist.
        if program != PROGRAM_UEFN:
            created = []
        for target in created:
            # Only ever removes something this journal recorded the agent creating,
            # and the listener re-checks that independently. restore_* is how Redo
            # puts the same spawn back.
            del_params: dict[str, Any] = {
                "kind": str(target.get("kind") or "actor"),
                "id": str(target.get("path") or usable_ident(str(target.get("id") or ""))
                          or target.get("label") or ""),
                "guid": usable_ident(str(target.get("guid") or "")),
            }
            if restore_cmd and isinstance(restore_params, Mapping) and restore_params:
                del_params["restore_command"] = restore_cmd
                del_params["restore_params"] = dict(restore_params)
            steps = steps + [{"command": "ducky_revert_creation", "params": del_params}]
        token = identity.bind(
            identity.RunContext(
                run_id=str(meta.get("run_id") or ""),
                conv_id=str(meta.get("conv_id") or ""),
                ducky_name=str(meta.get("ducky_name") or ""),
                source=identity.SOURCE_REVERT,
            )
        )
        try:
            for step in steps:
                command = str(step.get("command") or "")
                if not command:
                    continue
                params = _remap_params(dict(step.get("params") or {}), aliases)
                try:
                    result = post_inverse_step(command, params, program=program)
                    if deleted_id and command == "spawn_actor":
                        new = _spawned_ident(result)
                        if new:
                            aliases[deleted_id] = new
                except ConnectionError as exc:
                    where = "UEFN" if program == PROGRAM_UEFN else program
                    raise ConnectionError(f"open {where} to undo editor changes ({exc})") from exc
                except Exception as exc:
                    if command in ("wire_verse_device_ref", "wire_verse_device_array") and _actor_missing(exc):
                        actor = str(params.get("actor_path") or "")
                        field = str(params.get("field") or "")
                        if actor and field:
                            try:
                                post_inverse_step(
                                    "set_verse_editable",
                                    {"actor_path": actor, "field": field, "value": None},
                                    program=program,
                                )
                                continue
                            except ConnectionError as clear_exc:
                                where = "UEFN" if program == PROGRAM_UEFN else program
                                raise ConnectionError(
                                    f"open {where} to undo editor changes ({clear_exc})"
                                ) from clear_exc
                            except Exception as clear_exc:
                                if _actor_missing(clear_exc):
                                    continue
                                raise
                    if _actor_missing(exc):
                        continue
                    raise
        finally:
            identity.reset(token)
        return "reverted"

    def _replay_run(
        self,
        run: dict[str, Any],
        *,
        project_root: str,
        actor: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Put a fully-reverted agent run back when its compensating run is gone or empty."""
        from backend.workspace.plugin_revert import (
            PROGRAM_UEFN,
            ensure_programs_ready,
            post_inverse_step,
            programs_of_entries,
        )
        from backend.workspace.runtime import get_writer

        storage = self._storage(project_root)
        writer = get_writer()
        revert_run_id = f"revert:{uuid.uuid4().hex}"
        meta = {**identity.user_writer(tool="changeset_revert"), **dict(actor or {})}
        meta["source"] = identity.SOURCE_REVERT
        meta["run_id"] = revert_run_id
        meta["tool"] = "changeset_revert"
        meta["conv_id"] = str(meta.get("conv_id") or run.get("conv_id") or "")
        meta["group_id"] = str(meta.get("group_id") or run.get("group_id") or "")
        meta["profile_id"] = str(meta.get("profile_id") or run.get("profile_id") or "")
        meta["ducky_name"] = str(meta.get("ducky_name") or "Revert")

        replayed: list[int] = []
        errors: list[str] = []
        remap: dict[str, str] = {}

        def _replay_rank(entry: Mapping[str, Any]) -> int:
            if entry.get("op") in _TEXT_OPS:
                return 0
            spec = entry.get("editor") if isinstance(entry.get("editor"), Mapping) else {}
            cmd = str((spec or {}).get("command") or entry.get("tool") or "")
            if cmd == "spawn_actor" or (spec or {}).get("created"):
                return 1
            return 2

        pending = [
            e
            for e in (run.get("entries") or [])
            if e.get("outcome", OUTCOME_OK) == OUTCOME_OK and e.get("reverted")
        ]
        try:
            ensure_programs_ready(programs_of_entries(pending))
        except ConnectionError as exc:
            return {
                "ok": False,
                "run_id": run["run_id"],
                "revert_run_id": "",
                "reverted": [],
                "skipped_modified": [],
                "manual": [],
                "blocked_by": [],
                "errors": [str(exc)],
            }
        if any(str(e.get("path") or "").replace("\\", "/").endswith(".verse") for e in pending):
            _compile_verse_now()
        for entry in sorted(pending, key=lambda e: (_replay_rank(e), int(e.get("seq") or 0))):
            path = str(entry.get("path") or "")
            try:
                if entry.get("op") == OP_EDITOR:
                    spec = entry.get("editor") if isinstance(entry.get("editor"), Mapping) else {}
                    command = str((spec or {}).get("command") or entry.get("tool") or "")
                    params = (spec or {}).get("params") if isinstance((spec or {}).get("params"), Mapping) else {}
                    if not command or not params:
                        raise ValueError("no command recorded to put this back")
                    program = str((spec or {}).get("program") or "") or PROGRAM_UEFN
                    old_ids = [
                        str(t.get("path") or t.get("id") or "")
                        for t in list((spec or {}).get("created") or []) + list((spec or {}).get("targets") or [])
                        if isinstance(t, Mapping)
                    ]
                    token = identity.bind(
                        identity.RunContext(
                            run_id=revert_run_id,
                            conv_id=str(meta.get("conv_id") or ""),
                            ducky_name=str(meta.get("ducky_name") or ""),
                            source=identity.SOURCE_REVERT,
                        )
                    )
                    try:
                        result = post_inverse_step(command, _remap_params(dict(params), remap), program=program)
                        new = _spawned_ident(result)
                        if new:
                            for old in old_ids:
                                if old:
                                    remap[old] = new
                    finally:
                        identity.reset(token)
                elif entry.get("op") in _TEXT_OPS:
                    after = self._read_blob(storage, entry.get("after_blob"))
                    if after is None:
                        raise ValueError("content was not captured")
                    writer.write_text(path, after, tool="changeset_revert", writer=meta)
                else:
                    raise ValueError(f"cannot replay {entry.get('op')!r}")
            except ConnectionError as exc:
                errors.append(f"{path}: {exc}")
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{path}: {exc}")
                continue
            replayed.append(int(entry.get("seq") or 0))
        if replayed:
            with self._lock(storage):
                fresh = self._load_run(storage, run["run_id"]) or run
                for entry in fresh.get("entries", []):
                    if int(entry.get("seq") or 0) in replayed:
                        entry["reverted"] = False
                        entry["reverted_by_run"] = None
                fresh["status"] = self._status_from_applied(fresh.get("entries", []))
                self._save_run(storage, fresh)
                revert_doc = self._load_run(storage, revert_run_id)
                if revert_doc is not None:
                    revert_doc["reverts_run_id"] = fresh["run_id"]
                    self._save_run(storage, revert_doc)
        return {
            "ok": not errors and bool(replayed),
            "run_id": run["run_id"],
            "revert_run_id": revert_run_id if replayed else "",
            "reverted": sorted(replayed),
            "skipped_modified": [],
            "manual": [],
            "blocked_by": [],
            "errors": errors,
        }

    @staticmethod
    def _unlink_through_pipeline(writer: Any, path: str, meta: dict[str, Any]) -> str:
        full = writer.abs_path(path)
        if not os.path.isfile(full):
            return "skipped"

        def _unlink() -> None:
            abs_path = writer.abs_path(path)
            if os.path.isfile(abs_path):
                os.remove(abs_path)
            prune_empty_parents(abs_path, writer.root())

        writer.path_op("delete", path, tool="changeset_revert", perform=_unlink, writer=meta)
        return "reverted"

    @staticmethod
    def _move_back_through_pipeline(writer: Any, project_root: str, current: str, original: str, meta: dict[str, Any]) -> None:
        del project_root

        def _move() -> None:
            src = writer.abs_path(current)
            dst = writer.abs_path(original)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(src, dst)

        writer.path_op("move", original, source=current, tool="changeset_revert", perform=_move, writer=meta)

    # ------------------------------------------------------------- retention

    def set_archived(
        self,
        *,
        project_root: str,
        conv_id: str = "",
        group_id: str = "",
        run_id: str = "",
        archived: bool = True,
    ) -> dict[str, int]:
        """Move ledger rows to/from the archive. Does not revert the island."""
        storage = self._storage(project_root)
        conv_id = (conv_id or "").strip()
        group_id = (group_id or "").strip()
        run_id = (run_id or "").strip()
        want = bool(archived)
        updated = 0
        with self._lock(storage):
            for run in self._iter_run_docs(storage):
                if not self._match_run(run, conv_id=conv_id, group_id=group_id, run_id=run_id):
                    continue
                if run.get("status") in _OPEN_STATUSES:
                    continue
                if bool(run.get("archived")) == want:
                    continue
                run["archived"] = want
                if want:
                    run["archived_at"] = self._clock()
                else:
                    run["archived_at"] = None
                self._save_run(storage, run)
                updated += 1
        return {"updated": updated}

    def delete_runs(
        self,
        *,
        project_root: str,
        conv_id: str = "",
        group_id: str = "",
        run_id: str = "",
        archived_only: bool = False,
    ) -> dict[str, int]:
        """Drop ledger rows. Does not revert project files or editor work."""
        storage = self._storage(project_root)
        conv_id = (conv_id or "").strip()
        group_id = (group_id or "").strip()
        run_id = (run_id or "").strip()
        removed_runs = 0
        with self._lock(storage):
            deleted_ids: set[str] = set()
            keep: list[dict[str, Any]] = []
            for run in self._iter_run_docs(storage):
                match = self._match_run(run, conv_id=conv_id, group_id=group_id, run_id=run_id)
                if match and run.get("status") not in _OPEN_STATUSES:
                    if archived_only and not run.get("archived"):
                        keep.append(run)
                        continue
                    self._delete_run_doc(storage, run)
                    rid = str(run.get("run_id") or "")
                    if rid:
                        deleted_ids.add(rid)
                    removed_runs += 1
                    continue
                keep.append(run)
            if deleted_ids:
                index = self._load_index(storage)
                for key, stamp in list(index.items()):
                    if isinstance(stamp, dict) and str(stamp.get("run_id") or "") in deleted_ids:
                        del index[key]
                self._save_index(storage, index)
                self._drop_catalog_ids(storage, deleted_ids)
            removed_blobs = self._sweep_orphan_blobs(storage, keep)
        return {"removed_runs": removed_runs, "removed_blobs": removed_blobs}

    def delete_entries(self, run_id: str, seqs: list[int], *, project_root: str) -> dict[str, int]:
        """Drop blocked/failed rows from one run. Applied writes stay: they anchor revert.

        A run left with no rows (and not still running) is removed with its catalog entry.
        """
        storage = self._storage(project_root)
        want = {int(s) for s in seqs}
        with self._lock(storage):
            run = self._load_run(storage, run_id)
            if run is None:
                return {"removed": 0, "kept": len(want)}
            before = len(run["entries"])
            run["entries"] = [
                e for e in run["entries"] if not (e.get("seq") in want and e.get("outcome") != OUTCOME_OK)
            ]
            removed = before - len(run["entries"])
            if run["entries"] or run.get("status") in _OPEN_STATUSES:
                self._save_run(storage, run)
            else:
                self._delete_run_doc(storage, run)
                self._drop_catalog_ids(storage, {run_id})
        return {"removed": removed, "kept": len(want) - removed}

    @staticmethod
    def _require_revertable(run: dict[str, Any]) -> None:
        if run.get("archived"):
            raise ValueError(
                f"Run {run.get('run_id', '')!r} is archived; revert is locked. "
                "Unarchive it first, or read it with changeset_list / changeset_export / changeset_contents."
            )

    @staticmethod
    def _match_run(run: dict[str, Any], *, conv_id: str, group_id: str, run_id: str) -> bool:
        rid = str(run.get("run_id") or "")
        if run_id:
            return rid == run_id
        if conv_id or group_id:
            return (not conv_id or run.get("conv_id") == conv_id) and (
                not group_id or run.get("group_id") == group_id
            )
        return True

    def prune(self, *, project_root: str, max_age_days: int = 30, keep_runs: int = 300) -> dict[str, int]:
        storage = self._storage(project_root)
        cutoff = self._clock() - max(1, int(max_age_days)) * 86400.0
        removed_runs = 0
        with self._lock(storage):
            runs: list[tuple[float, dict[str, Any]]] = [
                (float(run.get("started") or 0.0), run) for run in self._iter_run_docs(storage)
            ]
            runs.sort(key=lambda t: t[0], reverse=True)
            keep: list[dict[str, Any]] = []
            dropped: set[str] = set()
            for i, (started, run) in enumerate(runs):
                open_run = run.get("status") in _OPEN_STATUSES
                if run.get("archived"):
                    keep.append(run)
                    continue
                if not open_run and (i >= keep_runs or started < cutoff):
                    self._delete_run_doc(storage, run)
                    rid = str(run.get("run_id") or "")
                    if rid:
                        dropped.add(rid)
                    removed_runs += 1
                    continue
                keep.append(run)
            if dropped:
                self._drop_catalog_ids(storage, dropped)
            removed_blobs = self._sweep_orphan_blobs(storage, keep)
        return {"removed_runs": removed_runs, "removed_blobs": removed_blobs}

    @staticmethod
    def _sweep_orphan_blobs(storage: Path, keep: list[dict[str, Any]]) -> int:
        if _use_db():
            # Refcount query over every project's entries, file versions and messages.
            return _repo().sweep_blobs()
        referenced: set[str] = set()
        for run in keep:
            for e in run.get("entries", []):
                for key in ("before_blob", "after_blob"):
                    if e.get(key):
                        referenced.add(str(e[key]))
        removed = 0
        blobs = storage / "blobs"
        if blobs.is_dir():
            for blob in blobs.glob("*.txt"):
                if blob.stem not in referenced:
                    blob.unlink(missing_ok=True)
                    removed += 1
        return removed

    # --------------------------------------------------------------- helpers

    def _storage(self, project_root: str) -> Path:
        if not project_root:
            raise ValueError("project_root is required")
        storage = self._storage_for_root(project_root)
        if not _use_db():
            (storage / "runs").mkdir(parents=True, exist_ok=True)
            (storage / "blobs").mkdir(parents=True, exist_ok=True)
        return storage

    def _delete_run_doc(self, storage: Path, run: dict[str, Any]) -> None:
        rid = str(run.get("run_id") or "")
        if _use_db():
            _repo().run_delete(_pid(storage), rid)
            return
        self._run_path(storage, rid).unlink(missing_ok=True)

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
        if _use_db():
            return _repo().run_get(_pid(storage), run_id)
        data = self._read_json(self._run_path(storage, run_id))
        return data if isinstance(data, dict) else None

    def _save_run(self, storage: Path, run: dict[str, Any]) -> None:
        if _use_db():
            _repo().run_put(_pid(storage), run)
            return
        self._write_json(self._run_path(storage, run["run_id"]), run)
        self._upsert_catalog(storage, run)

    def _load_index(self, storage: Path) -> dict[str, Any]:
        if _use_db():
            return _repo().index_get(_pid(storage))
        data = self._read_json(storage / "index.json")
        return data if isinstance(data, dict) else {}

    def _save_index(self, storage: Path, index: dict[str, Any]) -> None:
        if _use_db():
            _repo().index_replace(_pid(storage), index)
            return
        self._write_json(storage / "index.json", index)

    @staticmethod
    def _catalog_entry(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "conv_id": str(run.get("conv_id") or ""),
            "group_id": str(run.get("group_id") or ""),
            "started": float(run.get("started") or 0.0),
            "archived": bool(run.get("archived")),
        }

    def _load_catalog(self, storage: Path) -> dict[str, Any] | None:
        if _use_db():
            return _repo().catalog(_pid(storage))  # derived from the runs table
        data = self._read_json(storage / "catalog.json")
        return data if isinstance(data, dict) else None

    def _upsert_catalog(self, storage: Path, run: dict[str, Any]) -> None:
        rid = str(run.get("run_id") or "")
        if not rid or _use_db():
            return
        catalog = self._load_catalog(storage) or {}
        catalog[rid] = self._catalog_entry(run)
        self._write_json(storage / "catalog.json", catalog)

    def _drop_catalog_ids(self, storage: Path, run_ids: set[str]) -> None:
        if _use_db():
            return  # derived from the runs table
        catalog = self._load_catalog(storage)
        if not catalog:
            return
        for rid in run_ids:
            catalog.pop(rid, None)
        self._write_json(storage / "catalog.json", catalog)

    def _rebuild_catalog(self, storage: Path) -> dict[str, Any]:
        if _use_db():
            return _repo().catalog(_pid(storage))
        catalog: dict[str, Any] = {}
        for path in (storage / "runs").glob("*.json"):
            run = self._read_json(path)
            if not isinstance(run, dict):
                continue
            rid = str(run.get("run_id") or "")
            if rid:
                catalog[rid] = self._catalog_entry(run)
        self._write_json(storage / "catalog.json", catalog)
        return catalog

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
        if _use_db():
            return _repo().blob_put(content, digest=digest)
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
        if _use_db():
            return _repo().blob_get(str(digest))
        target = storage / "blobs" / f"{digest}.txt"
        try:
            return target.read_text(encoding="utf-8", newline="")
        except OSError:
            return None

    def _hydrate_editor_params(self, storage: Path, entries: list[dict[str, Any]]) -> None:
        """Copy actor/field params off the after-blob onto old wire rows (in memory)."""
        for entry in entries:
            spec = entry.get("editor")
            if not isinstance(spec, dict) or spec.get("params"):
                continue
            raw = self._read_blob(storage, entry.get("after_blob"))
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            params = data.get("params") if isinstance(data, dict) else None
            if isinstance(params, dict) and params:
                spec["params"] = params

    @staticmethod
    def _disk_hash(full: str) -> str:
        if not os.path.isfile(full):
            return ""
        with open(full, encoding="utf-8", errors="replace", newline="") as f:
            return content_hash(f.read())

    def _human_run_id(self) -> str:
        return time.strftime("human:%Y-%m-%d", time.localtime(self._clock()))

    @staticmethod
    def _export_editor_label(spec: Mapping[str, Any]) -> str:
        targets = spec.get("targets") if isinstance(spec, Mapping) else None
        target = targets[0] if isinstance(targets, list) and targets else {}
        if not isinstance(target, Mapping):
            return ""
        return str(target.get("label") or target.get("path") or target.get("id") or "")

    def _new_run(
        self, run_id: str, record: WriteRecord, writer: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        writer = dict(writer if writer is not None else record.writer)
        ctx = record.ctx
        lane = None if ctx is None or ctx.lane is None else {"write_allowed": list(ctx.lane)}
        source = str(writer.get("source") or identity.SOURCE_AGENT)
        human = source == identity.SOURCE_USER
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "conv_id": str(writer.get("conv_id") or ""),
            "profile_id": str(writer.get("profile_id") or ""),
            "ducky_name": str(writer.get("ducky_name") or ("You" if human else "")),
            "model": str(writer.get("model") or ""),
            "coding_agent": str(writer.get("coding_agent") or ""),
            "group_id": str(writer.get("group_id") or ""),
            "leader_conv_id": ctx.leader_conv_id if ctx else "",
            "source": source,
            "lane": lane,
            "started": self._clock(),
            "ended": self._clock() if human else None,
            "status": STATUS_DONE if human else STATUS_RUNNING,
            "archived": False,
            "seen": {},
            "entries": [],
        }

    def _make_entry(
        self, storage: Path, run: dict[str, Any], record: WriteRecord, conflict: dict[str, Any] | None
    ) -> dict[str, Any]:
        applied = record.outcome == OUTCOME_OK
        before_blob = self._store_blob(storage, record.before) if record.before else None
        after_blob = (
            self._store_blob(storage, record.after)
            if applied and record.op in _BLOB_OPS and record.after
            else None
        )
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
            "outcome": record.outcome,
            "reason": record.reason or None,
            "editor": dict(record.editor) if record.editor else None,
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
        if record.op not in _TEXT_OPS or record.outcome != OUTCOME_OK:
            return None
        prev = index.get(record.path)
        if not isinstance(prev, dict) or prev.get("deleted"):
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

    def _apply_index(
        self,
        index: dict[str, Any],
        record: WriteRecord,
        *,
        run_id: str,
        writer: Mapping[str, Any] | None = None,
    ) -> None:
        # Last writer per file *and* per editor slot, so later-writer detection
        # can see a rewire the same way it sees a Verse write. Blocked/failed
        # attempts wrote nothing.
        if record.outcome != OUTCOME_OK:
            return
        writer = dict(writer if writer is not None else record.writer)
        stamp = {
            "hash": record.after_hash,
            "run_id": run_id,
            "conv_id": str(writer.get("conv_id") or ""),
            "ducky_name": str(writer.get("ducky_name") or ""),
            "ts": record.ts or self._clock(),
            "source": str(writer.get("source") or ""),
        }
        if record.op == "delete":
            index[record.path] = {**stamp, "hash": "", "deleted": True}
            return
        if record.op in ("rename", "move") and record.from_path:
            index.pop(record.from_path, None)
        index[record.path] = stamp

    def index_stamp(self, path: str, *, project_root: str) -> dict[str, Any] | None:
        storage = self._storage(project_root)
        aliases = _path_aliases(path)
        with self._lock(storage):
            index = self._load_index(storage)
            for key in aliases:
                stamp = index.get(key)
                if isinstance(stamp, dict):
                    return dict(stamp)
        return None

    def recent_after_hash(
        self, path: str, digest: str, *, project_root: str, window_s: float = 60.0
    ) -> bool:
        """True when a run entry in the last *window_s* already recorded this after-hash."""
        if not digest:
            return False
        aliases = set(_path_aliases(path))
        now = self._clock()
        storage = self._storage(project_root)
        if _use_db():
            hit = _repo().latest_entry_for_path(_pid(storage), sorted(aliases), since=now - window_s)
            return bool(hit) and str(hit.get("after_hash") or "") == digest
        with self._lock(storage):
            for run in self._iter_run_docs(storage):
                for entry in run.get("entries") or []:
                    if str(entry.get("path") or "") not in aliases:
                        continue
                    if str(entry.get("after_hash") or "") != digest:
                        continue
                    if now - float(entry.get("ts") or 0) <= window_s:
                        return True
        return False

    def remember_blob(self, content: str, *, project_root: str) -> str:
        storage = self._storage(project_root)
        return self._store_blob(storage, content)
