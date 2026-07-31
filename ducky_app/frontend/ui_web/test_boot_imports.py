"""Cold-start guards: panel_api import + PanelApi() must not sync-load plugins.

Run: py -m frontend.ui_web.test_boot_imports
(from ducky_app/, with repo roots on sys.path via launcher / normal package layout)
"""

from __future__ import annotations

import sys
import time


def _purge(*keys: str) -> None:
    drop = set(keys)
    for key in list(sys.modules):
        if key == "mcp" or key.startswith("mcp.") or key in drop:
            del sys.modules[key]


def test_panel_api_import_skips_mcp() -> None:
    _purge(
        "backend.server",
        "frontend.ui_web.panel_api",
        "frontend.ui_web.agent_modes",
        "backend.agent.runner",
        "backend.agent.tools",
        "backend.agent.prompt",
    )

    t0 = time.perf_counter()
    import frontend.ui_web.panel_api  # noqa: F401

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    mcp_loaded = any(m == "mcp" or m.startswith("mcp.") for m in sys.modules)
    assert not mcp_loaded, "panel_api import pulled the mcp package (splash stays up too long)"
    assert "backend.server" not in sys.modules, "panel_api import pulled backend.server"
    # Warm machine budget; packaged EXE + AV can be higher — this catches the FastMCP regress.
    assert elapsed_ms < 3000.0, f"panel_api import too slow: {elapsed_ms:.0f}ms"
    print(f"ok panel_api import {elapsed_ms:.0f}ms without mcp")


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
