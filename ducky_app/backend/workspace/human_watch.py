"""Watch island text files for edits Ducky did not make (Explorer, VS Code, UEFN).

Panel process only. Pipeline writes (agent / panel save / revert) are deduped
against the ledger index so they do not become a second "You" row.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from backend.workspace import identity
from backend.workspace.diff_workers import line_delta
from backend.workspace.journal import FileChangeJournal, OUTCOME_OK
from backend.workspace.paths import content_hash, island_root, normalize_rel
from backend.workspace.policy import ALLOW
from backend.workspace.writer import TEXT_READ_MAX_BYTES, WriteRecord

log = logging.getLogger(__name__)

# Island Verse only. `.ducky/**` is Ducky (plans, tests) — never a You row.
WATCH_REL = ("Content/Verse",)
WATCH_EXT = {".verse", ".json", ".md", ".txt", ".csv"}
SKIP_DIR = frozenset({"Generated", "_INT", "__ExternalActors__", "__ExternalObjects__"})
POLL_S = 2.0
NO_ISLAND_S = 30.0  # nothing to watch: look for an island this often
RESCAN_S = 60.0  # with change notices, still rescan this often in case one was missed
STABLE_POLLS = 2
TOMBSTONE_S = 10.0
RECENT_WINDOW_S = 60.0

_INDEX_NAME = "human_index.json"

_started = False
_lock = threading.Lock()


def start(*, poll_s: float = POLL_S) -> None:
    """Start the panel watcher once. No-op if the journal is off or already running."""
    global _started
    with _lock:
        if _started:
            return
        try:
            from frontend.settings import PanelSettings

            if not bool(getattr(PanelSettings.load(), "changeset_journal_enabled", True)):
                return
        except Exception:
            pass
        watcher = HumanWatch()
        thread = threading.Thread(
            target=watcher.loop,
            args=(poll_s,),
            daemon=True,
            name="changeset-human-watch",
        )
        thread.start()
        _started = True


def reset_for_tests() -> None:
    global _started
    with _lock:
        _started = False


class HumanWatch:
    def __init__(self) -> None:
        self._seen: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._seeded = False
        # rel -> (mtime_ns, size, body, hash): lets _scan skip re-reading files
        # that have not been touched since the previous poll.
        self._reads: dict[str, tuple[int, int, str, str]] = {}
        self._island = ""
        self._open = False  # the last tick had an island to watch
        self._notifier: Any = None
        self._notify_dir = ""
        self._stop = threading.Event()

    def loop(self, poll_s: float) -> None:
        poll_s = max(0.2, float(poll_s))
        try:
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception:
                    log.debug("human watch tick failed", exc_info=True)
                self._wait(poll_s)
        finally:
            self._set_notifier("")

    def _wait(self, poll_s: float) -> None:
        """Sleep until the next tick: until the island changes (Windows change notice),
        POLL_S while an edit settles or without notices, NO_ISLAND_S with no island.
        A blind rescan every POLL_S cost ~0.12 ms per watched file (Oct 29 2026 profile)."""
        if not self._open:
            self._set_notifier("")
            self._stop.wait(NO_ISLAND_S)
            return
        # ponytail: watches the first WATCH_REL root only; one notifier per root if more are added.
        notifier = self._set_notifier(os.path.join(self._island, WATCH_REL[0].replace("/", os.sep)))
        if notifier is None or self._pending:
            self._stop.wait(poll_s)
            return
        deadline = time.monotonic() + RESCAN_S
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                if notifier.wait(min(remaining, poll_s)):
                    return
            except OSError:
                self._set_notifier("")  # retried next wait; until then the plain poll
                return

    def _set_notifier(self, path: str) -> Any:
        """The change notifier for *path* ("" closes it); None when it can't watch there."""
        if path == self._notify_dir and self._notifier is not None:
            return self._notifier
        if self._notifier is not None:
            self._notifier.close()
        self._notifier, self._notify_dir = None, path
        if path:
            self._notifier = _notifier_for(path)
        return self._notifier

    def tick(self) -> None:
        writer, journal, root = _runtime()
        if writer is None or journal is None or not root:
            self._open = False
            return
        self._open = True
        island = Path(island_root(root))
        if str(island) != self._island:
            # The panel switched projects. Diffing the new island against the old
            # one's file map turned every file of both into a "You" row, so start
            # over from the new island's own saved index.
            self._island = str(island)
            self._seen, self._pending, self._reads = {}, {}, {}
            self._seeded = False
        storage = _storage(journal, root)
        if not self._seeded:
            self._seen = _canon_seen(_load_saved(storage))
            self._seeded = True
            if not self._seen:
                self._seen = self._scan(island)
                for row in self._seen.values():
                    text = str(row.get("content") or "")
                    if text:
                        try:
                            journal.remember_blob(text, project_root=root)
                        except Exception:
                            pass
                _save_saved(storage, self._seen)
                return
        self._seen = _canon_seen(self._seen)
        current = self._scan(island)
        now = time.time()
        paths = set(self._seen) | set(current) | set(self._pending)
        committed = False
        try:
            for rel in paths:
                old = self._seen.get(rel)
                new = current.get(rel)
                fp = _fp(new)
                pending = self._pending.get(rel)
                if _same(old, new) and pending is None:
                    continue
                if pending is None or pending.get("fp") != fp:
                    self._pending[rel] = {"fp": fp, "polls": 1, "new": new, "old": old}
                    continue
                pending["polls"] = int(pending.get("polls") or 0) + 1
                pending["new"] = new
                if int(pending["polls"]) < STABLE_POLLS:
                    continue
                self._pending.pop(rel, None)
                self._commit(journal, writer, root, rel, old, new, now)
                if new is None:
                    self._seen.pop(rel, None)
                else:
                    self._seen[rel] = new
                committed = True
        finally:
            # One index write per poll, not one full rewrite per changed file.
            if committed:
                _save_saved(storage, self._seen)

    def _scan(self, island: Path) -> dict[str, dict[str, Any]]:
        """Current contents of every watched file, re-reading only what changed.

        This runs every POLL_S seconds for as long as the panel is open. It used
        to read every watched file in full on every pass, so an idle window with
        a normal island streamed megabytes a second off disk and spent most of
        its CPU in the kernel. A file whose mtime and size are both unchanged
        since the last pass has the same bytes for this watcher's purposes — an
        editor that writes a file always moves its mtime — so the previous
        body and hash are reused and the read is skipped entirely.
        """
        found: dict[str, dict[str, Any]] = {}
        island_s = str(island)
        cut = len(island_s.rstrip("\\/")) + 1
        for rel_root in WATCH_REL:
            base = island / rel_root.replace("/", os.sep)
            if not base.is_dir():
                continue
            stack = [str(base)]
            while stack:
                try:
                    entries = list(os.scandir(stack.pop()))
                except OSError:
                    continue
                for entry in entries:
                    name = entry.name
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if name not in SKIP_DIR and not name.startswith("."):
                                stack.append(entry.path)
                            continue
                    except OSError:
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in WATCH_EXT:
                        continue
                    full = entry.path
                    # The walk starts under the already-resolved island, so the path
                    # past it is the key. Resolving it with realpath cost two file
                    # handles per file per poll on Windows for the same answer.
                    rel = _canon_watch_key(full[cut:].replace(os.sep, "/"))
                    if not rel:
                        continue
                    try:
                        # On Windows this comes with the directory listing: no handle
                        # per file, which os.stat opened every poll.
                        st = entry.stat()
                    except OSError:
                        continue
                    if st.st_size > TEXT_READ_MAX_BYTES:
                        continue
                    mtime_ns = int(st.st_mtime_ns)
                    size = int(st.st_size)
                    cached = self._reads.get(rel)
                    if cached is not None and cached[0] == mtime_ns and cached[1] == size:
                        body, digest = cached[2], cached[3]
                    else:
                        try:
                            with open(full, encoding="utf-8", errors="replace", newline="") as handle:
                                body = handle.read()
                        except OSError:
                            continue
                        digest = content_hash(body)
                        self._reads[rel] = (mtime_ns, size, body, digest)
                    found[rel] = {
                        "hash": digest,
                        "content": body,
                        "mtime_ns": mtime_ns,
                        "size": size,
                        "full": full,
                    }
        for gone in set(self._reads) - set(found):
            self._reads.pop(gone, None)
        return found

    def _commit(
        self,
        journal: FileChangeJournal,
        writer: Any,
        root: str,
        rel: str,
        old: dict[str, Any] | None,
        new: dict[str, Any] | None,
        now: float,
    ) -> None:
        before = _read_known(journal, root, old)
        after = _read_file(new)
        before_hash = str((old or {}).get("hash") or (content_hash(before) if before else ""))
        after_hash = content_hash(after) if after else ""
        if new is not None:
            new["hash"] = after_hash
            new["content"] = after
            if after:
                try:
                    journal.remember_blob(after, project_root=root)
                except Exception:
                    pass
        if old is not None and not before_hash and before:
            before_hash = content_hash(before)
        if _pipeline_owns(journal, root, rel, before_hash, after_hash, new is None, now):
            return
        if before_hash == after_hash and bool(old) == bool(new):
            return
        if old is None and new is not None:
            op = "create"
        elif new is None:
            op = "delete"
        else:
            op = "write"
        added, removed = line_delta(before, after)
        record = WriteRecord(
            op=op,
            path=rel,
            from_path="",
            before=before,
            after=after,
            before_hash=before_hash,
            after_hash=after_hash,
            existed_before=old is not None,
            tool="external",
            writer=_watch_writer(),
            ctx=None,
            ts=now,
            lines_added=added,
            lines_removed=removed,
            decision=ALLOW,
            project_root=root,
            abs_path=str((new or old or {}).get("full") or ""),
            outcome=OUTCOME_OK,
        )
        journal.record(record)


