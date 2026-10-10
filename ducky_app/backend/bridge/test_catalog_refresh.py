import asyncio
import json
import os
import queue
import threading
from types import SimpleNamespace

from mcp.types import Tool, ToolAnnotations

from backend.bridge import shared_mcp
from backend.bridge.test_shared_mcp import FakeMcp, _serve
from frontend import shared_mcp_adapter as adapter


def test_shared_and_dedicated_rows_match_including_unavailable_metadata(monkeypatch):
    from backend.server import ProtectedFastMCP
    from backend.workspace import ai_ignore
    mcp = ProtectedFastMCP("catalog-test")
    mcp._ducky_skip_plugin_wait = True
    mcp.add_tool(lambda path: path, name="docs__read",
                 annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False),
                 meta={"connection_state": "unavailable", "reason": "disconnected"})
    monkeypatch.setattr(ai_ignore, "current_policy", lambda: SimpleNamespace(strict=False))
    shared = shared_mcp.list_tools_for_handshake(mcp)
    dedicated = [shared_mcp._tool_row(t) for t in asyncio.run(mcp.list_tools())]
    assert shared == dedicated
    assert shared[0]["_meta"]["connection_state"] == "unavailable"
    monkeypatch.setattr(ai_ignore, "current_policy", lambda: SimpleNamespace(strict=True))
    assert shared_mcp.list_tools_for_handshake(mcp) == []
    assert asyncio.run(mcp.list_tools()) == []


