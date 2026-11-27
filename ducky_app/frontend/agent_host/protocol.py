"""App <-> agent-host channel: one named pipe per host, JSON frames, a reader thread per side.

The app listens on ``\\\\.\\pipe\\uefn-ducky-host-<app pid>-<slot>`` (Linux: an AF_UNIX socket,
``$XDG_RUNTIME_DIR/uefn-ducky/uefn-ducky-host-<app pid>-<slot>.sock``); the host connects.
Every frame is ``{"op": ..., **fields}``, e.g.
``{"op": "start_turn", "conv_id": "c1", "text": "hi", "mode": "agent", "model": "claude-haiku-5-5"}``.

``send`` never blocks the caller (the app's window thread): frames go on a queue and a
writer thread sends them, packing bounded batches into length-prefixed pipe messages.
The reader thread only reads; a handler thread hands each frame to ``on_frame`` in order (the
app's handler passes events to the push path), so a slow push never stops reads and never looks
like a missing heartbeat. A dead host shows as pipe EOF at once, a hung one as a missing heartbeat.

Nov 4 2026 (40 seats, a loaded PC): a 9.2 s app-side freeze looked like a silent host and the
app closed a busy host mid-turn. So time this side itself was stalled never counts as silence,
and a host whose process still runs (``alive``) is closed only after ``SILENT_DEAD_S`` of silence.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import socket
import stat
import sys
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
SILENT_DEAD_S = 30.0  # a host whose process still runs is hung only after this long a silence
MAX_MESSAGE = 64 * 1024 * 1024
MAX_BATCH_BYTES = 1024 * 1024
MAX_BATCH_FRAMES = 128
_TICK_S = 1.0  # longest tick; close wakes it at once (_tick_s: shorter beats or silence limits)
_EOF = object()  # the reader's EOF, queued behind the frames read before it

OnFrame = Callable[[dict[str, Any]], None]
OnClosed = Callable[[str], None]


_MAX_SUN_PATH = 103 if sys.platform == "darwin" else 107  # sun_path: 104 bytes on macOS, 108 on Linux, NUL included


def _socket_dir() -> str:
    """Linux: ``$XDG_RUNTIME_DIR/uefn-ducky`` (else the app cache folder), kept 0700 by ``listen_at``."""
    run = os.environ.get("XDG_RUNTIME_DIR") or ""
    if os.path.isabs(run) and os.path.isdir(run):
        return os.path.join(run, "uefn-ducky")
    from frontend import app_paths

    return os.path.join(str(app_paths.cache_base()), app_paths.APP_DATA_DIR_NAME, "sock")


def pipe_name(slot: int | str, app_pid: int | None = None) -> str:
    leaf = f"uefn-ducky-host-{app_pid or os.getpid()}-{slot}"
    if os.name == "nt":
        return rf"\\.\pipe\{leaf}"
    path = os.path.join(_socket_dir(), f"{leaf}.sock")
    if len(os.fsencode(path)) > _MAX_SUN_PATH:
        path = os.path.join(_socket_dir(), hashlib.sha1(leaf.encode()).hexdigest()[:16] + ".sock")
    if len(os.fsencode(path)) > _MAX_SUN_PATH:
        # a long folder (macOS $TMPDIR /var/folders/..., a deep home): /tmp/ducky-<uid>, 0700 via listen_at
        path = os.path.join(f"/tmp/ducky-{os.getuid()}", os.path.basename(path))
    return path


def _clear_stale(path: str) -> None:
    """Remove a socket file nobody listens on (its app died), so bind doesn't hit EADDRINUSE."""
    if not os.path.exists(path):
        return
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        probe.connect(path)
    except (ConnectionRefusedError, FileNotFoundError):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
    except OSError:
        pass
    finally:
        probe.close()


