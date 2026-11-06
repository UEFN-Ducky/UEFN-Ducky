"""SSL's Windows certificate store loads on a thread for the window launch, and is waited for."""
from __future__ import annotations

import inspect
import sys
import threading
import types

from frontend import ssl_boot


def test_inject_now_never_raises(monkeypatch):
    broken = types.ModuleType("truststore")

    def boom():
        raise RuntimeError("no store")

    broken.inject_into_ssl = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "truststore", broken)
    ssl_boot.inject_now()


def test_background_inject_runs_once_and_wait_blocks_until_done(monkeypatch):
    calls: list[int] = []
    gate = threading.Event()
    fake = types.ModuleType("truststore")

    def inject():
        gate.wait(5)
        calls.append(1)

    fake.inject_into_ssl = inject  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "truststore", fake)
    monkeypatch.setattr(ssl_boot, "_thread", None)
    ssl_boot.inject_in_background()
    ssl_boot.inject_in_background()
    threading.Timer(0.05, gate.set).start()
    ssl_boot.wait(5)
    assert calls == [1]


def test_wait_without_a_thread_returns_at_once(monkeypatch):
    monkeypatch.setattr(ssl_boot, "_thread", None)
    ssl_boot.wait(0.01)


def test_inject_failure_is_logged(monkeypatch, caplog):
    broken = types.ModuleType("truststore")

    def boom():
        raise RuntimeError("no store")

    broken.inject_into_ssl = boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "truststore", broken)
    monkeypatch.setattr(ssl_boot, "_warned", False)
    with caplog.at_level("WARNING", logger="frontend.ssl_boot"):
        ssl_boot.inject_now()
    assert "no store" in caplog.text


def test_wait_past_its_timeout_injects_on_the_caller(monkeypatch):
    calls: list[str] = []
    hung = threading.Thread(target=threading.Event().wait, args=(5,), daemon=True)
    hung.start()
    monkeypatch.setattr(ssl_boot, "_thread", hung)
    monkeypatch.setattr(ssl_boot, "inject_now", lambda: calls.append("inline"))
    ssl_boot.wait(0.01)
    assert calls == ["inline"]


def test_https_context_is_windows_verified_and_fresh():
    import ssl

    import truststore

    a, b = ssl_boot.https_context(), ssl_boot.https_context()
    assert isinstance(a, truststore.SSLContext) and a is not b
    assert a.verify_mode == ssl.CERT_REQUIRED and a.check_hostname


def test_https_context_falls_back_to_a_verifying_context(monkeypatch):
    import ssl

    monkeypatch.setitem(sys.modules, "truststore", types.ModuleType("truststore"))
    monkeypatch.setattr(ssl_boot, "_warned", True)
    ctx = ssl_boot.https_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_app_openers_verify_with_windows_per_connection(monkeypatch):
    import truststore

    from frontend import net_access

    opener = net_access.build_opener()
    handler = next(h for h in opener.handlers if isinstance(h, net_access.WindowsTrustHTTPSHandler))
    assert not any(type(h).__name__ == "HTTPSHandler" for h in opener.handlers)
    seen: list[object] = []
    monkeypatch.setattr(handler, "do_open", lambda _cls, _req, context: seen.append(context))
    handler.https_open(object())
    handler.https_open(object())
    assert all(isinstance(c, truststore.SSLContext) for c in seen) and seen[0] is not seen[1]


def test_no_https_path_uses_openssl_alone():
    """Every HTTPS connection Ducky makes goes through ssl_boot.https_context()."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    bad = re.compile(r"ssl\.create_default_context\(|urllib\.request\.HTTPSHandler\(")
    hits = [
        f"{p.relative_to(root)}:{n}"
        for p in root.rglob("*.py")
        if not p.name.startswith("test_") and p.name != "ssl_boot.py" and "node_modules" not in p.parts
        for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
        if bad.search(line)
    ]
    assert hits == []


def test_window_launch_threads_it_and_every_https_user_waits():
    from frontend import boot_report, launcher
    from frontend.ui_web import webview_app

    main_src = inspect.getsource(launcher.main)
    assert "ssl_boot.inject_in_background()" in main_src
    assert "ssl_boot.inject_now()" in main_src  # agent host, bridge, db: right away
    assert "truststore.inject_into_ssl()" not in main_src
    # The window and --backend both build the panel API through boot_panel_api.
    assert "api = boot_panel_api()" in inspect.getsource(webview_app._run_panel)
    panel_src = inspect.getsource(webview_app.boot_panel_api)
    assert panel_src.index("wait_for_ssl_boot()") < panel_src.index("from frontend.ui_web.panel_api import")
    assert "wait_for_ssl_boot()" in inspect.getsource(boot_report._send)
