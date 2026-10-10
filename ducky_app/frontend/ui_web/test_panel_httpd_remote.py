"""Cookie gate for the remote panel HTTP server."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from frontend.ui_web import panel_httpd as httpd


@pytest.fixture
def remote_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(httpd, "default_app_data_dir", lambda: tmp_path)
    httpd._cookie_secret = None
    httpd._one_time.clear()
    httpd._sessions.clear()
    yield
    httpd._cookie_secret = None
    httpd._one_time.clear()
    httpd._sessions.clear()


def test_loopback_host_is_open(remote_auth):
    assert httpd.host_is_local("127.0.0.1:4199")
    assert httpd.request_is_authorized("127.0.0.1:4199", "/", None)
    assert httpd.request_is_authorized("127.0.0.1:4199", "/assets/x.js", None)
    assert httpd.request_is_authorized("localhost:4199", "/__panel_api/x", None)


def test_remote_host_without_cookie_is_403(remote_auth):
    host = "u-abc.app.uefnducky.org"
    assert not httpd.request_is_authorized(host, "/", None)
    assert not httpd.request_is_authorized(host, "/assets/app.js", None)
    assert not httpd.request_is_authorized(host, "/__panel_api/list_conversations", None)
    # Sandboxed plugin iframes fetch sibling assets with Origin: null (no cookie).
    assert httpd.request_is_authorized(host, "/plugin-ui/brainrot-tcg/ui/app.js", None)
    assert not httpd.request_is_authorized(host, "/plugin-ui/../__panel_api/x", None)


def test_native_browser_pane_rpc_skipped_when_remote():
    root = Path(httpd.__file__).resolve().parent / "web" / "src" / "plugin-ui"
    bridge = (root / "bridge.ts").read_text(encoding="utf-8")
    pane = (root / "PluginWebviewPane.tsx").read_text(encoding="utf-8")
    assert "if (isRemote()) return;" in bridge
    assert "if (isRemote()) return;" in pane
    cover = bridge.split("export function setBrowserUiCover")[1].split("export function")[0]
    scrub = bridge.split("export function scrubPluginShellInterference")[1].split("export function")[0]
    assert "if (isRemote()) return;" in cover
    assert "if (isRemote()) return;" in scrub
    sw = Path(httpd.__file__).resolve().parent / "web" / "public" / "sw.js"
    assert 'Access-Control-Allow-Origin' in sw.read_text(encoding="utf-8")


def test_login_token_single_use_and_expiry(remote_auth, monkeypatch):
    token = httpd.mint_remote_login_token()
    assert httpd.consume_remote_login_token(token)
    assert not httpd.consume_remote_login_token(token)

    token2 = httpd.mint_remote_login_token()
    real_time = time.time
    monkeypatch.setattr(httpd.time, "time", lambda: real_time() + 1000)
    assert not httpd.consume_remote_login_token(token2)


def test_cookie_bound_to_host(remote_auth):
    host = "u-abc.app.uefnducky.org"
    other = "u-other.app.uefnducky.org"
    cookie = httpd.issue_remote_cookie(host)
    header = f"{httpd._COOKIE_NAME}={cookie}"
    assert httpd.request_is_authorized(host, "/", header)
    assert not httpd.request_is_authorized(other, "/", header)
    assert httpd.remote_session_count() >= 1
    rows = httpd.remote_session_summaries()
    assert len(rows) >= 1
    assert set(rows[0]) == {"n", "expires_in_s"}
    assert rows[0]["n"] == 1
    assert rows[0]["expires_in_s"] > 0
    httpd.sign_out_all_remote()
    assert not httpd.request_is_authorized(host, "/", header)


def test_new_login_kicks_previous_cookie(remote_auth):
    host = "u-abc.app.uefnducky.org"
    first = httpd.issue_remote_cookie(host)
    second = httpd.issue_remote_cookie(host)
    assert httpd.request_is_authorized(host, "/", f"{httpd._COOKIE_NAME}={second}")
    assert not httpd.request_is_authorized(host, "/", f"{httpd._COOKIE_NAME}={first}")
    assert httpd.remote_session_count() == 1


def test_local_bridge_paths_stay_loopback(remote_auth):
    host = "u-abc.app.uefnducky.org"
    cookie = httpd.issue_remote_cookie(host)
    header = f"{httpd._COOKIE_NAME}={cookie}"
    assert not httpd.request_is_authorized(host, "/__panel_run", header)
    assert httpd.request_is_authorized("127.0.0.1:4199", "/__panel_run", None)
    assert not httpd.request_is_authorized(host, "/__panel_shared_mcp", header)
    assert httpd.request_is_authorized(host, "/__window_stream", header)


def test_publish_panel_events_reaches_pollers():
    before = httpd._event_seq
    httpd.publish_panel_events([{"type": "chats_changed", "conv_id": "c1"}])
    cursor, events = httpd._poll_panel_events(before, timeout=0.0)
    assert cursor > before
    assert any(e.get("type") == "chats_changed" and e.get("conv_id") == "c1" for e in events)


def test_big_file_edits_do_not_stay_pinned_in_the_event_backlog(monkeypatch):
    import json
    from collections import deque

    monkeypatch.setattr(httpd, "_event_backlog", deque())
    monkeypatch.setattr(httpd, "_event_backlog_bytes", 0, raising=False)
    text = "x" * 200_000
    for n in range(300):  # an agent rewriting a 200 KB file 300 times, then the app goes quiet
        httpd.publish_panel_events([{"type": "tool_done", "conv_id": "c1", "name": "workspace_write_file",
                                     "arguments": {"content": text}, "fileEdit": {"before": text, "after": text, "n": n}}])
    kept = [event for _seq, event, *_rest in httpd._event_backlog]
    assert sum(len(json.dumps(event)) for event in kept) <= 8 * 1024 * 1024
    last = httpd._event_seq
    cursor, events = httpd._poll_panel_events(last - 5, timeout=0.0)
    assert cursor == last
    assert [e["fileEdit"]["n"] for e in events] == [295, 296, 297, 298, 299]


def test_http11_for_cloudflare_origin():
    assert httpd._HTTP_PROTOCOL == "HTTP/1.1"


def test_http11_keepalive_and_backlog():
    """cloudflared pools origin sockets; Connection: close + backlog 5 caused 502s."""
    src = Path(httpd.__file__).read_text(encoding="utf-8")
    assert 'self.send_header("Connection", "close")' not in src
    assert "frame-ancestors" in src
    assert "X-Frame-Options" not in src
    assert httpd._PanelServer.request_queue_size >= 128
    assert "timeout = 30" in src


class _FakeSock:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def test_rtc_signal_reaches_registered_sock():
    sock = _FakeSock()
    httpd.register_window_rtc("s1", sock)
    try:
        assert httpd.rtc_signal("s1", {"type": "rtc", "sdp": {"type": "answer", "sdp": "x"}})
        assert sock.sent
        assert b"answer" in sock.sent[0]
    finally:
        httpd.unregister_window_rtc("s1")
    assert httpd.rtc_signal("s1", {"type": "rtc"}) is False


def test_publish_window_rtc_reaches_pollers():
    before = httpd._event_seq
    httpd.publish_window_rtc("abc", 42, {"type": "rtc", "sdp": {"type": "offer"}})
    _cursor, events = httpd._poll_panel_events(before, timeout=0.0)
    assert any(
        e.get("type") == "window_rtc" and e.get("session_id") == "abc" and e.get("hwnd") == 42
        for e in events
    )


def test_html_errors_never_show_python_404():
    src = Path(httpd.__file__).read_text(encoding="utf-8")
    assert "ud-remote-gone" in src
    assert 'parent.postMessage({type:"ud-remote-gone"},"*")' in src
    assert "https://uefnducky.org/profile" in src
    assert "def send_error" in src
    send_error = src.split("def send_error", 1)[1].split("def log_message", 1)[0]
    assert "_GONE_HTML" not in send_error
    assert "_IFRAME_ERROR_HTML" in send_error
    assert b"ud-remote-gone" in httpd._GONE_HTML
    assert b"ud-remote-gone" not in httpd._IFRAME_ERROR_HTML


def test_remote_cookie_samesite_none_secure():
    src = Path(httpd.__file__).read_text(encoding="utf-8")
    cookie_line = next(line for line in src.splitlines() if "Set-Cookie" in line or "_COOKIE_NAME}={cookie}" in line)
    assert "SameSite=None" in src
    assert "SameSite=Lax" not in src
    assert "HttpOnly" in cookie_line or "HttpOnly" in src
    assert "; Secure;" in src


def test_event_bus_retries_poll_403_instead_of_gone():
    src = (
        Path(httpd.__file__).resolve().parent / "web" / "src" / "hooks" / "useAgentEventBus.ts"
    ).read_text(encoding="utf-8")
    assert "response.status === 403 && window.parent" not in src
    assert "remoteGoneIsLive" in src
    assert 'type: "ud-remote-gone"' in src


def test_new_viewer_kicks_old_one():
    class _Viewer(_FakeSock):
        def __init__(self):
            super().__init__()
            self.closed = False

        def shutdown(self, _how):
            self.closed = True

        def close(self):
            self.closed = True

    first, second = _Viewer(), _Viewer()
    httpd._window_viewers.clear()
    try:
        assert httpd.kick_other_viewers(first) == 0
        assert httpd.kick_other_viewers(second) == 1
        assert first.closed and not second.closed
        assert any(b"kicked" in frame for frame in first.sent)
        assert httpd._window_viewers == [second]
        httpd._forget_viewer(second)
        assert httpd._window_viewers == []
    finally:
        httpd._window_viewers.clear()


def test_kick_all_remote_clears_sessions_and_wakes_viewers(remote_auth):
    host = "u-abc.app.uefnducky.org"
    cookie = httpd.issue_remote_cookie(host)
    header = f"{httpd._COOKIE_NAME}={cookie}"
    assert httpd.request_is_authorized(host, "/", header)

    class _Viewer(_FakeSock):
        def shutdown(self, _how):
            pass

        def close(self):
            pass

    viewer = _Viewer()
    httpd._window_viewers.clear()
    httpd._window_viewers.append(viewer)
    before = httpd._event_seq
    try:
        httpd.kick_all_remote()
        assert not httpd.request_is_authorized(host, "/", header)
        assert httpd.remote_session_count() == 0
        assert any(b"kicked" in frame for frame in viewer.sent)
        assert httpd._window_viewers == []
        _cursor, events = httpd._poll_panel_events(before, timeout=0.0)
        assert any(e.get("type") == "remote_gone" for e in events)
        _catch_cursor, catchup = httpd._poll_panel_events(0, timeout=0.0)
        assert _catch_cursor >= _cursor
        assert not any(e.get("type") == "remote_gone" for e in catchup)
    finally:
        httpd._window_viewers.clear()


@pytest.fixture
def panel_server(remote_auth, monkeypatch, tmp_path):
    from frontend.ui_web import panel_api

    monkeypatch.setattr(panel_api, "_shared_api", None)
    monkeypatch.setattr(panel_api.PanelApi, "__init__", lambda self: None)
    monkeypatch.setattr(httpd, "_server", None)
    monkeypatch.setattr(httpd, "_root", None)
    monkeypatch.setattr(httpd, "PANEL_UI_HTTP_PORT", 0)
    monkeypatch.setattr(httpd, "verify_panel_dist", lambda _root: None)
    httpd.start_panel_ui_server(tmp_path)
    server = httpd._server
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _ask(port: int, path: str, host: str, cookie: str = "", body: object = None) -> int:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=None if body is None else json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Host": host, "Cookie": cookie},
    )

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as err:
        return err.code


def test_phone_and_website_cannot_start_verse_lsp(panel_server, monkeypatch):
    """verse-lsp listens on the PC's 127.0.0.1: a remote browser cannot reach it, so a
    start from the phone only left a language server running on the PC for nobody."""
    from frontend.ui_web import panel_api

    calls: list[str] = []
    for name in ("get_verse_lsp_status", "start_verse_lsp", "stop_verse_lsp"):
        monkeypatch.setattr(
            panel_api.PanelApi, name, lambda self, *a, _n=name, **k: calls.append(_n) or {"running": True}
        )
    remote = "u-abc.app.uefnducky.org"
    cookie = f"{httpd._COOKIE_NAME}={httpd.issue_remote_cookie(remote)}"
    args = {"args": ["C:/Island", "w-phone"]}
    for method in ("start_verse_lsp", "stop_verse_lsp", "get_verse_lsp_status"):
        assert _ask(panel_server, f"/__panel_api/{method}", remote, cookie, args) == 403
    assert calls == []
    # A browser on this PC can reach the socket, so it keeps working.
    assert _ask(panel_server, "/__panel_api/start_verse_lsp", f"127.0.0.1:{panel_server}", body=args) == 200
    assert calls == ["start_verse_lsp"]


def test_a_phone_in_use_keeps_the_mailbox_quick(panel_server, monkeypatch):
    """Remote View from a phone asks through the site mailbox, which eases off while quiet."""
    from frontend import duckyos_account as acc
    from frontend.ui_web import panel_api

    woke: list[int] = []
    monkeypatch.setattr(acc, "note_remote_activity", lambda: woke.append(1))
    monkeypatch.setattr(panel_api.PanelApi, "list_folders", lambda self: [], raising=False)
    remote = "u-abc.app.uefnducky.org"
    token = httpd.mint_remote_login_token()
    assert _ask(panel_server, f"/__remote_login?t={token}", remote) == 302
    assert woke == [1]
    cookie = f"{httpd._COOKIE_NAME}={httpd.issue_remote_cookie(remote)}"
    assert _ask(panel_server, "/__panel_api/list_folders", remote, cookie, {}) == 200
    assert woke == [1, 1]
    # The Ducky window's own calls say nothing about a phone.
    assert _ask(panel_server, "/__panel_api/list_folders", f"127.0.0.1:{panel_server}", body={}) == 200
    assert woke == [1, 1]


def test_remote_view_viewer_cannot_start_verse_lsp():
    """Remote View forwards a viewer's calls through the Ducky window by this list."""
    from frontend.duckyos_account import remote_denied
    from frontend.ui_web.panel_api_window import PanelApiWindowMixin

    denied = PanelApiWindowMixin.remote_deny_methods(object())
    assert {"start_verse_lsp", "stop_verse_lsp", "get_verse_lsp_status"} <= set(denied)
    assert remote_denied("start_verse_lsp")
    assert not remote_denied("start_verse_lsp", on_this_pc=True)
    assert remote_denied("pick_project_path", on_this_pc=True)
    assert not remote_denied("scan_verse_diagnostics")
