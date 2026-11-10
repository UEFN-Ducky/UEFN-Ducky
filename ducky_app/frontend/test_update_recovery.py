"""Updater recovers interrupted transfers without handing Setup a partial EXE."""
import hashlib
import io
import ssl
import urllib.error

import pytest

from frontend import updater, version_check
from frontend.update_network import ATTEMPTS, retryable


class Response(io.BytesIO):
    def __init__(self, payload=b"complete installer", total=None, fail=None):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload) if total is None else total)}
        self.fail = fail

    def read(self, size=-1):
        if self.fail:
            failure, self.fail = self.fail, None
            raise failure
        return super().read(size)


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    updater._cancel.clear()
    monkeypatch.setattr("frontend.update_network.retry_delay", lambda _attempt: 0)
    yield
    updater._cancel.clear()


@pytest.mark.parametrize("first", [
    lambda: Response(fail=ConnectionResetError(10054, "forcibly closed")),
    lambda: Response(b"truncated", total=500),
])
def test_interrupted_transfer_restarts_and_exposes_only_complete_file(tmp_path, monkeypatch, first):
    dest = tmp_path / "Setup.exe"
    dest.write_bytes(b"previous verified download")
    calls = []
    def open_response(*args, **kwargs):
        calls.append(1)
        assert dest.read_bytes() == b"previous verified download"
        return first() if len(calls) == 1 else Response()
    monkeypatch.setattr(updater.urllib.request, "urlopen", open_response)
    assert updater._download("https://example.test/setup", dest) is None
    assert len(calls) == 2
    assert dest.read_bytes() == b"complete installer"
    assert not dest.with_suffix(".exe.part").exists()
    assert updater._verify_sha256(dest, hashlib.sha256(dest.read_bytes()).hexdigest()) is None


@pytest.fixture
def cutting_server():
    """A local HTTP server that honours Range but drops every connection after 300 KB,
    like the PC whose storage downloads died at 14% and never finished."""
    import http.server
    import threading

    body = bytes(range(256)) * 8192  # 2 MB
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            start = 0
            rng = self.headers.get("Range")
            if rng:
                start = int(rng.split("=")[1].split("-")[0])
            requests.append(start)
            self.send_response(206 if rng else 200)
            self.send_header("Content-Length", str(len(body) - start))
            if rng:
                self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
            self.end_headers()
            self.wfile.write(body[start:start + 300 * 1024])
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/file", body, requests
    server.shutdown()


def test_download_resumes_through_a_link_that_keeps_dropping(cutting_server):
    from frontend.update_network import download_resumable

    url, body, requests = cutting_server
    buf = io.BytesIO()
    download_resumable(url, buf, timeout=5)
    assert buf.getvalue() == body
    assert requests[0] == 0 and requests[1] == 300 * 1024  # resumed, never restarted
    assert len(requests) == 7


def test_updater_resumes_a_link_that_keeps_dropping(tmp_path, cutting_server):
    url, body, _requests = cutting_server
    dest = tmp_path / "Setup.exe"
    assert updater._download(url, dest, timeout=5) is None
    assert dest.read_bytes() == body
    assert updater._active_download_resp is None


def test_failed_retries_remove_partial_file(tmp_path, monkeypatch):
    calls = []
    def reset_connection(*args, **kwargs):
        calls.append(1)
        return Response(fail=ConnectionResetError(10054, "forcibly closed"))
    monkeypatch.setattr(updater.urllib.request, "urlopen", reset_connection)
    dest = tmp_path / "Setup.exe"
    assert "forcibly closed" in updater._download("https://example.test/setup", dest)
    assert len(calls) == ATTEMPTS
    assert not dest.exists()
    assert not dest.with_suffix(".exe.part").exists()
    assert updater._active_download_resp is None


def test_cancel_between_retries_does_not_launch_another_request(tmp_path, monkeypatch):
    calls = []
    def reset_connection(*args, **kwargs):
        calls.append(1)
        return Response(fail=ConnectionResetError("reset"))
    def cancel_backoff(_attempt):
        updater.cancel_update()
        return 0
    monkeypatch.setattr(updater.urllib.request, "urlopen", reset_connection)
    monkeypatch.setattr("frontend.update_network.retry_delay", cancel_backoff)
    dest = tmp_path / "Setup.exe"
    assert updater._download("https://example.test/setup", dest) == updater._CANCELLED
    assert len(calls) == 1
    assert not dest.with_suffix(".exe.part").exists()


def test_http_not_found_is_not_retried(tmp_path, monkeypatch):
    calls = []
    def not_found(*args, **kwargs):
        calls.append(1)
        raise urllib.error.HTTPError("https://example.test/setup", 404, "Not found", {}, None)
    monkeypatch.setattr(updater.urllib.request, "urlopen", not_found)
    assert "404" in updater._download("https://example.test/setup", tmp_path / "Setup.exe")
    assert len(calls) == 1


def test_certificate_errors_never_retry():
    assert not retryable(ssl.SSLCertVerificationError("certificate expired"))
    assert not retryable(urllib.error.URLError(ssl.SSLCertVerificationError("bad certificate")))
    assert retryable(OSError(10054, "forcibly closed"))
    assert not retryable(PermissionError("cannot write"))


