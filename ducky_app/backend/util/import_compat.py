"""Lazy legacy import aliases for pre-hierarchy Store plugins and callers.

Installed plugin zips may still `import backend.tools.actors` etc.
This finder resolves those paths to the organized modules on demand,
without eagerly registering Store-gated MCP tools.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
from typing import Any

_ALIASES: dict[str, str] = {
    "backend.bridge_plugin_gate": "backend.bridge.plugin_gate",
    "backend.builtin_toolsets": "backend.agent.builtin_toolsets",
    "backend.dynamic_tools": "backend.bridge.dynamic_tools",
    "backend.env_compat": "backend.util.env_compat",
    "backend.json_util": "backend.util.json_util",
    "backend.listener_serial": "backend.bridge.serial",
    "backend.listener_status": "backend.bridge.status",
    "backend.mcp_content": "backend.agent.mcp_content",
    "backend.panel_rpc": "backend.panel.rpc",
    "backend.project_memory": "backend.memory.project",
    "backend.serialization": "backend.agent.serialization",
    "backend.skill": "backend.skills.store",
    "backend.tools.actors": "backend.tools.uefn.actors",
    "backend.tools.ai": "backend.tools.uefn.ai",
    "backend.tools.animation_retarget": "backend.tools.animation.animation_retarget",
    "backend.tools.assets": "backend.tools.uefn.assets",
    "backend.tools.assets_pipeline": "backend.tools.uefn.assets_pipeline",
    "backend.tools.blockout_areas": "backend.tools.world.blockout_areas",
    "backend.tools.code_diagnostics": "backend.tools.core.code_diagnostics",
    "backend.tools.code_diagnostics_lib": "backend.tools.core.code_diagnostics_lib",
    "backend.tools.data_tables": "backend.tools.uefn.data_tables",
    "backend.tools.device_editor": "backend.tools.uefn.device_editor",
    "backend.tools.device_focused": "backend.tools.uefn.device_focused",
    "backend.tools.ducky_panel": "backend.tools.panel.ducky_panel",
    "backend.tools.editor": "backend.tools.uefn.editor",
    "backend.tools.fort": "backend.tools.world.fort",
    "backend.tools.hints": "backend.tools.core.hints",
    "backend.tools.introspection": "backend.tools.uefn.introspection",
    "backend.tools.level": "backend.tools.uefn.level",
    "backend.tools.level_design": "backend.tools.world.level_design",
    "backend.tools.level_viewport": "backend.tools.uefn.level_viewport",
    "backend.tools.memory": "backend.tools.uefn.memory",
    # Note: backend.tools.modeling is a real package — do not alias it.
    # Package __init__ imports modeling.modeling for legacy side-effect imports.
    "backend.tools.niagara": "backend.tools.vfx.niagara",
    "backend.tools.panel_ai_plugins": "backend.tools.panel.panel_ai_plugins",
    "backend.tools.panel_i18n": "backend.tools.panel.panel_i18n",
    "backend.tools.panel_mcp": "backend.tools.panel.panel_mcp",
    "backend.tools.panel_profiles": "backend.tools.panel.panel_profiles",
    "backend.tools.panel_settings": "backend.tools.panel.panel_settings",
    "backend.tools.panel_skills": "backend.tools.panel.panel_skills",
    "backend.tools.panel_store": "backend.tools.panel.panel_store",
    "backend.tools.panel_ui": "backend.tools.panel.panel_ui",
    "backend.tools.panel_verse_templates": "backend.tools.panel.panel_verse_templates",
    "backend.tools.pcg": "backend.tools.world.pcg",
    "backend.tools.plugin_gate": "backend.tools.support.plugin_gate",
    "backend.tools.scene_graph": "backend.tools.scene.scene_graph",
    "backend.tools.sequencer": "backend.tools.animation.sequencer",
    "backend.tools.skill_tool": "backend.tools.verse.skill_tool",
    "backend.tools.system": "backend.tools.core.system",
    "backend.tools.testing": "backend.tools.tester.suite",
    "backend.tools.translation_service": "backend.tools.integrations.translation_service",
    "backend.tools.translation_tools": "backend.tools.integrations.translation_tools",
    "backend.tools.umg": "backend.tools.verse.umg",
    # Note: backend.tools.verse is a real package — do not alias it.
    # Package __init__ imports verse.verse for legacy side-effect imports.
    "backend.tools.verse_diagnostics": "backend.tools.verse.verse_diagnostics",
    "backend.tools.verse_digests": "backend.tools.verse.verse_digests",
    "backend.tools.verse_editable": "backend.tools.verse.verse_editable",
    "backend.tools.verse_focused": "backend.tools.verse.verse_focused",
    "backend.tools.worldgen": "backend.tools.world.worldgen",
    "backend.uefn_editor_api_hints": "backend.tools.core.editor_api_hints",
}


class _LegacyLoader(importlib.abc.Loader):
    def create_module(self, spec: importlib.machinery.ModuleSpec) -> Any:
        return importlib.import_module(_ALIASES[spec.name])

    def exec_module(self, module: Any) -> None:
        return None


class _LegacyBackendFinder:
    """Meta path finder for renamed backend modules."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None):  # noqa: ARG002
        if fullname not in _ALIASES:
            return None
        # Never intercept names that already resolve as real packages/modules.
        # (Prevents recursion when an alias target lives under a same-named package.)
        if fullname in sys.modules:
            return None
        return importlib.util.spec_from_loader(fullname, _LegacyLoader())


def install() -> None:
    """Install the legacy finder once (idempotent)."""
    for finder in sys.meta_path:
        if isinstance(finder, _LegacyBackendFinder):
            return
    sys.meta_path.insert(0, _LegacyBackendFinder())


install()
