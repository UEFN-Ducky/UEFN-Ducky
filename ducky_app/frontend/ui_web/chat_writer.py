"""One thread writes every chat save (D12, D23).

Many threads each ran their own ``BEGIN IMMEDIATE`` save and queued on the write lock
and the GIL. :meth:`ChatWriter.submit` puts the chat's latest copy in a pending map and
returns a future; the ``chat-writer`` thread merges saves of the same chat (latest wins)
and commits them in ONE transaction every ``interval`` (at once when ``batch_max`` chats
wait or a flush asks). Reads look in :meth:`ChatWriter.peek` first, so a load right after
a save sees it. A metadata save equal to the last one written is skipped, and the writer
keeps ``ducky.db-wal`` small: PASSIVE after a big batch, TRUNCATE after a quiet spell.

In-reply checkpoints (:meth:`ChatWriter.submit_checkpoint`) ride the same queue, one
entry per (chat, run), latest wins. The queue keeps submit order, so a checkpoint
queued before a save of its chat is written before it and never lands over it.
"""

from __future__ import annotations

import atexit
import contextlib
import hashlib
import itertools
import json
import logging
import threading
import time
from concurrent.futures import Future, wait as wait_futures
from typing import Any, Callable

_log = logging.getLogger(__name__)

INTERVAL_S = 0.1
BATCH_MAX = 50
# A batch that wrote more than this many pages is checkpointed (PASSIVE) right away.
BIG_BATCH_PAGES = 1000
PAGE_BYTES = 4096
# No writes for this long: TRUNCATE the WAL back to nothing (db.WAL_SIZE_LIMIT only
# trims it when a checkpoint completes, so it sat at 64-101 MB).
IDLE_TRUNCATE_S = 30.0
TRUNCATE_BUSY_MS = 1000
_TRIES = 3
# Not part of "did this save change anything": the transcript (message saves are never
# skipped; the repo's row hashes already skip unchanged rows) and the touch timestamp.
_KEY_SKIP = ("messages", "updated")


