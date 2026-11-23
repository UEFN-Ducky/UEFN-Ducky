"""The line team-member turns start through, across every team (K3 seat queue).

Keeper wakes and plan dispatches ask ``request`` before starting a member's turn. There is no
cap by default: every seat starts (paced by the waves below). The card's "Run at once" slider
(``set_run_at_once``) sets an optional cap, any number; over it a seat waits in one FIFO line
and starts when a turn ends (``on_turn_end``). The running count is admission.py's member
slots plus seats this queue is starting. Free memory never holds a seat (Nov 5 2026):
admission.py only warns. ``pc_limit`` is only a hint for estimates, never a cap.

Safety: a watcher thread times its own short sleeps. When the app runs late (``LAG_HOLD_MS``
smoothed), new seats wait in line while at least one runs; running turns are never stopped,
so no work is lost. Once the lag falls back the line drains by itself.

First turns start in waves (``WAVE_SIZE`` per ``WAVE_GAP_S``): 3 of 14 seats opened with
"Ducky's tools didn't connect" when all started at once. A turn that ended that way never
really ran, so the seat is idle again (``is_busy`` False): a restart or recycle may go ahead.
Such a turn is started again once by itself; a second failure in a row tells the lead.

A cancelled, stopped or failed turn frees its slot at once (``on_turn_end``), and a slot whose
chat has run no agent for ``STALE_S`` is freed too: Oct 25 2026, a seat whose ping turn was
cancelled stayed "busy" and its plan assignment started nothing for over 3 minutes.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from frontend.ui_web import admission

PushFn = Callable[[dict[str, Any]], None]

WAVE_SIZE = 3
WAVE_GAP_S = 5.0
LAG_TICK_S = 0.25
LAG_HOLD_MS = 500.0  # ponytail: one threshold for every PC; tune if it holds a healthy app
STALE_S = 5.0
_ENDED_OK = frozenset({"", "done", "completed", "finished"})
_START_FAILED_RE = re.compile(
    r"tools didn.t connect|(?:backend|agent) (?:start error|failed to start)|could not start", re.I)


@dataclass
class _Seat:
    conv_id: str
    start: Callable[[], Any]


_lock = threading.RLock()
_line: deque[_Seat] = deque()
_starting: set[str] = set()  # handed to start(), not yet holding an admission slot
_user_cap: int | None = None
_seen: set[str] = set()  # seats whose first turn has started
_first_starts: deque[float] = deque()  # when first turns started, for the waves
_wave_timer = False
_starts: dict[str, Callable[[], Any]] = {}  # each seat's last start, for the one retry
_retried: set[str] = set()  # seats whose failed start was already retried once
_idle_since: dict[str, float] = {}  # admission slots whose chat runs no agent, since when
_now = time.monotonic
_lag_ms = 0.0  # how late the watcher's sleeps wake, smoothed
_watching = False


def _agent_running(conv_id: str) -> bool:
    try:
        from frontend.ui_web.agent_turns.state import is_agent_running

        return is_agent_running(conv_id)
    except Exception:
        return True  # can't tell: keep the slot


def _spawn(fn: Callable[[], None], name: str) -> None:
    threading.Thread(target=fn, daemon=True, name=name).start()


def _spawn_later(delay: float, fn: Callable[[], None]) -> None:
    timer = threading.Timer(delay, fn)
    timer.daemon = True
    timer.start()


def _notify_lead(conv_id: str, text: str) -> None:
    try:
        from backend.agent.a2a_client import send_notice
        from backend.tools.panel.permission_prompt import starter_root

        lead = starter_root(conv_id)
        if lead and lead != conv_id:
            send_notice(sender_conv_id=conv_id, receiver_conv_id=lead, body=text)
    except Exception:
        pass


def start_failed(detail: str) -> bool:
    """The turn ended before the agent really ran (its tools or its backend never came up)."""
    return bool(_START_FAILED_RE.search(detail or ""))


def total_memory_gb() -> float:
    if sys.platform.startswith("linux"):
        from frontend.low_memory import meminfo

        total = meminfo().get("MemTotal")
        if total:
            return total / 1024**3
    try:
        import ctypes

        kb = ctypes.c_ulonglong()
        if ctypes.WinDLL("kernel32").GetPhysicallyInstalledSystemMemory(ctypes.byref(kb)):
            return kb.value / 1024**2
    except (AttributeError, OSError):
        pass
    try:
        import psutil

        return psutil.virtual_memory().total / 1024**3
    except Exception:
        return 16.0


NO_CAP = 1_000_000


def pc_limit() -> int:
    """A hint for estimates only (one per two cores and per 2 GB of RAM, at least 2); never a cap."""
    # ponytail: fixed per-agent cost (half a core, 2 GB); measure real agent use if it misjudges.
    cores = os.cpu_count() or 4
    return max(2, min(cores // 2, int(total_memory_gb() // 2)))


def limit() -> int:
    return _user_cap or NO_CAP


def set_run_at_once(n: int | None) -> int:
    """The "Run at once" slider: an optional cap, any number (0 / None = no cap). Returns the cap."""
    global _user_cap
    with _lock:
        _user_cap = max(1, int(n)) if n else None
    drain()
    return limit()


def lagging() -> bool:
    return _lag_ms >= LAG_HOLD_MS


def _lag_sample(late_ms: float) -> None:
    """Fold one late wake into ``_lag_ms`` (rises at once, falls slowly); drain when it clears."""
    global _lag_ms
    was = lagging()
    _lag_ms = late_ms if late_ms > _lag_ms else _lag_ms * 0.8 + late_ms * 0.2
    if was and not lagging():
        drain()


def _watch_lag() -> None:
    # Event.wait and perf_counter, not time.sleep/monotonic: tests patch those process-wide
    # with fake clocks, and this thread would spin and push their clock forward.
    tick = threading.Event()
    while True:
        t0 = time.perf_counter()
        tick.wait(LAG_TICK_S)
        _lag_sample(max(0.0, (time.perf_counter() - t0 - LAG_TICK_S) * 1000))


def _ensure_watch() -> None:
    global _watching
    with _lock:
        if _watching:
            return
        _watching = True
    # Its own thread, not _spawn: tests run _spawn inline, and this loop never returns.
    threading.Thread(target=_watch_lag, name="seat-lag-watch", daemon=True).start()


def _running() -> set[str]:
    """Call under ``_lock``. Frees admission slots left by turns that ended unseen."""
    with admission._lock:
        held = set(admission._running)
    now = _now()
    for cid in held - _starting:
        if _agent_running(cid):
            _idle_since.pop(cid, None)
        elif now - _idle_since.setdefault(cid, now) >= STALE_S:
            _idle_since.pop(cid, None)
            admission.release(cid)
            held.discard(cid)
    for cid in set(_idle_since) - held:
        _idle_since.pop(cid, None)
    return held | _starting


def _room(running: int) -> bool:
    """Call under ``_lock``. A lagging app starts no more seats while one already runs."""
    return running < limit() and not (running and lagging())


def is_busy(conv_id: str) -> bool:
    """True while the seat runs a turn or waits in line (a restart would be refused)."""
    with _lock:
        return conv_id in _running() or any(s.conv_id == conv_id for s in _line)


def is_starting(conv_id: str) -> bool:
    """True while this queue is running the seat's ``start`` (its turn passes the cap now)."""
    with _lock:
        return conv_id in _starting


