"""Thin stdio -> shared-daemon adapter. Stdlib only; never imports backend/FastMCP.

``Bridge.exe bridge --port N --adapter``: forward newline-delimited MCP JSON-RPC
from the IDE to the shared daemon (length-prefixed frames on 127.0.0.1). If no
daemon can be reached, return False so the caller runs the dedicated bridge.

A host agent's turn never waits for the tool server: when its host does not answer at
once, the start-up requests are answered from the host's own last answers (kept in
``agent_tool_catalog-h<slot>.json``, the same bytes, so the agent's tool list and its
cached prompt stay what they were) while the join goes on beside the turn.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_STATE = "shared_mcp.json"
# backend.bridge.shared_mcp.SLOT_ENV: set on every agent host, its agents and their adapters.
_SLOT_ENV = "UEFN_DUCKY_SHARED_MCP_SLOT"
_FALLBACKS = "shared_mcp_fallbacks.json"
_MAX_FRAME = 16 * 1024 * 1024
# A busy daemon can miss the hello window; one more try before spawning or falling back
# to a dedicated bridge (a whole backend, 300-700 MB, per agent).
_HELLO_TIMEOUT_S = 10.0
_HELLO_BACKOFF_S = 1.0
# An agent host checks that its own tool server answers before it starts an agent
# (coding_agents.readiness, up to 20 s), so a host agent that cannot join at once gains nothing
# by waiting on. Nov 11-17 2026: 1101 of 1225 turn starts joined in under 5 s and none of the
# other 124 ever did, each after 31-71 s here plus its own backend's load. A host agent gets
# one hello window, then this long for a tool server to show up.
_SLOT_WAIT_S = 5.0
# A host's tool server answers a hello in about 15 ms (198 of 198, Nov 17 2026). An agent
# whose host has not answered in this long starts from the saved answers instead of waiting.
_QUICK_HELLO_S = 1.5
_START_HOLD_S = 90.0  # a tool call made during such a start waits this long for a tool server
# A tool server of the agent's own answers initialize within a couple of seconds, plugins
# still loading. One that has not by now is ended, and the adapter goes back to its host.
_CHILD_INIT_S = 60.0
# Requests this adapter makes itself: never an id an agent uses (those are numbers).
_VERIFY_ID = "ducky-adapter-catalog"
_CHILD_INIT_ID = "ducky-adapter-initialize"
_RECENT_KEPT = 40  # fallbacks kept with their time and reason (self_health reads them)
# Why the last try to join a tool server failed: the reason a dedicated bridge is counted under.
_miss: list[str] = []


def _note_miss(why: str) -> None:
    _miss[:] = [why]


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

    An agent host's own tool server (a slot) is never stale. The host serves it inside its
    own process: an agent can neither stop it nor start another one, and the host that
    started this agent is that version, so those are this agent's tools. Nov 17 2026: an
    update downloaded while Ducky was open handed every agent's adapter off to the new
    version, which refused the running (older) host, waited 33 s for a newer one that could
    not come and then loaded a whole backend of its own: 40-58 s before every turn, until
    Ducky was restarted.
    """
    if _slot():
        return False
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
            capture_output=True, text=True, errors="replace", timeout=5, creationflags=flags,
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
        _note_miss(f"nothing listens on its port (pid {state.get('pid') or '?'})")
        return None, False
    try:
        sock.settimeout(read_timeout)
        _write_frame(sock, {"op": "hello", "token": str(state["token"]), "key": state.get("key") or {}, "identity": _identity()})
        reply = _read_frame(sock)
    except (socket.timeout, TimeoutError):
        sock.close()
        _note_miss(f"it took the connection but did not answer in {read_timeout:g} s (pid {state.get('pid') or '?'})")
        return None, True
    except (OSError, ValueError, EOFError):
        sock.close()
        _note_miss("it closed the connection before answering")
        return None, False
    if reply.get("op") != "hello_ok":
        sock.close()
        _note_miss(f"it refused this agent ({reply.get('reason') or reply.get('op') or 'no reason'})")
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
    recent: list[dict] = []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        count = int(data.get("count") or 0) if isinstance(data, dict) else 0
        if isinstance(data, dict) and isinstance(data.get("recent"), list):
            recent = [row for row in data["recent"] if isinstance(row, dict)]
    except (OSError, ValueError, TypeError):
        count = 0
    count += 1
    now = time.time()
    recent = [*recent[-(_RECENT_KEPT - 1):], {"at": now, "slot": _slot(), "reason": reason}]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"count": count, "last_at": now, "slot": _slot(), "reason": reason,
                                    "pid": os.getpid(), "recent": recent}), encoding="utf-8")
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