def test_model_rows_keep_policy_and_full_schema():
    tool = Tool(name="docs__read", inputSchema={"oneOf": [{"required": ["path"]}]},
                annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    row = shared_mcp._tool_row(tool)
    assert row["annotations"]["readOnlyHint"] is True
    assert row["annotations"]["destructiveHint"] is False
    assert row["inputSchema"] == tool.inputSchema


def test_existing_stdio_client_receives_changes_and_updated_catalog(monkeypatch, tmp_path):
    mcp = FakeMcp()
    server = _serve(mcp, monkeypatch, tmp_path)
    state = shared_mcp.wait_ready()
    monkeypatch.setattr(adapter, "connect", lambda args: adapter._try_hello(state))
    incoming_r, incoming_w = os.pipe()
    outgoing_r, outgoing_w = os.pipe()
    stdin = os.fdopen(incoming_r, "rb", buffering=0)
    stdout = os.fdopen(outgoing_w, "wb", buffering=0)
    writer = os.fdopen(incoming_w, "wb", buffering=0)
    reader = os.fdopen(outgoing_r, "rb", buffering=0)
    monkeypatch.setattr(adapter.sys, "stdin", SimpleNamespace(buffer=stdin))
    monkeypatch.setattr(adapter.sys, "stdout", SimpleNamespace(buffer=stdout))
    replies = queue.Queue()
    def read():
        for line in reader:
            replies.put(json.loads(line))
    reading = threading.Thread(target=read, daemon=True)
    reading.start()
    relay = threading.Thread(target=adapter.run, args=([],), daemon=True)
    relay.start()
    def request(n):
        writer.write(json.dumps({"jsonrpc": "2.0", "id": n, "method": "tools/list"}).encode() + b"\n")
        reply = replies.get(timeout=4)
        assert reply["id"] == n
        return reply["result"]
    try:
        first = request(1)
        registry = mcp._tool_manager._tools
        for n, change in enumerate((
            lambda: registry.update(late=SimpleNamespace(name="late", description="late", inputSchema={"type": "object"})),
            lambda: setattr(registry["late"], "inputSchema", {"oneOf": [{"required": ["path"]}]}),
            lambda: setattr(registry["late"], "annotations", ToolAnnotations(readOnlyHint=True, destructiveHint=False)),
            lambda: setattr(registry["late"], "_meta", {"connection_state": "unavailable", "reason": "disconnected"}),
            lambda: registry.pop("late"),
            lambda: registry.clear(),
        ), 2):
            change()
            notice = replies.get(timeout=4)
            assert notice["method"] == "notifications/tools/list_changed"
            assert "id" not in notice and "result" not in notice
            current = request(n)
            assert current != first
            assert current["_meta"]["revision"] != first["_meta"]["revision"]
            if n == 3:
                assert next(t for t in current["tools"] if t["name"] == "late")["inputSchema"] == {"oneOf": [{"required": ["path"]}]}
            if n == 4:
                assert next(t for t in current["tools"] if t["name"] == "late")["annotations"]["readOnlyHint"] is True
            if n == 5:
                assert next(t for t in current["tools"] if t["name"] == "late")["_meta"]["connection_state"] == "unavailable"
            if n == 7:
                assert current["tools"] == []
            first = current
        assert replies.empty()
        assert mcp.seen == []  # refresh never executes/replays a tool
    finally:
        writer.close()
        relay.join(timeout=5)
        shared_mcp.request_stop()
        server.join(timeout=5)
        stdout.close()
        reading.join(timeout=2)
        stdin.close()
        reader.close()
    assert not relay.is_alive()


def test_shared_watchers_skip_an_unchanged_catalog(monkeypatch, tmp_path):
    """Each connected adapter rebuilt and hashed the whole catalog twice a second
    (~13 ms a tick at 1,000 tools) even when nothing had changed."""
    import socket
    import time

    from backend.bridge.test_shared_mcp import _hello, _rpc

    mcp = FakeMcp()
    server = _serve(mcp, monkeypatch, tmp_path)
    rows: list[str] = []
    real_row = shared_mcp._tool_row

    def counting_row(tool):
        rows.append(getattr(tool, "name", ""))
        return real_row(tool)

    monkeypatch.setattr(shared_mcp, "_tool_row", counting_row)
    clients = [_hello(f"run-{n}") for n in range(3)]
    try:
        for sock in clients:
            _rpc(sock, "tools/list")
        time.sleep(1.2)  # every watcher has looked at least once
        rows.clear()
        time.sleep(1.6)  # three more ticks per client, nothing changed
        assert rows == [], f"{len(rows)} rows rebuilt for an unchanged catalog"

        mcp._tool_manager._tools["late"] = SimpleNamespace(
            name="late", description="late", inputSchema={"type": "object"}
        )
        for sock in clients:
            notice = shared_mcp.read_frame(sock)
            assert notice["method"] == "notifications/tools/list_changed"
        for sock in clients:
            sock.settimeout(1.2)
            try:
                extra = shared_mcp.read_frame(sock)
            except (TimeoutError, socket.timeout):
                extra = None
            assert extra is None, f"a second notice for one change: {extra}"
    finally:
        for sock in clients:
            try:
                shared_mcp.write_frame(sock, {"op": "bye"})
            except OSError:
                pass
            shared_mcp.close_handle(sock)
        shared_mcp.request_stop()
        server.join(timeout=5)


def test_dedicated_watcher_skips_an_unchanged_catalog(monkeypatch):
    import anyio

    from backend.server import ProtectedFastMCP
    from backend.workspace import ai_ignore

    monkeypatch.setattr(ai_ignore, "current_policy", lambda: SimpleNamespace(strict=False))
    mcp = ProtectedFastMCP("watch-test")
    for n in range(200):
        mcp.add_tool(lambda value=0: value, name=f"tool_{n}")
    initial = shared_mcp._catalog_revision(
        [shared_mcp._tool_row(t) for t in asyncio.run(mcp.list_tools())]
    )
    ticks: list[float] = []
    rebuilt_at: list[int] = []
    sent: list[int] = []
    real_revision = shared_mcp._catalog_revision
    real_sleep = anyio.sleep

    def counting_revision(rows):
        rebuilt_at.append(len(ticks))
        return real_revision(rows)

    async def fake_sleep(delay):
        ticks.append(delay)
        if len(ticks) == 21:
            mcp.add_tool(lambda other=0: other, name="late")
        if len(ticks) > 25:
            raise anyio.ClosedResourceError
        await real_sleep(0)

    class Session:
        async def send_tool_list_changed(self):
            sent.append(len(ticks))

    monkeypatch.setattr(shared_mcp, "_catalog_revision", counting_revision)
    monkeypatch.setattr(anyio, "sleep", fake_sleep)
    anyio.run(mcp._watch_catalog, Session(), initial)
    idle = [tick for tick in rebuilt_at if tick <= 20]
    assert len(idle) <= 1, f"rebuilt on {len(idle)} of 20 idle ticks"
    assert sent == [21]
