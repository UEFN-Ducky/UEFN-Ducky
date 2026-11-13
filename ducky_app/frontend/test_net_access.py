import socket
import threading
import urllib.request

import pytest

from frontend import net_access as na

V6A = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1", 443, 0, 0))
V6B = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::2", 443, 0, 0))
V4A = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.21.73.49", 443))


def test_an_answer_unpacks_as_before_and_keeps_its_headers():
    got = na.Answer((429, {"error": "busy"}, "{}"), {"Retry-After": "7"})
    status, parsed, raw = got  # every api_request caller unpacks three
    assert (status, parsed, raw, len(got)) == (429, {"error": "busy"}, "{}", 3)
    assert got.headers["Retry-After"] == "7" and na.Answer((200, None, "")).headers is None


class _Sock:
    """A connected socket that remembers its timeout."""

    def __init__(self, timeout=None):
        self.timeout = timeout

    def settimeout(self, value):
        self.timeout = value

    def gettimeout(self):
        return self.timeout


def test_dead_ipv6_falls_to_ipv4_after_a_short_try(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V6A, V6B, V4A])

    def fake(addr, timeout=None, source=None, **_):
        calls.append((addr[0], timeout))
        if ":" in addr[0]:
            raise TimeoutError("timed out")
        return _Sock(timeout)

    monkeypatch.setattr(na, "_orig_create_connection", fake)
    sock = na.create_connection(("uefnducky.org", 443), 8)
    assert calls == [("2606:4700::1", 2.0), ("104.21.73.49", 2.0)]
    # The short try is for connecting only: the socket reads with the caller's timeout.
    assert sock.gettimeout() == 8


def test_a_fast_first_address_keeps_the_callers_read_timeout(monkeypatch):
    """1.2.464: the first address connected with 2 s and kept it, so every slower read died."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V6A, V4A])
    monkeypatch.setattr(na, "_orig_create_connection", lambda addr, timeout=None, source=None, **_: _Sock(timeout))
    assert na.create_connection(("uefnducky.org", 443), 60).gettimeout() == 60
    assert na.create_connection(("uefnducky.org", 443), None).gettimeout() is None
    previous = socket.getdefaulttimeout()
    socket.setdefaulttimeout(30)
    try:
        assert na.create_connection(("uefnducky.org", 443)).gettimeout() == 30
    finally:
        socket.setdefaulttimeout(previous)


def test_a_real_socket_reads_with_the_callers_timeout(monkeypatch):
    """Against a real listener on both families' loopback when IPv6 is there."""
    import contextlib

    srv = socket.create_server(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    v4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))
    v6dead = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 1, 0, 0))
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [v4, v6dead])
    with contextlib.closing(srv), contextlib.closing(na.create_connection(("localhost", port), 45)) as s:
        assert s.gettimeout() == 45


def test_last_address_gets_the_whole_timeout_and_the_error_surfaces(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V6A, V4A])

    def fake(addr, timeout=None, source=None, **_):
        calls.append(timeout)
        raise TimeoutError("timed out")

    monkeypatch.setattr(na, "_orig_create_connection", fake)
    with pytest.raises(TimeoutError):
        na.create_connection(("uefnducky.org", 443), 8)
    assert calls == [2.0, 8]


def test_one_family_is_left_to_the_stdlib(monkeypatch):
    seen = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V4A])
    monkeypatch.setattr(na, "_orig_create_connection", lambda addr, t=None, s=None, **_: seen.append((addr, t)) or "s")
    assert na.create_connection(("uefnducky.org", 443), 8) == "s"
    assert seen == [(("uefnducky.org", 443), 8)]


def test_pick_proxy_reads_winhttp_lists():
    assert na.pick_proxy("proxy.school:8080", "https") == "http://proxy.school:8080"
    assert na.pick_proxy("http=a:80;https=b:443", "https") == "http://b:443"
    assert na.pick_proxy("a:1 b:2", "http") == "http://a:1"
    assert na.pick_proxy("", "https") == ""


def _handler(resolve, cfg=None):
    return na.PacProxyHandler(resolve=resolve, config=lambda: cfg if cfg is not None else {"url": "http://wpad/x.pac"})


