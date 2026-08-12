"""Listener tuning constants (single place)."""

from __future__ import annotations

import os

PROTOCOL_VERSION = "0.4.0"
# When no env port: scan this inclusive range (multi-instance / default Epic workflow).
_FALLBACK_PORT_LO = 4200
_FALLBACK_PORT_HI = 4267
# Reference architecture: max commands drained per editor tick.
_TICK_BATCH_DEFAULT = 5
_TICK_BATCH_MAX = 32
# Editor-mutating commands (wiring, spawn, save) — max per tick to keep Slate responsive.
_HEAVY_TICK_DEFAULT = 1
_HEAVY_TICK_MAX = 8
# Wall-clock budget for draining the command queue inside one Slate tick.
# Always process at least one command; stop before starting the next once spent.
_TICK_BUDGET_MS_DEFAULT = 8
_TICK_BUDGET_MS_MAX = 50
HTTP_TIMEOUT_SEC = 30.0
POLL_INTERVAL_SEC = 0.02
STALE_CLEANUP_SEC = 60.0
# If in_flight is set but the tick is not dispatching and the queue is empty for this long,
# clear the flag (HTTP client timed out / orphaned accept). Does not interrupt a running command.
STUCK_INFLIGHT_SEC = 45.0
# Refresh cached project identity for GET health this often (game-thread only).
PROJECT_CACHE_REFRESH_SEC = 60.0
LOG_RING_SIZE = 200
COMMAND_TIMINGS_RING = 100


def tick_batch_limit() -> int:
    """Hard cap on MCP commands processed per editor tick (env: UEFN_DUCKY_TICK_BATCH)."""
    raw = os.environ.get("UEFN_DUCKY_TICK_BATCH", "").strip()
    if raw.isdigit():
        return max(1, min(_TICK_BATCH_MAX, int(raw)))
    return _TICK_BATCH_DEFAULT


def heavy_tick_limit() -> int:
    """Max editor-mutating commands per tick (env: UEFN_DUCKY_HEAVY_TICK)."""
    raw = os.environ.get("UEFN_DUCKY_HEAVY_TICK", "").strip()
    if raw.isdigit():
        return max(1, min(_HEAVY_TICK_MAX, int(raw)))
    return _HEAVY_TICK_DEFAULT


def tick_budget_ms() -> int:
    """Max wall-clock ms to spend draining MCP commands per tick (env: UEFN_DUCKY_TICK_BUDGET_MS)."""
    raw = os.environ.get("UEFN_DUCKY_TICK_BUDGET_MS", "").strip()
    if raw.isdigit():
        return max(1, min(_TICK_BUDGET_MS_MAX, int(raw)))
    return _TICK_BUDGET_MS_DEFAULT


def should_stop_tick_drain(
    *,
    processed: int,
    batch_limit: int,
    elapsed_ms: float,
    budget_ms: float,
) -> bool:
    """Pure gate for the tick drain loop (unit-tested without Unreal).

    Always allow the first command; stop before starting another once the batch
    or wall-clock budget is spent.
    """
    if processed >= batch_limit:
        return True
    if processed > 0 and elapsed_ms >= budget_ms:
        return True
    return False


# Backward-compatible name for imports
TICK_BATCH_LIMIT = tick_batch_limit()


def listener_bind_port_range() -> tuple[int, int]:
    """
    Port(s) the HTTP listener may bind.

    If ``UEFN_DUCKY_LISTENER_PORT`` or ``UEFN_DUCKY_PORT`` is set (e.g. by ``init_unreal.py``
    from the panel Deploy), only that port is tried. Otherwise scan ``_FALLBACK_PORT_LO``..``_FALLBACK_PORT_HI``.
    """
    for key in ("UEFN_DUCKY_LISTENER_PORT", "UEFN_DUCKY_PORT"):
        raw = os.environ.get(key, "").strip()
        if raw.isdigit():
            p = int(raw)
            if 1 <= p <= 65535:
                return (p, p)
    return (_FALLBACK_PORT_LO, _FALLBACK_PORT_HI)


def listener_port_hint() -> int:
    """Preferred port for UI defaults (env or 4200)."""
    lo, hi = listener_bind_port_range()
    if lo == hi:
        return lo
    return _FALLBACK_PORT_LO