def request(conv_id: str, start: Callable[[], Any]) -> str:
    """Start a member's turn under the cap. ``start`` begins the turn (e.g. run_message).

    Returns ``started``, ``queued`` (it starts when a turn ends) or ``busy`` (already running
    or waiting: nothing added).
    """
    _ensure_watch()
    with _lock:
        if is_busy(conv_id):
            return "busy"
        _starts[conv_id] = start
        _line.append(_Seat(conv_id, start))
    drain()
    with _lock:
        return "queued" if any(s.conv_id == conv_id for s in _line) else "started"


RESTART_EVERY_S = 600.0
RESTART_TEXT = "[Ducky] Your agent host restarted; continue your plan point."
_restarted: dict[str, float] = {}  # seat -> when it was last restarted


def _team_seat(conv_id: str) -> bool:
    try:
        from backend.agent.a2a_broker import team_role

        return team_role(conv_id) in ("member", "sub_lead")
    except Exception:
        return False


def _continue_start(conv_id: str) -> Callable[[], Any]:
    def start() -> Any:
        from frontend.ui_web.agent_turns.routing import run_message

        return run_message(conv_id, RESTART_TEXT, "agent", "", queue_if_busy=True)

    return start


def restart(conv_id: str, reason: str = "host_died") -> str:
    """Start a team seat's turn again after its agent host died mid-turn (the turn ended
    ``interrupted`` and nothing else would start it). It goes through the same line, cap and
    waves as ``request`` and tells the seat to go on with its point. Returns ``started``,
    ``queued`` or ``skipped``: not a team seat, a turn already runs or waits, or this seat was
    restarted in the last ``RESTART_EVERY_S`` (a host that keeps dying must not loop)."""
    del reason  # ponytail: one reason today; log it when there are several
    now = _now()
    with _lock:
        last = _restarted.get(conv_id)
        if last is not None and now - last < RESTART_EVERY_S:
            return "skipped"
    if not _team_seat(conv_id) or _agent_running(conv_id) or is_busy(conv_id):
        return "skipped"
    with _lock:
        _restarted[conv_id] = now
    got = request(conv_id, _continue_start(conv_id))
    return "skipped" if got == "busy" else got


