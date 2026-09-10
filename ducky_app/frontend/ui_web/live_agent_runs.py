"""Live changeset run ids without importing the agent stack.

``list_changesets`` runs on the pywebview UI thread. Importing ``agent_modes``
there stalls the panel. This module is a few lines so orphan-close can stay
synchronous and still skip runs whose agent thread is alive.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_ids: frozenset[str] = frozenset()
# ponytail: one live writer. Two ducks editing at once attributes to the last
# starter. Upgrade: map run_id → writer.
_writer: dict[str, Any] | None = None
_writer_run_id = ""
_writer_until = 0.0

#: Claude Code / Codex Edit lands on disk before the run is journaled. The
#: watcher must still pin those writes to this duck for a short time after stop.
_GRACE_S = 90.0


def get_live_run_ids() -> frozenset[str]:
    with _lock:
        return _ids


def add_live_run_id(run_id: str) -> None:
    rid = (run_id or "").strip()
    if not rid:
        return
    global _ids
    with _lock:
        _ids = _ids | {rid}


def set_live_writer(run_id: str, writer: dict[str, Any] | None) -> None:
    """Remember who is running so disk edits (Claude Code Edit) are not 'You'."""
    rid = (run_id or "").strip()
    if not rid:
        return
    add_live_run_id(rid)
    payload = dict(writer or {})
    payload["run_id"] = rid
    payload.setdefault("source", "agent")
    global _writer, _writer_run_id, _writer_until
    with _lock:
        _writer = payload
        _writer_run_id = rid
        _writer_until = 0.0


def recent_agent_writer(*, now: float | None = None, grace_s: float = _GRACE_S) -> dict[str, Any] | None:
    """Writer for a live run, or the one that just finished (native Edit lag)."""
    ts = time.time() if now is None else now
    with _lock:
        if _writer is None:
            return None
        if _writer_run_id in _ids:
            return dict(_writer)
        if _writer_until and ts <= _writer_until:
            return dict(_writer)
        return None


def discard_live_run_id(run_id: str) -> None:
    rid = (run_id or "").strip()
    if not rid:
        return
    global _ids, _writer_until
    with _lock:
        _ids = _ids - {rid}
        if rid == _writer_run_id:
            _writer_until = time.time() + _GRACE_S


def reset_for_tests() -> None:
    global _ids, _writer, _writer_run_id, _writer_until
    with _lock:
        _ids = frozenset()
        _writer = None
        _writer_run_id = ""
        _writer_until = 0.0
