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
_PANEL_PORT = 4199  # frontend.settings.PANEL_LISTENER_PORT - 1 (this module stays stdlib-only)
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
# Safe to send again to a fresh daemon. A tools/call is not: it may have run already.
_RESENDABLE = frozenset({"initialize", "ping", "tools/list", "resources/list", "prompts/list"})
_RESTARTED = (
    "Ducky's tool server restarted while this call was running, so it may not have finished. "
    "Check whether it took effect, then call it again if needed."
)
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


def _our_version() -> str:
    return (os.environ.get("UEFN_DUCKY_APP_VERSION") or "").strip()


def _state_version(state: dict) -> str:
    key = state.get("key") if isinstance(state.get("key"), dict) else {}
    return str(key.get("version") or "")


def _version_tuple(raw: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in raw.split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _stale(state: dict) -> bool:
    """An older daemon must not keep serving this exe (it lacks newer tools).

    A newer one is used as is: an agent still running the old exe killing it would
    cut every newer agent off, and they would kill its replacement in turn.
    """
    ours = _our_version()
    theirs = _state_version(state)
    if not ours or theirs == ours:
        return False
    return not theirs or _version_tuple(theirs) < _version_tuple(ours)


def _stop_bridge_pid(pid: int) -> None:
    """End a leftover UEFN-Ducky-Bridge that is holding the shared daemon."""
    if pid <= 0 or pid == os.getpid() or os.name != "nt":
        return
    flags = subprocess.CREATE_NO_WINDOW
    try:
        listed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=5, creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return
    if "uefn-ducky-bridge" not in (listed.stdout or "").lower():
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=5, creationflags=flags,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


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


def _ask_app_to_start() -> bool:
    """Have the running app start the daemon, so it is the app's child.

    Started from here it sat in this agent's process tree and job object, and died
    when this agent finished or was stopped, taking every other agent's tools with it.
    """
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"http://127.0.0.1:{_PANEL_PORT}/__panel_shared_mcp",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=10) as resp:
            body = json.loads(resp.read() or b"{}")
    except (OSError, ValueError):
        return False
    return isinstance(body, dict) and bool(body.get("ok"))


def _spawn_daemon(bridge_args: list[str]) -> None:
    if _ask_app_to_start():
        return
    # No app running (an IDE on its own): start it here, outside this agent's job if allowed.
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
    for extra in (_CREATE_BREAKAWAY_FROM_JOB, 0) if os.name == "nt" else (0,):
        try:
            subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=flags | extra, close_fds=True)
            return
        except OSError:
            continue  # the job refuses breakaway: start inside it


def connect(bridge_args: list[str], timeout_s: float = 30.0) -> socket.socket | None:
    """Existing daemon first; spawn one only if nobody answers.

    A daemon from a different app version is stopped (it still holds the
    single-instance lock) so this exe can serve tools such as web_search.
    """
    state = _read_state()
    if state and _stale(state):
        _stop_bridge_pid(int(state.get("pid") or 0))
        deadline = time.time() + 3.0
        while time.time() < deadline and state and _stale(state):
            time.sleep(0.1)
            state = _read_state()
    if state and not _stale(state) and (sock := _try_hello(state)):
        return sock
    _spawn_daemon(bridge_args)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _read_state()
        if state and not _stale(state) and (sock := _try_hello(state)):
            return sock
        time.sleep(0.1)
    return None


def _request_frame(msg: dict) -> dict:
    return {"op": "mcp", "id": msg.get("id"), "method": msg.get("method"), "params": msg.get("params") or {}}


def run(bridge_args: list[str]) -> bool:
    """Relay stdio <-> daemon until the IDE closes stdin. False = no daemon (use dedicated).

    If the daemon goes away mid-session this reconnects to a fresh one. Agents never
    reconnect an MCP server themselves, so exiting here left them without Ducky tools
    for the rest of the run ("Transport closed").
    """
    first = connect(bridge_args)
    if first is None:
        return False
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    out_lock = threading.Lock()
    link_lock = threading.Lock()  # guards the socket swap and every write
    link = {"sock": first}
    pending: dict[str, dict] = {}  # request id (as JSON) -> request, until answered
    closing = threading.Event()  # the IDE closed stdin: a dropped socket is ours, not a dead daemon

    def _emit(msg: dict) -> None:
        with out_lock:
            stdout.write(json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n")
            stdout.flush()

    def _send(frame: dict, request: dict | None = None) -> None:
        with link_lock:
            if request is not None:
                pending[json.dumps(request.get("id"))] = request
            try:
                _write_frame(link["sock"], frame)
            except OSError:
                pass  # the pump reconnects and settles what is pending

    def _reconnect(dead: socket.socket) -> bool:
        with link_lock:
            if closing.is_set():
                return False
            if link["sock"] is not dead:
                return True
            try:
                dead.close()
            except OSError:
                pass
            fresh = connect(bridge_args)
            if fresh is None:
                return False
            link["sock"] = fresh
            for key, request in list(pending.items()):
                if request.get("method") in _RESENDABLE:
                    try:
                        _write_frame(fresh, _request_frame(request))
                    except OSError:
                        pass
                    continue
                pending.pop(key, None)
                _emit({
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": {"content": [{"type": "text", "text": _RESTARTED}], "isError": True},
                })
        return True

    def _pump_daemon() -> None:
        while True:
            with link_lock:
                sock = link["sock"]
            try:
                frame = _read_frame(sock)
            except (OSError, ValueError, EOFError):
                if _reconnect(sock):
                    continue
                if closing.is_set():
                    return
                os._exit(0)  # no daemon could be started: let the IDE restart this server
            if frame.get("op") != "mcp":
                continue
            if frame.get("method") == "notifications/tools/list_changed" and "id" not in frame:
                _emit({"jsonrpc": "2.0", "method": frame["method"], "params": frame.get("params") or {}})
                continue
            pending.pop(json.dumps(frame.get("id")), None)
            if frame.get("error"):
                _emit({"jsonrpc": "2.0", "id": frame.get("id"), "error": frame["error"]})
            else:
                _emit({"jsonrpc": "2.0", "id": frame.get("id"), "result": frame.get("result") or {}})

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
                _send({"op": "cancel", "id": (msg.get("params") or {}).get("requestId")})
                continue
            if msg.get("id") is None:
                continue  # notification: no response expected
            _send(_request_frame(msg), msg)
    except (OSError, ValueError):
        pass
    finally:
        closing.set()
        with link_lock:
            sock = link["sock"]
            try:
                _write_frame(sock, {"op": "bye"})
            except OSError:
                pass
            sock.close()
    return True
