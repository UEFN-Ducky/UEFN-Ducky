"""A panel polling a plugin that fails to load doesn't reload it on every call, and
hears why it failed instead of "Unknown panel RPC" (Account polled account.status every
2 s, re-importing the plugin and logging a load error each time)."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.uefn_plugins import store
from backend.uefn_plugins.plugin_host import panel_rpc, state
from backend.uefn_plugins.plugin_host.testing import patch_host


def test_a_failed_plugin_is_retried_on_the_backoff_and_says_why(monkeypatch, tmp_path: Path) -> None:
    loads: list[str] = []

    def failing_load(pid, root, manifest):
        loads.append(pid)
        state._LOAD_FAILURES[pid] = "LicenseError: This copy isn't licensed for this PC"

    patch_host(monkeypatch, "is_plugin_enabled", lambda pid: True)
    patch_host(monkeypatch, "wait_plugin_toggles", lambda timeout=0: None)
    patch_host(monkeypatch, "_load_enabled_plugin", failing_load)
    monkeypatch.setattr(store, "load_plugin_manifest", lambda pid: {"id": pid})
    monkeypatch.setattr(store, "plugin_dir", lambda pid: tmp_path)
    patch_host(monkeypatch, "_PANEL_RPC", {})
    patch_host(monkeypatch, "_LOAD_FAILURES", {})
    patch_host(monkeypatch, "_REPAIR_ATTEMPTS", {})

    for _ in range(3):
        with pytest.raises(ValueError, match="didn't load: LicenseError"):
            panel_rpc.call_panel_rpc("account", "status")
    assert loads == ["account"]

    state._REPAIR_ATTEMPTS["account"] -= state._REPAIR_BACKOFF_S
    with pytest.raises(ValueError):
        panel_rpc.call_panel_rpc("account", "status")
    assert loads == ["account", "account"]
