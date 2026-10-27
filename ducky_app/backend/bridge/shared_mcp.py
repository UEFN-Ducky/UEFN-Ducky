"""Shared FastMCP daemon on 127.0.0.1 (token + daemon key).

Off unless Settings or ``UEFN_DUCKY_SHARED_MCP`` turn it on (not yet measured with many
agents on a real build). When on, each agent host's agents share one daemon
(``UEFN_DUCKY_SHARED_MCP_SLOT`` names its state file), so a turn no longer starts its own
tool process (backend import + 1,000+ tool registrations) and each host keeps its own GIL. Dedicated ``mcp.run()`` stdio is unchanged. Adapters speak MCP to the agent and
length-prefixed JSON frames to this process.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextvars
import hashlib
import json
import os
import re
import secrets
import socket
import struct
import threading
import time
from pathlib import Path
from typing import Any

_ENV = "UEFN_DUCKY_SHARED_MCP"
# Set by the supervisor on each agent host: its agents, their adapters and its daemon all
# read and write this host's own state file.
SLOT_ENV = "UEFN_DUCKY_SHARED_MCP_SLOT"
_STATE_NAME = "shared_mcp.json"
_MAX_FRAME = 16 * 1024 * 1024
# Ducky starts a new agent process every turn. A 3 s idle exit made almost every turn
# start this server again (5-9 s: backend import + 1,000+ tool registrations), and
# agents that wait only ~1 s for MCP servers came up with no Ducky tools.
_IDLE_EXIT_S = 900.0
_STARTUP_GRACE_S = 60.0
# Handshake must finish while another tool call is still running. A cold
# plugin load may wait this long; a ready load does not wait at all.
_LIST_WAIT_S = 3.0

_loop: asyncio.AbstractEventLoop | None = None
_clients = 0
# Bumped on every connect: an idle timer started before a reconnect must not stop us.
_idle_gen = 0
# The one pending idle timer. Each agent turn connects and disconnects, and a new
# 15-minute timer thread per disconnect piled up dozens of sleeping threads.
_idle_timer: threading.Timer | None = None
_clients_lock = threading.Lock()
_stop = threading.Event()
_ready = threading.Event()
_ready_info: dict[str, Any] = {}
_inflight: dict[tuple[int, str], asyncio.Future[Any]] = {}
_shared_serving = False
_listen_sock: socket.socket | None = None
# Answered from Ducky's own tool catalog and skill index: a call past this never answers,
# so it is stopped and the agent hears why. Oct 18 2026: a user's agent sat 29 minutes on
# ducky_find_tools; agents (Claude Code) wait on an MCP call with no limit of their own.
QUICK_TOOLS = frozenset({"ducky_get_tools", "ducky_find_tools", "skill_search", "skill_read_subskill"})
QUICK_TOOL_LIMIT_S = 60.0
# Any other call still running this long: where it waits is recorded (events kind
# "tool_stuck") and it keeps running, since workflows, renders and questions take minutes.
STUCK_REPORT_S = 120.0
# Calls that wait by design (on the person, or a command / workflow / window with its own
# timeout): still recorded where they wait, but not counted as an anonymous agent.stuck,
# which said "stuck" 117 times a day for questions and test suites (Oct 25 2026).
WAITS_BY_DESIGN = frozenset({
    "ducky_ask_user", "ducky_terminal_run", "sandbox_run", "run_workflow", "run_workflow_node",
    "ducky_wait_uefn_ready", "uefn_wait_window", "uefn_logs_wait", "fortnite_client_wait", "playtester_wait",
})


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

    slot = (os.environ.get(SLOT_ENV) or "").strip()
    return default_app_data_dir() / (f"shared_mcp-h{slot}.json" if slot.isdigit() else _STATE_NAME)


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
    # The calling agent's project (backend.bridge.client resolves workspace paths from it).
    # host_pipe / host_key: the agent host this chat's agent runs in (backend.panel.rpc, D20).
    project = {k: str(data.get(k) or "").strip()
               for k in ("project_root", "workspace_folders", "host_pipe", "host_key")}
    conv_id = str(data.get("conv_id") or "")
    leader_conv_id = str(data.get("leader_conv_id") or "")
    return RunContext(
        run_id=str(data.get("run_id") or ""),
        conv_id=conv_id,
        profile_id=str(data.get("profile_id") or ""),
        ducky_name=str(data.get("ducky_name") or ""),
        model=str(data.get("model") or ""),
        coding_agent=str(data.get("coding_agent") or "") or "ducky",
        group_id=str(data.get("group_id") or ""),
        leader_conv_id=leader_conv_id,
        # The payload names the leader, not a flag (see identity.from_env).
        is_leader=bool(leader_conv_id and leader_conv_id == conv_id),
        lane=lane_t,
        extra={k: v for k, v in project.items() if v},
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
    # Sync tools run on these threads (see _call_tool). Some wait minutes on an agent
    # or on the user, so the pool is wider than asyncio's default.
    loop.set_default_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=64, thread_name_prefix="shared-mcp-tool")
    )

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

def _dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(by_alias=True, exclude_none=True)
    return value


def _tool_row(tool: Any) -> dict[str, Any]:
    if hasattr(tool, "parameters") and not hasattr(tool, "inputSchema"):
        # FastMCP's own tool record (the tool-manager snapshot): its arguments are
        # ``parameters``. Reading only inputSchema sent every tool with no arguments.
        params = tool.parameters if isinstance(tool.parameters, dict) else None
        row: dict[str, Any] = {
            "name": getattr(tool, "name", "") or "",
            "description": getattr(tool, "description", "") or "",
            "inputSchema": params or {"type": "object"},
        }
        for key, attr in (("title", "title"), ("outputSchema", "output_schema"), ("annotations", "annotations"), ("icons", "icons"), ("_meta", "meta")):
            value = _dump(getattr(tool, attr, None))
            if isinstance(value, list):
                value = [_dump(item) for item in value]
            if value:
                row[key] = value
        return row
    if hasattr(tool, "model_dump"):
        dumped = tool.model_dump(by_alias=True, exclude_none=True)
        schema = dumped.get("inputSchema") or dumped.get("input_schema") or {"type": "object"}
        return {**dumped, "inputSchema": schema}
    schema = getattr(tool, "inputSchema", None) or getattr(tool, "input_schema", None)
    row = {
        "name": getattr(tool, "name", "") or "",
        "description": getattr(tool, "description", "") or "",
        "inputSchema": schema if isinstance(schema, dict) else {"type": "object"},
    }
    for key in ("annotations", "outputSchema", "title", "icons", "_meta"):
        value = _dump(getattr(tool, key, None))
        if value is not None:
            row[key] = value
    return row


def _call_result(raw: Any) -> dict[str, Any]:
    """FastMCP's call_tool return as a CallToolResult body, shaped as the stdio server shapes it.

    A tool with an outputSchema returns (content, structured). Keeping only the content
    made Claude Code reject every such result: "Tool X has an output schema but did not
    return structured content".
    """
    structured: Any = None
    if isinstance(raw, tuple) and len(raw) == 2:
        raw, structured = raw
    elif isinstance(raw, tuple) and raw:
        raw = raw[0]
    if hasattr(raw, "model_dump") and hasattr(raw, "content"):
        return raw.model_dump(by_alias=True, exclude_none=True)  # the tool's own CallToolResult
    if isinstance(raw, dict) and "content" in raw:
        return raw
    if isinstance(raw, dict):
        structured, raw = raw, [{"type": "text", "text": json.dumps(raw, indent=2)}]
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
    else:
        blocks = [{"type": "text", "text": "" if raw is None else str(raw)}]
    result: dict[str, Any] = {"content": blocks, "isError": False}
    if structured is not None:
        result["structuredContent"] = structured
    return result


def _plugins_ready() -> bool:
    try:
        from backend.uefn_plugins.host import plugins_ready

        return bool(plugins_ready())
    except Exception:
        return False


def _snapshot_tools(mcp: Any) -> list[Any]:
    """Registered tools, read off the asyncio loop.

    A blocked tools/call (ducky_ask_user, a long editor call) must not hold
    tools/list. The tool manager dict is the catalog Claude needs to leave
    "still connecting".
    """
    tm = getattr(mcp, "_tool_manager", None)
    raw = getattr(tm, "_tools", None) if tm is not None else None
    if isinstance(raw, dict) and raw:
        return list(raw.values())
    return []


def _policy_filtered(tools: list[Any]) -> list[Any]:
    """What a client's tools/list gets: ProtectedFastMCP's policy filter plus the lean list."""
    if not tools:
        return tools
    from backend.bridge.plugin_gate import lean_tool_list

    tools = lean_tool_list(tools)
    try:
        from backend.workspace.ai_ignore import SAFE_TOOLS, current_policy

        if current_policy().strict:
            return [t for t in tools if getattr(t, "name", "") in SAFE_TOOLS]
    except Exception:
        pass
    return tools


def list_tools_for_handshake(mcp: Any) -> list[dict[str, Any]]:
    """tools/list body. Caller thread — never queued behind a tool call."""
    if not getattr(mcp, "_ducky_skip_plugin_wait", False) and not _plugins_ready():
        try:
            from backend.bridge.plugin_gate import wait_until_plugins_loaded

            wait_until_plugins_loaded(_LIST_WAIT_S)
        except Exception:
            pass
        try:
            from backend.mcp_plugins.bridge_proxy import wait_until_nested_proxies_synced

            wait_until_nested_proxies_synced(_LIST_WAIT_S)
        except Exception:
            pass
    tools = _policy_filtered(_snapshot_tools(mcp))
    if not isinstance(getattr(getattr(mcp, "_tool_manager", None), "_tools", None), dict):
        tools = _run(_async_list_tools(mcp))
    from backend.bridge.plugin_gate import lean_schema

    rows = [_tool_row(t) for t in tools]
    for row in rows:
        row["inputSchema"] = lean_schema(row["inputSchema"])
    return rows


def _catalog_revision(rows: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(sorted(rows, key=lambda row: row["name"]),
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _catalog_marker(tools: list[Any]) -> tuple[list[Any], tuple[int, ...]]:
    """Every object the catalog rows are read from, and their identities.

    The catalog watchers run twice a second per connected client, and rebuilding
    plus hashing every row costs ~13 ms at 1,000 tools. Registered tools and their
    fields are replaced, never edited in place, so the same objects in the same
    order mean the same catalog. A watcher keeps the objects of its last check
    alive, so a new object can never reuse one of their ids.
    """
    objects: list[Any] = []
    for tool in tools:
        objects.append(tool)
        objects.extend((getattr(tool, "__dict__", None) or {}).values())
    return objects, tuple(map(id, objects))


# Catalog watchers sleep until a registered tool changes. Oct 24 2026: one thread per
# connected agent rebuilt the snapshot and marker (~15k objects) twice a second, a whole
# core with 16 agents. Every add/remove bumps this revision (the registry dict below),
# and so do in-place edits (plugin_gate.clear_mcp_tool_cache) and AI-ignore policy saves.
CATALOG_SAFETY_S = 30.0
_catalog_cond = threading.Condition()
_catalog_rev = 0


def bump_catalog_revision() -> None:
    global _catalog_rev
    with _catalog_cond:
        _catalog_rev += 1
        _catalog_cond.notify_all()


def catalog_revision() -> int:
    return _catalog_rev


def wait_catalog_change(seen: int, stop: threading.Event | None = None,
                        timeout: float | None = None) -> int:
    """Block until the revision moves past ``seen`` (or ``stop``, or the safety timeout)."""
    with _catalog_cond:
        _catalog_cond.wait_for(lambda: _catalog_rev != seen or bool(stop and stop.is_set()),
                               CATALOG_SAFETY_S if timeout is None else timeout)
        return _catalog_rev


def wake_catalog_watchers() -> None:
    with _catalog_cond:
        _catalog_cond.notify_all()


class _NotifyingTools(dict):
    """The tool registry: any key change bumps the catalog revision."""

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        bump_catalog_revision()

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(key)
        bump_catalog_revision()

    def pop(self, *args: Any) -> Any:
        try:
            return super().pop(*args)
        finally:
            bump_catalog_revision()

    def popitem(self) -> Any:
        try:
            return super().popitem()
        finally:
            bump_catalog_revision()

    def setdefault(self, key: Any, default: Any = None) -> Any:
        value = super().setdefault(key, default)
        bump_catalog_revision()
        return value

    def update(self, *args: Any, **kwargs: Any) -> None:
        super().update(*args, **kwargs)
        bump_catalog_revision()

    def clear(self) -> None:
        super().clear()
        bump_catalog_revision()


def watch_registry(mcp: Any) -> None:
    """Make ``mcp``'s tool registry bump the catalog revision on every add and remove."""
    tm = getattr(mcp, "_tool_manager", None)
    tools = getattr(tm, "_tools", None)
    if type(tools) is dict:
        tm._tools = _NotifyingTools(tools)
        bump_catalog_revision()


