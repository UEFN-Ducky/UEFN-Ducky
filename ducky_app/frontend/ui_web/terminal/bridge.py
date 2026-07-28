"""WebSocket bridge for one terminal session — accepts client reconnects."""

from __future__ import annotations

import json
import socket
import threading
from typing import Any, Callable

from frontend.ui_web.terminal.ws_util import (
    handshake_websocket,
    parse_ws_frame,
    send_ws_json,
)


class TerminalBridge:
    """Bridge PTY I/O to WebSocket; keeps listening until stop() for tab remounts."""

    def __init__(
        self,
        *,
        on_input: Callable[[str], None],
        on_resize: Callable[[int, int], None],
        get_replay: Callable[[], str] | None = None,
        on_attach: Callable[[], None] | None = None,
        get_status: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._on_input = on_input
        self._on_resize = on_resize
        self._get_replay = get_replay
        self._on_attach = on_attach
        self._get_status = get_status
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._port = 0
        self._stop = threading.Event()
        self._client_lock = threading.Lock()
        self._listening = threading.Event()
        self._exit_code: int | None = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self._port}" if self._port else ""

    def start(self) -> None:
        self._stop.clear()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(5)
        self._server_sock = server
        self._port = server.getsockname()[1]
        self._listening.clear()
        self._thread = threading.Thread(target=self._accept_loop, args=(server,), daemon=True, name="terminal-ws")
        self._thread.start()
        if not self._listening.wait(timeout=5.0):
            raise RuntimeError("terminal WebSocket bridge failed to start")

    def stop(self) -> None:
        self._stop.set()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None
        with self._client_lock:
            sock = self._client_sock
            self._client_sock = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
        self._port = 0
        self._listening.clear()

    def push_output(self, data: str) -> None:
        with self._client_lock:
            sock = self._client_sock
        if not sock:
            return
        try:
            send_ws_json(sock, {"type": "output", "data": data})
        except OSError:
            with self._client_lock:
                if self._client_sock is sock:
                    self._client_sock = None

    def push_exit(self, code: int) -> None:
        self._exit_code = code
        with self._client_lock:
            sock = self._client_sock
        if not sock:
            return
        try:
            send_ws_json(sock, {"type": "exit", "code": code})
        except OSError:
            with self._client_lock:
                if self._client_sock is sock:
                    self._client_sock = None

    def push_status(self, status: dict[str, Any]) -> None:
        with self._client_lock:
            sock = self._client_sock
        if not sock:
            return
        try:
            send_ws_json(sock, {"type": "status", **status})
        except OSError:
            with self._client_lock:
                if self._client_sock is sock:
                    self._client_sock = None

    def _accept_loop(self, server: socket.socket) -> None:
        try:
            self._listening.set()
            server.settimeout(0.5)
            while not self._stop.is_set():
                try:
                    client, _ = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        return
                    continue
                handler = threading.Thread(
                    target=self._client_loop,
                    args=(client,),
                    daemon=True,
                    name="terminal-ws-client",
                )
                handler.start()
        finally:
            try:
                server.close()
            except OSError:
                pass
            self._server_sock = None
            self._listening.clear()

    def _client_loop(self, client: socket.socket) -> None:
        with self._client_lock:
            prev = self._client_sock
            self._client_sock = client
        if prev and prev is not client:
            try:
                prev.close()
            except OSError:
                pass

        try:
            req = b""
            while b"\r\n\r\n" not in req:
                chunk = client.recv(4096)
                if not chunk:
                    return
                req += chunk
            handshake_websocket(client, req)

            if self._get_replay:
                replay = self._get_replay()
                if replay:
                    send_ws_json(client, {"type": "output", "data": replay})

            if self._on_attach:
                try:
                    self._on_attach()
                except Exception:
                    pass

            if self._get_status:
                try:
                    status = self._get_status()
                    if status:
                        send_ws_json(client, {"type": "status", **status})
                        exit_code = status.get("exit_code")
                        if exit_code is not None and not status.get("alive", True):
                            send_ws_json(client, {"type": "exit", "code": int(exit_code)})
                except Exception:
                    pass
            elif self._exit_code is not None:
                try:
                    send_ws_json(client, {"type": "exit", "code": self._exit_code})
                except OSError:
                    pass

            while not self._stop.is_set():
                try:
                    payload = parse_ws_frame(client)
                except (ConnectionError, OSError):
                    break
                if not payload:
                    continue
                try:
                    msg = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                mtype = str(msg.get("type", ""))
                if mtype == "input":
                    data = str(msg.get("data", ""))
                    if data:
                        self._on_input(data)
                elif mtype == "resize":
                    cols = int(msg.get("cols", 80))
                    rows = int(msg.get("rows", 24))
                    self._on_resize(cols, rows)
        finally:
            with self._client_lock:
                if self._client_sock is client:
                    self._client_sock = None
            try:
                client.close()
            except OSError:
                pass
