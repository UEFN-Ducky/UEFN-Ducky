"""The shared registries: one object each, the same one every host module reads."""

from __future__ import annotations

from backend.uefn_plugins import plugin_host
from backend.uefn_plugins.plugin_host import (
    api,
    boot,
    gateways,
    lifecycle,
    loader,
    services,
    state,
    tool_registry,
    ui_registry,
)


def test_every_module_reads_the_same_registries() -> None:
    for mod in (api, boot, gateways, lifecycle, loader, services, tool_registry, ui_registry):
        for name in ("_LOCK", "_CONTRIBUTIONS", "_REGISTERED", "_PLUGIN_TOOL_OWNER"):
            if name in vars(mod):
                assert getattr(mod, name) is getattr(state, name), f"{mod.__name__}.{name}"


def test_the_contribution_registry_has_every_ui_key() -> None:
    assert {"settings_tabs", "dock_panels", "llm_providers", "agent_tools", "reference_sources"} <= set(
        state._CONTRIBUTIONS)
    assert state.MEDIA_KINDS == ("image", "image_edit", "video", "music")


def test_the_package_says_what_each_module_does() -> None:
    assert "boot" in (plugin_host.__doc__ or "")