class _DirNotifier:
    """FindFirstChangeNotification on one folder tree: wait() is True once something in it
    was created, deleted, renamed or written since the last True."""

    _FILTER = 0x1 | 0x2 | 0x8 | 0x10  # FILE_NAME | DIR_NAME | SIZE | LAST_WRITE

    def __init__(self, path: str) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)  # own instance: own argtypes
        k32.FindFirstChangeNotificationW.restype = wintypes.HANDLE
        k32.FindFirstChangeNotificationW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL, wintypes.DWORD]
        k32.FindNextChangeNotification.argtypes = [wintypes.HANDLE]
        k32.FindCloseChangeNotification.argtypes = [wintypes.HANDLE]
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k32.WaitForSingleObject.restype = wintypes.DWORD
        handle = k32.FindFirstChangeNotificationW(path, True, self._FILTER)
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        self._k32, self._handle = k32, handle

    def wait(self, timeout_s: float) -> bool:
        result = self._k32.WaitForSingleObject(self._handle, max(0, int(timeout_s * 1000)))
        if result == 0x102:  # WAIT_TIMEOUT
            return False
        if result != 0 or not self._k32.FindNextChangeNotification(self._handle):
            raise OSError("change notification failed")
        return True

    def close(self) -> None:
        if self._handle:
            self._k32.FindCloseChangeNotification(self._handle)
            self._handle = None


