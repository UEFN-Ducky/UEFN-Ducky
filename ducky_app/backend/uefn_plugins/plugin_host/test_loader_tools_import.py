"""A plugin loading while another thread imports backend.tools gets whole modules."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

DUCKY_APP = Path(__file__).resolve().parents[3]

# The app imports backend.tools while two plugins import its panel modules mid-way, as
# Account did at start (Nov 10 2026).
_RACE = textwrap.dedent(
    """
    import importlib, sys, threading
    from backend.uefn_plugins.plugin_host import loader

    WAIT = sys.argv[1] == "1"
    PLUGIN_IMPORTS = ("backend.tools.panel.panel_ai_plugins", "backend.tools.panel.panel_ui",
                      "backend.tools.panel.panel_plugin_check")
    APP_IMPORTS = ("backend.tools", "backend.server", "backend.bridge.shared_mcp")
    errs, go = [], threading.Barrier(len(APP_IMPORTS) + len(PLUGIN_IMPORTS))

    def app(name):
        go.wait()
        try:
            importlib.import_module(name)
        except Exception as e:
            errs.append(repr(e))

    def plugin(name):
        go.wait()
        if WAIT:
            loader._wait_tools_import()
        try:
            importlib.import_module(name)
        except Exception as e:
            errs.append(repr(e))

    ts = [threading.Thread(target=app, args=(n,)) for n in APP_IMPORTS] + [threading.Thread(target=plugin, args=(n,)) for n in PLUGIN_IMPORTS]
    [t.start() for t in ts]
    [t.join() for t in ts]
    print("ERRS", errs)
    """
)


def _race(wait: bool) -> str:
    out = subprocess.run([sys.executable, "-c", _RACE, "1" if wait else "0"], cwd=DUCKY_APP,
                         capture_output=True, text=True, timeout=180)
    return out.stdout + out.stderr


def test_plugin_import_waits_for_backend_tools() -> None:
    for _ in range(3):
        out = _race(wait=True)
        assert "ERRS []" in out, out


def test_the_race_is_real_without_the_wait() -> None:
    """Guards the test above: without the wait this race breaks an import."""
    assert any("ERRS []" not in _race(wait=False) for _ in range(3))