def test_pac_proxy_tunnels_https(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    h = _handler(lambda url, cfg: "proxy.school:8080")
    req = h.https_request(urllib.request.Request("https://uefnducky.org/api/v1/x"))
    assert req.host == "proxy.school:8080" and req._tunnel_host == "uefnducky.org"


def test_loopback_never_asks_and_stays_direct(monkeypatch):
    def boom(*_):
        raise AssertionError("asked WinHTTP for loopback")

    h = _handler(boom)
    req = h.http_request(urllib.request.Request("http://127.0.0.1:4199/x"))
    assert req.host == "127.0.0.1:4199" and not req.has_proxy()


def test_resolver_failure_or_hang_means_direct(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(na, "PAC_WAIT_S", 0.1)

    def boom(*_):
        raise OSError("winhttp")

    assert _handler(boom).proxy_for("https://uefnducky.org/") == ""
    gate = threading.Event()
    assert _handler(lambda *_: gate.wait(5) or "p:1").proxy_for("https://uefnducky.org/") == ""
    gate.set()


def test_errors_become_a_stage_and_a_plain_sentence():
    import ssl
    import urllib.error

    cases = {
        urllib.error.URLError(TimeoutError("timed out")): "timeout",
        urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed")): "dns",
        urllib.error.URLError(ConnectionRefusedError(10061, "refused")): "connect",
        urllib.error.URLError(ssl.SSLError("certificate verify failed")): "tls",
        urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden")): "proxy",
        urllib.error.HTTPError("https://x", 503, "busy", {}, None): "http",
    }
    for exc, stage in cases.items():
        assert na.stage_of(exc) == stage, exc
    stage, text = na.explain(urllib.error.URLError(TimeoutError("timed out")), "uefnducky.org")
    assert stage == "timeout" and "uefnducky.org" in text and "Test network" in text and "(timed out)" in text


def test_diagnose_reports_each_hop_without_raising(monkeypatch):
    import urllib.error

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V6A, V4A])

    def conn(addr, timeout=None, *a, **k):
        if ":" in addr[0]:
            raise TimeoutError("timed out")
        return socket.socket()

    monkeypatch.setattr(na, "_orig_create_connection", conn)
    monkeypatch.setattr(na, "_HANDLER", _handler(lambda *_: "", cfg={}))
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})

    class Opener:
        def open(self, req, timeout=None):
            raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: Opener())
    out = na.diagnose("https://uefnducky.org/")
    assert out["dns"]["ipv4"] == 1 and out["dns"]["ipv6"] == 1
    assert out["ipv4"]["ok"] is True and out["ipv6"]["ok"] is False
    assert out["https"] == {"ok": False, "stage": "timeout", "error": out["https"]["error"]}
    assert out["ok"] is False and out["proxy"] == "direct" and out["proxy_mode"] == "none"


def test_an_address_that_cut_a_secure_handshake_is_tried_last_for_a_while(monkeypatch):
    """Nov 13 2026: the storage host's first address (IPv6) cut every handshake and the retries
    went back to it five times."""
    calls = []
    monkeypatch.setattr(na, "_tls_cut", {})
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V6A, V4A])
    monkeypatch.setattr(na, "_orig_create_connection",
                        lambda addr, timeout=None, source=None, **_: calls.append(addr[0]) or _Sock(timeout))
    na.create_connection(("bucket.fly.storage.tigris.dev", 443), 8)
    na.note_tls_cut(("2606:4700::1", 443, 0, 0))
    na.create_connection(("bucket.fly.storage.tigris.dev", 443), 8)
    assert calls == ["2606:4700::1", "104.21.73.49"]

    later = na.time.monotonic() + na.TLS_CUT_S + 1
    monkeypatch.setattr(na.time, "monotonic", lambda: later)
    na.create_connection(("bucket.fly.storage.tigris.dev", 443), 8)
    assert calls[-1] == "2606:4700::1"  # ten minutes on it gets its turn again


def test_one_family_with_a_cut_address_moves_to_the_next_one(monkeypatch):
    v4b = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.67.140.152", 443))
    calls = []
    monkeypatch.setattr(na, "_tls_cut", {})
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V4A, v4b])
    monkeypatch.setattr(na, "_orig_create_connection",
                        lambda addr, timeout=None, source=None, **_: calls.append(addr[0]) or _Sock(timeout))
    na.note_tls_cut(("104.21.73.49", 443))
    na.create_connection(("uefnducky.org", 443), 8)
    assert calls == ["172.67.140.152"]


def test_only_a_cut_handshake_counts_never_a_certificate():
    import ssl

    assert na.handshake_cut(ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"))
    assert na.handshake_cut(ConnectionResetError(10054, "reset"))
    assert not na.handshake_cut(ssl.SSLCertVerificationError(1, "certificate verify failed"))
    assert not na.handshake_cut(TimeoutError("timed out"))


def test_the_network_test_checks_the_host_the_download_comes_from(monkeypatch):
    """The site answered and plugins installed while every update download died on storage."""
    import urllib.error

    def leg(url, timeout=5.0):
        second = "alt=1" in url
        cut = {"ok": False, "stage": "cut", "error": "EOF occurred in violation of protocol"}
        return {"host": "bucket.t3.storage.dev" if second else "bucket.fly.storage.tigris.dev", "ok": second,
                "ipv4": {"ok": True, "ms": 80} if second else cut, "ipv6": {"ok": None}}

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V4A])
    monkeypatch.setattr(na, "_orig_create_connection", lambda addr, timeout=None, *a, **k: socket.socket())
    monkeypatch.setattr(na, "_HANDLER", _handler(lambda *_: "", cfg={}))
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})

    class Opener:
        def open(self, req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 405, "no HEAD", {}, None)  # the site answered

    monkeypatch.setattr(urllib.request, "build_opener", lambda *h: Opener())
    monkeypatch.setattr(na, "download_leg", leg)
    out = na.diagnose("https://uefnducky.org/", download_url="https://uefnducky.org/api/v1/files/app-release/latest")
    assert out["ok"] is True  # the site is fine
    assert out["download"]["ok"] is False and out["download"]["ipv4"]["stage"] == "cut"
    assert out["download_alt"]["host"] == "bucket.t3.storage.dev" and out["download_alt"]["ok"] is True
    assert "download" not in na.diagnose("https://uefnducky.org/")  # asked for the site only


def test_no_auto_config_uses_the_fixed_proxy(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://corp:3128"})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    h = _handler(lambda *_: "never", cfg={})
    assert h.proxy_for("https://uefnducky.org/") == "http://corp:3128"
