"""The write pipeline: the only code allowed to mutate files in the user's UEFN project.

Stages (see ``docs/architecture/write-pipeline.md``):

1. resolve   - confine the path to the project and apply the shared path guards
2. identity  - who is writing (``RunContext`` → ``DUCKY_*`` env → the user)
3. policy    - ``WritePolicy`` chain; a denial raises before any disk change
4. lock      - thread and OS file locks per absolute path; optional compare-and-swap
5. read      - previous content and hash
6. write     - temp file + ``os.replace`` (atomic)
7. journal   - ``ChangeJournal.record`` (never fails a landed write)
8. notify    - ``WriteObserver`` fan-out (file history, editor sync, UI)

Rename/move/delete keep their filesystem step with the caller (``perform``); the
pipeline still owns policy, locking, journal and notification for them.
"""

from __future__ import annotations

import errno
import hashlib
import logging
import os
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol, runtime_checkable

from backend.workspace import events, identity
from backend.workspace.diff_workers import line_delta
from backend.workspace.identity import RunContext
from backend.workspace.journal import ChangeJournal, NullJournal
from backend.workspace.paths import (
    content_hash,
    island_root,
    normalize_rel,
    rel_from_root,
    require_not_digest_path,
    require_writable_folder_path,
    require_writable_project_path,
)
from backend.workspace.policy import Decision, WritePolicy, WriteRequest, evaluate

log = logging.getLogger(__name__)


def _is_folder_project_root(root: str) -> bool:
    """True only for a folder project the user added (never an island or an unknown root).

    Any failure answers False, which keeps the stricter island rules.
    """
    try:
        from frontend.project_kind import is_saved_folder_project

        return is_saved_folder_project(root)
    except Exception:
        return False


def _is_lent_folder(root: str) -> bool:
    """A folder the workflow step this agent is doing lends it (backend.workspace.run_folders)."""
    try:
        from backend.workspace.run_folders import is_granted_root

        return is_granted_root(root)
    except Exception:
        return False


def _require_writable(full: str, root: str) -> None:
    # A lent folder is plain files, like a folder project: never the island rules.
    if _is_folder_project_root(root) or _is_lent_folder(root):
        require_writable_folder_path(full, root)
    else:
        require_writable_project_path(full)


def _beside_content(rel: str, root: str) -> str:
    """``.ducky/...`` under a ``…/Content`` root becomes the island's own ``.ducky``: UEFN
    submits everything inside Content, so Ducky's notes must never land there."""
    if root and os.path.basename(os.path.realpath(root)).lower() == "content" and \
            (rel == ".ducky" or rel.startswith(".ducky/")):
        return os.path.join(island_root(root), rel)
    return rel

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


def _in_own_sandbox(paths: tuple[str, ...], conv_id: str) -> bool:
    try:
        from backend.sandbox.paths import is_sandbox_path

        return bool(paths) and all(is_sandbox_path(p, conv_id) for p in paths if p)
    except Exception:
        return False