def doc_key(project_id: str, doc: dict[str, Any]) -> str:
    """What a metadata conv_save writes, minus the timestamp: equal keys, equal rows."""
    body = {k: v for k, v in doc.items() if k not in _KEY_SKIP}
    raw = json.dumps([project_id, body], sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _trace(kind: str, name: str, ms: float = 0.0, **meta: Any) -> None:
    try:
        from frontend.perf_trace import trace

        trace(kind, name, ms, **meta)
    except Exception:
        pass


class ChatWriter:
    """``write(conv_id, item)`` saves one chat on the writer thread and may return the
    repo's result (its ``bytes`` count toward the big-batch checkpoint)."""

    def __init__(
        self,
        write: Callable[[str, Any], Any],
        *,
        write_checkpoint: Callable[[str, Any], Any] | None = None,
        connect: Callable[[], Any],  # db.connect: only backend/store imports sqlite3
        root: Callable[[], Any] = lambda: None,
        interval: float = INTERVAL_S,
        batch_max: int = BATCH_MAX,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._write = write
        self._write_checkpoint = write_checkpoint
        self._connect = connect
        self._root = root
        self._interval = interval
        self._batch_max = batch_max
        self._clock = clock
        self._cv = threading.Condition()
        # conv_id (a save) or (conv_id, run_id) (a checkpoint) -> [item, futures, database
        # root at submit, tries], in the order of each key's latest submit
        self._pending: dict[Any, list[Any]] = {}
        self._inflight: dict[Any, list[Any]] = {}
        self._now = False
        self._closed = False
        self._thread: threading.Thread | None = None
        self._atexit = False
        self._saved: dict[str, str] = {}
        self._last_write = clock()
        self._truncated = True
        self.transactions = 0
        self.skipped = 0
        self.checkpoints = {"PASSIVE": 0, "TRUNCATE": 0}

    # ------------------------------------------------------------------ callers

    def submit(self, conv_id: str, item: Any) -> Future:
        return self._enqueue(conv_id, item)

    def submit_checkpoint(self, conv_id: str, run_id: str, item: Any) -> Future:
        """Queue a run's in-flight row; a newer one of the same run replaces it."""
        return self._enqueue((conv_id, run_id), item)

    def _write_key(self, key: Any, item: Any) -> Any:
        if isinstance(key, tuple):
            return self._write_checkpoint(key[0], item)  # type: ignore[misc]
        return self._write(key, item)

    def _enqueue(self, key: Any, item: Any) -> Future:
        fut: Future = Future()
        if self._closed or threading.current_thread() is self._thread:
            try:  # after exit, or from inside a write: save here, as before
                self._write_key(key, item)
                fut.set_result(None)
            except Exception as exc:
                fut.set_exception(exc)
            return fut
        root = self._root()
        with self._cv:
            # Popped and put back: the key moves to the end, so the batch writes it after
            # everything queued before it (an older checkpoint never lands over a save).
            old = self._pending.pop(key, None)
            self._pending[key] = [item, (old[1] if old else []) + [fut], root, 0]
            if len(self._pending) >= self._batch_max:
                self._now = True
            self._start()
            self._cv.notify_all()
        return fut

    def peek(self, conv_id: str) -> Any | None:
        """The newest copy still waiting to be written, else None (read-your-writes)."""
        with self._cv:
            entry = self._pending.get(conv_id) or self._inflight.get(conv_id)
            return entry[0] if entry else None

    def checkpoint_waits(self, conv_id: str) -> bool:
        """A checkpoint of this chat is not written yet (a load flushes it first)."""
        with self._cv:
            return any(
                isinstance(key, tuple) and key[0] == conv_id
                for key in itertools.chain(self._pending, self._inflight)
            )

    def flush(self, conv_id: str | None = None, timeout: float = 30.0) -> bool:
        """Write what waits (one chat's or all) now; True once it is all written.
        Cheap when nothing waits."""
        if threading.current_thread() is self._thread:
            return False
        with self._cv:
            futures = [
                f
                for key, entry in itertools.chain(self._pending.items(), self._inflight.items())
                if conv_id is None or (key[0] if isinstance(key, tuple) else key) == conv_id
                for f in entry[1]
            ]
            if not futures:
                return True
            self._now = True
            self._cv.notify_all()
        _, not_done = wait_futures(futures, timeout)
        return not not_done

    def close(self, timeout: float = 10.0) -> None:
        """Exit: write everything, stop the thread; later saves write in the caller."""
        self.flush(timeout=timeout)
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    # ------------------------------------------------------------- unchanged saves

    def unchanged(self, conv_id: str, key: str, messages: Any) -> bool:
        """A metadata save equal to the last one written: skip it."""
        if messages is not None or self._saved.get(conv_id) != key:
            return False
        self.skipped += 1
        _trace("conv_save_skipped", conv_id)
        return True

    def remember(self, conv_id: str, key: str) -> None:
        self._saved[conv_id] = key

    def forget(self, conv_id: str | None = None) -> None:
        """Someone wrote the row directly: the next save writes, whatever its key."""
        if conv_id is None:
            self._saved.clear()
        else:
            self._saved.pop(conv_id, None)

    # ------------------------------------------------------------------- thread

    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="chat-writer", daemon=True)
            self._thread.start()
            if not self._atexit:
                atexit.register(self.close)
                self._atexit = True

    def _idle_wait(self) -> float | None:
        if self._truncated:
            return None
        return max(0.05, IDLE_TRUNCATE_S - (self._clock() - self._last_write))

    def _run(self) -> None:
        while True:
            with self._cv:
                if not self._pending and not self._closed:
                    self._cv.wait(self._idle_wait())
                if self._pending and not self._now and not self._closed:
                    self._cv.wait_for(lambda: self._now or self._closed, self._interval)
                batch, self._pending, self._now = self._pending, {}, False
                self._inflight = batch
                if not batch and self._closed:
                    return
            try:
                if batch:
                    self._commit(batch)
                else:
                    self.idle_checkpoint()
            except Exception:
                _log.exception("chat-writer loop failed")
            batch = {}  # written: don't keep its transcripts alive while idle

    def _commit(self, batch: dict[str, list[Any]]) -> None:
        from backend.store import db

        here = self._root()  # each conv_save's db.write_txn nests as a SAVEPOINT of the batch
        results: dict[str, BaseException | None] = {}
        retry: dict[str, list[Any]] = {}
        written = 0
        t0 = time.perf_counter()
        try:
            conn = self._connect()
            with db.write_txn(conn):
                for cid, entry in batch.items():
                    if entry[2] != here:  # a test repointed the app folder meanwhile
                        results[cid] = RuntimeError("the chat database changed before this save was written")
                        continue
                    try:
                        result = self._write_key(cid, entry[0])
                        written += int((result or {}).get("bytes", 0) or 0)
                        results[cid] = None
                    except Exception as exc:
                        _log.warning("chat save %s failed: %s", cid, exc)
                        results[cid] = exc
            self.transactions += 1
        except Exception as exc:  # BEGIN or COMMIT failed: nothing of the batch was written
            _log.warning("chat save batch of %d failed: %s", len(batch), exc)
            results = {}
            for cid, entry in batch.items():
                if entry[3] + 1 < _TRIES:
                    retry[cid] = entry
                else:
                    results[cid] = exc
        with self._cv:
            self._inflight = {}
            again: dict[Any, list[Any]] = {}
            for cid, entry in retry.items():
                newer = self._pending.get(cid)
                if newer is not None:
                    newer[1] = entry[1] + newer[1]
                else:
                    entry[3] += 1
                    again[cid] = entry
            self._pending = {**again, **self._pending}  # older than anything queued since
        if not retry:
            self._last_write = self._clock()
            self._truncated = False
            _trace("chat_writer_batch", str(len(batch)), (time.perf_counter() - t0) * 1000.0, bytes=written)
            if written // PAGE_BYTES > BIG_BATCH_PAGES:
                self._checkpoint(conn, "PASSIVE")  # before the futures: a flush returns after it
        for cid, exc in results.items():
            for fut in batch[cid][1]:
                if exc is None:
                    fut.set_result(None)
                else:
                    fut.set_exception(exc)
        if retry:
            time.sleep(self._interval)

    # ---------------------------------------------------------------------- WAL

    def idle_checkpoint(self) -> bool:
        """After IDLE_TRUNCATE_S with no writes, TRUNCATE the WAL once."""
        if self._truncated or self._clock() - self._last_write < IDLE_TRUNCATE_S:
            return False
        with self._cv:
            if self._pending:
                return False
        self._checkpoint(self._connect(), "TRUNCATE")
        self._truncated = True
        return True

    def _checkpoint(self, conn: Any, mode: str) -> None:
        from backend.store import db

        try:
            if mode == "TRUNCATE":
                conn.execute(f"PRAGMA busy_timeout={TRUNCATE_BUSY_MS}")
            conn.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
        except Exception as exc:  # sqlite3.Error, e.g. busy
            _log.debug("wal_checkpoint(%s) failed: %s", mode, exc)
        finally:
            if mode == "TRUNCATE":
                with contextlib.suppress(Exception):
                    conn.execute(f"PRAGMA busy_timeout={db.BUSY_TIMEOUT_MS}")
        self.checkpoints[mode] += 1
