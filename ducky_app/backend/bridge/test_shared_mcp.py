"""Shared MCP daemon: flag default, identity isolation, cancel, tools/list."""

from __future__ import annotations

import hashlib
import json
import os
import socket
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


def test_an_adapter_never_stops_a_newer_daemon(monkeypatch) -> None:
    """An agent still on the old exe killing a newer daemon cut every newer agent off."""
    from frontend import shared_mcp_adapter as ad

    monkeypatch.setenv("UEFN_DUCKY_APP_VERSION", "1.2.360")
    assert ad._stale({"key": {"version": "1.2.361"}}) is False
    assert ad._stale({"key": {"version": "1.2.99"}}) is True
    assert ad._stale({"key": {"version": "1.2.360"}}) is False


def test_the_app_starts_the_daemon_outside_any_agent(monkeypatch) -> None:
    """The daemon is the app's child: stopping the agent that asked for it cannot kill it."""
    import subprocess

    monkeypatch.setenv("UEFN_DUCKY_SHARED_MCP", "1")
    monkeypatch.setenv("DUCKY_RUN_ID", "run-of-some-agent")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "x")
    monkeypatch.setattr(shared_mcp, "_spawned_at", 0.0)
    monkeypatch.setattr(shared_mcp, "_daemon_answers", lambda: False)
    started: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: started.append((cmd, kw["env"])))

    assert shared_mcp.start_daemon_from_app() == {"ok": True, "started": True}
    cmd, env = started[0]
    assert cmd[-1] == "--shared-daemon" and "--adapter" not in cmd
    assert not any(k.startswith(("DUCKY_", "_PYI_")) for k in env)
    # A second agent asking while it boots does not start another one.
    assert shared_mcp.start_daemon_from_app()["starting"] is True
    assert len(started) == 1
    monkeypatch.setattr(shared_mcp, "_daemon_answers", lambda: True)
    monkeypatch.setattr(shared_mcp, "_spawned_at", 0.0)
    assert shared_mcp.start_daemon_from_app() == {"ok": True, "started": False}
    assert len(started) == 1


def test_an_adapter_asks_the_app_before_starting_the_daemon_itself(monkeypatch) -> None:
    from frontend import shared_mcp_adapter as ad

    flags: list[int] = []

    def _popen(cmd, **kw):
        flags.append(kw["creationflags"])
        if kw["creationflags"] & ad._CREATE_BREAKAWAY_FROM_JOB:
            raise PermissionError("job refuses breakaway")

    monkeypatch.setattr(ad.subprocess, "Popen", _popen)
    monkeypatch.setattr(ad, "_ask_app_to_start", lambda: True)
    ad._spawn_daemon(["--port", "4200"])
    assert flags == []
    monkeypatch.setattr(ad, "_ask_app_to_start", lambda: False)
    ad._spawn_daemon(["--port", "4200"])
    if os.name == "nt":
        assert len(flags) == 2
        assert flags[0] & ad._CREATE_BREAKAWAY_FROM_JOB
        assert not flags[1] & ad._CREATE_BREAKAWAY_FROM_JOB
    else:
        assert len(flags) == 1


class _FrameDaemon:
    """A daemon that answers the first ``answer`` requests, then holds the rest."""

    def __init__(self, state_dir: Path, name: str, answer: int) -> None:
        self.name = name
        self.answer = answer
        self.conns: list[socket.socket] = []
        self.listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listen.bind(("127.0.0.1", 0))
        self.listen.listen(4)
        port = self.listen.getsockname()[1]
        (state_dir / "shared_mcp.json").write_text(json.dumps({
            "host": "127.0.0.1", "port": port, "token": "t",
            "key": {"version": "1.0.0", "port": "4200", "project": ""}, "pid": 0,
        }), encoding="utf-8")
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        try:
            while True:
                conn, _ = self.listen.accept()
                self.conns.append(conn)
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()
        except OSError:
            return

    def _serve(self, conn: socket.socket) -> None:
        try:
            shared_mcp.read_frame(conn)
            shared_mcp.write_frame(conn, {"op": "hello_ok"})
            while True:
                msg = shared_mcp.read_frame(conn)
                if msg.get("op") != "mcp" or self.answer <= 0:
                    continue
                self.answer -= 1
                shared_mcp.write_frame(conn, {"op": "mcp", "id": msg["id"], "result": {"from": self.name}})
        except (OSError, EOFError, ValueError):
            return

    def kill(self) -> None:
        self.listen.close()
        for conn in self.conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()