_snapshot_lock = threading.Lock()
_snapshots: dict[int, tuple[Any, int, tuple[int, ...], list[Any], str]] = {}
catalog_rebuilds = 0  # rows rebuilt and hashed (tests count them)


def catalog_digest(mcp: Any, *, recheck: bool = False) -> str:
    """The shared catalog's revision hash: one rebuild per revision for every watcher.
    ``recheck`` (the safety timeout) compares object identities before rebuilding."""
    global catalog_rebuilds
    rev = _catalog_rev
    with _snapshot_lock:
        entry = _snapshots.get(id(mcp))
        if entry is not None and entry[0] is not mcp:
            entry = None
        if entry is not None and entry[1] == rev and not recheck:
            return entry[4]
        tools = _policy_filtered(_snapshot_tools(mcp))
        objects, ids = _catalog_marker(tools)
        if entry is not None and entry[2] == ids:
            digest = entry[4]
        else:
            catalog_rebuilds += 1
            digest = _catalog_revision([_tool_row(t) for t in tools])
        # Holding the marked objects keeps them alive, so their ids stay unique.
        _snapshots[id(mcp)] = (mcp, rev, ids, objects, digest)
        return digest


async def _async_list_tools(mcp: Any) -> list[Any]:
    listed = await mcp.list_tools()
    return list(listed or [])


