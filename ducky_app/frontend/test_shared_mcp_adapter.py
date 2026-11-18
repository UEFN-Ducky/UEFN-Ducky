"""Shared MCP adapter: which daemon an agent joins, slow hellos, and counted fallbacks."""

from __future__ import annotations

import json

import pytest

from backend.bridge import shared_mcp
from backend.bridge.test_shared_mcp import FakeMcp, serve
from frontend import shared_mcp_adapter as ad


@pytest.fixture
def app_data(monkeypatch, tmp_path):
    """Adapter (LOCALAPPDATA/UEFN-Ducky) and daemon (default_app_data_dir) share one folder."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("UEFN_DUCKY_APP_VERSION", raising=False)
    monkeypatch.delenv(shared_mcp.SLOT_ENV, raising=False)
    folder = tmp_path / "UEFN-Ducky"
    folder.mkdir()
    return folder


@pytest.mark.parametrize("slot", [None, "0", "5", "x"])
def test_the_adapter_reads_the_same_state_file_as_the_daemon(monkeypatch, tmp_path, slot) -> None:
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path)
    if slot is None:
        monkeypatch.delenv(shared_mcp.SLOT_ENV, raising=False)
    else:
        monkeypatch.setenv(shared_mcp.SLOT_ENV, slot)
    assert ad._SLOT_ENV == shared_mcp.SLOT_ENV
    assert ad._state_name() == shared_mcp.state_path().name


def test_a_host_agent_joins_its_own_hosts_daemon(monkeypatch, app_data) -> None:
    """Nov 4 2026: every host agent joined the app's daemon; the host's own sat at 0 clients."""
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "0")
    decoy = {"host": "127.0.0.1", "port": 1, "token": "t", "key": {"version": "1", "port": "4200", "project": ""}}
    (app_data / "shared_mcp.json").write_text(json.dumps(decoy), encoding="utf-8")
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: pytest.fail("the host's daemon was up"))
    thread = serve(FakeMcp(), monkeypatch, app_data)
    try:
        assert (app_data / "shared_mcp-h0.json").is_file()
        sock = ad.connect(["--port", "4200"], timeout_s=2.0)
        assert sock is not None
        assert sock.getpeername()[1] == shared_mcp.wait_ready(1.0)["port"]
        ad._write_frame(sock, {"op": "bye"})
        sock.close()
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_a_host_agent_joins_its_host_after_an_update_was_downloaded(monkeypatch, app_data) -> None:
    """Nov 17 2026: 1.2.505 arrived while 1.2.504 ran. Every agent's adapter handed off to the
    new version, took its own host's tool server for a stale one, waited 33 s and then ran a
    dedicated bridge: 40-58 s before every turn until Ducky was restarted."""
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "0")
    monkeypatch.setattr(ad, "_stop_bridge_pid", lambda pid: pytest.fail("tried to stop the host"))
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: pytest.fail("the host's tool server was up"))
    thread = serve(FakeMcp(), monkeypatch, app_data)  # the host that is running: the older version
    try:
        state = json.loads((app_data / "shared_mcp-h0.json").read_text(encoding="utf-8"))
        newer = ".".join(str(n + 1) for n in ad._version_tuple(ad._state_version(state)))
        monkeypatch.setenv("UEFN_DUCKY_APP_VERSION", newer)  # the adapter, after the hand-off
        sock = ad.connect(["--port", "4200"], timeout_s=2.0)
        assert sock is not None
        assert sock.getpeername()[1] == shared_mcp.wait_ready(1.0)["port"]
        ad._write_frame(sock, {"op": "bye"})
        sock.close()
    finally:
        shared_mcp.request_stop()
        thread.join(timeout=6)


def test_only_a_daemon_this_exe_can_replace_counts_as_stale(monkeypatch) -> None:
    older = {"key": {"version": "1.9.9"}}
    monkeypatch.setenv("UEFN_DUCKY_APP_VERSION", "2.0.0")
    monkeypatch.delenv(shared_mcp.SLOT_ENV, raising=False)
    assert ad._stale(older)  # the app's daemon: this exe stops it and a newer one starts
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "0")
    assert not ad._stale(older)  # a host's own: nothing an agent does replaces it


def test_a_host_agent_never_asks_the_app_for_a_daemon(monkeypatch) -> None:
    """The app has no slot: it would start an unslotted daemon nobody in the host uses. Nor does
    the agent start one: its host serves the slot itself (a whole backend per agent before)."""
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "1")
    monkeypatch.setattr(ad, "_ask_app_to_start", lambda: pytest.fail("asked the app"))
    started: list[list[str]] = []
    monkeypatch.setattr(ad.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    ad._spawn_daemon(["--port", "4200"])
    assert started == []


def test_a_slow_hello_is_tried_once_more_before_spawning(monkeypatch, app_data) -> None:
    state = {"host": "127.0.0.1", "port": 9, "token": "t", "key": {}}
    (app_data / "shared_mcp.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(ad, "_HELLO_BACKOFF_S", 0.01)
    tries: list[float] = []
    answered = object()

    def _attempt(_state, read_timeout=ad._HELLO_TIMEOUT_S):
        tries.append(read_timeout)
        return (None, True) if len(tries) == 1 else (answered, False)

    monkeypatch.setattr(ad, "_hello_attempt", _attempt)
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: pytest.fail("spawned past a slow daemon"))
    assert ad.connect(["--port", "4200"], timeout_s=0.2) is answered
    assert tries == [ad._HELLO_TIMEOUT_S, ad._HELLO_TIMEOUT_S * 2]


def test_a_refused_hello_is_not_retried(monkeypatch, app_data) -> None:
    state = {"host": "127.0.0.1", "port": 9, "token": "t", "key": {}}
    (app_data / "shared_mcp.json").write_text(json.dumps(state), encoding="utf-8")
    tries: list[float] = []
    monkeypatch.setattr(ad, "_hello_attempt", lambda s, read_timeout=10.0: tries.append(read_timeout) or (None, False))
    spawned: list[list[str]] = []
    monkeypatch.setattr(ad, "_spawn_daemon", lambda args: spawned.append(args))
    monkeypatch.setattr(ad, "_try_hello", lambda s: None)
    assert ad.connect(["--port", "4200"], timeout_s=0.2) is None
    assert tries == [10.0] and spawned == [["--port", "4200"]]


def test_progress_from_the_daemon_reaches_the_cli(monkeypatch, app_data) -> None:
    """Claude Code aborts a call after 1800 s with no response or progress: the daemon's
    notifications/progress must come out of the adapter on stdout, before the result."""
    import os
    import socket
    import threading
    from types import SimpleNamespace

    listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen.bind(("127.0.0.1", 0))
    listen.listen(1)
    state = {"host": "127.0.0.1", "port": listen.getsockname()[1], "token": "t", "key": {}}
    (app_data / "shared_mcp.json").write_text(json.dumps(state), encoding="utf-8")

    def _daemon() -> None:
        conn, _ = listen.accept()
        with conn:
            ad._read_frame(conn)
            ad._write_frame(conn, {"op": "hello_ok"})
            msg = ad._read_frame(conn)
            token = msg["params"]["_meta"]["progressToken"]
            for n in (1, 2):
                ad._write_frame(conn, {"op": "mcp", "method": "notifications/progress",
                                       "params": {"progressToken": token, "progress": n}})
            ad._write_frame(conn, {"op": "mcp", "id": msg["id"], "result": {"content": []}})
            try:
                ad._read_frame(conn)  # the adapter's bye
            except (OSError, EOFError, ValueError):
                pass

    threading.Thread(target=_daemon, daemon=True).start()
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    monkeypatch.setattr(ad.sys, "stdin", SimpleNamespace(buffer=os.fdopen(in_r, "rb", buffering=0)))
    monkeypatch.setattr(ad.sys, "stdout", SimpleNamespace(buffer=os.fdopen(out_w, "wb", buffering=0)))
    writer, reader = os.fdopen(in_w, "wb", buffering=0), os.fdopen(out_r, "rb", buffering=0)
    relay = threading.Thread(target=ad.run, args=(["--port", "4200"],), daemon=True)
    relay.start()
    try:
        writer.write(json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": "ducky_ask_user", "arguments": {}, "_meta": {"progressToken": "p5"}}}).encode() + b"\n")
        lines, buf = [], b""
        while len(lines) < 3:
            chunk = reader.read(65536)
            assert chunk, "adapter closed stdout"
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                lines.append(json.loads(line))
        assert [m.get("method") for m in lines[:2]] == ["notifications/progress"] * 2
        assert [m["params"]["progress"] for m in lines[:2]] == [1, 2]
        assert all("id" not in m and m["params"]["progressToken"] == "p5" for m in lines[:2])
        assert lines[2]["id"] == 5 and lines[2]["result"] == {"content": []}
    finally:
        writer.close()
        relay.join(timeout=5)
        listen.close()


def test_each_dedicated_fallback_is_counted(monkeypatch, app_data) -> None:
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "2")
    monkeypatch.setattr(ad, "connect", lambda *args, **kwargs: None)
    assert ad.run(["--port", "4200"]) is False
    assert ad.run(["--port", "4200"]) is False
    record = json.loads((app_data / "shared_mcp_fallbacks.json").read_text(encoding="utf-8"))
    assert record["count"] == 2 and record["slot"] == "2" and record["reason"]
    assert [row["slot"] for row in record["recent"]] == ["2", "2"]


def test_a_fallback_names_its_reason_and_only_the_latest_are_kept(monkeypatch, app_data) -> None:
    """91 fallbacks had piled up by Nov 17 2026 under one reason, "no shared daemon answered"."""
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "3")  # no shared_mcp-h3.json: that host serves nothing
    monkeypatch.setattr(ad, "_SLOT_WAIT_S", 0.05)
    monkeypatch.setattr(ad, "_RECENT_KEPT", 3)
    for _ in range(5):
        assert ad.run(["--port", "4200"]) is False
    record = json.loads((app_data / "shared_mcp_fallbacks.json").read_text(encoding="utf-8"))
    assert record["count"] == 5 and len(record["recent"]) == 3
    assert "shared_mcp-h3.json" in record["reason"] and record["recent"][-1]["reason"] == record["reason"]


def test_a_host_agent_does_not_wait_on_a_host_that_stays_silent(monkeypatch, app_data) -> None:
    """Its host checked the tool server before starting the agent, so waiting on gains nothing:
    Nov 11-17 2026, every join that worked took under 5 s and every other one cost 31-71 s here."""
    import socket
    import time

    silent = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # takes connections, never answers
    silent.bind(("127.0.0.1", 0))
    silent.listen(8)
    state = {"host": "127.0.0.1", "port": silent.getsockname()[1], "token": "t", "pid": 4242,
             "key": {"version": "1", "port": "4200", "project": ""}}
    (app_data / "shared_mcp-h0.json").write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "0")
    monkeypatch.setattr(ad, "_QUICK_HELLO_S", 0.2)
    monkeypatch.setattr(ad, "_HELLO_TIMEOUT_S", 0.3)
    monkeypatch.setattr(ad, "_SLOT_WAIT_S", 0.3)
    monkeypatch.setattr(ad, "_hello_with_retry", lambda s: pytest.fail("a second, longer hello"))
    began = time.monotonic()
    try:
        assert ad.run(["--port", "4200"]) is False
    finally:
        silent.close()
    assert time.monotonic() - began < 2.0  # one hello window + the short wait, not 31-71 s
    record = json.loads((app_data / "shared_mcp_fallbacks.json").read_text(encoding="utf-8"))
    assert "did not answer" in record["reason"] and "4242" in record["reason"]


# --- a host agent's start never waits for the tool server -----------------------------------

_INIT = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": True}},
         "serverInfo": {"name": "uefn-ducky", "version": "1.2.504"}, "instructions": "Use the tools."}
_ROWS = [{"name": "zeta", "description": "Z", "inputSchema": {"type": "object", "properties": {}},
          "_meta": {"anthropic/alwaysLoad": True}},
         {"name": "alpha", "description": "A", "inputSchema": {"type": "object", "properties": {"n": {"type": "integer"}}}}]


def _kept(app_data, slot: str = "0", rows=_ROWS) -> dict:
    """The file an earlier agent of this host left: the host's own answers, as they came."""
    tools = {"tools": rows, "_meta": {"revision": ad._rows_revision(rows)}}
    kept = {"saved_at": 1.0, "revision": tools["_meta"]["revision"], "initialize": _INIT, "tools": tools}
    (app_data / f"agent_tool_catalog-h{slot}.json").write_text(json.dumps(kept), encoding="utf-8")
    return kept