def test_an_adapter_reconnects_when_the_daemon_dies(monkeypatch, tmp_path) -> None:
    """Agents never restart an MCP server: the adapter must outlive its daemon."""
    from frontend import shared_mcp_adapter as ad

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("UEFN_DUCKY_APP_VERSION", "1.0.0")
    state_dir = tmp_path / "UEFN-Ducky"
    state_dir.mkdir()
    first = _FrameDaemon(state_dir, "first", answer=1)
    replacements: list[_FrameDaemon] = []
    monkeypatch.setattr(
        ad, "_spawn_daemon", lambda args: replacements.append(_FrameDaemon(state_dir, "second", answer=9))
    )
    monkeypatch.setattr(ad.os, "_exit", lambda code: pytest.fail("adapter gave up"))

    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    monkeypatch.setattr(ad.sys, "stdin", SimpleNamespace(buffer=os.fdopen(in_r, "rb", buffering=0)))
    monkeypatch.setattr(ad.sys, "stdout", SimpleNamespace(buffer=os.fdopen(out_w, "wb", buffering=0)))
    writer = os.fdopen(in_w, "wb", buffering=0)
    reader = os.fdopen(out_r, "rb", buffering=0)
    replies: list[dict] = []

    def _read_replies() -> None:
        buf = b""
        while chunk := reader.read(65536):
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                replies.append(json.loads(line))

    threading.Thread(target=_read_replies, daemon=True).start()
    relay = threading.Thread(target=ad.run, args=(["--port", "4200"],), daemon=True)
    relay.start()

    def _send(msg: dict) -> None:
        writer.write(json.dumps(msg).encode() + b"\n")

    def _wait_for(n: int) -> dict:
        deadline = time.time() + 8
        while len(replies) < n and time.time() < deadline:
            time.sleep(0.02)
        return {r["id"]: r for r in replies}

    try:
        _send({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        _wait_for(1)
        _send({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "x", "arguments": {}}})
        _send({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        time.sleep(0.3)  # both held by the first daemon
        first.kill()
        by_id = _wait_for(3)
        assert by_id[1]["result"] == {"from": "first"}
        assert by_id[2]["result"]["isError"] is True
        assert "restarted" in by_id[2]["result"]["content"][0]["text"]
        assert by_id[3]["result"] == {"from": "second"}  # resent to the new daemon
        _send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "x", "arguments": {}}})
        assert _wait_for(4)[4]["result"] == {"from": "second"}
    finally:
        writer.close()
        relay.join(timeout=5)
        for daemon in replacements:
            daemon.kill()
    assert not relay.is_alive()
    assert len(replacements) == 1  # closing stdin did not start another daemon


@pytest.mark.skipif(os.name != "nt", reason="Windows process tree")
def test_stopping_an_agent_leaves_the_shared_daemon_running(monkeypatch, tmp_path) -> None:
    import subprocess
    import sys

    import psutil

    from backend.agent.coding_agents import proc_exec

    marker = tmp_path / "daemon.pid"
    code = (
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
        "time.sleep(60)\n"
    )
    agent = subprocess.Popen([sys.executable, "-c", code])
    deadline = time.time() + 10
    while not (marker.exists() and marker.read_text()) and time.time() < deadline:
        time.sleep(0.05)
    daemon_pid = int(marker.read_text())
    monkeypatch.setattr(proc_exec, "_shared_daemon_pid", lambda: daemon_pid)
    try:
        proc_exec._kill_windows_tree(agent.pid)
        agent.wait(timeout=5)
        assert psutil.Process(daemon_pid).is_running()
    finally:
        subprocess.run(["taskkill", "/PID", str(daemon_pid), "/F"], capture_output=True)
