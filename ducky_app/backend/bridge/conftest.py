"""Bridge tests list their own fake tools (``ping``...): off Windows, the no-UEFN-route
filter (plugin_gate) would hide them and ask the account for linked PCs. It stays off
here; test_tools_list_routes turns it back on."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _uefn_route_filter_off(monkeypatch):
    from backend.bridge import plugin_gate

    monkeypatch.setattr(plugin_gate, "_no_uefn_route", lambda: False)
