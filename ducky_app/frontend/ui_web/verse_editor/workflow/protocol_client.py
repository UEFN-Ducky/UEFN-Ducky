"""Content-Length framed Verse Workflow client — port of protocolClient.js."""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Callable
from typing import Any

from frontend.ui_web.verse_editor.workflow.protocol import MessageType, NoParams


class VerseWorkflowProtocolClient:
    TWO_CRLF = b"\r\n\r\n"

    def __init__(self) -> None:
        self._sequence = 1
        self._content_length = -1
        self._raw_data = bytearray()
        self._pending: dict[int, Callable[[dict[str, Any]], bool]] = {}
        self._sock: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._stop = threading.Event()
        self._handlers: dict[str, list[Callable[..., Any]]] = {}
        self._lock = threading.Lock()

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self._handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, *args: Any) -> bool:
        handlers = self._handlers.get(event, [])
        for handler in handlers:
            try:
                result = handler(*args)
                if result is True:
                    return True
            except TypeError:
                handler(*args)
        return False

    def connect_socket(self, sock: socket.socket) -> None:
        self._sock = sock
        self._stop.clear()
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def disconnect(self) -> None:
        self._stop.set()
        sock = self._sock
        self._sock = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def send_notification(self, command: str, params: Any = NoParams) -> None:
        self._send(MessageType.Notification, command, params)

    def send_request(self, command: str, params: Any = NoParams, timeout_s: float = 120.0) -> Any:
        done = threading.Event()
        result_holder: dict[str, Any] = {"value": None, "error": None}

        def callback(response: dict[str, Any]) -> bool:
            if response.get("result") is not None:
                result_holder["value"] = response["result"]
                done.set()
                return True
            if response.get("error") is not None:
                result_holder["error"] = response["error"]
                done.set()
                return True
            return False

        self._send(MessageType.Request, command, params, callback)
        if not done.wait(timeout_s):
            raise TimeoutError(f"Verse workflow request timed out: {command}")
        if result_holder["error"] is not None:
            raise RuntimeError(str(result_holder["error"]))
        return result_holder["value"]

    def _send(
        self,
        msg_type: MessageType,
        command: str,
        params: Any,
        callback: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        sock = self._sock
        if sock is None:
            raise RuntimeError("Verse workflow socket not connected")
        with self._lock:
            seq = self._sequence
            self._sequence += 1
        message: dict[str, Any] = {
            "seq": seq,
            "type": int(msg_type),
            "command": command,
            "params": {} if params is NoParams else params,
        }
        if msg_type == MessageType.Request and callback is not None:
            self._pending[seq] = callback
        payload = json.dumps(message).encode("utf-8")
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        sock.sendall(header + payload)

    def _read_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        try:
            while not self._stop.is_set():
                chunk = sock.recv(65536)
                if not chunk:
                    break
                self._handle_data(chunk)
        except OSError:
            pass

    def _handle_data(self, data: bytes) -> None:
        self._raw_data.extend(data)
        while True:
            if self._content_length >= 0:
                if len(self._raw_data) >= self._content_length:
                    body = bytes(self._raw_data[: self._content_length]).decode("utf-8")
                    del self._raw_data[: self._content_length]
                    if body:
                        self._dispatch(body)
                    self._content_length = -1
                    continue
            else:
                idx = self._raw_data.find(self.TWO_CRLF)
                if idx != -1:
                    header = bytes(self._raw_data[:idx]).decode("utf-8", errors="replace")
                    del self._raw_data[: idx + len(self.TWO_CRLF)]
                    for line in header.split("\r\n"):
                        pair = line.split(": ", 1)
                        if len(pair) == 2 and pair[0] == "Content-Length":
                            self._content_length = int(pair[1])
                    continue
            break

    def _dispatch(self, body: str) -> None:
        try:
            raw = json.loads(body)
        except json.JSONDecodeError:
            return
        msg_type = raw.get("type")
        if msg_type == MessageType.Notification:
            command = str(raw.get("command") or "")
            params = raw.get("params")
            self.emit(command, params)
        elif msg_type == MessageType.Response:
            seq = int(raw.get("seq") or 0)
            callback = self._pending.pop(seq, None)
            if callback and not callback(raw):
                pass
