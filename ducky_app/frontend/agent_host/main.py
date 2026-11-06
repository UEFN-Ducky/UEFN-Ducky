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
from multiprocessing.connection import Client
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
    serve(args.pipe, key)
    return 0