def test_dropped_tls_connection_retries():
    eof = ssl.SSLEOFError(8, "EOF occurred in violation of protocol (_ssl.c:1032)")
    assert retryable(eof)
    assert retryable(urllib.error.URLError(eof))
    assert retryable(ssl.SSLZeroReturnError(6, "TLS/SSL connection has been closed"))


def test_update_feed_recovers_reset_while_reading(monkeypatch):
    calls = []
    def open_response(*args, **kwargs):
        calls.append(1)
        return (Response(fail=ConnectionResetError("reset")) if len(calls) == 1
                else Response(b'{"payload":{"currentVersion":"1.2.350"}}'))
    monkeypatch.setattr(version_check.urllib.request, "urlopen", open_response)
    monkeypatch.setattr(version_check, "update_base_url", lambda: "https://example.test")
    payload, error = version_check.fetch_remote_payload()
    assert error is None
    assert payload["currentVersion"] == "1.2.350"
    assert len(calls) == 2


def test_on_screen_check_gives_up_after_two_short_tries(monkeypatch):
    calls = []

    def timed_out(req, timeout=None, **_):
        calls.append(timeout)
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(version_check.urllib.request, "urlopen", timed_out)
    monkeypatch.setattr(version_check, "update_base_url", lambda: "https://example.test")
    monkeypatch.setattr(version_check.time, "sleep", lambda s: None)
    payload, error = version_check.fetch_remote_payload(timeout=5.0, attempts=2)
    assert payload is None and "timed out" in error
    assert calls == [5.0, 15.0]  # a timeout is retried once with 15 s


def test_timeout_then_ok_recovers_with_a_longer_wait(monkeypatch):
    calls = []

    def slow_then_ok(req, timeout=None, **_):
        calls.append(timeout)
        if len(calls) == 1:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return Response(b'{"payload":{"currentVersion":"1.2.488"}}')

    monkeypatch.setattr(version_check.urllib.request, "urlopen", slow_then_ok)
    monkeypatch.setattr(version_check, "update_base_url", lambda: "https://example.test")
    monkeypatch.setattr(version_check.time, "sleep", lambda s: None)
    payload, error = version_check.fetch_remote_payload(timeout=5.0, attempts=2)
    assert error is None and payload["currentVersion"] == "1.2.488"
    assert calls == [5.0, 15.0]
    calls.clear()
    version_check.fetch_remote_payload()
    assert len(calls) == ATTEMPTS  # background checks keep every try


def test_packaged_panel_recovers_when_listener_folder_is_missing(tmp_path, monkeypatch):
    import sys
    from frontend.bundle_root import packaged_data_root
    from frontend.ui_web import panel_assets
    from frontend.ui_web.webview_app import _web_root

    root = tmp_path / "_internal"
    dist = root / "frontend" / "ui_web" / "web" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text('<script src="./main.js"></script>', encoding="utf-8")
    (dist / "main.js").write_text("window.booted=true", encoding="utf-8")
    panel_assets.create_panel_archive(dist, dist.parent.parent / "panel-dist.zip")
    (dist / "main.js").unlink()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(root), raising=False)
    assert not (root / "bundle" / "uefn_listener").exists()
    assert packaged_data_root() == root
    try:
        recovered = _web_root()
        assert recovered != dist
        assert (recovered / "main.js").read_text() == "window.booted=true"
    finally:
        recovery = panel_assets._recovered.pop(dist.resolve(), None)
        if recovery:
            recovery.cleanup()


def test_startup_failure_records_original_error_before_generic_popup(monkeypatch):
    import sys
    from frontend.ui_web import shutdown
    recorded = []
    monkeypatch.setattr("frontend.error_log.record_error",
                        lambda source, detail: recorded.append((source, detail)))
    monkeypatch.setattr(shutdown, "hard_exit", lambda **kwargs: None)
    monkeypatch.setitem(sys.modules, "tkinter", None)
    error = FileNotFoundError("Panel build incomplete: assets/missing.js")
    shutdown.fatal_error_and_exit(error)
    assert recorded[0][0] == "startup"
    assert "assets/missing.js" in recorded[0][1]
    assert "FileNotFoundError" in recorded[0][1]


def test_a_missing_installed_dll_says_how_to_repair(monkeypatch):
    """1.2.462 (Nov 3 2026): pywebview's 'Cannot find Microsoft.Web.WebView2.Core.dll' was a traceback."""
    from frontend import bundle_root
    from frontend.ui_web import shutdown

    error = FileNotFoundError("Cannot find Microsoft.Web.WebView2.Core.dll")
    monkeypatch.setattr(bundle_root, "is_packaged_runtime", lambda: True)
    text = shutdown._format_fatal_message(error)
    assert "Microsoft.Web.WebView2.Core.dll" in text and "uefnducky.org" in text and "Traceback" not in text
    monkeypatch.setattr(bundle_root, "is_packaged_runtime", lambda: False)
    assert shutdown._format_fatal_message(error).startswith("FileNotFoundError"), "a source run keeps the raw error"
    monkeypatch.setattr(bundle_root, "is_packaged_runtime", lambda: True)
    assert "uefnducky.org" not in shutdown._format_fatal_message(FileNotFoundError("settings.json"))
