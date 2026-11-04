"""The plugin_host test helpers swap every copy of a name, and put them all back."""

from __future__ import annotations

import pytest

from backend.uefn_plugins.plugin_host import gateways, lifecycle, tool_registry, ui_registry
from backend.uefn_plugins.plugin_host.testing import holders, host_patched, patch_host


def test_holders_lists_every_module_that_binds_the_name() -> None:
    assert {lifecycle, gateways, tool_registry, ui_registry} <= set(holders("is_plugin_enabled"))


def test_an_unknown_name_is_refused() -> None:
    with pytest.raises(AttributeError):
        holders("no_such_name_anywhere")


def test_patch_host_reaches_every_copy(monkeypatch) -> None:
    real = lifecycle.is_plugin_enabled

    def fake(plugin_id: str) -> bool:
        return True

    patch_host(monkeypatch, "is_plugin_enabled", fake)
    assert lifecycle.is_plugin_enabled is gateways.is_plugin_enabled is ui_registry.is_plugin_enabled is fake
    monkeypatch.undo()
    assert lifecycle.is_plugin_enabled is gateways.is_plugin_enabled is ui_registry.is_plugin_enabled is real


def test_host_patched_shares_one_mock_and_restores() -> None:
    real = lifecycle.is_plugin_enabled
    with host_patched("is_plugin_enabled", return_value=True) as fake:
        assert lifecycle.is_plugin_enabled is tool_registry.is_plugin_enabled is fake
    assert lifecycle.is_plugin_enabled is tool_registry.is_plugin_enabled is real