def connect(bridge_args: list[str], timeout_s: float = 30.0, *, patient: bool = True) -> socket.socket | None:
    """Existing daemon first; spawn one only if nobody answers.

    A daemon from a different app version is stopped (it still holds the
    single-instance lock) so this exe can serve tools such as web_search.

    ``patient=False`` (a host agent's first join, see ``_SLOT_WAIT_S``): one hello window and
    no try that runs past ``timeout_s``.
    """
    state = _read_state()
    if state and _stale(state):
        _stop_bridge_pid(int(state.get("pid") or 0))
        deadline = time.time() + 3.0
        while time.time() < deadline and state and _stale(state):
            time.sleep(0.1)
            state = _read_state()
    if state is None:
        _note_miss(f"no tool server is registered ({_state_name()} is missing or unreadable)")
    elif _stale(state):
        _note_miss(f"the tool server is version {_state_version(state) or '?'}, this agent {_our_version()}")
    elif sock := (_hello_with_retry(state) if patient else _hello_attempt(state, _HELLO_TIMEOUT_S)[0]):
        return sock
    _spawn_daemon(bridge_args)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = _read_state()
        if state and not _stale(state):
            if patient:
                sock = _try_hello(state)
            else:
                sock = _hello_attempt(state, max(0.5, min(_HELLO_TIMEOUT_S, deadline - time.time())))[0]
            if sock:
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