def drop(conv_id: str) -> None:
    """Take the seat out of the line (Stop)."""
    with _lock:
        for seat in [s for s in _line if s.conv_id == conv_id]:
            _line.remove(seat)


def on_turn_end(conv_id: str, reason: str = "", detail: str = "") -> None:
    """A member's turn ended: free its place and start the next seats in line.

    ``detail`` is the turn's error or reply text; a failed start leaves the seat idle (its
    admission slot freed, its next start counted as a first turn again), starts the turn
    again once, and tells the lead when the retry failed too.
    """
    failed = start_failed(detail)
    retry: Callable[[], Any] | None = None
    with _lock:
        _starting.discard(conv_id)
        _idle_since.pop(conv_id, None)
        if not failed:
            _retried.discard(conv_id)
        else:
            _seen.discard(conv_id)
            if conv_id in _retried:
                _retried.discard(conv_id)
            else:
                retry = _starts.get(conv_id)
                if retry is not None:
                    _retried.add(conv_id)
    if failed or str(reason or "").lower() not in _ENDED_OK:
        admission.release(conv_id)  # cancelled / stopped / error: free the slot now
    if retry is not None:
        request(conv_id, retry)  # through the line: the cap and the waves still apply
    elif failed:
        text = (f"Seat {conv_id} could not start its turn twice: {(detail or '').strip()[:300]}\n"
                "Restart it, or recycle it with ducky_recycle_member.")
        _spawn(lambda: _notify_lead(conv_id, text), f"seat-notice-{conv_id[:8]}")
    drain()


def drain() -> int:
    """Start waiting seats, oldest first, while there is room. Returns how many started."""
    global _wave_timer
    started: list[_Seat] = []
    wait = 0.0
    with _lock:
        running = len(_running())
        now = _now()
        while _first_starts and now - _first_starts[0] >= WAVE_GAP_S:
            _first_starts.popleft()
        for seat in list(_line):
            if not _room(running):
                break
            if seat.conv_id not in _seen:
                if len(_first_starts) >= WAVE_SIZE:  # this wave is full: later seats may go
                    wait = WAVE_GAP_S - (now - _first_starts[0])
                    continue
                _seen.add(seat.conv_id)
                _first_starts.append(now)
            _line.remove(seat)
            _starting.add(seat.conv_id)
            started.append(seat)
            running += 1
        if wait > 0 and not _wave_timer:
            _wave_timer = True
        else:
            wait = 0.0
    if wait:
        _spawn_later(wait, _next_wave)
    for seat in started:
        _spawn(lambda s=seat: _run(s), f"seat-{seat.conv_id[:8]}")
    return len(started)


def _next_wave() -> None:
    global _wave_timer
    with _lock:
        _wave_timer = False
    drain()


def _run(seat: _Seat) -> None:
    try:
        seat.start()
    except Exception:
        with _lock:
            _starting.discard(seat.conv_id)
        admission.release(seat.conv_id)  # a start that raised holds nothing
        drain()
        return
    with _lock:
        # Started: admission now holds its slot until agent_stopped. Queued there or no turn: free.
        _starting.discard(seat.conv_id)
    drain()


def status() -> dict[str, Any]:
    """For the team bar, card and Settings: ``{running, waiting, limit, pc_limit, text}``."""
    with _lock:
        running, waiting = len(_running()), len(_line)
    held = lagging() and waiting > 0
    return {"running": running, "waiting": waiting, "limit": limit(), "pc_limit": pc_limit(),
            "held": held, "lag_ms": round(_lag_ms),
            "text": f"{running} running · {waiting} waiting" + (" · paused: app lagging" if held else "")}
