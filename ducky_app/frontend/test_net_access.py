import socket
import threading
import urllib.request

import pytest

from frontend import net_access as na

V6A = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::1", 443, 0, 0))
V6B = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700::2", 443, 0, 0))
V4A = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("104.21.73.49", 443))


def test_dead_ipv6_falls_to_ipv4_after_a_short_try(monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [V6A, V6B, V4A])

    def fake(addr, timeout=None, source=None, **_):
        calls.append((addr[0], timeout))
        if ":" in addr[0]:
            raise TimeoutError("timed out")
        return "sock"

    monkeypatch.setattr(na, "_orig_create_connection", fake)
    assert na.create_connection(("uefnducky.org", 443), 8) == "sock"
    assert calls == [("2606:4700::1", 2.0), ("104.21.73.49", 2.0)]


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


def test_no_auto_config_uses_the_fixed_proxy(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"https": "http://corp:3128"})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    h = _handler(lambda *_: "never", cfg={})
    assert h.proxy_for("https://uefnducky.org/") == "http://corp:3128"
