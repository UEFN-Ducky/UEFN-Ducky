"""Minimal WebSocket framing helpers (loopback bridge)."""

from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
from typing import Any

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def ws_accept_key(key: str) -> str:
    digest = hashlib.sha1((key + _WS_GUID).encode()).digest()
    return base64.b64encode(digest).decode()


def read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed")
        buf.extend(chunk)
    return bytes(buf)


def parse_ws_frame(sock: socket.socket) -> bytes:
    header = read_exact(sock, 2)
    masked = (header[1] & 0x80) != 0
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", read_exact(sock, 8))[0]
    mask = read_exact(sock, 4) if masked else b""
    payload = read_exact(sock, length)
    if masked:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return payload


def send_ws_text(sock: socket.socket, text: str) -> None:
    data = text.encode("utf-8")
    frame = bytearray([0x81])
    ln = len(data)
    if ln < 126:
        frame.append(ln)
    elif ln < 65536:
        frame.append(126)
        frame.extend(struct.pack("!H", ln))
    else:
        frame.append(127)
        frame.extend(struct.pack("!Q", ln))
    frame.extend(data)
    sock.sendall(frame)


def send_ws_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    send_ws_text(sock, json.dumps(payload, ensure_ascii=False))


def handshake_websocket(client: socket.socket, req: bytes) -> None:
    headers: dict[str, str] = {}
    for line in req.decode("utf-8", errors="replace").split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    key = headers.get("sec-websocket-key", "")
    accept = ws_accept_key(key)
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    )
    client.sendall(resp.encode())