class _Agent:
    """The CLI's side of an adapter that runs on a thread: says requests, hears answers."""

    def __init__(self, monkeypatch) -> None:
        import os
        import queue
        import threading
        from types import SimpleNamespace

        in_r, in_w = os.pipe()
        out_r, out_w = os.pipe()
        monkeypatch.setattr(ad.sys, "stdin", SimpleNamespace(buffer=os.fdopen(in_r, "rb", buffering=0)))
        monkeypatch.setattr(ad.sys, "stdout", SimpleNamespace(buffer=os.fdopen(out_w, "wb", buffering=0)))
        self._writer = os.fdopen(in_w, "wb", buffering=0)
        self._lines: queue.Queue = queue.Queue()
        self.result: list = []
        reader = os.fdopen(out_r, "rb", buffering=0)

        def _read() -> None:
            buf = b""
            while chunk := reader.read(65536):
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    self._lines.put(line)

        threading.Thread(target=_read, daemon=True).start()
        self._relay = threading.Thread(target=lambda: self.result.append(ad.run(["--port", "4200"])), daemon=True)
        self._relay.start()

    def say(self, msg: dict) -> None:
        self._writer.write(json.dumps(msg).encode() + b"\n")

    def hear_raw(self, timeout: float = 5.0) -> bytes:
        return self._lines.get(timeout=timeout)

    def hear(self, timeout: float = 5.0) -> dict:
        return json.loads(self.hear_raw(timeout))

    def quiet(self, seconds: float = 0.3) -> bool:
        import queue

        try:
            self._lines.put(self._lines.get(timeout=seconds))
            return False
        except queue.Empty:
            return True

    def close(self) -> None:
        self._writer.close()
        self._relay.join(timeout=5)


