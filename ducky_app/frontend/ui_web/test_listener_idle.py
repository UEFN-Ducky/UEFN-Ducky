"""UEFN open and idle: no client sends the listener a request; one tool call sends exactly one.

Nov 5 2026: with UEFN open and no tool calls the listener counted 11-17 GET/min, ``panel:``
6-12 (the island watch's health GET every round, the tray icon's probe, the presence beat)
and ``shared-daemon:`` 4-5. A real local listener counts every request by ``X-Ducky-Client``.
"""

from __future__ import annotations

import json
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

FIVE_MIN = 300.0
ROUND_S = 8.0  # the icon heartbeat's period, the shortest of the background rounds


class _Listener(BaseHTTPRequestHandler):
    def _answer(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self.server.hits.append(("GET", self.headers.get("X-Ducky-Client", "")))
        self._answer({"status": "ok", "island_dir": self.server.island, "busy": False, "tick_age_sec": 0.1})

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.hits.append(("POST", self.headers.get("X-Ducky-Client", "")))
        self._answer({"success": True, "result": {"project_name": "Island"}})

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def listener(monkeypatch, tmp_path):
    from backend.bridge import client, editor_gateway, editor_presence
    from frontend import settings
    from frontend.ui_web.lag import listener_status_cache
    from frontend.ui_web.verse_editor.urc import island_watch

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Listener)
    server.hits = []
    server.island = str(tmp_path / "Island")  # no .lore folder: a round stops after the island
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    tag = {"value": "panel:1"}
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: True)  # UEFN is open
    monkeypatch.setattr(client, "client_tag", lambda: tag["value"])
    monkeypatch.setattr(client, "_record_editor_change", lambda *a, **k: None)
    monkeypatch.setattr(editor_gateway, "call", lambda chat, op, mutating, fn: fn())
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", port)
    monkeypatch.setattr(listener_status_cache, "_last", None)
    monkeypatch.setattr(island_watch, "_read_doc", lambda: None)
    monkeypatch.setattr(island_watch, "_read_locks", lambda: None)
    monkeypatch.setattr(island_watch, "_write_doc", lambda doc: None)
    monkeypatch.setattr(island_watch, "_write_locks", lambda doc: None)
    client.set_port_override(port)
    client._invalidate_cache()
    island_watch.reset_for_tests()
    try:
        yield types.SimpleNamespace(hits=server.hits, tag=tag, port=port)
    finally:
        island_watch.reset_for_tests()
        client.set_port_override(None)
        client._invalidate_cache()
        server.shutdown()
        server.server_close()


def _panel_idle(seconds: float) -> int:
    """Every background round the window runs while nobody calls a tool; returns the rounds."""
    import frontend.duckyos_account as account
    from frontend.ui_web import webview_app as app
    from frontend.ui_web.verse_editor.urc import island_watch

    api = types.SimpleNamespace()
    mode = None
    rounds = 0
    elapsed = 0.0
    while elapsed < seconds:
        island_watch.tick()  # the island watch a UEFN call started
        mode = app._poll_once(api, mode)  # the tray / taskbar icon heartbeat
        account._presence_uefn_online()  # the account presence beat
        elapsed += ROUND_S
        rounds += 1
    return rounds


def _from(hits: list, prefix: str) -> list:
    return [hit for hit in hits if hit[1].startswith(prefix)]


def test_panel_sends_nothing_in_five_idle_minutes(listener, monkeypatch) -> None:
    from frontend.ui_web.lag import listener_status_cache
    from frontend.ui_web.verse_editor.urc import island_watch

    # The UEFN call that started the watch: it asks the listener which island is open, once.
    island_watch.tick()
    assert listener.hits == [("GET", "panel:1")]
    monkeypatch.setattr(listener_status_cache, "_last", {"online": True})  # that call's status
    before = len(listener.hits)
    assert _panel_idle(FIVE_MIN) >= 35
    assert _from(listener.hits[before:], "panel:") == []


def test_shared_daemon_sends_nothing_in_five_idle_minutes(listener, monkeypatch) -> None:
    from backend import process_role
    from backend.bridge import shared_mcp
    from frontend.ui_web.verse_editor.urc import island_watch

    listener.tag["value"] = "shared-daemon:2"
    monkeypatch.setattr(process_role, "role", lambda: "tool_server")
    island_watch.on_uefn_call_ok()  # a tool server never runs the island watch
    assert not island_watch.running()
    elapsed = 0.0
    while elapsed < FIVE_MIN:
        # The daemon's own background work: the stuck-idle check and the catalog watchers.
        shared_mcp._stuck_idle()
        shared_mcp.wait_catalog_change(shared_mcp.catalog_revision(), timeout=0)
        island_watch.for_agents()
        island_watch.others_locks("C:/Island")
        elapsed += ROUND_S
    assert _from(listener.hits, "shared-daemon:") == []


def test_one_get_project_info_is_exactly_one_request(listener) -> None:
    from backend import bridge
    from frontend.ui_web.verse_editor.urc import island_watch

    island_watch.tick()  # the watch already knows the island
    before = len(listener.hits)
    assert bridge.send_command("get_project_info")["project_name"] == "Island"
    assert listener.hits[before:] == [("POST", "panel:1")]
    after = len(listener.hits)
    _panel_idle(60.0)  # and nothing more once the call is done
    assert listener.hits[after:] == []


def test_the_watch_asks_again_after_uefn_closed(listener, monkeypatch) -> None:
    from backend.bridge import editor_presence
    from frontend.ui_web.verse_editor.urc import island_watch

    island_watch.tick()
    island_watch.tick()
    assert len(listener.hits) == 1
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: False)
    island_watch.tick()  # UEFN closed: the known island is dropped, and nothing connects
    assert len(listener.hits) == 1
    monkeypatch.setattr(editor_presence, "uefn_running", lambda: True)
    island_watch.tick()  # a new UEFN: one GET for its island
    assert len(listener.hits) == 2