def _is_sync_tool(mcp: Any, name: str) -> bool:
    tools = getattr(getattr(mcp, "_tool_manager", None), "_tools", None) or {}
    tool = tools.get(name)
    return tool is not None and getattr(tool, "is_async", True) is False


def _calling_mode(ident: Any) -> str:
    """Read the host-written turn checkpoint, never a client-supplied mode."""
    from frontend.ui_web.project_chats import load_conversation

    conv_id = getattr(ident, "conv_id", "")
    run_id = getattr(ident, "run_id", "")
    external = getattr(ident, "coding_agent", "ducky") in {"codex", "claude_code", "cursor"}
    if conv_id and run_id:
        conv = load_conversation(conv_id)
        for message in reversed(getattr(conv, "messages", []) or []):
            if message.get("role") == "assistant" and message.get("run_id") == run_id:
                # Older Agent checkpoints predate requested_mode.
                return message.get("requested_mode") or "agent"
    if external:
        raise ValueError("Cannot determine calling chat mode: no matching host turn checkpoint")
    return "agent"


class _CallTimedOut(Exception):
    """A quick tool did not answer within its limit: the agent gets this text as an error."""


def _in_own_thread(fn: Any, thread: dict[str, Any], name: str) -> asyncio.Future[Any]:
    """Run ``fn`` on a new thread with this call's context (identity, mode); its result
    settles the returned future on this loop. ``thread['id']`` names the thread."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[Any] = loop.create_future()
    ctx = contextvars.copy_context()

    def _settle(value: Any, error: BaseException | None) -> None:
        if fut.done():
            return
        if error is not None:
            fut.set_exception(error)
        else:
            fut.set_result(value)

    def _run() -> None:
        thread["id"] = threading.get_ident()
        try:
            value, error = ctx.run(fn), None
        except BaseException as exc:  # noqa: BLE001 — handed to the awaiting call
            value, error = None, exc
        try:
            loop.call_soon_threadsafe(_settle, value, error)
        except RuntimeError:
            pass  # the loop is gone (daemon stopping)

    threading.Thread(target=_run, daemon=True, name=f"shared-mcp-call:{name}"[:60]).start()
    return fut


async def _watched(work: asyncio.Future[Any], name: str, thread: dict[str, Any], ident: Any,
                   waits: dict[str, Any] | None = None) -> Any:
    """Await one tool call. A quick tool past :data:`QUICK_TOOL_LIMIT_S` is stopped and
    raises :class:`_CallTimedOut`; any call past the first check is recorded where it waits."""
    limit = QUICK_TOOL_LIMIT_S if name in QUICK_TOOLS else None
    first = min(limit, STUCK_REPORT_S) if limit is not None else STUCK_REPORT_S
    try:
        done, _ = await asyncio.wait({work}, timeout=first)
        if not done:
            _report_stuck(name, first, work, thread, ident, waits)
            if limit is not None:
                work.cancel()
                raise _CallTimedOut(
                    f"{name} did not answer within {limit:.0f} s and was stopped. Call it once more; "
                    "if it stops again, carry on without it and tell the person."
                )
            await asyncio.wait({work})
    except asyncio.CancelledError:
        work.cancel()  # the agent cancelled the call
        raise
    return work.result()


def _await_chain(work: Any) -> list[str]:
    """Where a suspended call waits: its coroutine await chain, innermost last."""
    lines: list[str] = []
    coro = work.get_coro() if isinstance(work, asyncio.Task) else None
    for _ in range(80):
        if coro is None:
            break
        frame = getattr(coro, "cr_frame", None) or getattr(coro, "gi_frame", None)
        if frame is None:
            lines.append(f"  awaiting {type(coro).__name__}\n")
            break
        lines.append(f'  File "{frame.f_code.co_filename}", line {frame.f_lineno}, in {frame.f_code.co_name}\n')
        coro = getattr(coro, "cr_await", None) or getattr(coro, "gi_yieldfrom", None)
    return lines


_LOOP_FILES = (f"{os.sep}asyncio{os.sep}", f"{os.sep}selectors.py")


def _idle_in_loop(lines: list[str]) -> bool:
    """The stack ends in an event loop waiting for work (``windows_events.py _poll``)."""
    last = lines[-1] if lines else ""
    return any(part in last for part in _LOOP_FILES)


def _call_stack(work: Any, thread: dict[str, Any]) -> list[str]:
    """Where one call waits. A sync tool runs on a thread with its own loop: while its
    coroutine awaits (a ``to_thread``, a question gate) that thread only sits in the loop's
    ``_poll``, which every Oct 24 2026 report showed. Then the coroutine's await chain and
    the loop's worker threads name the wait."""
    import sys
    import traceback

    frames = sys._current_frames()
    tid = thread.get("id")
    lines = traceback.format_stack(frames[tid]) if tid and tid in frames else []
    if lines and not _idle_in_loop(lines):
        return lines
    task = thread.get("task")
    chain = _await_chain(task if isinstance(task, asyncio.Task) else work)
    executor = getattr(thread.get("loop"), "_default_executor", None)
    for worker in list(getattr(executor, "_threads", None) or ()):
        stack = traceback.format_stack(frames[worker.ident]) if worker.ident in frames else []
        # A busy worker runs a work item; an idle one waits on its queue.
        if any("futures" in line and ", in run" in line for line in stack):
            chain += [f"{worker.name}:\n", *stack[-12:]]
    return chain or lines