def listen_at(name: str, authkey: bytes) -> Listener:
    """Listen on ``name`` (a ``pipe_name``). Linux: 0700 folder, stale socket cleared, socket 0600;
    ``Listener.close`` unlinks the socket file."""
    if os.name == "nt":
        return Listener(name, authkey=authkey)
    folder = os.path.dirname(name)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    st = os.lstat(folder)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():  # /tmp/ducky-<uid>: anyone may make it first
        raise PermissionError(f"{folder} is not this user's folder")
    os.chmod(folder, 0o700)
    _clear_stale(name)
    listener = Listener(name, family="AF_UNIX", authkey=authkey)
    os.chmod(name, 0o600)
    return listener


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
        alive: Callable[[], bool] | None = None,
        silent_dead_s: float = SILENT_DEAD_S,
    ) -> None:
        self._conn = conn
        self._send_ops = send_ops
        self._recv_ops = recv_ops
        self._on_frame = on_frame
        self._on_closed = on_closed
        self._heartbeat_s = heartbeat_s
        self._dead_after_s = dead_after_s
        self._alive = alive
        self._silent_dead_s = silent_dead_s
        self._out: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._in: queue.SimpleQueue[Any] = queue.SimpleQueue()
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self.close_reason = ""
        self._last_rx = self._last_tick = time.monotonic()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def start(self) -> Channel:
        loops = ((self._read_loop, "reader"), (self._handle_loop, "handler"), (self._write_loop, "writer"),
                 (self._tick_loop, "tick"))
        self._last_rx = self._last_tick = time.monotonic()
        for target, name in loops:
            threading.Thread(target=target, name=f"agent-host-{name}", daemon=True).start()
        return self

    def send(self, op: str, **fields: Any) -> bool:
        """Queue one frame; False once the channel is closed."""
        if op not in self._send_ops:
            raise ValueError(f"unknown op for this side: {op!r}")
        if self._closed.is_set():
            return False
        raw = json.dumps({"op": op, **fields}, separators=(",", ":")).encode("utf-8")
        if len(raw) + 2 > MAX_MESSAGE:
            raise ValueError("agent-host frame exceeds MAX_MESSAGE")
        with self._lock:
            if self.closed:
                return False
            self._out.put(raw)
        return True

    def close(self, reason: str = "closed") -> None:
        with self._lock:
            if self._closed.is_set():
                return
            self.close_reason = reason
            self._closed.set()
            # Unsent frames go (the pipe closes below). Frames already read stay: the handler
            # hands them over in order before it stops, so a turn's last events (its end, its
            # error) are never lost to a close that raced them and left the chat "running".
            while True:
                try:
                    self._out.get_nowait()
                except queue.Empty:
                    break
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
            except (EOFError, OSError, TypeError, ValueError):
                # TypeError / ValueError: close() cleared the pipe handle while recv_bytes waited.
                if not self._closed.is_set():
                    self._in.put(_EOF)
                return
            self._last_rx = time.monotonic()
            self._in.put(raw)
            del raw

    def _handle_loop(self) -> None:
        while True:
            raw = self._in.get()
            if raw is None:
                return
            if raw is _EOF:
                self.close("eof")
                return
            frames = frame = None
            try:
                try:
                    frames = json.loads(raw)
                except ValueError:
                    continue
                for frame in frames if isinstance(frames, list) else [frames]:
                    if isinstance(frame, dict) and frame.get("op") in self._recv_ops and frame["op"] != "heartbeat":
                        self._on_frame(frame)
            finally:
                del raw, frames, frame

    def _write_loop(self) -> None:
        carry = None
        while True:
            item = carry if carry is not None else self._out.get()
            carry = None
            if item is None:
                return
            batch = [item]
            size = len(item) + 2
            while len(batch) < MAX_BATCH_FRAMES and size < MAX_BATCH_BYTES:
                try:
                    more = self._out.get_nowait()
                except queue.Empty:
                    break
                if more is None:
                    self._out.put(None)
                    break
                if size + len(more) + 1 > min(MAX_BATCH_BYTES, MAX_MESSAGE):
                    carry = more
                    break
                batch.append(more)
                size += len(more) + 1
            try:
                self._conn.send_bytes(b"[" + b",".join(batch) + b"]")
            except (OSError, ValueError):
                self.close("eof")
                return
            finally:
                item = more = None
                batch.clear()

    def _tick_loop(self) -> None:
        last_beat = 0.0
        while not self._closed.wait(tick := self._tick_s()):
            now = time.monotonic()
            # half a tick early: tick jitter never stretches a 2 s beat to 3 s
            if self._heartbeat_s and now - last_beat >= self._heartbeat_s - tick / 2:
                last_beat = now
                self.send("heartbeat", ts=time.time())
            self._check_silence(now)

    def _tick_s(self) -> float:
        """1 s for the real 2 s beat and 6 s limit; finer only for shorter ones (read each tick)."""
        tick = _TICK_S
        if self._heartbeat_s:
            tick = min(tick, self._heartbeat_s / 2)
        if self._dead_after_s:
            tick = min(tick, self._dead_after_s / 4)
        return tick

    def _check_silence(self, now: float) -> None:
        """Close on a missing heartbeat, never for time this side itself was stalled."""
        gap, self._last_tick = now - self._last_tick, now
        if not self._dead_after_s:
            return
        if gap > self._dead_after_s:
            # This side froze (GIL, a stuck window, a loaded PC): the host's beats wait unread.
            self._last_rx = now
            return
        silent = now - self._last_rx
        if silent <= self._dead_after_s:
            return
        if self._alive is None or silent >= self._silent_dead_s or not self._process_alive():
            self.close("heartbeat")

    def _process_alive(self) -> bool:
        try:
            return bool(self._alive and self._alive())
        except Exception:  # noqa: BLE001 - can't tell: wait for the long silence
            return True


def listen(slot: int | str, authkey: bytes, app_pid: int | None = None) -> Listener:
    """App side: create this host's pipe. Pass ``authkey`` to the host (env) so no other process can attach."""
    return listen_at(pipe_name(slot, app_pid), authkey)


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
