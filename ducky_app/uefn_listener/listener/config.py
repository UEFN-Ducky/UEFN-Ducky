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
HTTP_TIMEOUT_SEC = 30.0
POLL_INTERVAL_SEC = 0.02
STALE_CLEANUP_SEC = 60.0
# If in_flight is set but the tick is not dispatching and the queue is empty for this long,
# clear the flag (HTTP client timed out / orphaned accept). Does not interrupt a running command.
STUCK_INFLIGHT_SEC = 45.0
LOG_RING_SIZE = 200


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
