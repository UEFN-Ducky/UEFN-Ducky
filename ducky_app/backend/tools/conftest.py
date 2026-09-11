"""Tool tests run against an empty AppData (root conftest), where no Store
plugin is installed. The tool bodies under test forward parameters to the
listener; the Store enable gate is a separate concern with its own tests
(``backend/uefn_plugins``). Open the gate here so the suite is hermetic.

Before this fixture, twelve tests passed only on a machine with the ``uefn``,
``verse`` and ``leveldesign`` plugins enabled in the real settings.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _open_plugin_gate(monkeypatch: pytest.MonkeyPatch):
    from backend.tools.support import plugin_gate

    monkeypatch.setattr(plugin_gate, "require_plugin", lambda plugin_id: None)
    yield