def _rows_revision(rows: list) -> str:
    """backend.bridge.shared_mcp._catalog_revision, for a tool list that came without one."""
    try:
        return hashlib.sha256(json.dumps(sorted(rows, key=lambda row: row["name"]), sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()
    except (TypeError, KeyError, ValueError):
        return ""


def _catalog_path() -> Path:
    return _app_data() / f"agent_tool_catalog-h{_slot()}.json"


def _read_catalog() -> dict:
    try:
        data = json.loads(_catalog_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_catalog() -> dict | None:
    """What this host's tool server last answered to ``initialize`` and ``tools/list``: an agent
    whose host is not answering starts from these. None until both have been kept."""
    data = _read_catalog()
    tools = data.get("tools")
    if not isinstance(data.get("initialize"), dict) or not isinstance(tools, dict):
        return None
    if not isinstance(tools.get("tools"), list) or not tools["tools"]:
        return None
    return data


def _keep_catalog(method: object, result: object, mem: dict) -> None:
    """Keep the host's answer for the next start, written only when it differs from the kept
    one (``mem``: this process's copy of the file, read once).

    The answer is stored as it came. An agent started from it gets the same tool list, in the
    same order, as one the host answered: the lean rows and their load marks are what keep an
    agent's prompt small and cached, so nothing here rebuilds or reorders them.
    """
    if not isinstance(result, dict) or method not in ("initialize", "tools/list"):
        return
    if not mem.get("read"):
        mem.update(_read_catalog())
        mem["read"] = True
    if method == "tools/list":
        rows = result.get("tools")
        if not isinstance(rows, list) or not rows:
            return
        meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
        revision = str(meta.get("revision") or "") or _rows_revision(rows)
        if revision and revision == mem.get("revision") and isinstance(mem.get("tools"), dict):
            return
        mem["tools"], mem["revision"] = result, revision
    else:
        def _fixed(answer: dict) -> dict:
            return {k: v for k, v in answer.items() if k != "protocolVersion"}  # echoes each client's own

        if isinstance(mem.get("initialize"), dict) and _fixed(mem["initialize"]) == _fixed(result):
            return
        mem["initialize"] = result
    path = _catalog_path()
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"saved_at": time.time(), "revision": mem.get("revision") or "",
                                   "initialize": mem.get("initialize"), "tools": mem.get("tools")},
                                  separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _saved_answer(saved: dict, request: dict) -> dict | None:
    """The result for a start-up request, from the host's kept answers (None: not one of them)."""
    method = request.get("method")
    if method == "initialize":
        answer = dict(saved["initialize"])
        params = request.get("params") if isinstance(request.get("params"), dict) else {}
        if params.get("protocolVersion"):
            answer["protocolVersion"] = str(params["protocolVersion"])  # as the tool server echoes it
        return answer
    if method == "tools/list":
        return saved["tools"]
    if method == "prompts/list":
        return {"prompts": []}
    if method == "resources/list":
        return {"resources": []}
    if method == "ping":
        return {}
    return None


def _quick_join() -> socket.socket | None:
    """A host agent's own tool server, when it answers at once (it nearly always does)."""
    state = _read_state()
    if state is None:
        _note_miss(f"no tool server is registered ({_state_name()} is missing or unreadable)")
        return None
    return _hello_attempt(state, _QUICK_HELLO_S)[0]


def _child_command(bridge_args: list[str]) -> tuple[list[str], str | None]:
    """A tool server of this agent's own: this program's ``bridge`` without ``--adapter``."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "bridge", *bridge_args], None
    return [sys.executable, "-m", "frontend", "bridge", *bridge_args], str(Path(__file__).resolve().parent.parent)


def _write_line(proc: subprocess.Popen, msg: dict) -> bool:
    try:
        proc.stdin.write(json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n")  # type: ignore[union-attr]
        proc.stdin.flush()  # type: ignore[union-attr]
        return True
    except (OSError, ValueError, AttributeError):
        return False


def _end_child(proc: subprocess.Popen) -> None:
    try:
        proc.stdin.close()  # type: ignore[union-attr]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        proc.wait(2.0)
    except (subprocess.TimeoutExpired, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _start_child(bridge_args: list[str], client_init: dict | None) -> subprocess.Popen | None:
    """Start that tool server and send it ``initialize`` (it answers into ``_CHILD_INIT_ID``)."""
    cmd, cwd = _child_command(bridge_args)
    try:
        proc = subprocess.Popen(cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except OSError:
        return None
    try:
        from frontend.app_versions import kill_with_me

        kill_with_me(proc)  # it never outlives this adapter
    except Exception:  # noqa: BLE001 - without the job it still ends when its stdin closes
        pass
    params = (client_init or {}).get("params")
    params = params if isinstance(params, dict) else {}
    hello = {"jsonrpc": "2.0", "id": _CHILD_INIT_ID, "method": "initialize", "params": {
        "protocolVersion": str(params.get("protocolVersion") or "2025-06-18"),
        # No client features: the agent had its initialize answered long ago, so nothing the
        # tool server then asked the client for (roots, sampling) would be understood.
        "capabilities": {},
        "clientInfo": params["clientInfo"] if isinstance(params.get("clientInfo"), dict)
        else {"name": "uefn-ducky-adapter", "version": _our_version() or "0"},
    }}
    if not _write_line(proc, hello):
        _end_child(proc)
        return None
    return proc


def run(bridge_args: list[str]) -> bool:
    """Relay stdio <-> the tool server until the IDE closes stdin. False = no daemon (use dedicated).

    If the daemon goes away mid-session this joins a fresh one, however long that takes:
    agents never reconnect an MCP server themselves, so exiting here left them without
    Ducky tools for the rest of the run ("Transport closed"; Codex after a Ducky update).

    A host agent whose host does not answer at once starts from the host's kept answers
    (``_load_catalog``) and goes on joining beside its turn (``_join_late``): after
    ``_SLOT_WAIT_S`` with no host, a tool server of its own runs as a child and is relayed
    to. Nov 11-17 2026: 124 of 1225 turn starts sat 35-100 s in this join before the model
    saw the message. With nothing kept yet (a host's first agent) it waits as before.
    """
    saved: dict | None = None
    if _slot():
        first = _quick_join()
        if first is None:
            saved = _load_catalog()
            if saved is None:
                first = connect(bridge_args, _SLOT_WAIT_S, patient=False)
    else:
        first = connect(bridge_args)
    if first is None and saved is None:
        _record_fallback(_miss[0] if _miss else "no shared daemon answered")
        return False
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    out_lock = threading.Lock()
    link_lock = threading.Lock()  # guards the link swap and every write
    # What serves the tools now: the daemon's socket, or this agent's own tool server (a child
    # process). Both None while neither answers.
    link: dict[str, Any] = {"sock": first, "child": None}
    pending: dict[str, dict] = {}  # request id (as JSON) -> request, until answered
    closing = threading.Event()  # the IDE closed stdin: a dropped socket is ours, not a dead daemon
    up = threading.Event()  # a tool server is joined
    starting = threading.Event()  # started from the kept answers: the first join is still on
    kept: dict[str, Any] = {}  # _keep_catalog's copy of the kept answers
    # A start from the kept answers: the agent's initialize, the revision of the tool list it
    # was given, and the child process once one is started.
    early: dict[str, Any] = {"init": None, "revision": "", "child": None}
    if first is not None:
        up.set()
    else:
        starting.set()

    def _emit(msg: dict) -> None:
        with out_lock:
            stdout.write(json.dumps(msg, separators=(",", ":")).encode("utf-8") + b"\n")
            stdout.flush()

    def _send(msg: dict) -> None:
        """One request from the agent, to whatever serves its tools now."""
        method = msg.get("method")
        if method not in _RESENDABLE and not up.is_set():
            # A quick restart, or a start still joining: the call goes to the tool server once
            # one is there.
            up.wait(_START_HOLD_S if starting.is_set() else _HOLD_CALL_S)
        with link_lock:
            sock, child = link["sock"], link["child"]
            if child is not None:
                if method == "initialize" and saved is not None:
                    # This adapter initialized the child itself: the agent gets the kept answer.
                    _emit({"jsonrpc": "2.0", "id": msg.get("id"), "result": _saved_answer(saved, msg)})
                    return
                if method is not None:
                    pending[json.dumps(msg.get("id"))] = msg
                _write_line(child, msg)
                return
            if sock is None:
                answer = _saved_answer(saved, msg) if saved is not None and starting.is_set() else None
                if answer is not None:
                    if method == "initialize":
                        early["init"] = msg
                    elif method == "tools/list":
                        early["revision"] = (str(saved.get("revision") or "")  # type: ignore[union-attr]
                                             or _rows_revision(answer.get("tools") or []))
                    _emit({"jsonrpc": "2.0", "id": msg.get("id"), "result": answer})
                elif method in _RESENDABLE:
                    # No tool server yet; one is being joined. What can be sent again waits for it.
                    pending[json.dumps(msg.get("id"))] = msg
                else:
                    _emit(_cannot_run(msg, _DOWN))  # a tool call hears why it did not run
                return
            pending[json.dumps(msg.get("id"))] = msg
            try:
                _write_frame(sock, _request_frame(msg))
            except OSError:
                pass  # the pump reconnects and settles what is pending

    def _aside(msg: dict) -> None:
        """A cancel, or another notification from the agent (only its own tool server takes those)."""
        with link_lock:
            sock, child = link["sock"], link["child"]
            if child is not None:
                if msg.get("method") != "notifications/initialized":  # this adapter sent the child its own
                    _write_line(child, msg)
            elif sock is not None and msg.get("method") == "notifications/cancelled":
                try:
                    _write_frame(sock, {"op": "cancel", "id": (msg.get("params") or {}).get("requestId")})
                except OSError:
                    pass

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

    def _checked(result: object) -> None:
        """The host's own tool list, asked for after a start from the kept one."""
        if not isinstance(result, dict):
            return
        _keep_catalog("tools/list", result, kept)
        meta = result.get("_meta") if isinstance(result.get("_meta"), dict) else {}
        live = str(meta.get("revision") or "") or _rows_revision(result.get("tools") or [])
        if live and early["revision"] and live != early["revision"]:
            # Only when it really differs: a change notice makes the agent list its tools
            # again, and a new tool list moves its cached prompt.
            _emit({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

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
            if frame.get("id") == _VERIFY_ID:
                _checked(frame.get("result"))
                continue
            request = pending.pop(json.dumps(frame.get("id")), None)
            if frame.get("error"):
                _emit({"jsonrpc": "2.0", "id": frame.get("id"), "error": frame["error"]})
            else:
                _emit({"jsonrpc": "2.0", "id": frame.get("id"), "result": frame.get("result") or {}})
                if request is not None and _slot():
                    _keep_catalog(request.get("method"), frame.get("result"), kept)

    def _watch_for_host() -> None:
        """No tool server left to relay to: join the host's when it is back."""
        dead = socket.socket()
        with link_lock:
            link["sock"] = dead
        if _reconnect(dead):
            _pump_daemon()

    def _pump_child(child: subprocess.Popen) -> None:
        answered = threading.Event()

        def _give_up() -> None:
            if not answered.is_set():
                try:
                    child.kill()  # its stdout ends, and the loop below with it
                except OSError:
                    pass

        deadline = threading.Timer(_CHILD_INIT_S, _give_up)
        deadline.daemon = True
        deadline.start()
        for raw in child.stdout:  # type: ignore[union-attr]
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("id") == _CHILD_INIT_ID and "method" not in msg:
                # Its answer to this adapter's initialize: the agent already has the kept one.
                answered.set()
                deadline.cancel()
                with link_lock:
                    if closing.is_set():
                        break
                    _write_line(child, {"jsonrpc": "2.0", "method": "notifications/initialized"})
                    link["child"] = child
                    for request in list(pending.values()):  # what waited for a tool server
                        _write_line(child, request)
                starting.clear()
                up.set()
                continue
            if "id" in msg and "method" not in msg:
                pending.pop(json.dumps(msg.get("id")), None)
            _emit(msg)
        deadline.cancel()
        with link_lock:
            link["child"] = None
            up.clear()
            lost = list(pending.values())
            pending.clear()
        starting.clear()
        if closing.is_set():
            _end_child(child)  # the agent left while it was still starting
            return
        for request in lost:
            _emit(_cannot_run(request, _DOWN))
        _watch_for_host()

    def _join_late() -> None:
        """After a start from the kept answers: the host's tool server when it answers within
        its window, else a tool server of this agent's own."""
        sock = connect(bridge_args, _SLOT_WAIT_S, patient=False)
        if sock is not None:
            with link_lock:
                if closing.is_set():
                    sock.close()
                    return
                link["sock"] = sock
                try:
                    for request in list(pending.values()):
                        _write_frame(sock, _request_frame(request))
                    if early["revision"]:
                        # The agent has the kept tool list: ask for the host's own. That also
                        # starts the host's change notices on this connection.
                        _write_frame(sock, {"op": "mcp", "id": _VERIFY_ID, "method": "tools/list", "params": {}})
                except OSError:
                    pass
            starting.clear()
            up.set()
            _pump_daemon()
            return
        if closing.is_set():
            return
        _record_fallback(_miss[0] if _miss else "no shared daemon answered")
        child = _start_child(bridge_args, early["init"])
        if child is None:
            starting.clear()
            _watch_for_host()
            return
        early["child"] = child
        _pump_child(child)

    if first is not None:
        threading.Thread(target=_pump_daemon, daemon=True, name="shared-mcp-pump").start()
    else:
        threading.Thread(target=_join_late, daemon=True, name="shared-mcp-join").start()
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
            if msg.get("id") is None:
                _aside(msg)  # a notification: no response expected
                continue
            _send(msg)
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
        if early["child"] is not None:
            _end_child(early["child"])
    return True
