"""Cold-start guards: panel_api import + PanelApi() must not sync-load plugins.

Run: py -m frontend.ui_web.test_boot_imports
(from ducky_app/, with repo roots on sys.path via launcher / normal package layout)
"""

from __future__ import annotations

import sys
import time


_COLD_IMPORT_PROBE = r"""
import sys, time
t0 = time.perf_counter()
import frontend.ui_web.panel_api  # noqa: F401
elapsed_ms = (time.perf_counter() - t0) * 1000.0
mcp_loaded = any(m == "mcp" or m.startswith("mcp.") for m in sys.modules)
print("RESULT", int(mcp_loaded), int("backend.server" in sys.modules), f"{elapsed_ms:.0f}")
"""


def test_panel_api_import_skips_mcp() -> None:
    """Cold import in a child interpreter: purging sys.modules in-process left
    later tests holding two copies of agent_modes (monkeypatches missed)."""
    import os
    import subprocess

    app_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ)
    env["PYTHONPATH"] = app_root + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", _COLD_IMPORT_PROBE],
        capture_output=True,
        text=True,
        env=env,
        cwd=app_root,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT "))
    _, mcp_loaded, server_loaded, elapsed = line.split()
    assert mcp_loaded == "0", "panel_api import pulled the mcp package (splash stays up too long)"
    assert server_loaded == "0", "panel_api import pulled backend.server"
    # Warm machine budget; packaged EXE + AV can be higher — this catches the FastMCP regress.
    assert float(elapsed) < 3000.0, f"panel_api import too slow: {elapsed}ms"


def test_panel_api_init_does_not_await_plugins() -> None:
    """PanelApi() must return without waiting for ensure_plugins_loaded()."""
    import frontend.ui_web.panel_api as panel_api
    from backend.uefn_plugins import host as plugins_host

    async_calls: list[object] = []
    original_async = plugins_host.ensure_plugins_loaded_async

    def _fake_async(on_done=None):  # type: ignore[no-untyped-def]
        async_calls.append(on_done)
        # Do not load — simulates a long background job still running.

    def _fake_warm() -> None:
        return

    plugins_host.ensure_plugins_loaded_async = _fake_async  # type: ignore[assignment]
    panel_api._warm_model_cache = _fake_warm  # type: ignore[attr-defined]
    try:
        # Force "not ready" so a regress that sync-waits would hang or take forever.
        was_loaded = plugins_host._LOADED
        was_ui_ready = plugins_host._UI_READY
        plugins_host._LOADED = False
        plugins_host._UI_READY = False
        t0 = time.perf_counter()
        api = panel_api.PanelApi()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert elapsed_ms < 2000.0, f"PanelApi() blocked too long: {elapsed_ms:.0f}ms"
        assert async_calls, "PanelApi() should kick ensure_plugins_loaded_async"
        contrib = api.get_uefn_plugin_contributions()
        assert contrib.get("ok") is False
        assert contrib.get("error") == "plugins_loading"
        print(f"ok PanelApi() {elapsed_ms:.0f}ms without awaiting plugins")
    finally:
        plugins_host.ensure_plugins_loaded_async = original_async  # type: ignore[assignment]
        plugins_host._LOADED = was_loaded
        plugins_host._UI_READY = was_ui_ready


def main() -> None:
    test_panel_api_import_skips_mcp()
    test_panel_api_init_does_not_await_plugins()


if __name__ == "__main__":
    main()
