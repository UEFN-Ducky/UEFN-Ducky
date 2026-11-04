"""No new imports of another package's private (``_name``) names.

skills/store.py and uefn_plugins/store.py imported each other's private caches, and the
skill deploy loop came in through that seam. A package's ``_name`` is free to change, so
code outside that package (``backend.<pkg>`` / ``frontend.<pkg>``) must go through a
public name instead.

BASELINE lists the crossings that exist today, so this test passes now and fails on any
new one. An entry that no longer exists fails too: delete it, the list only shrinks.

Entries are keyed by the importer's package (``backend/<pkg>``, ``frontend/<pkg>``; a file
right under ``backend/`` or ``frontend/`` is its own package), not its file: splitting a
file moves its crossings to new files in the same package, and that is no new crossing.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1]  # ducky_app/
_ROOTS = ("backend", "frontend")
_SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "venv", "build", "dist"}

# "importer package -> module._name", one per crossing found today.
BASELINE: set[str] = {
    'backend/agent -> backend.bridge.shared_mcp._snapshot_tools',
    'backend/agent -> backend.bridge.shared_mcp._tool_row',
    'backend/agent -> backend.tools.panel.ducky_panel._resolve_plan_chat_id',
    'backend/agent -> backend.tools.panel.panel_ai_plugins._load_plugin_reference',
    'backend/agent -> backend.uefn_plugins.plugin_lint._Backend',
    'backend/agent -> frontend.duckyos_account._panel_cap_settings',
    'backend/agent -> frontend.skill_deploy._is_managed_skill_dir',
    'backend/agent -> frontend.ui_web.agent_turns.push._resolve_push',
    'backend/agent -> frontend.ui_web.chats.store._conversation_lock',
    'backend/agent -> frontend.ui_web.context_tokens._coding_agent_context_limit',
    'backend/agent -> frontend.ui_web.context_tokens._conversation_report',
    'backend/agent -> frontend.ui_web.conversation_attachments._CHAT_FILE_RE',
    'backend/agent -> frontend.ui_web.panel_httpd._CHAT_ATTACHMENT_RE',
    'backend/agent -> frontend.ui_web.plugin_llm._complete_text',
    'backend/agent -> frontend.ui_web.plugin_llm._resolve_api_model',
    'backend/automations -> backend.bridge.status._project_from_health',
    'backend/automations -> backend.tools.panel.ducky_panel._panel_api',
    'backend/automations -> backend.tools.panel.ducky_panel._profile_spawn_kwargs',
    'backend/automations -> backend.tools.panel.ducky_panel_terminal._terminal_manager',
    'backend/automations -> backend.uefn_plugins.plugin_host.contributions._automation_template_row',
    'backend/automations -> backend.uefn_plugins.test_scopes._Who',
    'backend/automations -> frontend.ui_web.panel_api._first_available_api_model',
    'backend/automations -> frontend.ui_web.chats.store._repo',
    'backend/automations -> frontend.ui_web.chats.store._use_db',
    'backend/automations -> frontend.window_view._uefn_running',
    'backend/bridge -> backend.mcp_plugins.epic._listening_process',
    'backend/bridge -> frontend.ui_web.agent_turns.run._cap_ui_result',
    'backend/memory -> backend.tools.panel.ducky_panel._resolve_project_root_arg',
    'backend/sandbox -> backend.bridge.client._recent_project_roots',
    'backend/sandbox -> backend.tools.core.web_lookup._blocked_ip',
    'backend/sandbox -> backend.tools.panel.permission_prompt._chat_exists',
    'backend/sandbox -> backend.workspace.writer._in_own_sandbox',
    'backend/sandbox -> frontend.ui_web.terminal.manager._chat_title',
    'backend/search -> backend.automations.runner._LIVE',
    'backend/search -> backend.skills.manifest._manifest_from_skill_dir',
    'backend/search -> backend.skills.manifest._pack_stamp',
    'backend/search -> backend.store.repos.search._STOP',
    'backend/search -> backend.uefn_plugins.plugin_host.errors._plugin_label',
    'backend/search -> backend.uefn_plugins.plugin_host.state._PLUGIN_TOOL_OWNER',
    'backend/search -> frontend.ui_web.mcp_catalog._tool_category_map',
    'backend/server -> backend.bridge.shared_mcp._catalog_marker',
    'backend/server -> backend.bridge.shared_mcp._catalog_revision',
    'backend/server -> backend.bridge.shared_mcp._policy_filtered',
    'backend/server -> backend.bridge.shared_mcp._snapshot_tools',
    'backend/server -> backend.bridge.shared_mcp._tool_row',
    'backend/skills -> backend.search.chunker._FENCE',
    'backend/skills -> backend.search.chunker._HEADING',
    'backend/skills -> backend.tools.verse.skill_tool._lean',
    'backend/store -> backend.agent.coding_agents.plans._normalize_plan_doc',
    'backend/store -> backend.memory.project._default_description',
    'backend/store -> backend.memory.project._parse_entry',
    'backend/tools -> backend.agent.a2a_broker._show_in_group_chat',
    'backend/tools -> backend.agent.builtin_toolsets._ACTIVE_BUILTIN_GROUPS',
    'backend/tools -> backend.agent.coding_agents.conftest._meta_reads_follow_faked_chats',
    'backend/tools -> backend.agent.coding_agents.plans._chat_and_group_ids',
    'backend/tools -> backend.agent.coding_agents.plans._live_plan',
    'backend/tools -> backend.agent.coding_agents.proc_exec._kill_windows_tree',
    'backend/tools -> backend.agent.prompt._rules_body',
    'backend/tools -> backend.agent.toolsets.intents._LEVEL_DESIGN_TOOLS',
    'backend/tools -> backend.agent.toolsets.intents._WORLDGEN_TOOLS',
    'backend/tools -> backend.automations.runner._REGISTRY_RUNS',
    'backend/tools -> backend.automations.runner._signature',
    'backend/tools -> backend.bridge.shared_mcp._refused',
    'backend/tools -> backend.bridge.status._normalize_project_path',
    'backend/tools -> backend.bridge.status._same_or_nested',
    'backend/tools -> backend.mcp_plugins.store._ACTIVE_PLUGIN_IDS',
    'backend/tools -> backend.testing.live_graph._call_epic_tool',
    'backend/tools -> backend.uefn_plugins.plugin_host.api._PluginApi',
    'backend/tools -> backend.uefn_plugins.plugin_host.state._ACTIVE_UEFN_AGENT_IDS',
    'backend/tools -> backend.workspace.lanes._is_abs',
    'backend/tools -> frontend.ui_web.agent_turns.run._note_run_starter',
    'backend/tools -> frontend.ui_web.group_orchestrator._hub_chain',
    'backend/tools -> frontend.ui_web.plugin_llm._complete_text',
    'backend/tools -> frontend.ui_web.plugin_llm._resolve_api_model',
    'backend/tools -> frontend.ui_web.chats.conversations._keep_newer_settings',
    'backend/tools -> frontend.ui_web.project_files._project_root',
    'backend/tools -> frontend.ui_web.team_keeper._staffed_leaders',
    'backend/tools -> frontend.ui_web.verse_editor.workflow.client._presave_dirty_packages',
    'backend/tools -> frontend.window_view._window_box',
    'backend/uefn_plugins -> backend.automations.catalog._fields',
    'backend/uefn_plugins -> backend.tools.panel.panel_ai_plugins._minimal_manifest',
    'backend/uefn_plugins -> backend.tools.panel.panel_ai_plugins._register_stub',
    'backend/uefn_plugins -> backend.tools.panel.panel_ai_plugins._resolve_jailed',
    'backend/uefn_plugins -> backend.tools.panel.panel_ai_plugins._skill_stub',
    'backend/uefn_plugins -> frontend.duckyos_account._load_blob',
    'backend/uefn_plugins -> frontend.duckyos_account._plugin_collect',
    'backend/uefn_plugins -> frontend.duckyos_account._store_collect',
    'backend/uefn_plugins -> frontend.ide_apply._active_ide_kinds',
    'backend/uefn_plugins -> frontend.ui_web.plugin_host_api._tts_voices_work',
    'backend/uefn_plugins -> frontend.ui_web.plugin_host_api._tts_work',
    'backend/voice -> frontend.ui_web.plugin_llm._complete_text',
    'backend/voice -> frontend.ui_web.plugin_llm._resolve_api_model',
    'backend/workspace -> backend.bridge.client._recent_project_roots',
    'backend/workspace -> frontend.ui_web.project_files._invalidate_file_paths_cache',
    'frontend/chat_store -> frontend.ui_web.chats.conversations._load_all_conversations',
    'frontend/ducky_link -> frontend.duckyos_account._load_blob',
    'frontend/ducky_link -> frontend.duckyos_account._plugin_collect',
    'frontend/duckyos_account -> frontend.ui_web.panel_api._save_panel_settings',
    'frontend/feedback_submit -> frontend.support_dump._redact',
    'frontend/perf_trace -> frontend.self_health._cmdline',
    'frontend/perf_trace -> frontend.self_health._role',
    'frontend/runtime_smoke -> frontend.ui_web.webview_app._web_root',
    'frontend/self_health -> backend.tools.panel.permission_prompt._chat_exists',
    'frontend/test_old_chats_pick_up_rules -> backend.agent.prompt_cache._drift_lines',
    'frontend/test_project_kind -> backend.agent.coding_agents.mcp_inject._folder_project_note',
    'frontend/test_project_kind -> backend.bridge.status._project_from_health',
    'frontend/test_starter_llm_gateways -> frontend.ui_web.panel_api._messages_to_ui',
    'frontend/test_update_recovery -> frontend.ui_web.webview_app._web_root',
    'frontend/ui_web -> backend.agent.a2a_broker._retry_queue_later',
    'frontend/ui_web -> backend.agent.coding_agents.plans._chat_and_group_ids',
    'frontend/ui_web -> backend.agent.coding_agents.plans._flatten_nodes',
    'frontend/ui_web -> backend.agent.coding_agents.runner._CHECKPOINT_EVERY_S',
    'frontend/ui_web -> backend.agent.coding_agents.runner._TurnCheckpoint',
    'frontend/ui_web -> backend.agent.model_fetch._CAPABILITY_CACHE',
    'frontend/ui_web -> backend.agent.model_fetch._cache_provider_models',
    'frontend/ui_web -> backend.agent.prompt_cache._blocks_from_parts',
    'frontend/ui_web -> backend.agent.providers._TRANSIENT_RE',
    'frontend/ui_web -> backend.agent.tools._ensure_mcp',
    'frontend/ui_web -> backend.automations.runner._LIVE',
    'frontend/ui_web -> backend.sandbox.jobs._Job',
    'frontend/ui_web -> backend.server._with_steer',
    'frontend/ui_web -> backend.store.repos.chats._fts_query',
    'frontend/ui_web -> backend.tools.panel.ducky_panel._SUBAGENT_HANDOFF_PROMPT',
    'frontend/ui_web -> backend.tools.panel.swarm_build._loads',
    'frontend/ui_web -> backend.uefn_plugins.plugin_host.backends._import_backend',
    'frontend/ui_web -> frontend.favorite_models._available_api_models',
    'frontend/ui_web -> frontend.window_view._window_box',
    'frontend/usage_stats -> backend.tools.core.uefn_modal._enum_uefn_windows',
    'frontend/usage_stats -> frontend.duckyos_account._store_collect',
    'frontend/window_view -> backend.bridge.status._project_from_health',
    'frontend/window_view -> backend.tools.core.uefn_modal._enum_uefn_windows',
}


def importer_package(rel: Path) -> str:
    """``backend/skills/skill_index.py`` -> ``backend/skills``; ``frontend/chat_store.py`` ->
    ``frontend/chat_store``: the unit a move inside keeps the same."""
    parts = rel.with_suffix("").parts
    return "/".join(parts[:2])


def _module_parts(rel: Path) -> list[str]:
    parts = list(rel.with_suffix("").parts)
    return parts[:-1] if parts[-1] == "__init__" else parts


def _resolve(node: ast.ImportFrom, rel: Path) -> str:
    """The absolute module an ``from X import ...`` names, relative imports resolved."""
    if not node.level:
        return node.module or ""
    here = list(rel.parent.parts)  # the importer's package
    base = here[: len(here) - (node.level - 1)] if node.level > 1 else here
    return ".".join([*base, *(node.module.split(".") if node.module else [])])


def _owner(module: str) -> tuple[str, ...]:
    """The package a module belongs to: ``backend.<pkg>`` / ``frontend.<pkg>``."""
    parts = module.split(".")
    return tuple(parts[:2]) if len(parts) >= 2 else tuple(parts[:1])


def _is_private(name: str) -> bool:
    return name.startswith("_") and not (name.startswith("__") and name.endswith("__"))


def _is_test(rel: Path) -> bool:
    return rel.name.startswith("test_") or rel.name == "conftest.py"


def scan(app: Path = APP) -> dict[str, str]:
    """{"importer package -> module._name": "importer:line"} for every cross-package private
    import (the first file found, for the message)."""
    hits: dict[str, str] = {}
    for root in _ROOTS:
        for path in sorted((app / root).rglob("*.py")):
            rel = path.relative_to(app)
            if _SKIP_DIRS.intersection(rel.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            except (SyntaxError, UnicodeDecodeError):
                continue
            importer = tuple(_module_parts(rel))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                module = _resolve(node, rel)
                if module.split(".")[0] not in _ROOTS:
                    continue
                owner = _owner(module)
                if importer[: len(owner)] == owner:
                    continue  # inside the package (its tests too)
                if _is_test(rel) and tuple(module.split(".")[:-1]) == importer[:-1]:
                    continue  # a test of the module next to it
                for alias in node.names:
                    if _is_private(alias.name):
                        key = f"{importer_package(rel)} -> {module}.{alias.name}"
                        hits.setdefault(key, f"{rel.as_posix()}:{node.lineno}")
    return hits


def test_no_new_cross_package_private_imports():
    hits = scan()
    new = sorted(k for k in hits if k not in BASELINE)
    assert not new, (
        "New import of another package's private name; add a public name to that package "
        "and import that instead:\n" + "\n".join(f"  {hits[k]}  {k}" for k in new)
    )


def test_baseline_has_no_stale_entries():
    stale = sorted(BASELINE - set(scan()))
    assert not stale, "These crossings are gone; delete them from BASELINE:\n" + "\n".join(
        f"  {k!r}," for k in stale)


def test_a_planted_private_import_is_caught(tmp_path):
    (tmp_path / "backend" / "skills").mkdir(parents=True)
    (tmp_path / "backend" / "uefn_plugins").mkdir(parents=True)
    (tmp_path / "backend" / "skills" / "store.py").write_text(
        "from backend.skills.cache import _ok\n"  # own package: fine
        "from .cache import _also_ok\n"
        "from backend.uefn_plugins.store import _cache, public, __all__\n")
    (tmp_path / "backend" / "uefn_plugins" / "store.py").write_text("from ..skills import _hits\n")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "test_temp_cleanup.py").write_text(
        "from frontend.temp_cleanup import _dir_in_use\n")  # a test of the module next to it: fine
    assert scan(tmp_path) == {
        "backend/skills -> backend.uefn_plugins.store._cache": "backend/skills/store.py:3",
        "backend/uefn_plugins -> backend.skills._hits": "backend/uefn_plugins/store.py:1",
    }


def test_a_crossing_moved_to_another_file_of_its_package_keeps_its_key(tmp_path):
    # skills/store.py split into skills/skill_index.py: same crossing, same key.
    (tmp_path / "backend" / "skills").mkdir(parents=True)
    (tmp_path / "backend" / "skills" / "store.py").write_text("from backend.search.chunker import _FENCE\n")
    before = set(scan(tmp_path))
    (tmp_path / "backend" / "skills" / "store.py").write_text("")
    (tmp_path / "backend" / "skills" / "skill_index.py").write_text("from backend.search.chunker import _FENCE\n")
    assert set(scan(tmp_path)) == before == {"backend/skills -> backend.search.chunker._FENCE"}
    # The same import from another package is a new crossing.
    (tmp_path / "backend" / "voice").mkdir()
    (tmp_path / "backend" / "voice" / "x.py").write_text("from backend.search.chunker import _FENCE\n")
    assert "backend/voice -> backend.search.chunker._FENCE" in scan(tmp_path)
