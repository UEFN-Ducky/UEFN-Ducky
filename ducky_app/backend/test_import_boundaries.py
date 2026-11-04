"""No new imports of another package's private (``_name``) names.

skills/store.py and uefn_plugins/store.py imported each other's private caches, and the
skill deploy loop came in through that seam. A package's ``_name`` is free to change, so
code outside that package (``backend.<pkg>`` / ``frontend.<pkg>``) must go through a
public name instead.

BASELINE lists the crossings that exist today, so this test passes now and fails on any
new one. An entry that no longer exists fails too: delete it, the list only shrinks.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1]  # ducky_app/
_ROOTS = ("backend", "frontend")
_SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "venv", "build", "dist"}

# "importer path (relative to ducky_app) -> module._name", one per crossing found today.
BASELINE: set[str] = {
    'backend/agent/attachments.py -> frontend.ui_web.context_tokens._coding_agent_context_limit',
    'backend/agent/coding_agents/mcp_inject.py -> frontend.skill_deploy._is_managed_skill_dir',
    'backend/agent/coding_agents/plan_hooks.py -> frontend.ui_web.agent_modes._resolve_push',
    'backend/agent/coding_agents/plans.py -> frontend.ui_web.agent_modes._resolve_push',
    'backend/agent/coding_agents/plans.py -> frontend.ui_web.project_chats._conversation_lock',
    'backend/agent/coding_agents/test_mcp_inject.py -> backend.bridge.shared_mcp._snapshot_tools',
    'backend/agent/coding_agents/test_mcp_inject.py -> backend.bridge.shared_mcp._tool_row',
    'backend/agent/coding_agents/test_plans.py -> backend.tools.panel.ducky_panel._resolve_plan_chat_id',
    'backend/agent/coding_agents/tick_gate.py -> backend.uefn_plugins.plugin_lint._Backend',
    'backend/agent/context_memory.py -> frontend.ui_web.plugin_llm._complete_text',
    'backend/agent/context_memory.py -> frontend.ui_web.plugin_llm._resolve_api_model',
    'backend/agent/test_context_trim.py -> frontend.ui_web.context_tokens._conversation_report',
    'backend/agent/test_prompt_cache_epoch.py -> backend.tools.panel.panel_ai_plugins._load_plugin_reference',
    'backend/agent/test_prompt_cache_live.py -> frontend.ui_web.plugin_llm._complete_text',
    'backend/agent/test_prompt_cache_live.py -> frontend.ui_web.plugin_llm._resolve_api_model',
    'backend/agent/test_video_attachments.py -> frontend.ui_web.conversation_attachments._CHAT_FILE_RE',
    'backend/agent/test_video_attachments.py -> frontend.ui_web.panel_httpd._CHAT_ATTACHMENT_RE',
    'backend/agent/tools.py -> frontend.duckyos_account._panel_cap_settings',
    'backend/automations/play.py -> backend.bridge.status._project_from_health',
    'backend/automations/play.py -> frontend.window_view._uefn_running',
    'backend/automations/runner.py -> backend.tools.panel.ducky_panel._panel_api',
    'backend/automations/runner.py -> backend.tools.panel.ducky_panel._profile_spawn_kwargs',
    'backend/automations/runner.py -> backend.tools.panel.ducky_panel_terminal._terminal_manager',
    'backend/automations/runner.py -> frontend.ui_web.panel_api._first_available_api_model',
    'backend/automations/test_automations.py -> frontend.ui_web.project_chats._repo',
    'backend/automations/test_automations.py -> frontend.ui_web.project_chats._use_db',
    'backend/automations/test_daily_check.py -> backend.uefn_plugins.host._automation_template_row',
    'backend/automations/test_workflow_owners.py -> backend.uefn_plugins.test_scopes._Who',
    'backend/automations/test_workflow_shared_folders.py -> backend.uefn_plugins.test_scopes._Who',
    'backend/bridge/revive.py -> backend.mcp_plugins.epic._listening_process',
    'backend/bridge/test_plugin_gate.py -> frontend.ui_web.agent_modes._cap_ui_result',
    'backend/memory/test_project.py -> backend.tools.panel.ducky_panel._resolve_project_root_arg',
    'backend/sandbox/fetch.py -> backend.tools.core.web_lookup._blocked_ip',
    'backend/sandbox/gitops.py -> backend.bridge.client._recent_project_roots',
    'backend/sandbox/service.py -> backend.tools.panel.permission_prompt._chat_exists',
    'backend/sandbox/service.py -> frontend.ui_web.terminal.manager._chat_title',
    'backend/sandbox/test_sandbox.py -> backend.workspace.writer._in_own_sandbox',
    'backend/search/find.py -> backend.uefn_plugins.host._PLUGIN_TOOL_OWNER',
    'backend/search/find.py -> backend.uefn_plugins.host._plugin_label',
    'backend/search/hints.py -> backend.automations.runner._LIVE',
    'backend/search/indexer.py -> backend.skills.store._pack_stamp',
    'backend/search/indexer.py -> frontend.ui_web.mcp_catalog._tool_category_map',
    'backend/search/sealed_records.py -> backend.store.repos.search._STOP',
    'backend/search/test_chunker.py -> backend.skills.store._manifest_from_skill_dir',
    'backend/server.py -> backend.bridge.shared_mcp._catalog_marker',
    'backend/server.py -> backend.bridge.shared_mcp._catalog_revision',
    'backend/server.py -> backend.bridge.shared_mcp._policy_filtered',
    'backend/server.py -> backend.bridge.shared_mcp._snapshot_tools',
    'backend/server.py -> backend.bridge.shared_mcp._tool_row',
    'backend/skills/store.py -> backend.search.chunker._FENCE',
    'backend/skills/store.py -> backend.search.chunker._HEADING',
    'backend/skills/test_search_reads.py -> backend.tools.verse.skill_tool._lean',
    'backend/store/importers/phase4.py -> backend.agent.coding_agents.plans._normalize_plan_doc',
    'backend/store/importers/phase4.py -> backend.memory.project._default_description',
    'backend/store/importers/phase4.py -> backend.memory.project._parse_entry',
    'backend/tools/core/test_uefn_popups.py -> frontend.ui_web.verse_editor.workflow.client._presave_dirty_packages',
    'backend/tools/core/test_web_lookup.py -> backend.agent.prompt._rules_body',
    'backend/tools/core/test_web_lookup.py -> frontend.ui_web.project_chats._keep_newer_settings',
    'backend/tools/core/uefn_windows.py -> frontend.window_view._window_box',
    'backend/tools/core/workspace_code.py -> backend.agent.coding_agents.proc_exec._kill_windows_tree',
    'backend/tools/integrations/translation_service.py -> frontend.ui_web.plugin_llm._complete_text',
    'backend/tools/integrations/translation_service.py -> frontend.ui_web.plugin_llm._resolve_api_model',
    'backend/tools/panel/ducky_panel.py -> backend.agent.a2a_broker._show_in_group_chat',
    'backend/tools/panel/ducky_panel.py -> backend.agent.builtin_toolsets._ACTIVE_BUILTIN_GROUPS',
    'backend/tools/panel/ducky_panel.py -> backend.agent.coding_agents.plans._live_plan',
    'backend/tools/panel/ducky_panel.py -> backend.bridge.status._normalize_project_path',
    'backend/tools/panel/ducky_panel.py -> backend.bridge.status._same_or_nested',
    'backend/tools/panel/ducky_panel.py -> backend.mcp_plugins.store._ACTIVE_PLUGIN_IDS',
    'backend/tools/panel/ducky_panel.py -> backend.uefn_plugins.host._ACTIVE_UEFN_AGENT_IDS',
    'backend/tools/panel/ducky_panel.py -> frontend.ui_web.team_keeper._staffed_leaders',
    'backend/tools/panel/ducky_panel_terminal.py -> backend.automations.runner._REGISTRY_RUNS',
    'backend/tools/panel/panel_ai_plugins.py -> backend.uefn_plugins.host._PluginApi',
    'backend/tools/panel/panel_automations.py -> backend.automations.runner._signature',
    'backend/tools/panel/panel_automations.py -> frontend.ui_web.group_orchestrator._hub_chain',
    'backend/tools/panel/panel_profiles.py -> backend.agent.coding_agents.plans._chat_and_group_ids',
    'backend/tools/panel/swarm_build.py -> backend.workspace.lanes._is_abs',
    'backend/tools/panel/test_ducky_panel_plans_project.py -> backend.agent.coding_agents.conftest._meta_reads_follow_faked_chats',
    'backend/tools/panel/test_ducky_panel_terminal_release.py -> backend.automations.runner._REGISTRY_RUNS',
    'backend/tools/panel/test_panel_ui_show.py -> backend.uefn_plugins.host._PluginApi',
    'backend/tools/panel/test_permission_prompt.py -> frontend.ui_web.agent_modes._note_run_starter',
    'backend/tools/panel/test_tool_search_ranking.py -> backend.bridge.shared_mcp._refused',
    'backend/tools/panel/test_tool_search_ranking.py -> backend.uefn_plugins.host._ACTIVE_UEFN_AGENT_IDS',
    'backend/tools/tester/session_play.py -> backend.testing.live_graph._call_epic_tool',
    'backend/tools/uefn/test_agent_hardening.py -> backend.agent.prompt._rules_body',
    'backend/tools/verse/template_verify.py -> frontend.ui_web.project_files._project_root',
    'backend/tools/world/test_blockout_areas_tools.py -> backend.agent.toolsets.intents._LEVEL_DESIGN_TOOLS',
    'backend/tools/world/test_worldgen_tools.py -> backend.agent.toolsets.intents._WORLDGEN_TOOLS',
    'backend/uefn_plugins/asset_packs.py -> frontend.duckyos_account._store_collect',
    'backend/uefn_plugins/data_crypto.py -> frontend.duckyos_account._load_blob',
    'backend/uefn_plugins/data_crypto.py -> frontend.duckyos_account._plugin_collect',
    'backend/uefn_plugins/host.py -> backend.automations.catalog._fields',
    'backend/uefn_plugins/test_ide_hookup.py -> frontend.ide_apply._active_ide_kinds',
    'backend/uefn_plugins/test_plugin_lint.py -> backend.tools.panel.panel_ai_plugins._minimal_manifest',
    'backend/uefn_plugins/test_plugin_lint.py -> backend.tools.panel.panel_ai_plugins._register_stub',
    'backend/uefn_plugins/test_plugin_lint.py -> backend.tools.panel.panel_ai_plugins._skill_stub',
    'backend/uefn_plugins/test_uefn_plugins.py -> backend.tools.panel.panel_ai_plugins._resolve_jailed',
    'backend/uefn_plugins/test_uefn_plugins.py -> frontend.ui_web.plugin_host_api._tts_voices_work',
    'backend/uefn_plugins/test_uefn_plugins.py -> frontend.ui_web.plugin_host_api._tts_work',
    'backend/voice/summary.py -> frontend.ui_web.plugin_llm._complete_text',
    'backend/voice/summary.py -> frontend.ui_web.plugin_llm._resolve_api_model',
    'backend/workspace/ai_ignore.py -> backend.bridge.client._recent_project_roots',
    'backend/workspace/lanes.py -> backend.bridge.client._recent_project_roots',
    'backend/workspace/runtime.py -> backend.bridge.client._recent_project_roots',
    'backend/workspace/writer.py -> frontend.ui_web.project_files._invalidate_file_paths_cache',
    'frontend/chat_store.py -> frontend.ui_web.project_chats._load_all_conversations',
    'frontend/ducky_link.py -> frontend.duckyos_account._load_blob',
    'frontend/ducky_link.py -> frontend.duckyos_account._plugin_collect',
    'frontend/duckyos_account.py -> frontend.ui_web.panel_api._save_panel_settings',
    'frontend/feedback_submit.py -> frontend.support_dump._redact',
    'frontend/perf_trace.py -> frontend.self_health._cmdline',
    'frontend/perf_trace.py -> frontend.self_health._role',
    'frontend/runtime_smoke.py -> frontend.ui_web.webview_app._web_root',
    'frontend/self_health.py -> backend.tools.panel.permission_prompt._chat_exists',
    'frontend/test_old_chats_pick_up_rules.py -> backend.agent.prompt_cache._drift_lines',
    'frontend/test_project_kind.py -> backend.agent.coding_agents.mcp_inject._folder_project_note',
    'frontend/test_project_kind.py -> backend.bridge.status._project_from_health',
    'frontend/test_starter_llm_gateways.py -> frontend.ui_web.panel_api._messages_to_ui',
    'frontend/test_update_recovery.py -> frontend.ui_web.webview_app._web_root',
    'frontend/ui_web/agent_modes.py -> backend.agent.a2a_broker._retry_queue_later',
    'frontend/ui_web/agent_modes.py -> backend.agent.coding_agents.runner._TurnCheckpoint',
    'frontend/ui_web/agent_modes.py -> backend.agent.providers._TRANSIENT_RE',
    'frontend/ui_web/agent_modes.py -> backend.automations.runner._LIVE',
    'frontend/ui_web/context_limit.py -> backend.tools.panel.ducky_panel._SUBAGENT_HANDOFF_PROMPT',
    'frontend/ui_web/context_tokens.py -> backend.agent.prompt_cache._blocks_from_parts',
    'frontend/ui_web/mcp_catalog.py -> backend.agent.tools._ensure_mcp',
    'frontend/ui_web/panel_api.py -> backend.agent.model_fetch._cache_provider_models',
    'frontend/ui_web/panel_api.py -> frontend.favorite_models._available_api_models',
    'frontend/ui_web/panel_api_plan_progress.py -> backend.agent.coding_agents.plans._flatten_nodes',
    'frontend/ui_web/panel_api_settings.py -> backend.uefn_plugins.host._import_backend',
    'frontend/ui_web/panel_api_swarm.py -> backend.tools.panel.swarm_build._loads',
    'frontend/ui_web/team_keeper.py -> backend.agent.a2a_broker._last_assistant_text',
    'frontend/ui_web/team_keeper.py -> backend.agent.coding_agents.plans._chat_and_group_ids',
    'frontend/ui_web/terminal/session.py -> backend.sandbox.jobs._Job',
    'frontend/ui_web/test_checkpoint_coding_turn.py -> backend.agent.coding_agents.runner._CHECKPOINT_EVERY_S',
    'frontend/ui_web/test_checkpoint_coding_turn.py -> backend.agent.coding_agents.runner._TurnCheckpoint',
    'frontend/ui_web/test_models_cache_disk.py -> backend.agent.model_fetch._CAPABILITY_CACHE',
    'frontend/ui_web/test_steer.py -> backend.server._with_steer',
    'frontend/ui_web/test_workflow_tool_catalog.py -> backend.agent.tools._ensure_mcp',
    'frontend/ui_web/test_workspace_search_tokens.py -> backend.store.repos.chats._fts_query',
    'frontend/ui_web/window_spotlight.py -> frontend.window_view._window_box',
    'frontend/usage_stats.py -> backend.tools.core.uefn_modal._enum_uefn_windows',
    'frontend/usage_stats.py -> frontend.duckyos_account._store_collect',
    'frontend/window_view.py -> backend.bridge.status._project_from_health',
    'frontend/window_view.py -> backend.tools.core.uefn_modal._enum_uefn_windows',
}


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
    """{"importer -> module._name": "importer:line"} for every cross-package private import."""
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
                        key = f"{rel.as_posix()} -> {module}.{alias.name}"
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
        "backend/skills/store.py -> backend.uefn_plugins.store._cache": "backend/skills/store.py:3",
        "backend/uefn_plugins/store.py -> backend.skills._hits": "backend/uefn_plugins/store.py:1",
    }
