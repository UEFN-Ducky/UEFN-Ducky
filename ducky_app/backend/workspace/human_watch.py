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
from backend.workspace.journal import FileChangeJournal, OUTCOME_OK
from backend.workspace.paths import content_hash, island_root, line_delta, normalize_rel, rel_from_root
from backend.workspace.policy import ALLOW
from backend.workspace.writer import TEXT_READ_MAX_BYTES, WriteRecord

log = logging.getLogger(__name__)

# Island Verse only. `.ducky/**` is Ducky (plans, tests) — never a You row.
WATCH_REL = ("Content/Verse",)
WATCH_EXT = {".verse", ".json", ".md", ".txt", ".csv"}
SKIP_DIR = frozenset({"Generated", "_INT", "__ExternalActors__", "__ExternalObjects__"})
POLL_S = 2.0
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

    def loop(self, poll_s: float) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                log.debug("human watch tick failed", exc_info=True)
            time.sleep(max(0.2, float(poll_s)))

    def tick(self) -> None:
        writer, journal, root = _runtime()
        if writer is None or journal is None or not root:
            return
        island = Path(island_root(root))
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
            _save_saved(storage, self._seen)

    def _scan(self, island: Path) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        island_s = str(island)
        for rel_root in WATCH_REL:
            base = island / rel_root.replace("/", os.sep)
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [
                    name for name in dirnames
                    if name not in SKIP_DIR and not name.startswith(".")
                ]
                for name in filenames:
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in WATCH_EXT:
                        continue
                    full = os.path.join(dirpath, name)
                    rel = _canon_watch_key(rel_from_root(full, island_s) or "")
                    if not rel:
                        continue
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    if st.st_size > TEXT_READ_MAX_BYTES:
                        continue
                    try:
                        with open(full, encoding="utf-8", errors="replace", newline="") as handle:
                            body = handle.read()
                    except OSError:
                        continue
                    found[rel] = {
                        "hash": content_hash(body),
                        "content": body,
                        "mtime_ns": int(st.st_mtime_ns),
                        "size": int(st.st_size),
                        "full": full,
                    }
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
