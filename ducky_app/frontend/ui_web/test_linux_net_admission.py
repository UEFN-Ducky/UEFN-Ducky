"""Linux cases for net_access (environment proxy, no WinHTTP), admission (MemAvailable) and the
seat queue (MemTotal)."""

from __future__ import annotations

import socket
import threading
import urllib.request

from frontend import low_memory, net_access
from frontend.ui_web import admission, seat_queue

GB = 1024**3


def _on_linux(monkeypatch) -> None:
    monkeypatch.setattr(net_access.sys, "platform", "linux")


def test_linux_has_no_winhttp_and_takes_the_environment_proxy(monkeypatch):
    _on_linux(monkeypatch)
    # Cleared first: Windows env names ignore case, so a later delenv("HTTPS_PROXY") would drop https_proxy.
    for key in ("HTTPS_PROXY", "NO_PROXY", "http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("https_proxy", "http://proxy.lan:3128")
    monkeypatch.setenv("no_proxy", "intranet.lan")
    assert net_access._winhttp_config() == {}
    handler = net_access.PacProxyHandler(resolve=lambda url, cfg: "never asked:1")
    assert handler.proxy_for("https://uefnducky.org/x") == "http://proxy.lan:3128"
    assert handler.proxy_for("https://intranet.lan/x") == ""
    assert handler.proxy_for("http://127.0.0.1:8765/panel") == ""  # loopback is never proxied


def test_linux_without_any_proxy_goes_direct(monkeypatch):
    _on_linux(monkeypatch)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
    assert net_access.PacProxyHandler().proxy_for("https://uefnducky.org/") == ""


def test_the_local_window_reaches_a_loopback_panel_server():
    # The panel server is loopback-only; the window's request through install()'s
    # create_connection and opener reaches it direct (no proxy, no WinHTTP).
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def answer() -> None:
        conn, _ = server.accept()
        conn.recv(4096)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        conn.close()

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    try:
        sock = net_access.create_connection(("localhost", port), timeout=5)
        sock.sendall(b"GET /panel HTTP/1.1\r\nHost: localhost\r\n\r\n")
        assert sock.recv(4096).endswith(b"ok")
        sock.close()
    finally:
        t.join(5)
        server.close()


def test_admission_warns_from_mem_available_on_linux(monkeypatch):
    monkeypatch.setattr(admission.sys, "platform", "linux")
    monkeypatch.setattr(low_memory, "meminfo", lambda: {"MemTotal": 16 * GB, "MemAvailable": int(1.0 * GB)})
    monkeypatch.setattr(admission, "_read_free", admission.free_memory_gb)
    monkeypatch.setattr(admission, "min_free_gb", lambda: 2.4)
    monkeypatch.setattr(admission, "_counts", lambda conv_id, source: True)
    monkeypatch.setattr(admission, "biggest_users", lambda n=5: [])
    monkeypatch.setattr(admission, "_warned", {"low": False})
    pushed: list = []
    assert admission.may_start("linux-seat", "agent", push=pushed.append) is True  # never held
    admission.release("linux-seat")
    assert pushed and pushed[0]["type"] == "low_memory_warning" and pushed[0]["free_gb"] == 1.0


def test_seat_queue_reads_mem_total_on_linux(monkeypatch):
    monkeypatch.setattr(seat_queue.sys, "platform", "linux")
    monkeypatch.setattr(low_memory, "meminfo", lambda: {"MemTotal": 32 * GB})
    assert seat_queue.total_memory_gb() == 32.0
    assert admission.default_min_free_gb() == 4.8
    monkeypatch.setattr(low_memory, "meminfo", lambda: {})  # no /proc: psutil, then 16
    assert seat_queue.total_memory_gb() > 0
