"""App <-> agent-host channel: one named pipe per host, JSON frames, a reader thread per side.

The app listens on ``\\\\.\\pipe\\uefn-ducky-host-<app pid>-<slot>``; the host connects.
Every frame is ``{"op": ..., **fields}``, e.g.
``{"op": "start_turn", "conv_id": "c1", "text": "hi", "mode": "agent", "model": "claude-haiku-5-5"}``.

``send`` never blocks the caller (the app's window thread): frames go on a queue and a
writer thread sends them, packing whatever is queued into one length-prefixed pipe message.
The reader thread only reads; a handler thread hands each frame to ``on_frame`` in order (the
app's handler passes events to the push path), so a slow push never stops reads and never looks
like a missing heartbeat. A dead host shows as pipe EOF at once, a hung one as a missing heartbeat.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from multiprocessing.connection import Client, Connection, Listener
from typing import Any, Callable

# ponytail: multiprocessing's pipe Connection already frames length-prefixed messages over a
# Windows named pipe; shared_mcp.write_frame/read_frame are socket-only (sendall/recv).
APP_OPS = frozenset({"start_turn", "cancel", "steer_post", "question_answer", "shutdown"})
HOST_OPS = frozenset({"event", "turn_ended", "saved", "steer_take", "steer_ack", "heartbeat"})

HEARTBEAT_S = 2.0
DEAD_AFTER_S = 3 * HEARTBEAT_S
MAX_MESSAGE = 64 * 1024 * 1024
_TICK_S = 0.1
_EOF = object()  # the reader's EOF, queued behind the frames read before it

OnFrame = Callable[[dict[str, Any]], None]
OnClosed = Callable[[str], None]


def pipe_name(slot: int | str, app_pid: int | None = None) -> str:
    return rf"\\.\pipe\uefn-ducky-host-{app_pid or os.getpid()}-{slot}"


class Channel:
    """One end of the pipe. ``on_frame`` runs on the handler thread; ``on_closed(reason)`` runs once."""

    def __init__(
        self,
        conn: Connection,
        *,
        send_ops: frozenset[str],
        recv_ops: frozenset[str],
        on_frame: OnFrame,
        on_closed: OnClosed | None = None,
        heartbeat_s: float = 0.0,
        dead_after_s: float = 0.0,
    ) -> None:
        self._conn = conn
        self._send_ops = send_ops
        self._recv_ops = recv_ops
        self._on_frame = on_frame
        self._on_closed = on_closed
        self._heartbeat_s = heartbeat_s
        self._dead_after_s = dead_after_s
        self._out: queue.SimpleQueue[dict[str, Any] | None] = queue.SimpleQueue()
        self._in: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self.close_reason = ""
        self._last_rx = time.monotonic()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def start(self) -> Channel:
        loops = ((self._read_loop, "reader"), (self._handle_loop, "handler"), (self._write_loop, "writer"),
                 (self._tick_loop, "tick"))
        for target, name in loops:
            threading.Thread(target=target, name=f"agent-host-{name}", daemon=True).start()
        return self

    def send(self, op: str, **fields: Any) -> bool:
        """Queue one frame; False once the channel is closed."""
        if op not in self._send_ops:
            raise ValueError(f"unknown op for this side: {op!r}")
        if self._closed.is_set():
            return False
        self._out.put({"op": op, **fields})
        return True

    def close(self, reason: str = "closed") -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self.close_reason = reason
            self._closed.set()
        self._out.put(None)
        self._in.put(None)
        try:
            self._conn.close()
        except OSError:
            pass
        if self._on_closed:
            self._on_closed(reason)

    def _read_loop(self) -> None:
        while not self._closed.is_set():
            try:
                raw = self._conn.recv_bytes(MAX_MESSAGE)
            except (EOFError, OSError):
                self._in.put(_EOF)
                return
            self._last_rx = time.monotonic()
            self._in.put(raw)

    def _handle_loop(self) -> None:
        while True:
            raw = self._in.get()
            if raw is None:
                return
            if raw is _EOF:
                self.close("eof")
                return
            try:
                frames = json.loads(raw)
            except ValueError:
                continue
            for frame in frames if isinstance(frames, list) else [frames]:
                if isinstance(frame, dict) and frame.get("op") in self._recv_ops and frame["op"] != "heartbeat":
                    self._on_frame(frame)

    def _write_loop(self) -> None:
        while True:
            item = self._out.get()
            if item is None:
                return
            batch = [item]
            while True:
                try:
                    more = self._out.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    self._out.put(None)
                    break
                batch.append(more)
            try:
                self._conn.send_bytes(json.dumps(batch, separators=(",", ":")).encode("utf-8"))
            except (OSError, ValueError):
                self.close("eof")
                return

    def _tick_loop(self) -> None:
        last_beat = 0.0
        while not self._closed.wait(_TICK_S):
            now = time.monotonic()
            if self._heartbeat_s and now - last_beat >= self._heartbeat_s:
                last_beat = now
                self.send("heartbeat", ts=time.time())
            if self._dead_after_s and now - self._last_rx > self._dead_after_s:
                self.close("heartbeat")


def listen(slot: int | str, authkey: bytes, app_pid: int | None = None) -> Listener:
    """App side: create this host's pipe. Pass ``authkey`` to the host (env) so no other process can attach."""
    return Listener(pipe_name(slot, app_pid), authkey=authkey)


def accept_host(
    listener: Listener, on_frame: OnFrame, on_closed: OnClosed | None = None, *, dead_after_s: float = DEAD_AFTER_S
) -> Channel:
    """App side: wait for the host to connect (call off the window thread). Returns the started channel."""
    conn = listener.accept()
    return Channel(
        conn, send_ops=APP_OPS, recv_ops=HOST_OPS, on_frame=on_frame, on_closed=on_closed, dead_after_s=dead_after_s
    ).start()


def connect_app(
    name: str, authkey: bytes, on_frame: OnFrame, on_closed: OnClosed | None = None, *, heartbeat_s: float = HEARTBEAT_S
) -> Channel:
    """Host side: connect to the app's pipe and start sending heartbeats."""
    conn = Client(name, authkey=authkey)
    return Channel(
        conn, send_ops=HOST_OPS, recv_ops=APP_OPS, on_frame=on_frame, on_closed=on_closed, heartbeat_s=heartbeat_s
    ).start()
