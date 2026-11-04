"""What Ducky sends into UEFN's listener, and what the editor tick costs.

Everything a Ducky process sends here grows with the agent count: heartbeat GETs,
a health GET before every POST, 503 busy retries. The tick handler runs every editor
frame and shares the GIL with the HTTP threads. These counters show both:

* per minute, for the last ``WINDOW_MIN`` minutes, per client (``X-Ducky-Client``
  header, else the peer port): GET, POST, 503 busy, bytes out
* per minute: tick handler time (avg / p95 / max ms) and the gap between ticks
  (the frame time; a long gap is a hitch)

GET ``/`` carries ``summary()``; the ``listener_traffic`` command returns ``table()``.
Cheap enough for every frame: one lock, a few adds. No ``unreal`` import.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from listener.dispatch import register

WINDOW_MIN = 10
CLIENT_HEADER = "X-Ducky-Client"
# Tick samples kept per minute for the p95 (60 fps is 3,600; the rest only count).
_TICK_SAMPLES_PER_MIN = 4000
_TOP_CLIENTS = 5


def client_key(headers: Any, peer: Any) -> str:
    named = str((headers.get(CLIENT_HEADER) if headers is not None else "") or "").strip()
    if named:
        return named[:64]
    try:
        return f"port:{int(peer[1])}"
    except (TypeError, ValueError, IndexError):
        return "unknown"


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))]


class _Minute:
    __slots__ = ("clients", "ticks", "tick_count", "tick_sum", "tick_max", "gap_max")

    def __init__(self) -> None:
        self.clients: dict[str, dict[str, int]] = {}
        self.ticks: list[float] = []
        self.tick_count = 0
        self.tick_sum = 0.0
        self.tick_max = 0.0
        self.gap_max = 0.0


class Traffic:
    def __init__(self, clock: Callable[[], float] = time.time, window_min: int = WINDOW_MIN) -> None:
        self._clock = clock
        self.window_min = int(window_min)
        self._lock = threading.Lock()
        self._minutes: dict[int, _Minute] = {}
        self._last_tick_at: float | None = None

    def _bucket_locked(self, now: float) -> _Minute:
        minute = int(now // 60)
        bucket = self._minutes.get(minute)
        if bucket is None:
            bucket = self._minutes[minute] = _Minute()
            for old in [m for m in self._minutes if m <= minute - self.window_min]:
                del self._minutes[old]
        return bucket

    def record_request(self, client: str, method: str, status: int, bytes_out: int) -> None:
        now = self._clock()
        with self._lock:
            row = self._bucket_locked(now).clients.setdefault(
                client or "unknown", {"GET": 0, "POST": 0, "busy_503": 0, "bytes_out": 0}
            )
            if method in ("GET", "POST"):
                row[method] += 1
            if status == 503:
                row["busy_503"] += 1
            row["bytes_out"] += max(0, int(bytes_out))

    def record_tick(self, started_at: float, duration_ms: float) -> None:
        """One tick_handler run: when it started (clock seconds) and how long it took."""
        with self._lock:
            bucket = self._bucket_locked(started_at)
            if self._last_tick_at is not None:
                bucket.gap_max = max(bucket.gap_max, (started_at - self._last_tick_at) * 1000.0)
            self._last_tick_at = started_at
            bucket.tick_count += 1
            bucket.tick_sum += duration_ms
            bucket.tick_max = max(bucket.tick_max, duration_ms)
            if len(bucket.ticks) < _TICK_SAMPLES_PER_MIN:
                bucket.ticks.append(duration_ms)

    def _live_locked(self) -> list[tuple[int, _Minute]]:
        floor = int(self._clock() // 60) - self.window_min
        return sorted((m, b) for m, b in self._minutes.items() if m > floor)

    def summary(self) -> dict[str, Any]:
        """Totals over the window (small: rides on every GET /)."""
        with self._lock:
            live = self._live_locked()
            totals = {"GET": 0, "POST": 0, "busy_503": 0, "bytes_out": 0}
            clients: set[str] = set()
            ticks: list[float] = []
            gap = tick_max = 0.0
            for _, bucket in live:
                for name, row in bucket.clients.items():
                    clients.add(name)
                    for key in totals:
                        totals[key] += row[key]
                ticks.extend(bucket.ticks)
                gap = max(gap, bucket.gap_max)
                tick_max = max(tick_max, bucket.tick_max)
        return {"window_min": self.window_min, **totals, "clients": len(clients),
                "tick_ms_p95": round(_p95(ticks), 2), "tick_ms_max": round(tick_max, 2),
                "frame_gap_ms_max": round(gap, 1)}

    def table(self) -> dict[str, Any]:
        """Per minute: requests by client (top senders first) and tick timings."""
        with self._lock:
            live = self._live_locked()
            per_min = []
            ticks: list[float] = []
            gap = 0.0
            for minute, bucket in live:
                rows = bucket.clients
                sent = {name: row["GET"] + row["POST"] for name, row in rows.items()}
                per_min.append({
                    "minute": minute * 60,
                    "GET": sum(r["GET"] for r in rows.values()),
                    "POST": sum(r["POST"] for r in rows.values()),
                    "busy_503": sum(r["busy_503"] for r in rows.values()),
                    "bytes_out": sum(r["bytes_out"] for r in rows.values()),
                    "top": [[name, n] for name, n in sorted(sent.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_CLIENTS]],
                    "clients": {name: dict(row) for name, row in rows.items()},
                    "ticks": bucket.tick_count,
                    "tick_ms_avg": round(bucket.tick_sum / bucket.tick_count, 2) if bucket.tick_count else 0.0,
                    "tick_ms_p95": round(_p95(bucket.ticks), 2),
                    "tick_ms_max": round(bucket.tick_max, 2),
                    "frame_gap_ms_max": round(bucket.gap_max, 1),
                })
                ticks.extend(bucket.ticks)
                gap = max(gap, bucket.gap_max)
        return {"window_min": self.window_min, "per_min": per_min,
                "tick_ms_p95": round(_p95(ticks), 2), "frame_gap_ms_max": round(gap, 1)}


TRAFFIC = Traffic()


@register("listener_traffic")
def cmd_listener_traffic() -> dict:
    """Requests per client per minute (last 10 min) and editor tick timings."""
    return TRAFFIC.table()