def _ask_first(request: WriteRequest) -> None:
    """"Ask before changes" for a coding agent that changes files through Ducky's tools
    (Codex, Claude Code, Cursor): it waits for the person's OK on a card in its chat.
    Asked before the file lock, so nothing else waits on the person. Ducky's own agent
    asks before it calls the tool, so it is never asked twice."""
    ctx = request.ctx
    if ctx is None or ctx.source != identity.SOURCE_AGENT or not ctx.conv_id:
        return
    if _in_own_sandbox(request.paths, ctx.conv_id):
        return  # the agent's own scratch space: never a card, like a scratch folder
    agent = (ctx.coding_agent or "ducky").strip().lower()
    if agent == "ducky":
        return
    try:
        from backend.tools.panel import permission_prompt as prompts

        if prompts.permission_mode(ctx.conv_id) != prompts.MODE_ASK:
            return
        approved = prompts.approve_agent_change(ctx.conv_id, agent, request.op, request.paths)
    except Exception:  # noqa: BLE001 - the person asked to be asked: never write unasked
        log.warning("approval card for %s failed", request.paths[-1:], exc_info=True)
        approved = False
    if not approved:
        raise WriteDenied(
            Decision(
                allow=False,
                reason="The person did not approve this change (the chat is set to Ask before changes).",
                policy="ask_before_changes",
            ),
            request,
        )


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
        other_roots: Callable[[], Iterable[str]] = tuple,
    ) -> None:
        self._root_resolver = root_resolver
        self._path_resolver = path_resolver
        # The person's other Ducky projects: a path in one is written and journaled there.
        self._other_roots = other_roots
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
        request = WriteRequest(op=op, paths=(canonical,), tool=tool, ctx=ctx, other_project=self._other_project(root))
        _ask_first(request)
        with self._lock_many((full,)):
            if tool.startswith("workspace_") or (ctx is not None and ctx.source == identity.SOURCE_AGENT):
                from backend.workspace.ai_ignore import require_ai_mutation
                require_ai_mutation(os.path.join(root, normalize_rel(rel)))
                require_ai_mutation(full)
            decision = self._decide(request)
            before, existed = self._read_text(full)
            if op == "create" and existed:
                raise ValueError(f"Already exists: {canonical}")
            before_hash = content_hash(before) if existed else ""
            if expected_hash is not None and before_hash != expected_hash:
                raise StaleWrite(canonical, expected_hash, before_hash)
            added, removed = line_delta(before, content)
            # A cancelled/failed comparison must not leave an already-written
            # file without its change record.
            self._atomic_write(
                full, content,
                ai=tool.startswith("workspace_") or (ctx is not None and ctx.source == identity.SOURCE_AGENT),
            )
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

    def preflight_paths(self, op: str, paths: Iterable[str], *, tool: str = "") -> Decision:
        """Check every canonical path before a composite filesystem operation.

        This reserves nothing: each actual operation must still run its own
        policy check. It prevents a known later denial from partially applying
        an otherwise permitted earlier step, not concurrent policy changes.
        """
        if op not in PATH_OPS:
            raise ValueError(f"Unknown path op: {op!r}")
        resolved = [self._resolve(path) for path in paths]
        if not resolved:
            raise ValueError("At least one path is required.")
        roots = {os.path.normcase(root) for _c, _f, root in resolved}
        if len(roots) > 1:
            raise ValueError("One operation stays inside one project.")
        return self._decide(WriteRequest(
            op=op, paths=tuple(c for c, _f, _r in resolved), tool=tool, ctx=identity.resolve_context(),
            other_project=self._other_project(resolved[0][2]),
        ))

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
            src_rel, _src_full, src_root = self._resolve(source)
            if os.path.normcase(src_root) != os.path.normcase(root):
                raise ValueError("Moving or copying between projects is not supported: both paths must be in one project.")
        policy_paths = (src_rel, dst_rel) if op in _SOURCE_MUTATED else (dst_rel,)
        lock_paths = (_src_full, dst_full) if src_rel else (dst_full,)
        ctx = identity.resolve_context()
        writer_meta = dict(writer) if writer is not None else identity.current_writer(tool=tool)
        request = WriteRequest(op=op, paths=policy_paths, tool=tool, ctx=ctx, other_project=self._other_project(root))
        _ask_first(request)
        with self._lock_many(lock_paths):
            if tool.startswith("workspace_") or (ctx is not None and ctx.source == identity.SOURCE_AGENT):
                from backend.workspace.ai_ignore import require_ai_mutation, require_ai_path_operation
                require_ai_mutation(os.path.join(root, normalize_rel(path)))
                if source:
                    require_ai_mutation(os.path.join(root, normalize_rel(source)))
                require_ai_path_operation(_src_full if source else dst_full, dst_full if source else None)
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
        root = self._active_root(rel)
        if root and os.path.basename(os.path.realpath(root)).lower() == "content" and rel.lower().startswith("content/"):
            rel = rel[8:]
        rel = _beside_content(rel, root)
        require_not_digest_path(rel)
        full = self._path_resolver(rel)
        require_not_digest_path(full)
        root = self._owning_root(full, root)
        _require_writable(full, root)
        canonical = rel_from_root(full, root)
        if not canonical:
            raise ValueError(f"Path is outside the active project: {rel_in!r}")
        return canonical, full, root

    def _active_root(self, rel: str) -> str:
        """The open project, or "" for a full path when none is open: that path still
        reaches the project (or lent folder) that holds it."""
        try:
            return self._root_resolver()
        except ValueError:
            if os.path.isabs(rel) or (len(rel) > 2 and rel[1] == ":"):
                return ""
            raise

    def _owning_root(self, full: str, active: str) -> str:
        """The open project, or the person's other Ducky project that holds ``full``."""
        if active and rel_from_root(full, active):
            return active
        island = island_root(active) if active else ""
        if island and island != os.path.realpath(active) and rel_from_root(full, island):
            return island  # the island's .ducky beside Content
        try:
            others = list(self._other_roots())
        except Exception:  # noqa: BLE001 - no recent-project list: the open project only
            others = []
        for other in others:
            if other and rel_from_root(full, other):
                return other
        return active

    def _other_project(self, root: str) -> str:
        """``root`` when it is not the open project, else ""."""
        try:
            active = self._root_resolver()
        except ValueError:
            return root
        return "" if os.path.normcase(root) == os.path.normcase(active) else root

    def _resolve_for_read(self, rel_in: str) -> tuple[str, str, str]:
        rel = normalize_rel(rel_in)
        if not rel:
            raise ValueError("Path must not be empty.")
        from backend.workspace.ai_ignore import require_ai_access

        root = self._active_root(rel)
        rel = _beside_content(rel, root)
        ctx = identity.resolve_context()
        guarded = ctx is not None and ctx.source == identity.SOURCE_AGENT
        if guarded:
            require_ai_access(os.path.join(root, rel))
        full = self._path_resolver(rel)
        if guarded:
            require_ai_access(full)
        root = self._owning_root(full, root)
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
    def _atomic_write(full: str, content: str, *, ai: bool = False) -> None:
        parent = os.path.dirname(full)
        tmp_name = os.path.join(parent, f".{os.path.basename(full)}.{uuid.uuid4().hex}.tmp")
        if ai:
            from backend.workspace.ai_ignore import require_ai_mutation
            require_ai_mutation(tmp_name)
        if parent:
            os.makedirs(parent, exist_ok=True)
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(content)
            # Windows readers (including scanners) can briefly deny replacement.
            # Keep the caller's thread/OS CAS locks throughout the retry: the
            # bytes checked above must still be the bytes this write replaces.
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    os.replace(tmp_name, full)
                    break
                except OSError as exc:
                    if (os.name != "nt" or getattr(exc, "winerror", None) not in (5, 32, 33)
                            or time.monotonic() >= deadline):
                        raise
                    time.sleep(0.01)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _journal_and_notify(self, record: WriteRecord, result: WriteResult) -> None:
        try:
            from backend.sandbox.paths import is_sandbox_path

            if is_sandbox_path(record.abs_path or record.project_root):
                return  # sandbox scratch is not a project change: no Changes tab, no file-tree push
        except Exception:
            pass
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
        key = os.path.normcase(canonical)
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    @contextmanager
    def _lock_many(self, paths: Iterable[str]):
        # Absolute real paths make window/tool-server roots and aliases agree.
        keys = sorted({os.path.normcase(os.path.realpath(p)) for p in paths if p})
        with ExitStack() as stack:
            for key in keys:
                stack.enter_context(self._lock_for(key))
                stack.enter_context(_process_file_lock(key))
            yield


@contextmanager
def _process_file_lock(path: str):
    """Lock a stable sidecar, never the target inode replaced by atomic writes.

    Sidecars stay on disk: unlinking them could split waiters across two locks.
    Closing the handle (including process death) releases the OS lock.
    """
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    lock_dir = os.path.join(base, "UEFN-Ducky", "locks")
    os.makedirs(lock_dir, exist_ok=True)
    name = hashlib.sha256(path.encode("utf-8")).hexdigest() + ".lock"
    with open(os.path.join(lock_dir, name), "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            deadline = time.monotonic() + 60
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise OSError(f"Cannot acquire write lock for {path}: {exc}") from exc
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"Write refused: timed out waiting for file lock: {path}") from exc
                    time.sleep(0.01)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
