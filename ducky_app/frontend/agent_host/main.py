"""Host side: ``UEFN-Ducky.exe agent-host --slot N --pipe <name>`` (``python -m frontend agent-host`` from source).

Started only by the app's supervisor. Connects back over the pipe with the key in
``UEFN_DUCKY_HOST_KEY`` and runs until the app says ``shutdown`` or the pipe closes. It never
takes the single-instance lock, opens a window, applies IDE config or syncs skills.

Turn events go through ``feed(event)``: a ``Batcher`` sends them as ``event`` frames, live text
only for chats on screen (the app's ``visible_chats`` frame), one status a second for the rest.
Other frames go to ``frontend.agent_host.turns.on_frame(channel, frame)`` once that module exists.
"""

from __future__ import annotations

import argparse
import os
import threading
from multiprocessing.connection import Client
from typing import Any

from frontend.agent_host import protocol
from frontend.agent_host.batching import Batcher
from frontend.agent_host.supervisor import APP_OPS, KEY_ENV

_TICK_S = 0.05
_batcher: Batcher | None = None


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
        elif handler is not None and box:
            handler(box[0], frame)

    conn = Client(pipe, authkey=key)
    box.append(protocol.Channel(conn, send_ops=protocol.HOST_OPS, recv_ops=APP_OPS, on_frame=on_frame,
                                on_closed=lambda _reason: done.set(), heartbeat_s=protocol.HEARTBEAT_S).start())
    _batcher = batcher
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