def _report_stuck(name: str, seconds: float, work: Any, thread: dict[str, Any], ident: Any,
                  waits: dict[str, Any] | None = None) -> None:
    """One events row (kind ``tool_stuck``) with where the call waits. Never raises."""
    try:
        import sys
        import traceback

        frames = sys._current_frames()
        lines = _call_stack(work, thread)
        # A wait on another loop (nested MCP servers) continues on that loop's thread.
        for t in threading.enumerate():
            if t.name == "mcp-plugin-pool" and t.ident in frames:
                lines += ["mcp-plugin-pool:\n", *traceback.format_stack(frames[t.ident])[-12:]]
        from backend.store.repos import events
        from backend.util.privacy import scrub

        now = time.time()
        stack = scrub("".join(lines))[-8000:]
        conv_id = str(getattr(ident, "conv_id", "") or "")
        payload: dict[str, Any] = {"tool": name, "seconds": seconds, "conv_id": conv_id,
                                   "coding_agent": str(getattr(ident, "coding_agent", "") or ""), "stack": stack}
        waiting = dict(waits or {})
        if waiting:
            payload["waiting_on"] = waiting
        if name == "ducky_terminal_run":
            # Root cause 11: a run that waits on the person's approval card looks like a stuck command.
            payload["phase"] = "approval card" if waiting.get("panel_rpc") == "ask_user" else "running command"
        what = payload.get("phase") or (f"panel_rpc {waiting['panel_rpc']}" if waiting.get("panel_rpc") else "")
        message = (f"{name}: waiting_on {what} (conv {conv_id[:8]}, {seconds:.0f} s)" if what
                   else f"{name} still running after {seconds:.0f} s")
        events.insert("tool_stuck", ts=now, source=name, message=message, payload=payload)
        events.trim("tool_stuck", older_than=now - 30 * 86400, keep=200)
        if name in WAITS_BY_DESIGN or waiting.get("panel_rpc") == "ask_user":
            return
        from frontend import usage_stats

        usage_stats.stuck(str(getattr(ident, "coding_agent", "") or ""), float(seconds))
    except Exception:  # noqa: BLE001 — a report never breaks the call
        pass


