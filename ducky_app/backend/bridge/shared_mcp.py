"""Shared FastMCP daemon on 127.0.0.1 (token + daemon key).

Default-off. Dedicated ``mcp.run()`` stdio is unchanged. Adapters speak MCP
to the IDE and length-prefixed JSON frames to this process.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any

_ENV = "UEFN_DUCKY_SHARED_MCP"
_STATE_NAME = "shared_mcp.json"
_MAX_FRAME = 16 * 1024 * 1024
_IDLE_EXIT_S = 3.0
_STARTUP_GRACE_S = 60.0

_loop: asyncio.AbstractEventLoop | None = None
_clients = 0
_clients_lock = threading.Lock()
_stop = threading.Event()
_ready = threading.Event()
_ready_info: dict[str, Any] = {}
_inflight: dict[tuple[int, str], asyncio.Future[Any]] = {}
_shared_serving = False
_listen_sock: socket.socket | None = None


def enabled(settings: Any | None = None) -> bool:
    raw = (os.environ.get(_ENV) or "").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    if settings is not None:
        return bool(getattr(settings, "shared_mcp", False))
    try:
        from frontend.settings import PanelSettings

        return bool(getattr(PanelSettings.load(), "shared_mcp", False))
    except Exception:
        return False


def daemon_key(*, port: int | None = None, project: str | None = None) -> dict[str, str]:
    from frontend import __version__

    if port is None:
        try:
            from backend.bridge.client import configured_listener_port

            port = int(configured_listener_port())
        except Exception:
            port = 4200
    if project is None:
        project = (os.environ.get("UEFN_DUCKY_PROJECT_ROOT") or "").strip()
        if not project:
            try:
                from frontend.settings import PanelSettings

                project = (PanelSettings.load().uefn_project_root or "").strip()
            except Exception:
                project = ""
    return {
        "version": str(__version__),
        "port": str(int(port)),
        "project": project.replace("\\", "/").lower(),
    }


def pipe_name() -> str:
    override = (os.environ.get("UEFN_DUCKY_SHARED_MCP_PIPE") or "").strip()
    if override:
        return override if override.startswith(r"\\.\pipe\\") else rf"\\.\pipe\{override}"
    user = "".join(ch if ch.isalnum() else "_" for ch in (os.environ.get("USERNAME") or "user"))
    return rf"\\.\pipe\uefn-ducky-mcp-{user}"


def state_path() -> Path:
    from frontend.settings import default_app_data_dir

    return default_app_data_dir() / _STATE_NAME


def keys_compatible(left: dict[str, str], right: dict[str, str]) -> bool:
    def _proj(row: dict[str, str]) -> str:
        return str(row.get("project") or "").replace("\\", "/").lower()

    return (
        str(left.get("version") or "") == str(right.get("version") or "")
        and str(left.get("port") or "") == str(right.get("port") or "")
        and _proj(left) == _proj(right)
    )


def identity_from_payload(raw: dict[str, Any] | None) -> Any:
    from backend.workspace.identity import RunContext

    data = raw if isinstance(raw, dict) else {}
    lane = data.get("lane")
    lane_t = tuple(str(x) for x in lane) if isinstance(lane, (list, tuple)) else None
    return RunContext(
        run_id=str(data.get("run_id") or ""),
        conv_id=str(data.get("conv_id") or ""),
        profile_id=str(data.get("profile_id") or ""),
        ducky_name=str(data.get("ducky_name") or ""),
        model=str(data.get("model") or ""),
        coding_agent=str(data.get("coding_agent") or "") or "ducky",
        group_id=str(data.get("group_id") or ""),
        leader_conv_id=str(data.get("leader_conv_id") or ""),
        lane=lane_t,
    )


def identity_from_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    from backend.workspace.identity import from_env

    ctx = from_env(environ)
    if ctx is None:
        return {}
    return {
        "run_id": ctx.run_id,
        "conv_id": ctx.conv_id,
        "profile_id": ctx.profile_id,
        "ducky_name": ctx.ducky_name,
        "model": ctx.model,
        "coding_agent": ctx.coding_agent,
        "group_id": ctx.group_id,
        "leader_conv_id": ctx.leader_conv_id,
    }


def _ensure_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is not None:
        if not _loop.is_running():
            deadline = time.time() + 2.0
            while not _loop.is_running() and time.time() < deadline:
                time.sleep(0.01)
        if _loop.is_running():
            return _loop
    loop = asyncio.new_event_loop()

    def _run() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    threading.Thread(target=_run, daemon=True, name="shared-mcp-loop").start()
    deadline = time.time() + 2.0
    while not loop.is_running() and time.time() < deadline:
        time.sleep(0.01)
    _loop = loop
    return loop


def _write_state(token: str, key: dict[str, str], *, host: str, port: int) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"host": host, "port": port, "token": token, "key": key, "pid": os.getpid()}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def _clear_state() -> None:
    try:
        state_path().unlink(missing_ok=True)
    except OSError:
        pass


def read_state() -> dict[str, Any] | None:
    path = state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --- localhost framed JSON ----------------------------------------------------

def close_handle(sock: socket.socket | None) -> None:
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


def _read_exact(sock: socket.socket, n: int) -> bytes:
    chunks: list[bytes] = []
    remain = n
    while remain:
        chunk = sock.recv(remain)
        if not chunk:
            raise EOFError("socket closed")
        chunks.append(chunk)
        remain -= len(chunk)
    return b"".join(chunks)


def _write_exact(sock: socket.socket, data: bytes) -> None:
    sock.sendall(data)


def read_frame(sock: socket.socket) -> dict[str, Any]:
    header = _read_exact(sock, 4)
    (n,) = struct.unpack(">I", header)
    if n > _MAX_FRAME:
        raise ValueError("frame too large")
    raw = _read_exact(sock, n)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("frame must be an object")
    return data


def write_frame(sock: socket.socket, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    _write_exact(sock, struct.pack(">I", len(raw)) + raw)


def connect_and_hello(
    *,
    token: str,
    key: dict[str, str],
    identity: dict[str, Any] | None = None,
    timeout_s: float = 30.0,
    host: str | None = None,
    port: int | None = None,
) -> socket.socket:
    info = _ready_info or read_state() or {}
    host = host or str(info.get("host") or "127.0.0.1")
    port = int(port or info.get("port") or 0)
    deadline = time.time() + timeout_s
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            sock = socket.create_connection((host, port), timeout=2.0)
            sock.settimeout(30.0)
            write_frame(
                sock,
                {"op": "hello", "token": token, "key": key, "identity": identity or {}},
            )
            reply = read_frame(sock)
            if reply.get("op") == "hello_ok":
                return sock
            close_handle(sock)
            raise RuntimeError(str(reply.get("reason") or "hello rejected"))
        except RuntimeError:
            raise
        except Exception as exc:
            last_err = exc
            time.sleep(0.05)
    raise RuntimeError(f"shared mcp connect failed: {last_err}")


# --- MCP helpers --------------------------------------------------------------

def _tool_row(tool: Any) -> dict[str, Any]:
    if hasattr(tool, "model_dump"):
        dumped = tool.model_dump(by_alias=True, exclude_none=True)
        schema = dumped.get("inputSchema") or dumped.get("input_schema") or {"type": "object"}
        return {
            "name": dumped.get("name") or "",
            "description": dumped.get("description") or "",
            "inputSchema": schema,
        }
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    return {
        "name": getattr(tool, "name", "") or "",
        "description": getattr(tool, "description", "") or "",
        "inputSchema": schema if isinstance(schema, dict) else {"type": "object"},
    }


def _call_result(raw: Any) -> dict[str, Any]:
    if isinstance(raw, tuple) and raw:
        raw = raw[0]
    if isinstance(raw, dict) and "content" in raw:
        return raw
    blocks: list[Any]
    if isinstance(raw, list):
        blocks = []
        for item in raw:
            if hasattr(item, "model_dump"):
                blocks.append(item.model_dump(by_alias=True, exclude_none=True))
            elif isinstance(item, dict):
                blocks.append(item)
            else:
                blocks.append({"type": "text", "text": str(getattr(item, "text", item))})
        return {"content": blocks, "isError": False}
    return {"content": [{"type": "text", "text": "" if raw is None else str(raw)}], "isError": False}


async def _list_tools(mcp: Any) -> list[dict[str, Any]]:
    if not getattr(mcp, "_ducky_skip_plugin_wait", False):
        # Same readiness as plugin_gate.list_tools_after_plugins: Store plugins,
        # then nested Epic/HTTP proxies, so the catalog matches a dedicated bridge.
        try:
            from backend.bridge.plugin_gate import wait_until_plugins_loaded

            await asyncio.to_thread(wait_until_plugins_loaded, 45.0)
        except Exception:
            pass
        try:
            from backend.mcp_plugins.bridge_proxy import wait_until_nested_proxies_synced

            await asyncio.to_thread(wait_until_nested_proxies_synced, 20.0)
        except Exception:
            pass
    tools = await mcp.list_tools()
    return [_tool_row(t) for t in tools]


async def _call_tool(mcp: Any, name: str, arguments: dict[str, Any], ident: Any) -> dict[str, Any]:
    from backend.agent import hammer_guard
    from backend.workspace import identity

    token = identity.bind(ident)
    shared = identity.mark_shared()
    hammer = hammer_guard.bind_conversation(getattr(ident, "conv_id", "") or "")
    try:
        if not getattr(mcp, "_ducky_skip_plugin_wait", False):
            try:
                from backend.bridge.plugin_gate import wait_until_plugins_loaded

                await asyncio.to_thread(wait_until_plugins_loaded, 45.0)
            except Exception:
                pass
        raw = await mcp.call_tool(name, arguments or {})
        return _call_result(raw)
    finally:
        hammer_guard.reset_conversation(hammer)
        identity.reset_shared(shared)
        identity.reset(token)


def _run(coro: Any) -> Any:
    fut = asyncio.run_coroutine_threadsafe(coro, _ensure_loop())
    return fut.result()


def _handle_mcp(mcp: Any, msg: dict[str, Any], ident: Any, conn_id: int) -> dict[str, Any]:
    method = str(msg.get("method") or "")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    req_id = msg.get("id")
    key = (conn_id, str(req_id))
    if method == "initialize":
        from frontend import __version__

        protocol = str(params.get("protocolVersion") or "2025-11-25")
        instructions = getattr(mcp, "instructions", None) or getattr(mcp, "_instructions", "") or ""
        return {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "uefn-ducky", "version": str(__version__)},
            "instructions": instructions,
        }
    if method == "ping" or method == "notifications/initialized":
        return {}
    if method == "resources/list":
        return {"resources": []}
    if method == "prompts/list":
        return {"prompts": []}
    if method == "tools/list":
        return {"tools": _run(_list_tools(mcp))}
    if method == "tools/call":
        name = str(params.get("name") or "")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        fut = asyncio.run_coroutine_threadsafe(_call_tool(mcp, name, args, ident), _ensure_loop())
        _inflight[key] = fut
        try:
            return fut.result()
        except asyncio.CancelledError:
            return {
                "content": [{"type": "text", "text": "Cancelled"}],
                "isError": True,
            }
        finally:
            _inflight.pop(key, None)
    raise ValueError(f"unsupported method {method}")


def _client_loop(mcp: Any, handle: int, token: str, key: dict[str, str], conn_id: int) -> None:
    ident = identity_from_payload({})
    write_lock = threading.Lock()

    def _send(payload: dict[str, Any]) -> None:
        with write_lock:
            write_frame(handle, payload)

    def _dispatch(msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        try:
            result = _handle_mcp(mcp, msg, ident, conn_id)
            _send({"op": "mcp", "id": req_id, "result": result})
        except Exception as exc:
            _send({"op": "mcp", "id": req_id, "error": {"code": -32000, "message": str(exc)}})

    try:
        hello = read_frame(handle)
        if hello.get("op") != "hello" or str(hello.get("token") or "") != token:
            write_frame(handle, {"op": "hello_reject", "reason": "auth"})
            return
        client_key = hello.get("key") if isinstance(hello.get("key"), dict) else {}
        if not keys_compatible(key, {str(k): str(v) for k, v in client_key.items()}):
            write_frame(handle, {"op": "hello_reject", "reason": "key_mismatch"})
            return
        ident = identity_from_payload(hello.get("identity") if isinstance(hello.get("identity"), dict) else {})
        write_frame(handle, {"op": "hello_ok"})
        while True:
            msg = read_frame(handle)
            op = str(msg.get("op") or "")
            if op == "bye":
                _send({"op": "bye"})
                return
            if op == "cancel":
                fut = _inflight.get((conn_id, str(msg.get("id"))))
                if fut is not None:
                    fut.cancel()
                _send({"op": "cancelled", "id": msg.get("id")})
                continue
            if op != "mcp":
                _send({"op": "error", "error": "unknown op"})
                continue
            threading.Thread(
                target=_dispatch, args=(msg,), daemon=True, name=f"shared-mcp-call-{conn_id}"
            ).start()
    except (EOFError, OSError):
        return
    finally:
        close_handle(handle)


def _acquire_single_instance() -> Any | None:
    """Exclusive OS lock so adapter races cannot start two daemons. None = another owns it."""
    path = state_path().with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")  # noqa: SIM115 — held for the process lifetime
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def serve_daemon(mcp: Any) -> None:
    """Block until idle after the last adapter disconnects."""
    global _shared_serving, _clients, _listen_sock
    lock = _acquire_single_instance()
    if lock is None:
        return  # a daemon is already serving; the adapter will find it via state
    _shared_serving = True
    _stop.clear()
    token = secrets.token_hex(16)
    key = daemon_key()
    listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen.bind(("127.0.0.1", 0))
    listen.listen(32)
    listen.settimeout(0.4)
    host, port = listen.getsockname()
    _listen_sock = listen
    _write_state(token, key, host=host, port=int(port))
    _ready_info.update({"token": token, "key": key, "host": host, "port": int(port)})
    _ready.set()
    _ensure_loop()
    conn_seq = 0
    # No adapter within the grace window (spawner died / raced) → do not linger.
    grace = threading.Timer(_STARTUP_GRACE_S, lambda: _clients or conn_seq or request_stop())
    grace.daemon = True
    grace.start()
    try:
        while not _stop.is_set():
            try:
                conn, _addr = listen.accept()
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                if _stop.is_set():
                    break
                continue
            if _stop.is_set():
                close_handle(conn)
                break
            conn_seq += 1
            with _clients_lock:
                _clients += 1
            cid = conn_seq

            def _run_client(h: socket.socket = conn, n: int = cid) -> None:
                global _clients
                try:
                    _client_loop(mcp, h, token, key, n)
                finally:
                    with _clients_lock:
                        _clients -= 1
                        if _clients <= 0 and not _stop.is_set():
                            idle = threading.Timer(_IDLE_EXIT_S, request_stop)
                            idle.daemon = True
                            idle.start()

            threading.Thread(target=_run_client, daemon=True, name=f"shared-mcp-{cid}").start()
    finally:
        close_handle(listen)
        _listen_sock = None
        _clear_state()
        _shared_serving = False
        try:
            lock.close()
        except OSError:
            pass


def request_stop() -> None:
    _stop.set()
    sock = _listen_sock
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass


def serving() -> bool:
    return _shared_serving


def reset_for_tests() -> None:
    global _clients, _shared_serving, _listen_sock
    _stop.clear()
    _ready.clear()
    _ready_info.clear()
    _clients = 0
    _shared_serving = False
    _inflight.clear()
    if _listen_sock is not None:
        close_handle(_listen_sock)
        _listen_sock = None
    _clear_state()


def wait_ready(timeout: float = 8.0) -> dict[str, Any]:
    if not _ready.wait(timeout):
        raise RuntimeError("daemon state missing")
    return dict(_ready_info)
