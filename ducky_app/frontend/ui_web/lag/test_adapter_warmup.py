"""A per-agent --adapter bridge with a live shared daemon must start cheap: no warmup."""
import sys

import pytest

from frontend import launcher


@pytest.fixture
def counted(monkeypatch):
    from backend.uefn_plugins import host
    from frontend import appdata_maintenance, ship_newest

    calls = []
    # raising=False: the plugin-host split may move it; the import in launcher still finds this.
    monkeypatch.setattr(host, "ensure_plugins_loaded", lambda *a, **k: calls.append("plugins"), raising=False)
    monkeypatch.setattr(ship_newest, "ship_newest_everywhere", lambda *a, **k: calls.append("ship") or [])
    monkeypatch.setattr(appdata_maintenance, "start_appdata_maintenance_async",
                        lambda *a, **k: calls.append("maintenance"))
    monkeypatch.setattr("backend.process_role.set_role", lambda role: None)
    monkeypatch.setenv("UEFN_DUCKY_APP_VERSION", "")  # run_bridge sets it; restored after
    return calls


def test_adapter_with_a_live_daemon_relays_and_skips_every_warmup(monkeypatch, counted):
    from frontend import shared_mcp_adapter

    relayed = []
    monkeypatch.setattr(shared_mcp_adapter, "run", lambda args: relayed.append(args) or True)
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky-Bridge.exe", "bridge", "--port", "4201", "--adapter",
                                      "--ducky-run-id", "run-a"])
    launcher.run_bridge()
    assert relayed == [["--port", "4201", "--ducky-run-id", "run-a"]]
    assert counted == []  # no ensure_plugins_loaded, no ship, no maintenance


def test_adapter_without_a_daemon_falls_back_to_the_dedicated_bridge(monkeypatch, counted):
    from frontend import shared_mcp_adapter

    monkeypatch.setattr(shared_mcp_adapter, "run", lambda args: False)
    monkeypatch.setattr(sys, "argv", ["UEFN-Ducky-Bridge.exe", "bridge", "--adapter"])

    class Fallback(Exception):
        pass

    def stop_here():
        raise Fallback

    monkeypatch.setattr(launcher, "_patch_mcp_request_responder_idempotent", stop_here)
    with pytest.raises(Fallback):
        launcher.run_bridge()  # reached the dedicated path (warmup runs there, as before)