_REFUSED = re.compile(r'^\s*(?:\{\s*"ok"\s*:\s*false\b|ERROR\b)')


def _refused(result: dict[str, Any]) -> bool:
    """A call that answered {"ok": false, …} or "ERROR: …" without isError: Ducky's plan,
    memory and skill tools refuse that way, and every such refusal counted as a call that
    worked (Oct 22 2026: a Verify tick refused three times showed as three successes)."""
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return bool(_REFUSED.match(str(block.get("text") or "")[:200]))
    return False


def _usage_tool_done(name: str, ok: bool, t0: float, ident: Any, arguments: dict[str, Any] | None) -> None:
    """One anonymous tool.call count for an outside agent's call; never raises."""
    try:
        from backend.agent.toolsets import effective_tool_name
        from frontend import usage_stats

        # ducky_call_tool counts as the tool it ran (as for Ducky's own agent), so a failure
        # names the tool that failed.
        args = arguments if isinstance(arguments, dict) else {}
        usage_stats.tool_done(
            effective_tool_name(name, args), ok=ok, duration_ms=(time.monotonic() - t0) * 1000.0,
            conv_id=str(getattr(ident, "conv_id", "") or ""), args=arguments if isinstance(arguments, dict) else None,
        )
    except Exception:
        pass


async def _call_tool(mcp: Any, name: str, arguments: dict[str, Any], ident: Any) -> dict[str, Any]:
    from backend.agent import hammer_guard
    from backend.workspace import identity

    from backend.agent.run_context import set_mode, reset_mode
    from backend.agent.toolsets.plan_safe import mode_permission_answer, mode_tool_block_reason

    mode = await asyncio.to_thread(_calling_mode, ident)
    mode_token = set_mode(mode)
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
        if mode != "agent":
            catalog = {tool.name: tool for tool in await _async_list_tools(mcp)}
            answer = mode_permission_answer(mode, name, arguments, catalog)
            if answer is not None:
                return {"content": [{"type": "text", "text": answer}], "isError": False}
            reason = mode_tool_block_reason(mode, name, arguments, catalog)
            if reason:
                return {"content": [{"type": "text", "text": reason}], "isError": True}
        thread: dict[str, Any] = {}
        from backend.panel import rpc

        waits: dict[str, Any] = {}
        waits_token = rpc.track_waits(waits)  # before the work starts: its context copies it
        if _is_sync_tool(mcp, name):
            # A sync tool runs on a thread of its own. On this loop it held up every agent
            # sharing the daemon, and one that waits on another agent (run_workflow,
            # ducky_send_chat_message, ducky_ask_user, …) deadlocked as soon as that
            # agent called a tool here: the Tycoony demo's preflight hung this way. Not on
            # the loop's pool either: calls that wait for minutes (or were given up) kept
            # its threads, and every later call queued behind them at its first step.
            # Listener POSTs stay serialized by bridge.client._listener_lock.
            async def _main() -> Any:
                thread["task"], thread["loop"] = asyncio.current_task(), asyncio.get_running_loop()
                return await mcp.call_tool(name, arguments or {})

            work = _in_own_thread(lambda: asyncio.run(_main()), thread, name)
        else:
            work = asyncio.ensure_future(mcp.call_tool(name, arguments or {}))
        rpc.untrack_waits(waits_token)
        t0 = time.monotonic()
        try:
            raw = await _watched(work, name, thread, ident, waits)
        except _CallTimedOut as exc:
            _usage_tool_done(name, False, t0, ident, arguments)
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        result = _call_result(raw)
        _usage_tool_done(name, not bool(result.get("isError")) and not _refused(result), t0, ident, arguments)
        return result
    finally:
        hammer_guard.reset_conversation(hammer)
        identity.reset_shared(shared)
        identity.reset(token)
        reset_mode(mode_token)


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
        rows = list_tools_for_handshake(mcp)
        return {"tools": rows, "_meta": {"revision": _catalog_revision(rows)}}
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
    catalog_lock = threading.Lock()
    closed = threading.Event()
    last_revision: str | None = None

    def _send(payload: dict[str, Any]) -> None:
        with write_lock:
            write_frame(handle, payload)

    def _dispatch(msg: dict[str, Any]) -> None:
        nonlocal last_revision
        req_id = msg.get("id")
        try:
            if msg.get("method") == "tools/list":
                with catalog_lock:
                    result = _handle_mcp(mcp, msg, ident, conn_id)
                    _send({"op": "mcp", "id": req_id, "result": result})
                    last_revision = result["_meta"]["revision"]
            else:
                result = _handle_mcp(mcp, msg, ident, conn_id)
                if req_id is not None:
                    _send({"op": "mcp", "id": req_id, "result": result})
        except Exception as exc:
            _send({"op": "mcp", "id": req_id, "error": {"code": -32000, "message": str(exc)}})

    def _watch_catalog() -> None:
        nonlocal last_revision
        # Observe only the local registry. Never probe/reconnect providers or wait
        # on the tool execution loop; unavailable registered rows stay visible.
        seen = -1
        while not closed.is_set():
            rev = wait_catalog_change(seen, closed)
            timed_out, seen = rev == seen, rev
            if closed.is_set():
                return
            try:
                with catalog_lock:
                    if last_revision is None:
                        continue
                    revision = catalog_digest(mcp, recheck=timed_out)
                    if revision != last_revision:
                        _send({"op": "mcp", "method": "notifications/tools/list_changed",
                               "params": {"_meta": {"revision": revision}}})
                        last_revision = revision
            except (OSError, EOFError):
                return
            except (ValueError, TypeError, RuntimeError):
                seen = -1  # a concurrent registration: look again at once

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
        threading.Thread(target=_watch_catalog, daemon=True, name=f"shared-mcp-catalog-{conn_id}").start()
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
        closed.set()
        wake_catalog_watchers()
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


