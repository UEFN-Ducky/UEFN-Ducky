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
# backend.bridge.shared_mcp.SLOT_ENV: set on every agent host, its agents and their adapters.
_SLOT_ENV = "UEFN_DUCKY_SHARED_MCP_SLOT"
_FALLBACKS = "shared_mcp_fallbacks.json"
_MAX_FRAME = 16 * 1024 * 1024
# A busy daemon can miss the hello window; one more try before spawning or falling back
# to a dedicated bridge (a whole backend, 300-700 MB, per agent).
_HELLO_TIMEOUT_S = 10.0
_HELLO_BACKOFF_S = 1.0


def _moved_panel_port() -> int:
    """settings.PANEL_PORT_FILE: the port the app moved to when Windows reserved 4199/4200."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    try:
        return int(json.loads((Path(base) / "UEFN-Ducky" / "panel_port.json").read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


# frontend.settings.PANEL_LISTENER_PORT - 1, read the same way (this module stays stdlib-only).
_PANEL_PORT = int(os.environ.get("UEFN_DUCKY_PANEL_PORT") or _moved_panel_port() or 4200) - 1
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
# Safe to send again to a fresh daemon. A tools/call is not: it may have run already.
_RESENDABLE = frozenset({"initialize", "ping", "tools/list", "resources/list", "prompts/list"})
_RESTARTED = (
    "Ducky's tool server restarted while this call was running, so it may not have finished. "
    "Check whether it took effect, then call it again if needed."
)
# Ducky is closed, restarting or updating itself. Nov 11 2026: a restart that took over 30 s
# made this adapter exit, and Codex (which never restarts an MCP server) had no Ducky tools
# for the rest of its session. The adapter now stays up and joins the tool server when it is
# back; a call that arrives in between hears this instead of hanging.
_DOWN = (
    "UEFN Ducky is not running or is restarting, so this did not run. Its tools come back by "
    "themselves once Ducky is up: try again in a minute."
)
_FIRST_WAIT_S = 30.0  # the usual restart: a fresh tool server answers within this
_HOLD_CALL_S = 45.0  # a tool call that arrives during a restart waits this long for it
# After that: watch for a tool server (the app coming back starts its own) and ask a running
# app for one this often. Asking is cheap: the app starts at most one, and answers "starting"
# for 45 s after it did (seen live Nov 12 2026: a tool server ended 10 s after its start, the
# first ask was inside those 45 s, and the next one came two minutes later).
_ASK_EVERY_S = 15.0
_WATCH_S = 120.0  # with no app to ask: start one from here at most this often
_RESPAWN_WAIT_S = 30.0
_IDENTITY_ENV = (
    ("DUCKY_RUN_ID", "run_id"),
    ("DUCKY_CONV_ID", "conv_id"),
    ("DUCKY_PROFILE_ID", "profile_id"),
    ("DUCKY_DUCKY_NAME", "ducky_name"),
    ("DUCKY_MODEL", "model"),
    ("DUCKY_CODING_AGENT", "coding_agent"),
    ("DUCKY_GROUP_ID", "group_id"),
    ("DUCKY_LEADER_CONV_ID", "leader_conv_id"),
    # The project this agent was started in. The shared daemon was started for whichever
    # project was open then; without these an agent in another project wrote there.
    ("UEFN_DUCKY_PROJECT_ROOT", "project_root"),
    ("UEFN_VSCODE_WORKSPACE_FOLDERS", "workspace_folders"),
    # The agent host this agent runs in: steer and question checks go to it, not the app.
    ("DUCKY_AGENT_HOST_PIPE", "host_pipe"),
    ("DUCKY_AGENT_HOST_KEY", "host_key"),
)


def _app_data() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "UEFN-Ducky"


def _slot() -> str:
    slot = (os.environ.get(_SLOT_ENV) or "").strip()
    return slot if slot.isdigit() else ""


def _state_name() -> str:
    """Same file as ``shared_mcp.state_path``: an agent host's agents join that host's daemon.

    Nov 4 2026: adapters always read ``shared_mcp.json``, so every host agent joined the
    app's daemon and each host's own daemon idled at 0 clients and ~700 MB.
    """
    slot = _slot()
    return f"shared_mcp-h{slot}.json" if slot else _STATE


def _read_state() -> dict | None:
    try:
        data = json.loads((_app_data() / _state_name()).read_text(encoding="utf-8"))
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


def _hello_attempt(state: dict, read_timeout: float = _HELLO_TIMEOUT_S) -> tuple[socket.socket | None, bool]:
    """(socket or None, True when the daemon accepted but missed the hello window)."""
    try:
        sock = socket.create_connection((str(state.get("host") or "127.0.0.1"), int(state["port"])), timeout=2.0)
    except OSError:
        return None, False
    try:
        sock.settimeout(read_timeout)
        _write_frame(sock, {"op": "hello", "token": str(state["token"]), "key": state.get("key") or {}, "identity": _identity()})
        reply = _read_frame(sock)
    except (socket.timeout, TimeoutError):
        sock.close()
        return None, True
    except (OSError, ValueError, EOFError):
        sock.close()
        return None, False
    if reply.get("op") != "hello_ok":
        sock.close()
        return None, False
    sock.settimeout(None)
    return sock, False


def _try_hello(state: dict) -> socket.socket | None:
    return _hello_attempt(state)[0]


def _hello_with_retry(state: dict) -> socket.socket | None:
    """A daemon that is up but slow gets one more, longer, try after a short backoff."""
    sock, slow = _hello_attempt(state)
    if sock is None and slow:
        time.sleep(_HELLO_BACKOFF_S)
        sock, _slow = _hello_attempt(state, _HELLO_TIMEOUT_S * 2)
    return sock


def _record_fallback(reason: str) -> int:
    """Count each agent that runs its own dedicated bridge instead of the shared daemon."""
    path = _app_data() / _FALLBACKS
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        count = int(data.get("count") or 0) if isinstance(data, dict) else 0
    except (OSError, ValueError, TypeError):
        count = 0
    count += 1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"count": count, "last_at": time.time(), "slot": _slot(),
                                    "reason": reason, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass
    try:
        sys.stderr.write(f"[ducky] shared tool server unreachable ({reason}): dedicated bridge #{count}\n")
        sys.stderr.flush()
    except (OSError, ValueError, AttributeError):
        pass
    return count


def _panel_port() -> int:
    """The app's panel port now. A restarted app may sit on another pair than the one this
    adapter started with (Windows reserved 4199/4200): the port file names it."""
    if os.environ.get("UEFN_DUCKY_PANEL_PORT"):
        return _PANEL_PORT
    moved = _moved_panel_port()
    return moved - 1 if moved else _PANEL_PORT


def _ask_app_to_start() -> bool:
    """Have the running app start the daemon, so it is the app's child.

    Started from here it sat in this agent's process tree and job object, and died
    when this agent finished or was stopped, taking every other agent's tools with it.
    """
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        f"http://127.0.0.1:{_panel_port()}/__panel_shared_mcp",
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
    # An agent host's agent never starts a tool server: its host serves the slot itself
    # (agent_host.tool_server), and outlives the agent. A slow hello used to start a whole
    # --shared-daemon backend per agent here; connect() just keeps waiting for the host.
    if _slot():
        return
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
    if state and not _stale(state) and (sock := _hello_with_retry(state)):
        return sock
    _spawn_daemon(bridge_args)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _read_state()
        if state and not _stale(state) and (sock := _try_hello(state)):
            return sock
        time.sleep(0.1)
    return None


def _watch_for_daemon(timeout_s: float, stop: threading.Event | None = None) -> socket.socket | None:
    """Join a daemon someone else starts (the app coming back up). Never starts one."""
    deadline = time.time() + timeout_s
    while time.time() < deadline and not (stop is not None and stop.is_set()):
        state = _read_state()
        if state and not _stale(state) and (sock := _try_hello(state)):
            return sock
        time.sleep(0.25)
    return None


def _request_frame(msg: dict) -> dict:
    return {"op": "mcp", "id": msg.get("id"), "method": msg.get("method"), "params": msg.get("params") or {}}


def _cannot_run(request: dict, text: str) -> dict:
    """The answer for a request that did not run (or may not have): a failed tool result for
    a tool call, a JSON-RPC error for anything else."""
    if request.get("method") == "tools/call":
        return {"jsonrpc": "2.0", "id": request.get("id"),
                "result": {"content": [{"type": "text", "text": text}], "isError": True}}
    return {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": text}}


def run(bridge_args: list[str]) -> bool:
    """Relay stdio <-> daemon until the IDE closes stdin. False = no daemon (use dedicated).

    If the daemon goes away mid-session this joins a fresh one, however long that takes:
    agents never reconnect an MCP server themselves, so exiting here left them without
    Ducky tools for the rest of the run ("Transport closed"; Codex after a Ducky update).
    """
    first = connect(bridge_args)
    if first is None:
        _record_fallback("no shared daemon answered")
        return False
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    out_lock = threading.Lock()
    link_lock = threading.Lock()  # guards the socket swap and every write
    link: dict[str, socket.socket | None] = {"sock": first}  # None while no daemon answers
    pending: dict[str, dict] = {}  # request id (as JSON) -> request, until answered
    closing = threading.Event()  # the IDE closed stdin: a dropped socket is ours, not a dead daemon
    up = threading.Event()  # a daemon is joined
    up.set()

    def _emit(msg: dict) -> None:
        with out_lock:
            stdout.write(json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n")
            stdout.flush()

    def _send(frame: dict, request: dict | None = None) -> None:
        if request is not None and request.get("method") not in _RESENDABLE and not up.is_set():
            up.wait(_HOLD_CALL_S)  # a quick restart: the call goes to the fresh tool server
        with link_lock:
            sock = link["sock"]
            if sock is None:
                # No tool server yet; the pump is bringing one back. What can be sent again
                # waits for it, a tool call hears why it did not run.
                if request is not None and request.get("method") in _RESENDABLE:
                    pending[json.dumps(request.get("id"))] = request
                elif request is not None:
                    _emit(_cannot_run(request, _DOWN))
                return
            if request is not None:
                pending[json.dumps(request.get("id"))] = request
            try:
                _write_frame(sock, frame)
            except OSError:
                pass  # the pump reconnects and settles what is pending

    def _reconnect(dead: socket.socket) -> bool:
        """Join a fresh daemon. False only when the IDE closed stdin: with none to reach this
        keeps trying for as long as the IDE holds the server."""
        with link_lock:
            if closing.is_set():
                return False
            if link["sock"] is not dead:
                return True
            try:
                dead.close()
            except OSError:
                pass
            link["sock"] = None
            up.clear()
            # A call that was running may or may not have finished: say so now, not after
            # however long the restart takes.
            for key, request in list(pending.items()):
                if request.get("method") not in _RESENDABLE:
                    pending.pop(key, None)
                    _emit(_cannot_run(request, _RESTARTED))
        fresh = connect(bridge_args, _FIRST_WAIT_S)
        started_here = time.time()
        while fresh is None and not closing.is_set():
            # A slow restart or an update: join the app's own tool server when it shows up.
            fresh = _watch_for_daemon(_ASK_EVERY_S, closing)
            if fresh is not None or closing.is_set():
                break
            if not _slot() and _ask_app_to_start():
                continue  # the app is up and has one coming (or just started one): keep watching
            if time.time() - started_here >= _WATCH_S:
                # No app to ask for this long: start one from here, never one per try.
                fresh = connect(bridge_args, _RESPAWN_WAIT_S)
                started_here = time.time()
        if fresh is None:
            return False
        with link_lock:
            if closing.is_set():
                fresh.close()
                return False
            link["sock"] = fresh
            for request in list(pending.values()):  # only what is safe to send again is left
                try:
                    _write_frame(fresh, _request_frame(request))
                except OSError:
                    pass
        up.set()
        return True

    def _pump_daemon() -> None:
        while True:
            with link_lock:
                sock = link["sock"]
            try:
                if sock is None:
                    raise OSError("no daemon")
                frame = _read_frame(sock)
            except (OSError, ValueError, EOFError):
                if sock is not None and _reconnect(sock):
                    continue
                return  # the IDE closed stdin
            if frame.get("op") != "mcp":
                continue
            if frame.get("method") and "id" not in frame:
                # Notifications: tools/list_changed, and progress for a running call, which
                # keeps the CLI from aborting it after 1800 s with no response or progress.
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
        up.set()  # nothing waits on a restart once the IDE is gone
        with link_lock:
            sock = link["sock"]
            if sock is not None:
                try:
                    _write_frame(sock, {"op": "bye"})
                except OSError:
                    pass
                sock.close()
    return True
