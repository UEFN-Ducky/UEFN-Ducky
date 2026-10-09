"""Shared MCP daemon: flag default, identity isolation, cancel, tools/list."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.bridge import shared_mcp
from frontend import mcp_block
from frontend.settings import PanelSettings


def _fake_tools() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            name="ping",
            description="p",
            inputSchema={"type": "object", "properties": {}},
        ),
        SimpleNamespace(
            name="workspace_write_file",
            description="w",
            inputSchema={"type": "object"},
        ),
    ]


class FakeMcp:
    _ducky_skip_plugin_wait = True

    def __init__(self) -> None:
        self.seen: list[str] = []
        tools = _fake_tools()
        self._tool_manager = SimpleNamespace(_tools={t.name: t for t in tools})

    async def list_tools(self):
        return _fake_tools()

    async def call_tool(self, name, arguments):
        from backend.workspace.identity import current

        ctx = current()
        run_id = ctx.run_id if ctx else ""
        self.seen.append(run_id)
        if (arguments or {}).get("hold"):
            import asyncio

            await asyncio.sleep(20)
        if (arguments or {}).get("block"):
            # Blocks the daemon loop the way ducky_ask_user does. tools/list
            # must still return from the tool-manager snapshot.
            time.sleep(4)
        return [{"type": "text", "text": f"{name}:{run_id}"}]


def _fingerprint(tools: list[dict]) -> str:
    blob = json.dumps(
        [(t.get("name"), t.get("inputSchema")) for t in tools],
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode()).hexdigest()


def _serve(mcp: FakeMcp, monkeypatch, tmp_path: Path) -> threading.Thread:
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)
    shared_mcp.reset_for_tests()
    t = threading.Thread(target=shared_mcp.serve_daemon, args=(mcp,), daemon=True)
    t.start()
    shared_mcp.wait_ready(8.0)
    return t


def _hello(run_id: str, *, key=None):
    state = shared_mcp.wait_ready(8.0)
    sock = shared_mcp.connect_and_hello(
        token=str(state["token"]),
        key=key or state["key"],
        identity={"run_id": run_id, "conv_id": f"c-{run_id}"},
        timeout_s=8,
        host=str(state.get("host") or "127.0.0.1"),
        port=int(state.get("port") or 0),
    )
    sock.settimeout(8.0)
    return sock


def _rpc(handle: int, method: str, params: dict | None = None, req_id: int = 1) -> dict:
    shared_mcp.write_frame(
        handle, {"op": "mcp", "id": req_id, "method": method, "params": params or {}}
    )
    reply = shared_mcp.read_frame(handle)
    assert reply.get("op") == "mcp"
    if reply.get("error"):
        raise RuntimeError(reply["error"])
    return reply.get("result") or {}


def test_flag_default_off(monkeypatch) -> None:
    monkeypatch.delenv("UEFN_DUCKY_SHARED_MCP", raising=False)
    assert shared_mcp.enabled() is False
    assert shared_mcp.enabled(PanelSettings()) is False


def test_flag_env_on(monkeypatch) -> None:
    monkeypatch.setenv("UEFN_DUCKY_SHARED_MCP", "1")
    assert shared_mcp.enabled() is True
    monkeypatch.setenv("UEFN_DUCKY_SHARED_MCP", "0")
    assert shared_mcp.enabled(PanelSettings(shared_mcp=True)) is False


def test_server_block_unchanged_when_flag_off(monkeypatch) -> None:
    monkeypatch.delenv("UEFN_DUCKY_SHARED_MCP", raising=False)
    block = mcp_block.build_uefn_server_block(PanelSettings())
    assert "DUCKY_SHARED_MCP" not in (block.get("env") or {})


def test_server_block_adds_adapter_when_flag_on(monkeypatch) -> None:
    """Same exe, one extra arg; no node dependency."""
    monkeypatch.setenv("UEFN_DUCKY_SHARED_MCP", "1")
    on = mcp_block.build_uefn_server_block(PanelSettings())
    monkeypatch.setenv("UEFN_DUCKY_SHARED_MCP", "0")
    off = mcp_block.build_uefn_server_block(PanelSettings())
    assert on["command"] == off["command"]
    assert on["args"] == off["args"] + ["--adapter"]
    assert "--adapter" not in off["args"]


def test_adapter_stops_stale_daemon_before_spawn(monkeypatch, tmp_path) -> None:
    """An older shared daemon must not keep answering a newer app."""
    from frontend import shared_mcp_adapter as ad

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("UEFN_DUCKY_APP_VERSION", "9.9.9")
    state_dir = tmp_path / "UEFN-Ducky"
    state_dir.mkdir()
    path = state_dir / "shared_mcp.json"
    path.write_text(json.dumps({
        "host": "127.0.0.1",
        "port": 1,
        "token": "t",
        "key": {"version": "1.0.0", "port": "4200", "project": ""},
        "pid": 999999,
    }), encoding="utf-8")
    stopped: list[int] = []

    def _stop(pid: int) -> None:
        stopped.append(pid)
        path.unlink()

    spawned: list[list[str]] = []
    monkeypatch.setattr(ad, "_stop_bridge_pid", _stop)
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: spawned.append(args))
    assert ad.connect(["--port", "4200"], timeout_s=0.3) is None
    assert stopped == [999999]
    assert spawned == [["--port", "4200"]]


def test_adapter_falls_back_when_no_daemon(monkeypatch, tmp_path) -> None:
    """No daemon reachable -> run() is False so run_bridge continues dedicated."""
    from frontend import shared_mcp_adapter as ad

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    spawned: list[list[str]] = []
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: spawned.append(args))
    assert ad.connect(["--port", "4200"], timeout_s=0.3) is None
    assert spawned == [["--port", "4200"]]


def test_adapter_relays_to_daemon(monkeypatch, tmp_path) -> None:
    """Adapter hello + tools/list via the same frames the daemon speaks."""
    from frontend import shared_mcp_adapter as ad

    mcp = FakeMcp()
    thread = _serve(mcp, monkeypatch, tmp_path)
    try:
        state = shared_mcp.wait_ready(8.0)
        sock = ad._try_hello({"host": state["host"], "port": state["port"], "token": state["token"], "key": state["key"]})
        assert sock is not None
        ad._write_frame(sock, {"op": "mcp", "id": 1, "method": "tools/list", "params": {}})
        reply = ad._read_frame(sock)
        assert [t["name"] for t in reply["result"]["tools"]] == ["ping", "workspace_write_file"]
        sock.close()
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_keys_compatible() -> None:
    a = {"version": "1.2.1", "port": "4200", "project": r"C:/Game"}
    b = {"version": "1.2.1", "port": "4200", "project": "c:/game"}
    assert shared_mcp.keys_compatible(a, b)
    assert not shared_mcp.keys_compatible(a, {**b, "port": "4201"})


def test_concurrent_run_ids_isolated(monkeypatch, tmp_path) -> None:
    mcp = FakeMcp()
    thread = _serve(mcp, monkeypatch, tmp_path)
    try:
        a = _hello("run-a")
        b = _hello("run-b")
        ra = _rpc(a, "tools/call", {"name": "workspace_write_file", "arguments": {}}, 1)
        rb = _rpc(b, "tools/call", {"name": "workspace_write_file", "arguments": {}}, 1)
        texts = [
            (ra.get("content") or [{}])[0].get("text"),
            (rb.get("content") or [{}])[0].get("text"),
        ]
        assert texts == ["workspace_write_file:run-a", "workspace_write_file:run-b"]
        assert set(mcp.seen) == {"run-a", "run-b"}
        shared_mcp.write_frame(a, {"op": "bye"})
        shared_mcp.write_frame(b, {"op": "bye"})
        shared_mcp.close_handle(a)
        shared_mcp.close_handle(b)
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_cancel_does_not_kill_other_client(monkeypatch, tmp_path) -> None:
    mcp = FakeMcp()
    thread = _serve(mcp, monkeypatch, tmp_path)
    try:
        a = _hello("run-a")
        b = _hello("run-b")
        shared_mcp.write_frame(
            a,
            {
                "op": "mcp",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {"hold": True}},
            },
        )
        time.sleep(0.2)
        shared_mcp.write_frame(a, {"op": "cancel", "id": 7})
        # drain cancel ack + call result
        for _ in range(3):
            try:
                shared_mcp.read_frame(a)
            except Exception:
                break
        rb = _rpc(b, "tools/call", {"name": "ping", "arguments": {}}, 2)
        assert (rb.get("content") or [{}])[0].get("text") == "ping:run-b"
        shared_mcp.close_handle(a)
        shared_mcp.close_handle(b)
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_tools_list_fingerprint_stable(monkeypatch, tmp_path) -> None:
    mcp = FakeMcp()
    thread = _serve(mcp, monkeypatch, tmp_path)
    try:
        a = _hello("run-a")
        b = _hello("run-b")
        la = _rpc(a, "tools/list")
        lb = _rpc(b, "tools/list")
        assert _fingerprint(la["tools"]) == _fingerprint(lb["tools"])
        assert [t["name"] for t in la["tools"]] == ["ping", "workspace_write_file"]
        shared_mcp.close_handle(a)
        shared_mcp.close_handle(b)
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_hello_rejects_incompatible_key(monkeypatch, tmp_path) -> None:
    mcp = FakeMcp()
    thread = _serve(mcp, monkeypatch, tmp_path)
    try:
        state = shared_mcp.wait_ready(8.0)
        bad = {**state["key"], "port": "9999"}
        with pytest.raises(RuntimeError, match="key_mismatch"):
            shared_mcp.connect_and_hello(
                token=str(state["token"]),
                key=bad,
                identity={"run_id": "x"},
                timeout_s=4,
                host=str(state.get("host") or "127.0.0.1"),
                port=int(state.get("port") or 0),
            )
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_ready_plugins_skip_the_long_wait(monkeypatch) -> None:
    """A loaded plugin host returns the catalog without the 45s/20s waits."""
    waited: list[float] = []

    def _wait(timeout: float = 45.0) -> bool:
        waited.append(float(timeout))
        return True

    monkeypatch.setattr("backend.uefn_plugins.host.plugins_ready", lambda: True)
    monkeypatch.setattr("backend.bridge.plugin_gate.wait_until_plugins_loaded", _wait)
    monkeypatch.setattr(
        "backend.mcp_plugins.bridge_proxy.wait_until_nested_proxies_synced", _wait
    )
    tool = SimpleNamespace(name="ducky_plugin_scaffold", description="s", inputSchema={})
    mcp = SimpleNamespace(
        _ducky_skip_plugin_wait=False,
        _tool_manager=SimpleNamespace(_tools={tool.name: tool}),
    )
    rows = shared_mcp.list_tools_for_handshake(mcp)
    assert [t["name"] for t in rows] == ["ducky_plugin_scaffold"]
    assert waited == []


def test_tools_list_returns_while_a_call_blocks_the_loop(monkeypatch, tmp_path) -> None:
    mcp = FakeMcp()
    thread = _serve(mcp, monkeypatch, tmp_path)
    try:
        a = _hello("run-a")
        b = _hello("run-b")
        shared_mcp.write_frame(
            a,
            {
                "op": "mcp",
                "id": 9,
                "method": "tools/call",
                "params": {"name": "ping", "arguments": {"block": True}},
            },
        )
        time.sleep(0.3)
        started = time.monotonic()
        listed = _rpc(b, "tools/list")
        assert time.monotonic() - started < 2.0
        assert [t["name"] for t in listed["tools"]] == ["ping", "workspace_write_file"]
        # Let the blocked call finish so its thread does not write a closed socket.
        time.sleep(4.2)
        try:
            shared_mcp.read_frame(a)
        except Exception:
            pass
        shared_mcp.close_handle(a)
        shared_mcp.close_handle(b)
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_gui_closed_daemon_lists_tools(monkeypatch, tmp_path) -> None:
    """No WebView: adapter can start the daemon and list tools."""
    mcp = FakeMcp()
    thread = _serve(mcp, monkeypatch, tmp_path)
    try:
        h = _hello("headless")
        listed = _rpc(h, "tools/list")
        assert listed["tools"][0]["name"] == "ping"
        shared_mcp.close_handle(h)
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


class WaitingMcp(FakeMcp):
    """Two sync tools: one waits (on its thread) until another agent's call lands.

    That is run_workflow waiting for a workflow agent which itself needs a tool from
    the same daemon. On the daemon loop the first call blocked the second forever.
    """

    def __init__(self) -> None:
        super().__init__()
        self.answered = threading.Event()
        for name in ("run_workflow", "workspace_list_verse_errors"):
            self._tool_manager._tools[name] = SimpleNamespace(name=name, is_async=False)

    async def call_tool(self, name, arguments):
        from backend.workspace.identity import current

        run_id = current().run_id
        if name == "run_workflow":
            # FastMCP calls a sync tool inline: this is that body.
            assert self.answered.wait(5), "the workflow agent's tool call never ran"
        else:
            self.answered.set()
        return [{"type": "text", "text": f"{name}:{run_id}"}]


def test_a_sync_tool_waiting_on_another_agent_does_not_deadlock(monkeypatch, tmp_path) -> None:
    mcp = WaitingMcp()
    thread = _serve(mcp, monkeypatch, tmp_path)
    try:
        caller = _hello("run-caller")
        agent = _hello("run-agent")
        shared_mcp.write_frame(
            caller,
            {"op": "mcp", "id": 1, "method": "tools/call", "params": {"name": "run_workflow", "arguments": {}}},
        )
        time.sleep(0.2)  # run_workflow is now waiting
        ra = _rpc(agent, "tools/call", {"name": "workspace_list_verse_errors", "arguments": {}}, 2)
        assert (ra.get("content") or [{}])[0].get("text") == "workspace_list_verse_errors:run-agent"
        rc = shared_mcp.read_frame(caller)
        assert (rc.get("result", {}).get("content") or [{}])[0].get("text") == "run_workflow:run-caller"
        shared_mcp.close_handle(caller)
        shared_mcp.close_handle(agent)
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_tools_keep_their_arguments_over_the_shared_bridge() -> None:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("t")
    mcp._ducky_skip_plugin_wait = True

    @mcp.tool()
    def make_plan(title: str, body: str = "") -> str:
        """Make a plan."""
        return title

    (row,) = shared_mcp.list_tools_for_handshake(mcp)
    schema = row["inputSchema"]
    assert set(schema["properties"]) == {"title", "body"}
    assert schema["required"] == ["title"]


def test_a_reconnect_inside_the_idle_window_keeps_the_server(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(shared_mcp, "_IDLE_EXIT_S", 0.4)
    thread = _serve(FakeMcp(), monkeypatch, tmp_path)
    try:
        first = _hello("run-1")
        shared_mcp.write_frame(first, {"op": "bye"})
        shared_mcp.close_handle(first)
        time.sleep(0.15)  # first client gone, idle timer running
        second = _hello("run-2")
        time.sleep(0.8)  # the old timer fires while run-2 is connected
        assert shared_mcp.serving()
        assert [t["name"] for t in _rpc(second, "tools/list")["tools"]] == ["ping", "workspace_write_file"]
        shared_mcp.write_frame(second, {"op": "bye"})
        shared_mcp.close_handle(second)
        thread.join(timeout=5)  # really idle now: it stops on its own
        assert not thread.is_alive()
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)
