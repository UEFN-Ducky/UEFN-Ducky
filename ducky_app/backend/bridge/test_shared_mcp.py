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


class FakeMcp:
    _ducky_skip_plugin_wait = True

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def list_tools(self):
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

    async def call_tool(self, name, arguments):
        from backend.workspace.identity import current

        ctx = current()
        run_id = ctx.run_id if ctx else ""
        self.seen.append(run_id)
        if (arguments or {}).get("hold"):
            import asyncio

            await asyncio.sleep(20)
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


def test_server_block_wraps_when_flag_on(monkeypatch) -> None:
    monkeypatch.setenv("UEFN_DUCKY_SHARED_MCP", "1")
    block = mcp_block.build_uefn_server_block(PanelSettings())
    if not (mcp_block.shutil.which("node") or mcp_block.shutil.which("node.exe")):
        pytest.skip("node not installed")
    assert (block.get("env") or {}).get("DUCKY_SHARED_MCP") == "1"
    assert "DUCKY_BRIDGE_ARGV" in (block.get("env") or {})
    assert any("mcp_bridge_host.mjs" in str(a) for a in block.get("args") or [])


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
