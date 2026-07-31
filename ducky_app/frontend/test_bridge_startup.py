"""Bridge must not sync-wait on Store plugins before mcp.run().

Run: py -m frontend.test_bridge_startup  (from ducky_app/)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch


def main() -> None:
    import frontend.launcher as launcher
    import backend.server as server
    import backend.uefn_plugins.host as host

    import threading

    ran: list[str] = []
    ensure_started = threading.Event()

    def _fake_run() -> None:
        ran.append("mcp.run")

    def _slow_ensure() -> None:
        ran.append("ensure_bg")
        ensure_started.set()
        time.sleep(30)

    with (
        patch.object(launcher, "_ensure_repo_on_path", return_value="."),
        patch.object(server.mcp, "run", _fake_run),
        patch.object(host, "ensure_plugins_loaded", _slow_ensure),
        patch("frontend.ship_newest.ship_newest_everywhere", lambda **_k: []),
        patch("frontend.appdata_maintenance.start_appdata_maintenance_async", lambda: None),
        patch("backend.bridge.dynamic_tools.register_dynamic_listener_tools", lambda: None),
        patch("backend.mcp_plugins.bridge_proxy.sync_nested_mcp_proxies", lambda: []),
    ):
        t0 = time.perf_counter()
        launcher.run_bridge()
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert "mcp.run" in ran, "mcp.run() must be reached on critical path"
    assert elapsed_ms < 5000.0, f"run_bridge blocked before mcp.run: {elapsed_ms:.0f}ms"
    # Background may have started ensure — must not have blocked mcp.run.
    assert launcher.os.environ.get("UEFN_DUCKY_MCP_BRIDGE") == "1"
    print(f"ok run_bridge reached mcp.run in {elapsed_ms:.0f}ms (bg ensure started={ensure_started.is_set()})")


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    ducky_app = here.parent
    root = ducky_app.parent
    for p in (str(root), str(ducky_app)):
        if p not in sys.path:
            sys.path.insert(0, p)
    main()
