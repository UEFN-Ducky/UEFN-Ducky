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


def test_a_plugins_media_link_streams_ranges_and_lets_the_panel_read_frames(panel_server, tmp_path, monkeypatch):
    # Oct 12 2026: api.media_url links read the whole file into memory (a 7 GB original froze the
    # app), had no Range (no seeking) and no CORS header, so the panel's frame capture of a
    # crossorigin video failed with "MEDIA_ELEMENT_ERROR: Format error".
    import urllib.request

    from backend.automations import files

    monkeypatch.setattr(files, "_sign", lambda text: "0" * 32)
    video = tmp_path / "proxy.webm"
    video.write_bytes(bytes(range(256)) * 64)
    url = files.media_url(video)
    path = "/" + url.split("/", 3)[3]
    req = urllib.request.Request(f"http://127.0.0.1:{panel_server}{path}",
                                 headers={"Host": "127.0.0.1", "Range": "bytes=10-19"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 206
        assert resp.headers["Content-Range"] == f"bytes 10-19/{256 * 64}"
        assert resp.headers["Accept-Ranges"] == "bytes"
        assert resp.headers["Access-Control-Allow-Origin"] == "*"
        assert resp.read() == bytes(range(10, 20))


def test_the_pc_link_gets_plugin_pages_and_the_ui_kit_from_the_panel_server(panel_server, monkeypatch, tmp_path):
    # Oct 22 2026: the phone panel framed plugin pages by URL, which resolved on the site
    # (404, frame-ancestors 'none'). Now it builds them from what the PC's end of the link
    # fetches here (directPeer -> fetch("/plugin-ui/...")): the page with the host's
    # scripts first, and the shared UI kit its <script src="../../_kit/ducky.js"> names.
    import urllib.request

    from backend.uefn_plugins import webview

    page = tmp_path / "index.html"
    page.write_text('<!doctype html><html><head><script src="../../_kit/ducky.js"></script></head>'
                    '<body>Account</body></html>', encoding="utf-8")
    monkeypatch.setattr(webview, "resolve_plugin_ui_file", lambda pid, rel: page if (pid, rel) == ("account", "ui/index.html") else None)

    def get(path: str) -> tuple[int, str, str]:
        req = urllib.request.Request(f"http://127.0.0.1:{panel_server}{path}", headers={"Host": "127.0.0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.headers["Content-Type"], resp.read().decode("utf-8")

    status, kind, html = get("/plugin-ui/account/ui/index.html")
    assert status == 200 and kind.startswith("text/html")
    assert "Account" in html and html.index("<script>") < html.index('src="../../_kit/ducky.js"')
    status, kind, kit = get("/plugin-ui/_kit/ducky.js")
    assert status == 200 and "javascript" in kind and kit
    protocol = Path(httpd.__file__).resolve().parent / "web" / "src" / "remote" / "protocol.ts"
    assert '"/plugin-ui/"' in protocol.read_text(encoding="utf-8")  # the blob channel may carry them


def test_plugin_icons_answer_304_when_the_phone_already_has_them(panel_server, monkeypatch, tmp_path):
    # Oct 29 2026: no ETag on anything the PC served, so the phone panel's Service Worker
    # (it caches only responses with one) fetched every header icon over WebRTC on every
    # open. Now each answer carries an ETag and a repeat with If-None-Match is a 304.
    import urllib.error
    import urllib.request

    from backend.uefn_plugins import webview

    icon = tmp_path / "icon.svg"
    icon.write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr(webview, "resolve_plugin_ui_file", lambda pid, rel: icon if (pid, rel) == ("forge", "icon.svg") else None)
    url = f"http://127.0.0.1:{panel_server}/plugin-ui/forge/icon.svg"

    with urllib.request.urlopen(urllib.request.Request(url, headers={"Host": "127.0.0.1"}), timeout=10) as resp:
        assert resp.status == 200
        etag = resp.headers["ETag"]
        assert etag and etag.startswith('"')
        assert resp.read() == b"<svg/>"

    again = urllib.request.Request(url, headers={"Host": "127.0.0.1", "If-None-Match": etag})
    try:
        urllib.request.urlopen(again, timeout=10)
        raise AssertionError("expected 304")
    except urllib.error.HTTPError as err:
        assert err.code == 304
        assert err.headers["ETag"] == etag

    icon.write_text("<svg id='new'/>", encoding="utf-8")
    with urllib.request.urlopen(again, timeout=10) as resp:
        assert resp.status == 200 and resp.headers["ETag"] != etag


def test_app_scripts_keep_their_type_when_the_registry_says_text_plain(panel_server, monkeypatch, tmp_path):
    # Oct 17 2026: a PC whose registry maps .js to text/plain never got past the boot
    # splash, because WebView2 refuses a module script that isn't served as JavaScript.
    import mimetypes
    import urllib.request

    monkeypatch.setattr(mimetypes, "guess_type", lambda *a, **k: ("text/plain", None))
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-x.js").write_text("export {};", encoding="utf-8")
    (tmp_path / "assets" / "index-x.css").write_text("body{}", encoding="utf-8")
    for name, want in (("index-x.js", "text/javascript"), ("index-x.css", "text/css")):
        req = urllib.request.Request(f"http://127.0.0.1:{panel_server}/assets/{name}",
                                     headers={"Host": "127.0.0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.headers["Content-Type"] == want