def _notifier_for(path: str) -> _DirNotifier | None:
    """A change notifier for *path*, or None (not Windows, no such folder): then the plain poll."""
    if os.name != "nt" or not os.path.isdir(path):
        return None
    try:
        return _DirNotifier(path)
    except OSError:
        return None


def _watch_writer() -> dict[str, Any]:
    """A live coding-agent run owns disk edits (Claude Code Edit), not You."""
    try:
        from frontend.ui_web.live_agent_runs import recent_agent_writer

        live = recent_agent_writer()
        if live and live.get("run_id"):
            out = dict(live)
            out["tool"] = "external"
            out.setdefault("source", identity.SOURCE_AGENT)
            return out
    except Exception:
        pass
    return identity.user_writer(tool="external")


def _runtime() -> tuple[Any, FileChangeJournal | None, str]:
    try:
        from backend.workspace.runtime import get_writer

        writer = get_writer()
        journal = writer.journal
        if not isinstance(journal, FileChangeJournal):
            return writer, None, ""
        return writer, journal, writer.root()
    except Exception:
        return None, None, ""


def _storage(journal: FileChangeJournal, root: str) -> Path:
    return journal._storage(root)  # noqa: SLF001 — same AppData ledger


def _watch_use_db() -> bool:
    from backend.store.switch import use_db

    return use_db("ledger")


