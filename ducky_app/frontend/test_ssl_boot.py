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