def _cancel_idle_timer() -> None:
    """Caller holds ``_clients_lock``."""
    global _idle_timer
    if _idle_timer is not None:
        _idle_timer.cancel()
        _idle_timer = None


def _idle_expired(gen: int) -> None:
    """Stop only if nobody has connected since this idle timer started."""
    with _clients_lock:
        if _clients > 0 or gen != _idle_gen:
            return
    request_stop()


def serve_daemon(mcp: Any) -> None:
    """Block until idle after the last adapter disconnects."""
    global _shared_serving, _clients, _idle_gen, _listen_sock
    lock = _acquire_single_instance()
    if lock is None:
        return  # a daemon is already serving; the adapter will find it via state
    _shared_serving = True
    _stop.clear()
    watch_registry(mcp)
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
                _idle_gen += 1
                _cancel_idle_timer()
            cid = conn_seq

            def _run_client(h: socket.socket = conn, n: int = cid) -> None:
                global _clients, _idle_timer
                try:
                    _client_loop(mcp, h, token, key, n)
                finally:
                    with _clients_lock:
                        _clients -= 1
                        if _clients <= 0 and not _stop.is_set():
                            _cancel_idle_timer()
                            _idle_timer = threading.Timer(_IDLE_EXIT_S, _idle_expired, args=(_idle_gen,))
                            _idle_timer.daemon = True
                            _idle_timer.start()

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