def _load_saved(storage: Path) -> dict[str, dict[str, Any]]:
    if _watch_use_db():
        from backend.store.importers import phase3
        from backend.store.repos import ledger as repo

        phase3.ensure()
        raw: Any = repo.watch_get(storage.name)
    else:
        path = storage / _INDEX_NAME
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        rel = _canon_watch_key(str(key))
        if isinstance(value, dict):
            digest = str(value.get("hash") or "")
        else:
            digest = str(value or "")
        if rel and digest:
            out[rel] = {"hash": digest, "mtime_ns": 0, "size": 0, "full": ""}
    return out


def _save_saved(storage: Path, seen: dict[str, dict[str, Any]]) -> None:
    payload = {rel: str(row.get("hash") or "") for rel, row in seen.items() if row.get("hash")}
    if _watch_use_db():
        from backend.store.repos import ledger as repo

        repo.watch_replace(storage.name, payload)
        return
    tmp = storage / (_INDEX_NAME + ".tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    tmp.replace(storage / _INDEX_NAME)


def _canon_watch_key(rel: str) -> str:
    """Always island-relative. Old Content-rooted keys (`Verse/x`) become `Content/Verse/x`."""
    p = normalize_rel(rel)
    if not p or p.startswith(".ducky/") or p == ".ducky":
        return ""
    if p.lower().startswith("content/"):
        return p
    if p.lower().startswith("verse/") or p.lower().startswith("python/"):
        return f"Content/{p}"
    return p


def _canon_seen(seen: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for key, row in seen.items():
        rel = _canon_watch_key(key)
        if rel:
            out[rel] = row
    return out


def _fp(row: dict[str, Any] | None) -> str:
    # Hash only — UEFN/VS Code touch mtime without changing bytes; that used to
    # reset the stable-poll counter so real edits never committed.
    if row is None:
        return "missing"
    return str(row.get("hash") or "") or "empty"


def _same(old: dict[str, Any] | None, new: dict[str, Any] | None) -> bool:
    if old is None and new is None:
        return True
    if old is None or new is None:
        return False
    if old.get("hash") and new.get("hash"):
        return old["hash"] == new["hash"]
    return int(old.get("mtime_ns") or 0) == int(new.get("mtime_ns") or 0) and int(
        old.get("size") or 0
    ) == int(new.get("size") or 0)


def _read_file(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    full = str(row.get("full") or "")
    if not full or not os.path.isfile(full):
        return ""
    try:
        with open(full, encoding="utf-8", errors="replace", newline="") as handle:
            return handle.read()
    except OSError:
        return ""


def _read_known(journal: FileChangeJournal, root: str, old: dict[str, Any] | None) -> str:
    if not old:
        return ""
    cached = old.get("content")
    if isinstance(cached, str) and cached:
        return cached
    digest = str(old.get("hash") or "")
    if digest:
        text = journal._read_blob(journal._storage(root), digest)  # noqa: SLF001
        if text is not None:
            return text
    return _read_file(old)


def _pipeline_owns(
    journal: FileChangeJournal,
    root: str,
    rel: str,
    before_hash: str,
    after_hash: str,
    disappeared: bool,
    now: float,
) -> bool:
    stamp = journal.index_stamp(rel, project_root=root)
    if isinstance(stamp, dict):
        if disappeared and stamp.get("deleted"):
            age = now - float(stamp.get("ts") or 0)
            if age <= TOMBSTONE_S:
                return True
        want = after_hash if not disappeared else ""
        if want and str(stamp.get("hash") or "") == want:
            return True
        if disappeared and stamp.get("deleted"):
            return True
    if after_hash and journal.recent_after_hash(
        rel, after_hash, project_root=root, window_s=RECENT_WINDOW_S
    ):
        return True
    return False
