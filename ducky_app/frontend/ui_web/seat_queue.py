"""One cap on team-member turns running at once, across every team (K3 seat queue).

Oct 24 2026: 16 agents made the app lag (GIL-capped app, a 539-thread UI herd, chats taking
10–45 s). Keeper wakes and plan dispatches ask ``request`` before starting a member's turn:
under the cap it starts now, over it the seat waits in one FIFO line and starts when a turn
ends (``on_turn_end``). The cap sits on top of the memory admission (admission.py), so there
is one gate: a seat starts only while fewer than ``limit()`` member turns run *and* admission
has memory room; the running count is admission's own slots plus seats this queue is starting.
``limit()`` defaults from CPU cores and RAM (``pc_limit``); the card's "Run at once" slider
(``set_run_at_once``) can lower it, never raise it past the PC limit.

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
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from frontend.ui_web import admission

PushFn = Callable[[dict[str, Any]], None]

WAVE_SIZE = 3
WAVE_GAP_S = 5.0
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


def pc_limit() -> int:
    """Turns this PC can run at once: one per two cores and per 2 GB of RAM, at least 2."""
    # ponytail: fixed per-agent cost (half a core, 2 GB); measure real agent use if it misjudges.
    cores = os.cpu_count() or 4
    return max(2, min(cores // 2, int(total_memory_gb() // 2)))


def limit() -> int:
    pc = pc_limit()
    return min(pc, _user_cap) if _user_cap else pc


def set_run_at_once(n: int | None) -> int:
    """The "Run at once" slider: lowers the cap (0 / None = the PC limit). Returns the cap."""
    global _user_cap
    with _lock:
        _user_cap = max(1, min(int(n), pc_limit())) if n else None
    drain()
    return limit()


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
    """Call under ``_lock``. Seats being started count against memory before admission sees them."""
    if running >= limit():
        return False
    return admission.has_room(running, extra=len(_starting))


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
    with _lock:
        if is_busy(conv_id):
            return "busy"
        _starts[conv_id] = start
        _line.append(_Seat(conv_id, start))
    drain()
    with _lock:
        return "queued" if any(s.conv_id == conv_id for s in _line) else "started"


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
    return {"running": running, "waiting": waiting, "limit": limit(), "pc_limit": pc_limit(),
            "text": f"{running} running · {waiting} waiting"}
