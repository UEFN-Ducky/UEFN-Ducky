"""The write pipeline: the only code allowed to mutate files in the user's UEFN project.

Stages (see ``docs/architecture/write-pipeline.md``):

1. resolve   - confine the path to the project and apply the shared path guards
2. identity  - who is writing (``RunContext`` → ``DUCKY_*`` env → the user)
3. policy    - ``WritePolicy`` chain; a denial raises before any disk change
4. lock      - one ``threading.Lock`` per project path; optional compare-and-swap
5. read      - previous content and hash
6. write     - temp file + ``os.replace`` (atomic)
7. journal   - ``ChangeJournal.record`` (never fails a landed write)
8. notify    - ``WriteObserver`` fan-out (file history, editor sync, UI)

Rename/move/delete keep their filesystem step with the caller (``perform``); the
pipeline still owns policy, locking, journal and notification for them.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from backend.workspace import events, identity
from backend.workspace.identity import RunContext
from backend.workspace.journal import ChangeJournal, NullJournal
from backend.workspace.paths import (
    content_hash,
    line_delta,
    normalize_rel,
    rel_from_root,
    require_not_digest_path,
    require_writable_project_path,
)
from backend.workspace.policy import Decision, WritePolicy, WriteRequest, evaluate

log = logging.getLogger(__name__)

# Same cap the panel uses for opening text; larger files are never read for a
# restore point (they are not something an agent edits as text anyway).
TEXT_READ_MAX_BYTES = 8 * 1024 * 1024

# rename/move/copy have an in-project source; import/restore bring content in from
# outside Content (external disk, the undo trash); delete removes.
PATH_OPS = frozenset({"rename", "move", "delete", "copy", "import", "restore"})
_SOURCE_REQUIRED = frozenset({"rename", "move", "copy"})
# Ops whose source is itself mutated, so policy must clear both ends.
_SOURCE_MUTATED = frozenset({"rename", "move"})
TEXT_OPS = frozenset({"write", "create"})

RootResolver = Callable[[], str]
PathResolver = Callable[[str], str]
PerformFn = Callable[[], Mapping[str, Any] | None]


class WriteDenied(ValueError):
    """A policy refused the write. Raised before any disk change."""

    def __init__(self, decision: Decision, request: WriteRequest) -> None:
        super().__init__(decision.reason or "Write denied by policy.")
        self.decision = decision
        self.request = request
        self.hint = decision.hint


class StaleWrite(ValueError):
    """``expected_hash`` did not match the file on disk. Raised before any disk change."""

    def __init__(self, path: str, expected: str, found: str) -> None:
        super().__init__(
            f"{path} changed on disk since it was read (expected {expected or 'no file'}, "
            f"found {found or 'no file'}). Re-read the file and apply your change again."
        )
        self.path = path
        self.expected = expected
        self.found = found


@dataclass(frozen=True)
class WriteRecord:
    """One applied mutation, as handed to the journal and observers."""

    op: str
    path: str
    from_path: str
    before: str
    after: str
    before_hash: str
    after_hash: str
    existed_before: bool
    tool: str
    writer: Mapping[str, Any]
    ctx: RunContext | None
    ts: float
    lines_added: int
    lines_removed: int
    decision: Decision
    project_root: str
    abs_path: str
    trash_token: str = ""
    #: Editor-change payload (command, targets, inverse) for op == "editor".
    editor: Mapping[str, Any] | None = None
    #: ok | blocked | failed. Anything but ok changed nothing and is recorded
    #: for the history only — never reverted, never indexed.
    outcome: str = "ok"
    reason: str = ""

    @property
    def in_lane(self) -> bool | None:
        """True/False when a lane policy applied, None when no lane governs this writer."""
        if not self.decision.policy:
            return None
        return self.decision.in_lane


@dataclass
class WriteResult:
    op: str = "write"
    path: str = ""
    from_path: str = ""
    before_hash: str = ""
    after_hash: str = ""
    bytes_written: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    before_content: str = ""
    warning: str = ""
    changeset: dict[str, Any] = field(default_factory=dict)
    in_lane: bool | None = None
    trash_token: str = ""
    abs_path: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "relative_path": self.path,
            "bytes_written": self.bytes_written,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
        }
        if self.from_path:
            payload["from_path"] = self.from_path
        if self.changeset:
            payload["changeset"] = dict(self.changeset)
        if self.in_lane is not None:
            payload["in_lane"] = self.in_lane
        if self.warning:
            payload["warning"] = self.warning
        if self.trash_token:
            payload["trash_token"] = self.trash_token
        return payload


@runtime_checkable
class WriteObserver(Protocol):
    def on_write(self, record: WriteRecord, result: WriteResult) -> None: ...


class ProjectWriter:
    def __init__(
        self,
        *,
        root_resolver: RootResolver,
        path_resolver: PathResolver,
        policies: Iterable[WritePolicy] = (),
        observers: Iterable[WriteObserver] = (),
        journal: ChangeJournal | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root_resolver = root_resolver
        self._path_resolver = path_resolver
        self._policies: list[WritePolicy] = list(policies)
        self._observers: list[WriteObserver] = list(observers)
        self._journal: ChangeJournal = journal or NullJournal()
        self._clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @classmethod
    def for_root(cls, root: str, **kwargs: Any) -> ProjectWriter:
        """A writer confined to one project root (tests, explicit roots)."""
        base = os.path.realpath(os.path.abspath(root))

        def resolve(rel: str) -> str:
            full = os.path.realpath(os.path.abspath(os.path.join(base, rel)))
            if os.path.commonpath([full, base]) != base:
                raise ValueError(f"Path escapes workspace root: {rel!r}")
            return full

        return cls(root_resolver=lambda: base, path_resolver=resolve, **kwargs)

    # -- configuration -----------------------------------------------------

    def root(self) -> str:
        """The project root every relative path resolves against."""
        return self._root_resolver()

    def abs_path(self, rel: str) -> str:
        """Absolute path for a journalled relative path (Content/ prefix stripped when the root is Content/)."""
        _canonical, full, _root = self._resolve(rel)
        return full

    def add_policy(self, policy: WritePolicy) -> None:
        if policy not in self._policies:
            self._policies.append(policy)

    def add_observer(self, observer: WriteObserver) -> None:
        if observer not in self._observers:
            self._observers.append(observer)

    def remove_observer(self, observer: WriteObserver) -> None:
        if observer in self._observers:
            self._observers.remove(observer)

    def set_journal(self, journal: ChangeJournal | None) -> None:
        self._journal = journal or NullJournal()

    @property
    def journal(self) -> ChangeJournal:
        return self._journal

    @property
    def policies(self) -> tuple[WritePolicy, ...]:
        return tuple(self._policies)

    # -- public operations -------------------------------------------------

    def write_text(
        self,
        rel: str,
        content: str,
        *,
        op: str = "write",
        expected_hash: str | None = None,
        tool: str = "",
        writer: Mapping[str, Any] | None = None,
    ) -> WriteResult:
        """Overwrite (or, with ``op="create"``, create) one UTF-8 text file."""
        if op not in TEXT_OPS:
            raise ValueError(f"Unknown text op: {op!r}")
        canonical, full, root = self._resolve(rel)
        ctx = identity.resolve_context()
        writer_meta = dict(writer) if writer is not None else identity.current_writer(tool=tool)
        request = WriteRequest(op=op, paths=(canonical,), tool=tool, ctx=ctx)
        with self._lock_for(canonical):
            decision = self._decide(request)
            before, existed = self._read_text(full)
            if op == "create" and existed:
                raise ValueError(f"Already exists: {canonical}")
            before_hash = content_hash(before) if existed else ""
            if expected_hash is not None and before_hash != expected_hash:
                raise StaleWrite(canonical, expected_hash, before_hash)
            self._atomic_write(full, content)
            added, removed = line_delta(before, content)
            record = WriteRecord(
                op=op,
                path=canonical,
                from_path="",
                before=before,
                after=content,
                before_hash=before_hash,
                after_hash=content_hash(content),
                existed_before=existed,
                tool=tool,
                writer=writer_meta,
                ctx=ctx,
                ts=self._clock(),
                lines_added=added,
                lines_removed=removed,
                decision=decision,
                project_root=root,
                abs_path=full,
            )
            result = WriteResult(
                op=op,
                path=canonical,
                before_hash=record.before_hash,
                after_hash=record.after_hash,
                bytes_written=len(content.encode("utf-8")),
                lines_added=added,
                lines_removed=removed,
                before_content=before,
                in_lane=record.in_lane,
                abs_path=full,
            )
            self._journal_and_notify(record, result)
        return result

    def create(
        self,
        rel: str,
        content: str,
        *,
        tool: str = "",
        writer: Mapping[str, Any] | None = None,
    ) -> WriteResult:
        return self.write_text(rel, content, op="create", tool=tool, writer=writer)

    def note_read(self, rel: str, content: str) -> None:
        """Tell the journal what base this run saw (conflict detection). Never raises."""
        try:
            canonical, _full, root = self._resolve_for_read(rel)
            self._journal.note_read(canonical, content_hash(content), project_root=root)
        except Exception:  # noqa: BLE001 - bookkeeping only
            log.debug("note_read failed", exc_info=True)

    def path_op(
        self,
        op: str,
        path: str,
        *,
        source: str | None = None,
        tool: str = "",
        perform: PerformFn,
        writer: Mapping[str, Any] | None = None,
    ) -> WriteResult:
        """Rename, move, copy, import, restore or delete. ``perform`` does the filesystem step.

        ``path`` is the in-project path the operation lands on (the destination
        for rename/move/copy/import/restore, the removed entry for delete);
        ``source`` is the in-project origin for rename/move/copy. Policy clears
        the destination always and the source when it is mutated (rename/move).
        ``perform`` runs under the path locks and may return a mapping;
        ``trash_token`` (delete) is kept so a revert can restore from the trash.
        """
        if op not in PATH_OPS:
            raise ValueError(f"Unknown path op: {op!r}")
        if op in _SOURCE_REQUIRED and not source:
            raise ValueError(f"{op} needs a source")
        if op not in _SOURCE_REQUIRED and source:
            raise ValueError(f"{op} takes no source")
        dst_rel, dst_full, root = self._resolve(path)
        src_rel = ""
        if source:
            src_rel, _src_full, _ = self._resolve(source)
        policy_paths = (src_rel, dst_rel) if op in _SOURCE_MUTATED else (dst_rel,)
        lock_paths = (src_rel, dst_rel) if src_rel else (dst_rel,)
        ctx = identity.resolve_context()
        writer_meta = dict(writer) if writer is not None else identity.current_writer(tool=tool)
        request = WriteRequest(op=op, paths=policy_paths, tool=tool, ctx=ctx)
        with self._lock_many(lock_paths):
            decision = self._decide(request)
            existed = os.path.exists(dst_full)
            before = ""
            if op == "delete" and os.path.isfile(dst_full):
                before, _ = self._read_text(dst_full)
            record_outcome = "ok"
            record_reason = ""
            if op == "delete" and not existed:
                record_outcome = "failed"
                record_reason = "nothing on disk"
            outcome = dict(perform() or {})
            record = WriteRecord(
                op=op,
                path=dst_rel,
                from_path=src_rel,
                before=before,
                after="",
                before_hash=content_hash(before) if before else "",
                after_hash="",
                existed_before=existed,
                tool=tool,
                writer=writer_meta,
                ctx=ctx,
                ts=self._clock(),
                lines_added=0,
                lines_removed=len(before.splitlines()) if before else 0,
                decision=decision,
                project_root=root,
                abs_path=dst_full,
                trash_token=str(outcome.get("trash_token") or ""),
                outcome=record_outcome,
                reason=record_reason,
            )
            result = WriteResult(
                op=op,
                path=record.path,
                from_path=record.from_path,
                before_hash=record.before_hash,
                lines_removed=record.lines_removed,
                before_content=before,
                in_lane=record.in_lane,
                trash_token=record.trash_token,
                abs_path=dst_full,
            )
            self._journal_and_notify(record, result)
        return result

    # -- stages ------------------------------------------------------------

    def _resolve(self, rel_in: str) -> tuple[str, str, str]:
        """(canonical project-relative path, absolute path, project root)."""
        rel = normalize_rel(rel_in)
        if not rel:
            raise ValueError("Path must not be empty.")
        root = self._root_resolver()
        if os.path.basename(os.path.realpath(root)).lower() == "content" and rel.lower().startswith("content/"):
            rel = rel[8:]
        require_not_digest_path(rel)
        full = self._path_resolver(rel)
        require_not_digest_path(full)
        require_writable_project_path(full)
        canonical = rel_from_root(full, root) or rel
        return canonical, full, root

    def _resolve_for_read(self, rel_in: str) -> tuple[str, str, str]:
        rel = normalize_rel(rel_in)
        if not rel:
            raise ValueError("Path must not be empty.")
        full = self._path_resolver(rel)
        root = self._root_resolver()
        return rel_from_root(full, root) or rel, full, root

    def _decide(self, request: WriteRequest) -> Decision:
        decision = evaluate(self._policies, request)
        ctx = request.ctx
        common = {
            "path": request.paths[-1],
            "tool": request.tool,
            "conv_id": ctx.conv_id if ctx else "",
            "run_id": ctx.run_id if ctx else "",
            "lane": tuple(ctx.lane or ()) if ctx else (),
            "leader_conv_id": ctx.leader_conv_id if ctx else "",
        }
        if not decision.allow:
            log.info(
                "workspace.write.denied policy=%s op=%s path=%s run=%s conv=%s",
                decision.policy, request.op, common["path"], common["run_id"], common["conv_id"],
            )
            events.emit(
                events.FileGuardEvent(
                    kind=str(decision.details.get("kind") or events.GUARD_POLICY_DENIED),
                    text=decision.reason,
                    details=dict(decision.details),
                    **common,
                ).to_dict()
            )
            raise WriteDenied(decision, request)
        if decision.shadow_violation:
            log.info(
                "workspace.write.shadow_violation policy=%s op=%s path=%s run=%s conv=%s",
                decision.policy, request.op, common["path"], common["run_id"], common["conv_id"],
            )
            events.emit(
                events.FileGuardEvent(
                    kind=events.GUARD_SHADOW_VIOLATION,
                    text=decision.reason,
                    details=dict(decision.details),
                    **common,
                ).to_dict()
            )
        return decision

    @staticmethod
    def _read_text(full: str) -> tuple[str, bool]:
        if not os.path.isfile(full):
            return "", False
        try:
            if os.path.getsize(full) > TEXT_READ_MAX_BYTES:
                return "", True
        except OSError:
            return "", True
        with open(full, encoding="utf-8", errors="replace") as f:
            return f.read(), True

    @staticmethod
    def _atomic_write(full: str, content: str) -> None:
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=parent or None,
            prefix=f".{os.path.basename(full)}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            os.replace(tmp_name, full)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _journal_and_notify(self, record: WriteRecord, result: WriteResult) -> None:
        try:
            info = self._journal.record(record) or {}
            result.changeset = {k: v for k, v in info.items() if k != "warning"}
            warning = str(info.get("warning") or "")
            if warning:
                result.warning = warning
        except Exception:  # noqa: BLE001 - the file already landed; never undo silently
            log.warning("workspace journal failed for %s", record.path, exc_info=True)
            result.warning = (result.warning + " " if result.warning else "") + (
                "Change journal unavailable: this edit was applied but not ledgered."
            )
        for observer in list(self._observers):
            try:
                observer.on_write(record, result)
            except Exception:  # noqa: BLE001 - observers are UI glue
                log.warning("workspace observer %r failed", observer, exc_info=True)
        try:
            from frontend.ui_web.project_files import _invalidate_file_paths_cache

            _invalidate_file_paths_cache()
        except Exception:
            pass

    # -- locks -------------------------------------------------------------

    def _lock_for(self, canonical: str) -> threading.Lock:
        key = canonical.lower()
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _lock_many(self, paths: Iterable[str]) -> _MultiLock:
        keys = sorted({p.lower() for p in paths if p})
        return _MultiLock([self._lock_for(k) for k in keys])


class _MultiLock:
    """Acquire several locks in a stable order (sorted keys) to avoid deadlocks."""

    def __init__(self, locks: list[threading.Lock]) -> None:
        self._locks = locks

    def __enter__(self) -> None:
        for lock in self._locks:
            lock.acquire()

    def __exit__(self, *exc: object) -> None:
        for lock in reversed(self._locks):
            lock.release()
