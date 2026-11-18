"""Host side: ``UEFN-Ducky.exe agent-host --slot N --pipe <name>`` (``python -m frontend agent-host`` from source).

Started only by the app's supervisor. Connects back over the pipe with the key in
``UEFN_DUCKY_HOST_KEY`` and runs until the app says ``shutdown`` or the pipe closes. It never
takes the single-instance lock, opens a window, applies IDE config or syncs skills.

Turn events go through ``feed(event)``: a ``Batcher`` sends them as ``event`` frames, live text
only for chats on screen (the app's ``visible_chats`` frame), one status a second for the rest.
Other frames go to ``frontend.agent_host.turns.on_frame(channel, frame)`` once that module exists.

``shed`` (the warm host idle, from the supervisor's reaper): :func:`shed` on a thread unloads
the plugin engines turns loaded, drops the chat caches turns filled, collects and trims the heap.
"""

from __future__ import annotations

import argparse
import gc
import importlib
import logging
import os
import sys
import threading
import time
from multiprocessing.connection import Client
from pathlib import Path
from typing import Any

from frontend.agent_host import protocol
from frontend.agent_host.batching import Batcher
from frontend.agent_host.supervisor import APP_OPS, KEY_ENV

_TICK_S = 0.05
_batcher: Batcher | None = None
_log = logging.getLogger(__name__)
_shed_lock = threading.Lock()


def feed(event: dict[str, Any]) -> None:
    """One turn event (``conv_id``, ``run_id``, ``type``, ...) on its way to the app."""
    if _batcher is not None:
        _batcher.feed(event)


def _turns_handler() -> Any:
    try:
        from frontend.agent_host import turns
    except ImportError:
        return None
    return getattr(turns, "on_frame", None)


def _drop_caches() -> list[str]:
    """Empty the chat caches turns filled here (only modules already loaded)."""
    dropped: list[str] = []
    saves = sys.modules.get("frontend.ui_web.chats.saves")
    stats = getattr(saves, "_PREFIX_STATS", None)
    if stats:  # each entry pins a chat's whole message list (Nov 5 2026: 278 MB)
        with saves._row_stats_lock:  # type: ignore[union-attr]
            stats.clear()
        dropped.append("prefix_stats")
    chats = sys.modules.get("backend.store.repos.chats")
    if chats is not None and getattr(chats, "_parts_open", None):
        chats.forget_parts()
        dropped.append("parts_open")
    return dropped


def _trim_heap() -> bool:
    try:  # sys.modules at call time; `from frontend import heap_trim` reads a stale package attribute
        heap_trim = importlib.import_module("frontend.heap_trim")
    except ImportError:
        return False
    trim = getattr(heap_trim, "trim", None)
    if not callable(trim):
        return False
    trim()
    return True


def shed() -> dict[str, Any]:
    """The warm host has had no agents for a while: give back what its turns left behind.
    A turn that started meanwhile skips it (its engines and caches are in use)."""
    from frontend.agent_host import tool_server, turns

    with _shed_lock:
        if turns.running():
            return {"skipped": "turns running"}
        out: dict[str, Any] = {}
        steps = (("engines", tool_server.shed_engines), ("caches", _drop_caches),
                 ("collected", gc.collect), ("trimmed", _trim_heap))
        for name, step in steps:
            try:
                out[name] = step()
            except Exception:  # noqa: BLE001 - one step failing never stops the others
                _log.debug("shed step %s failed", name, exc_info=True)
        _log.info("agent host shed: %s", out)
        return out


STALL_S = 15.0
_STALL_LOG_MAX = 1_000_000


def watch_stalls(path: Path, stall_s: float = STALL_S, beat_s: float = 1.0) -> threading.Event:
    """Write every thread's Python stack to ``path`` when this host's Python stops running for
    ``stall_s`` (one thread keeping the interpreter inside native code). Set the returned event
    to stop watching.

    While it is stopped no agent can join the host's tool server and every tool call of every
    chat waits. Nov 11-17 2026: about five times a day a turn started 50-100 s late that way
    (tools that answer in 0.1 s took over 100 s at those moments) and nothing recorded what
    the host was doing. faulthandler's watchdog is a C thread, so it fires even then; the beat
    re-arms it and, once Python runs again, dates the dump and says how long the stop was.
    """
    import faulthandler

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > _STALL_LOG_MAX:
            path.unlink()
    except OSError:
        pass
    out = open(path, "a", encoding="utf-8")  # noqa: SIM115 - held while the host runs
    stop = threading.Event()

    def _beat() -> None:
        last = time.monotonic()
        while not stop.is_set():
            faulthandler.dump_traceback_later(stall_s, file=out)  # replaces the one armed before
            stop.wait(beat_s)
            now = time.monotonic()
            stopped = now - last - beat_s
            if stopped >= stall_s:
                out.write(f"=== {time.strftime('%Y-%m-%d %H:%M:%S')}: this host's Python did not run for "
                          f"{stopped:.0f} s; the stacks above were taken {stall_s:.0f} s into that stop\n")
                out.flush()
            last = now
        faulthandler.cancel_dump_traceback_later()
        out.close()

    threading.Thread(target=_beat, name="host-stall-watch", daemon=True).start()
    return stop


def serve(pipe: str, key: bytes, done: threading.Event | None = None) -> None:
    """Connect to the app and run until ``shutdown``, the pipe closing, or ``done`` being set."""
    global _batcher
    done = done or threading.Event()
    box: list[protocol.Channel] = []
    handler = _turns_handler()
    batcher = Batcher(lambda conv_id, events: box and box[0].send("event", conv_id=conv_id, events=events))

    def on_frame(frame: dict[str, Any]) -> None:
        op = frame["op"]
        if op == "shutdown":
            done.set()
        elif op == "visible_chats":
            ids = frame.get("conv_ids")
            batcher.set_visible(None if ids is None else [str(i) for i in ids])
        elif op == "shed":
            threading.Thread(target=lambda: shed(), name="agent-host-shed", daemon=True).start()
        elif handler is not None and box:
            handler(box[0], frame)

    conn = Client(pipe, authkey=key)
    # In box and _batcher before it reads: the app sends start_turn the moment we connect, and a
    # frame handled before then was dropped (its chats never started).
    box.append(protocol.Channel(conn, send_ops=protocol.HOST_OPS, recv_ops=APP_OPS, on_frame=on_frame,
                                on_closed=lambda _reason: done.set(), heartbeat_s=protocol.HEARTBEAT_S))
    _batcher = batcher
    box[0].start()
    try:
        while not done.wait(_TICK_S):
            batcher.flush_due()
        batcher.flush()
    finally:
        _batcher = None
        box[0].close("shutdown")
        from frontend.agent_host import tool_server

        tool_server.stop()  # its state file goes with the host


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="agent-host")
    ap.add_argument("--slot", required=True)
    ap.add_argument("--pipe", required=True)
    args = ap.parse_args(argv)
    from backend.process_role import set_role

    set_role("agent_host")  # before turns load plugins: app-only work stays in the window
    key = bytes.fromhex(os.environ.pop(KEY_ENV, ""))  # never handed on to the CLIs this host starts
    try:
        from frontend.settings import default_app_data_dir

        # Beside this host's own log (supervisor: logs/agent_hosts/host-<slot>.log).
        watch_stalls(default_app_data_dir() / "logs" / "agent_hosts" / f"host-{args.slot}-stalls.log")
    except Exception:  # noqa: BLE001 - a diagnostic never keeps the host from starting
        _log.warning("the stall watch did not start", exc_info=True)
    serve(args.pipe, key)
    return 0
