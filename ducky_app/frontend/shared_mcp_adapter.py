"""Thin stdio -> shared-daemon adapter. Stdlib only; never imports backend/FastMCP.

``Bridge.exe bridge --port N --adapter``: forward newline-delimited MCP JSON-RPC
from the IDE to the shared daemon (length-prefixed frames on 127.0.0.1). If no
daemon can be reached, return False so the caller runs the dedicated bridge.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

_STATE = "shared_mcp.json"
_MAX_FRAME = 16 * 1024 * 1024
_IDENTITY_ENV = (
    ("DUCKY_RUN_ID", "run_id"),
    ("DUCKY_CONV_ID", "conv_id"),
    ("DUCKY_PROFILE_ID", "profile_id"),
    ("DUCKY_DUCKY_NAME", "ducky_name"),
    ("DUCKY_MODEL", "model"),
    ("DUCKY_CODING_AGENT", "coding_agent"),
    ("DUCKY_GROUP_ID", "group_id"),
    ("DUCKY_LEADER_CONV_ID", "leader_conv_id"),
)


def _app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "UEFN-Ducky"


def _read_state() -> dict | None:
    try:
        data = json.loads((_app_data() / _STATE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("token") and data.get("port") else None
    except (OSError, ValueError):
        return None


def _identity() -> dict:
    return {k: v for env, k in _IDENTITY_ENV if (v := (os.environ.get(env) or "").strip())}


def _read_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise EOFError
        out += chunk
    return bytes(out)


def _read_frame(sock: socket.socket) -> dict:
    (n,) = struct.unpack(">I", _read_exact(sock, 4))
    if n > _MAX_FRAME:
        raise ValueError("frame too large")
    return json.loads(_read_exact(sock, n).decode("utf-8"))


def _write_frame(sock: socket.socket, payload: dict) -> None:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)


def _try_hello(state: dict) -> socket.socket | None:
    try:
        sock = socket.create_connection((str(state.get("host") or "127.0.0.1"), int(state["port"])), timeout=2.0)
    except OSError:
        return None
    try:
        sock.settimeout(10.0)
        _write_frame(sock, {"op": "hello", "token": str(state["token"]), "key": state.get("key") or {}, "identity": _identity()})
        reply = _read_frame(sock)
    except (OSError, ValueError, EOFError):
        sock.close()
        return None
    if reply.get("op") != "hello_ok":
        sock.close()
        return None
    sock.settimeout(None)
    return sock


def _spawn_daemon(bridge_args: list[str]) -> None:
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "bridge", *bridge_args, "--shared-daemon"]
        cwd = None
    else:
        cmd = [sys.executable, "-m", "frontend", "bridge", *bridge_args, "--shared-daemon"]
        cwd = str(Path(__file__).resolve().parent.parent)
    env = {k: v for k, v in os.environ.items() if not k.startswith("DUCKY_")}
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    except OSError:
        pass


def connect(bridge_args: list[str], timeout_s: float = 30.0) -> socket.socket | None:
    """Existing daemon first; spawn one only if nobody answers."""
    state = _read_state()
    if state and (sock := _try_hello(state)):
        return sock
    _spawn_daemon(bridge_args)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _read_state()
        if state and (sock := _try_hello(state)):
            return sock
        time.sleep(0.1)
    return None


def run(bridge_args: list[str]) -> bool:
    """Relay stdio <-> daemon until the IDE closes stdin. False = no daemon (use dedicated)."""
    sock = connect(bridge_args)
    if sock is None:
        return False
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    out_lock = threading.Lock()

    def _emit(msg: dict) -> None:
        with out_lock:
            stdout.write(json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n")
            stdout.flush()

    def _pump_daemon() -> None:
        try:
            while True:
                frame = _read_frame(sock)
                if frame.get("op") != "mcp":
                    continue
                if frame.get("error"):
                    _emit({"jsonrpc": "2.0", "id": frame.get("id"), "error": frame["error"]})
                else:
                    _emit({"jsonrpc": "2.0", "id": frame.get("id"), "result": frame.get("result") or {}})
        except (OSError, ValueError, EOFError):
            pass
        finally:
            os._exit(0)  # daemon gone: IDE reconnects and spawns a fresh adapter

    threading.Thread(target=_pump_daemon, daemon=True, name="shared-mcp-pump").start()
    try:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("method") == "notifications/cancelled":
                _write_frame(sock, {"op": "cancel", "id": (msg.get("params") or {}).get("requestId")})
                continue
            if msg.get("id") is None:
                continue  # notification: no response expected
            _write_frame(sock, {"op": "mcp", "id": msg.get("id"), "method": msg.get("method"), "params": msg.get("params") or {}})
    except (OSError, ValueError):
        pass
    finally:
        try:
            _write_frame(sock, {"op": "bye"})
        except OSError:
            pass
        sock.close()
    return True