_spawn_lock = threading.Lock()
_spawned_at = 0.0
# A daemon the app just started needs this long to import and write its state.
_SPAWN_HOLD_S = 45.0


def _daemon_answers(timeout_s: float = 3.0) -> bool:
    state = read_state() or {}
    if not state.get("token") or not state.get("port"):
        return False
    try:
        sock = connect_and_hello(
            token=str(state["token"]),
            key=state.get("key") if isinstance(state.get("key"), dict) else {},
            timeout_s=timeout_s,
            host=str(state.get("host") or "127.0.0.1"),
            port=int(state["port"]),
        )
    except (RuntimeError, OSError, ValueError):
        return False
    try:
        write_frame(sock, {"op": "bye"})
    except OSError:
        pass
    close_handle(sock)
    return True


def start_daemon_from_app() -> dict[str, Any]:
    """Start the shared daemon as a child of the app, never of an agent.

    An adapter that started the daemon itself left it inside that agent's process
    tree (and Codex's job object). When that agent finished or was stopped, the
    daemon died with it and every other agent lost its Ducky tools mid-run
    ("Transport closed").
    """
    global _spawned_at
    if not enabled():
        return {"ok": False, "error": "shared MCP is off"}
    with _spawn_lock:
        if _daemon_answers():
            return {"ok": True, "started": False}
        if time.time() - _spawned_at < _SPAWN_HOLD_S:
            return {"ok": True, "started": False, "starting": True}
        import subprocess

        from frontend.mcp_block import build_uefn_server_block
        from frontend.settings import PanelSettings

        block = build_uefn_server_block(PanelSettings.load())
        args = [a for a in block["args"] if a != "--adapter"] + ["--shared-daemon"]
        # No agent identity, and no PyInstaller boot vars (the child would reuse our extract dir).
        env = {
            k: v
            for k, v in {**os.environ, **block["env"]}.items()
            if not k.startswith(("DUCKY_", "_PYI_")) and k != "_MEIPASS2"
        }
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(
            [block["command"], *args],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
            close_fds=True,
        )
        _spawned_at = time.time()
        return {"ok": True, "started": True}


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
    global _clients, _idle_gen, _shared_serving, _listen_sock
    _stop.clear()
    _ready.clear()
    _ready_info.clear()
    _clients = 0
    _idle_gen = 0
    with _clients_lock:
        _cancel_idle_timer()
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