def _host(app_data, serve_conn, slot: str = "0"):
    """A host's tool server on a thread: ``serve_conn(n, conn)`` for its n-th connection."""
    import socket
    import threading

    listen = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listen.bind(("127.0.0.1", 0))
    listen.listen(8)
    state = {"host": "127.0.0.1", "port": listen.getsockname()[1], "token": "t", "pid": 4242,
             "key": {"version": "1.2.504", "port": "4200", "project": ""}}
    (app_data / f"shared_mcp-h{slot}.json").write_text(json.dumps(state), encoding="utf-8")

    def _accept() -> None:
        n = 0
        while True:
            try:
                conn, _ = listen.accept()
            except OSError:
                return
            n += 1
            threading.Thread(target=serve_conn, args=(n, conn), daemon=True).start()

    threading.Thread(target=_accept, daemon=True).start()
    return listen


def _short_waits(monkeypatch) -> None:
    monkeypatch.setenv(shared_mcp.SLOT_ENV, "0")
    monkeypatch.setattr(ad, "_QUICK_HELLO_S", 0.2)
    monkeypatch.setattr(ad, "_HELLO_TIMEOUT_S", 0.4)
    monkeypatch.setattr(ad, "_SLOT_WAIT_S", 0.3)


def test_a_host_agent_starts_from_the_kept_answers_while_its_host_is_silent(monkeypatch, app_data) -> None:
    """Nov 11-17 2026: 124 of 1225 turn starts sat 35-100 s in the join before the model saw the
    message. The agent now gets the host's own last answers at once, byte for byte: the same
    tool list in the same order is what keeps its prompt cached."""
    import time

    _short_waits(monkeypatch)
    kept = _kept(app_data)
    silent = _host(app_data, lambda n, conn: time.sleep(30))  # takes connections, never answers
    monkeypatch.setattr(ad, "_start_child", lambda args, init: None)
    began = time.monotonic()
    agent = _Agent(monkeypatch)
    try:
        agent.say({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                   "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "cli"}}})
        first = agent.hear()
        assert time.monotonic() - began < 2.0
        assert first == {"jsonrpc": "2.0", "id": 0, "result": {**_INIT, "protocolVersion": "2025-11-25"}}
        agent.say({"jsonrpc": "2.0", "method": "notifications/initialized"})
        agent.say({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        # Exactly what the relay writes for the host's own answer: same rows, order and marks.
        live = json.dumps({"jsonrpc": "2.0", "id": 1, "result": kept["tools"]}, separators=(",", ":")).encode()
        assert agent.hear_raw() == live
        agent.say({"jsonrpc": "2.0", "id": 2, "method": "prompts/list"})
        assert agent.hear()["result"] == {"prompts": []}
    finally:
        agent.close()
        silent.close()


@pytest.mark.parametrize("changed", [False, True])
def test_a_host_that_answers_late_is_joined_beside_the_turn(monkeypatch, app_data, changed) -> None:
    """The agent hears of a tool change only when the host's list really differs from the kept
    one: a change notice makes it list its tools again, which moves its cached prompt."""
    import time

    _short_waits(monkeypatch)
    kept = _kept(app_data)
    rows = [*_ROWS, {"name": "new_tool", "description": "N", "inputSchema": {"type": "object"}}] if changed else _ROWS
    asked: list = []

    def _serve(n: int, conn) -> None:
        with conn:
            if n == 1:
                time.sleep(0.6)  # busy: the at-once hello gets no answer
                return
            ad._read_frame(conn)
            ad._write_frame(conn, {"op": "hello_ok"})
            while True:
                try:
                    msg = ad._read_frame(conn)
                except (OSError, EOFError, ValueError):
                    return
                asked.append(msg.get("method") or msg.get("op"))
                if msg.get("method") == "tools/list":
                    ad._write_frame(conn, {"op": "mcp", "id": msg["id"], "result": {
                        "tools": rows, "_meta": {"revision": ad._rows_revision(rows)}}})
                elif msg.get("method") == "tools/call":
                    ad._write_frame(conn, {"op": "mcp", "id": msg["id"], "result": {"content": []}})

    host = _host(app_data, _serve)
    monkeypatch.setattr(ad, "_start_child", lambda args, init: pytest.fail("the host answered in its window"))
    agent = _Agent(monkeypatch)
    try:
        agent.say({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        assert agent.hear()["result"] == _INIT
        agent.say({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert agent.hear()["result"] == kept["tools"]
        agent.say({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "alpha", "arguments": {}}})
        heard = [agent.hear(), agent.hear()] if changed else [agent.hear()]
        assert {"jsonrpc": "2.0", "id": 2, "result": {"content": []}} in heard
        notices = [m for m in heard if m.get("method") == "notifications/tools/list_changed"]
        assert len(notices) == (1 if changed else 0) and agent.quiet()
        assert "tools/list" in asked  # the host's own list was asked for, which starts its change notices
        saved = json.loads((app_data / "agent_tool_catalog-h0.json").read_text(encoding="utf-8"))
        assert saved["revision"] == ad._rows_revision(rows)
        assert not (app_data / "shared_mcp_fallbacks.json").exists()  # joined: not a fallback
    finally:
        agent.close()
        host.close()


_OWN_SERVER = """
import json, sys
seen = []
for line in sys.stdin:
    msg = json.loads(line)
    seen.append(msg.get("method"))
    if msg.get("method") == "initialize":
        out = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {}, "serverInfo": {"name": "own"}}
    elif msg.get("method") == "tools/call":
        out = {"content": [{"type": "text", "text": ",".join(seen)}]}
    else:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": out}) + chr(10))
    sys.stdout.flush()
"""


def test_with_no_host_in_its_window_the_agent_gets_a_tool_server_of_its_own(monkeypatch, app_data) -> None:
    """The turn has started from the kept answers by then, so that tool server is a child this
    adapter relays to (it cannot hand the agent's stdin over any more), initialized once by the
    adapter and counted as a fallback with its reason."""
    import sys

    _short_waits(monkeypatch)  # no shared_mcp-h0.json: this host serves nothing
    kept = _kept(app_data)
    monkeypatch.setattr(ad, "_child_command", lambda args: ([sys.executable, "-c", _OWN_SERVER], None))
    agent = _Agent(monkeypatch)
    try:
        agent.say({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        assert agent.hear()["result"] == _INIT
        agent.say({"jsonrpc": "2.0", "method": "notifications/initialized"})
        agent.say({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert agent.hear()["result"] == kept["tools"]
        agent.say({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "alpha", "arguments": {}}})
        answer = agent.hear(timeout=15)
        # The child saw one initialize and one initialized, both from the adapter, then the call.
        assert answer["id"] == 2
        assert answer["result"]["content"][0]["text"] == "initialize,notifications/initialized,tools/call"
        agent.say({"jsonrpc": "2.0", "id": 3, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
        assert agent.hear()["result"] == _INIT  # a second one never reaches the child
        record = json.loads((app_data / "shared_mcp_fallbacks.json").read_text(encoding="utf-8"))
        assert record["count"] == 1 and "shared_mcp-h0.json" in record["reason"]
    finally:
        agent.close()


def test_the_hosts_answers_are_kept_as_they_came_and_written_once(monkeypatch, app_data) -> None:
    """A host that answers at once is relayed live, exactly as before; its answers are kept for
    the next start, and an unchanged tool list is not written again."""
    _short_waits(monkeypatch)
    tools = {"tools": _ROWS, "_meta": {"revision": ad._rows_revision(_ROWS)}}

    def _serve(n: int, conn) -> None:
        with conn:
            ad._read_frame(conn)
            ad._write_frame(conn, {"op": "hello_ok"})
            while True:
                try:
                    msg = ad._read_frame(conn)
                except (OSError, EOFError, ValueError):
                    return
                if msg.get("method") == "initialize":
                    ad._write_frame(conn, {"op": "mcp", "id": msg["id"], "result": _INIT})
                elif msg.get("method") == "tools/list":
                    ad._write_frame(conn, {"op": "mcp", "id": msg["id"], "result": tools})

    host = _host(app_data, _serve)
    writes: list = []
    real_replace = ad.os.replace
    monkeypatch.setattr(ad.os, "replace", lambda src, dst: (writes.append(str(dst)), real_replace(src, dst))[1])
    path = app_data / "agent_tool_catalog-h0.json"
    try:
        for _ in range(2):
            agent = _Agent(monkeypatch)
            try:
                agent.say({"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}})
                assert agent.hear() == {"jsonrpc": "2.0", "id": 0, "result": _INIT}
                agent.say({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
                assert agent.hear() == {"jsonrpc": "2.0", "id": 1, "result": tools}
                agent.say({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                agent.hear()  # the keep of the answer before it is done by now
            finally:
                agent.close()
            kept = json.loads(path.read_text(encoding="utf-8"))
            assert kept["initialize"] == _INIT and kept["tools"] == tools
            assert kept["revision"] == tools["_meta"]["revision"]
            assert [row["name"] for row in kept["tools"]["tools"]] == ["zeta", "alpha"]  # the host's order
        assert len(writes) == 2  # the initialize and the tool list, once each, by the first agent
    finally:
        host.close()
