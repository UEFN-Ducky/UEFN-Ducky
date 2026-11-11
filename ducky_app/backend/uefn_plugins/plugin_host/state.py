"""The plugin host's shared registries: what loaded plugins contributed, their tools,
factories and callbacks, and the locks around them. Never rebound, only mutated, so every
host module imports them by name."""

from __future__ import annotations

import logging
import re
import sys
import threading
from contextvars import ContextVar
from typing import Any, Callable

_log = logging.getLogger("uefn_plugins")
_LOCK = threading.RLock()
# Single-flight first load: splash must not wait; agent paths wait on this Event.
_LOAD_DONE = threading.Event()
_LOAD_START_LOCK = threading.Lock()
_LOAD_CALLBACKS: list[Callable[[], None]] = []
_LOAD_CALLBACKS_LOCK = threading.Lock()
# Re-entrancy guard for _repair_missing_backends (per thread; _LOCK is an RLock).
_REPAIR_STATE = threading.local()
# Marks the load thread and every register() worker it spawns. Those threads must
# never wait on the load they are part of: unity-mcp register() → PanelSettings
# .save() → validate() → contributed_coding_agents() → get_contributions() →
# ensure_plugins_loaded() self-deadlocked, so _LOADED never flipped and the panel
# stayed stuck in "plugins loading" (no gateways, no models, dead Store toggles).
_LOAD_THREAD_STATE = threading.local()
_REPAIR_THREAD_LOCK = threading.Lock()
# plugin_id -> monotonic timestamp of last repair *attempt* (recorded before register).
_REPAIR_ATTEMPTS: dict[str, float] = {}
_REPAIR_BACKOFF_S = 60.0
# Rotation for uefn_plugin_load_errors.jsonl (see _record_plugin_load_error).
_LOAD_ERROR_LOG_MAX_BYTES = 256 * 1024
_LOAD_ERROR_LOG_KEEP = 200
# Deferred register_ide_hookup(auto_apply=True) work — never on the load thread.
_IDE_APPLY_PENDING: list[tuple[str, Callable[[str], None]]] = []
_IDE_APPLY_LOCK = threading.Lock()
# In-flight Store enable/disable background register()/unload() workers.
_TOGGLE_THREADS: list[threading.Thread] = []
_TOGGLE_THREADS_LOCK = threading.Lock()
_REGISTERED: set[str] = set()
_CONTRIBUTIONS: dict[str, Any] = {
    "settings_tabs": [],
    "settings_sections": [],
    "dock_panels": [],
    "editor_kinds": [],
    "header_buttons": [],
    "header_menus": [],  # {id, title?, icon?, ui: "panel:<id>", width?, order?, plugin_id}
    "ui_panels": [],
    "chat_references": [],  # {trigger, id, label, group, href, plugin_id}
    "shell_boots": [],  # {plugin_id, entry}
    "appearance_profiles": [],  # {id, name, plugin_id, foundation?, overrides?, status_overrides?}
    "appearance_css": [],  # {plugin_id, entry}
    "appearance_effects": [],  # {id, label, plugin_id, entry}
    "appearance_skins": [],  # {id, label, plugin_id, entry, css?}
    "sounds": [],  # {id, label, file, plugin_id}
    "hooks": [],  # {id, label, plugin_id}
    "automations_nodes": [],  # contributes.automations.nodes (+ plugin_id)
    "automations_triggers": [],  # contributes.automations.triggers (+ plugin_id)
    "automations_templates": [],  # contributes.automations.templates (+ plugin_id, graph)
    # Text to Image backends a plugin offers: {id, label, tool, credits, args?, prompt_arg?, plugin_id}
    "automations_image_generators": [],
    # Backends a plugin adds to the other image / 3D nodes (Text to 3D, Rig…): {node, id, label, tool, pins, …}
    "automations_media_backends": [],
    # New-file Verse scaffolds for the template picker (content inlined at load).
    "verse_templates": [],  # {id, name, icon, description?, content, order?, file?, plugin_id}
    "tts_voices": [],  # {id, label, plugin_id}
    # LLM gateway rows for Settings → LLMs (e.g. OpenAI / Ollama Store plugins).
    "llm_providers": [],  # {id, label, kind, secret_key?, default_url?, order?, plugin_id}
    # Coding agents contributed by gateway plugins (e.g. Codex via OpenAI plugin).
    "llm_coding_agents": [],  # {id, label, order?, plugin_id}
    # IDE MCP Apply targets from plugin.json (register() also fills _IDE_HOOKUPS).
    "ide_hookups": [],  # {kind, label, plugin_id}
    "agent_tools": {},  # plugin_id -> {category, tools, intent_pattern?}
    "walkthroughs": [],  # contributes.walkthrough rows (+ plugin_id)
    # Tabs in the Projects window: {id, label, icon?, rpc, plugin_id}; rpc lists the projects.
    "project_sources": [],
    # Live @ / / rows: {id, rpc, panel, label?, group?, trigger?, icon?, plugin_id}; rpc lists items.
    "reference_sources": [],
}
# plugin_id -> synthesize(text, voice_id) -> {ok?, audio_base64, mime}
_TTS_SYNTHESIZERS: dict[str, Any] = {}
# plugin_id -> list_voices() -> [{id, label}]  (dynamic voices fetched at runtime,
# e.g. an account's cloned voices — on top of the static manifest tts.voices).
_TTS_VOICE_LISTERS: dict[str, Any] = {}
# (kind, plugin_id) -> {fn, describe}; kind is one of MEDIA_KINDS (gateway media makers).
_MEDIA_GENERATORS: dict[tuple[str, str], dict[str, Any]] = {}
MEDIA_KINDS = ("image", "image_edit", "video", "music")
# provider_id -> {plugin_id, factory, fetch_models?, test_key_model?, test_key?}
_LLM_PROVIDER_FACTORIES: dict[str, dict[str, Any]] = {}
# coding_agent_id -> {plugin_id, factory, complete_one_shot?, test_key?, resolve_api_fallback?}
_CODING_AGENT_FACTORIES: dict[str, dict[str, Any]] = {}
# ide_kind (cursor/claude/antigravity) -> {plugin_id, label}
_IDE_HOOKUPS: dict[str, dict[str, Any]] = {}
# (plugin_id, compiled_pattern, tool_names)
_PLUGIN_INTENT_ROWS: list[tuple[str, re.Pattern[str], frozenset[str]]] = []
_PLUGIN_TOOL_NAMES: set[str] = set()
# Explicit Plan-mode allowlist from contributes.agent.tools.plan_tools.
_PLUGIN_PLAN_TOOLS: set[str] = set()
# Explicit destructive names from contributes.agent.tools.destructive_tools.
_PLUGIN_DESTRUCTIVE_TOOLS: set[str] = set()
# Survives reload (like _REGISTERED): tools registered via api.tool().
_API_TOOL_REGISTRY: dict[str, set[str]] = {}
_API_INTENT_PATTERNS: dict[str, str] = {}
# tool_name → plugin_id; survives reload so disable can hide orphaned MCP tools.
_PLUGIN_TOOL_OWNER: dict[str, str] = {}
# Every plugin tool this process has served, kept through unloads, and why each plugin's
# last load failed: a call to a tool whose plugin failed to reload says so (it used to
# fall through to the editor gate and read "UEFN listener offline").
_TOOL_LAST_OWNER: dict[str, str] = {}
_LOAD_FAILURES: dict[str, str] = {}
# Compiled backends executed in this process: module name -> (module, .pyd path). CPython
# never unloads an extension, so running one's init again is native UB (Oct 29 2026
# ACCESS_VIOLATION reloading the Blender .pyd). They stay loaded until Ducky restarts.
_COMPILED_LOADED: dict[str, tuple[Any, str]] = {}
# Plugins whose newer compiled version waits for a restart: pid -> message.
_RESTART_PENDING: dict[str, str] = {}
# api.tool(visible=fn): tools a plugin lists for agents only while fn() says so (what the
# tool reaches is not this account's to use right now). The plugin's own tool still answers a call.
_TOOL_VISIBLE: dict[str, Any] = {}
# api.tool(listener=False) — host-cache / non-editor tools (skip UEFN listener gate).
_PLUGIN_HOST_ONLY_TOOLS: set[str] = set()
# Plugins that touched api.data: their tool results name the scope ("space Alpha Studio").
_DATA_PLUGINS: set[str] = set()
# How many of one plugin's tool calls run at once (plugin.json "tool_concurrency"). Agents
# sharing a tool server call the same plugin from many threads; a plugin that holds one
# connection (Blender's socket, one SQLite handle, a rate-limited API) declares 1.
DEFAULT_TOOL_CONCURRENCY = 8
TOOL_SLOT_WAIT_S = 120.0  # a Blender render or a UEFN import can hold a slot for a minute
_TOOL_CONCURRENCY: dict[str, int] = {}
_TOOL_SLOTS: dict[str, threading.BoundedSemaphore] = {}
_HELD_SLOTS = threading.local()  # plugin ids this thread already holds a slot for
# Plugins that keep declared folders (team_folders): the reload epoch each was loaded at
# in this process. Their folders are junctions any process can re-point (a switch, a
# pull, a project change); a moved epoch means this process reloads the plugin before
# its next tool call, and the app restarts it (reload_moved_plugins).
_FOLDER_EPOCH: dict[str, int] = {}
_FOLDER_MOVED: dict[str, tuple[int, float]] = {}  # app: epoch seen moved, and since when
_FOLDER_RELOAD_LOCKS: dict[str, threading.Lock] = {}
_FOLDER_RELOAD_GUARD = threading.Lock()
FOLDER_RELOAD_GRACE_S = 3.0  # an explicit restart (a switch) gets this long to start first
# plugin id → its plugin.json mtime when this process registered it. The agents' tool server
# reloads a plugin whose files another process installed since (reload_plugins_changed_on_disk).
_LOADED_SIGNATURE: dict[str, int] = {}
PLUGIN_FILES_POLL_S = 3.0
# secret_key → {plugin_id, test_fn} — Settings "Test" for plugin API keys (e.g. Meshy).
_SECRET_TESTERS: dict[str, dict[str, Any]] = {}
# plugin_id → {method_name → callable} — PanelApi.plugin_call / bridge plugin.call
_PANEL_RPC: dict[str, dict[str, Any]] = {}
# plugin_id → {label, program, fn} — Connections menu + revert preflight
_CONNECTION_PROBES: dict[str, dict[str, Any]] = {}
# Last probe row per plugin. Header status polls every 8s — do not re-hit
# unused MCP sockets on every tick.
_CONNECTION_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_CONNECTION_CACHE_TTL_S = 30.0
# plugin_id → {label, fn} — the header Online tab: who a plugin knows is around (api.people)
_PEOPLE_PROVIDERS: dict[str, dict[str, Any]] = {}
_PEOPLE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}

# Per-run allowlist for UEFN app-plugin tools.
# None = follow Store enable; [] = none; non-empty = those plugin ids only.
_ACTIVE_UEFN_AGENT_IDS: ContextVar[tuple[str, ...] | None] = ContextVar(
    "uefn_ducky_active_uefn_agent_plugins", default=None
)

# ABI of this interpreter. A compiled plugin built for another ABI is not imported.
PY_ABI = f"cp{sys.version_info.major}{sys.version_info.minor}-win_amd64"
# Where a compiled .pyd lands while its old copy is still mapped in-process (Windows
# never unloads an extension, so the file stays locked until Ducky exits). Moving the
# old file here lets the Store overwrite the plugin folder; swept on next launch.
_COMPILED_QUARANTINE = ".compiled_quarantine"
