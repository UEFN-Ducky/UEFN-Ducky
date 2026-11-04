"""Load enabled UEFN plugins: backend register() + UI contribution registry."""

from __future__ import annotations

import base64
import contextlib
import functools
import importlib.util
import json
import logging
import os
import re
import sys
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from backend.process_role import is_app
from backend.uefn_plugins.plugin_version import format_plugin_version, plugin_version_rank
from backend.uefn_plugins.store import (
    PLUGIN_MANIFEST,
    appdata_uefn_plugins_dir,
    get_enabled_plugin_ids,
)

# ponytail: inline plugin icon assets as data URLs for settings/header; 256KB ceiling.
_ICON_ASSET_MAX_BYTES = 256 * 1024
_ICON_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

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
_IDE_APPLY_THREAD: threading.Thread | None = None
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


def _merged_ide_hookups(enabled_set: set[str]) -> list[dict[str, Any]]:
    """plugin.json ide.hookups + register() bindings (register wins on kind clash)."""
    by_kind: dict[str, dict[str, Any]] = {}
    for row in _CONTRIBUTIONS.get("ide_hookups") or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "").strip().lower().replace("-", "_")
        pid = str(row.get("plugin_id") or "")
        if not kind or pid not in enabled_set:
            continue
        by_kind[kind] = {
            "kind": kind,
            "plugin_id": pid,
            "label": str(row.get("label") or kind).strip() or kind,
        }
    for kind, row in _IDE_HOOKUPS.items():
        pid = str(row.get("plugin_id") or "")
        if pid not in enabled_set:
            continue
        by_kind[kind] = {
            "kind": kind,
            "plugin_id": pid,
            "label": str(row.get("label") or kind).strip() or kind,
        }
    return sorted(by_kind.values(), key=lambda p: str(p.get("kind") or ""))


def normalize_chat_reference(row: Mapping[str, Any], plugin_id: str) -> dict[str, Any] | None:
    """Drop a composer reference whose href is not one of the tab schemes."""
    if not isinstance(row, Mapping):
        return None
    trigger = str(row.get("trigger") or "").strip()
    if trigger not in ("@", "/"):
        return None
    ref_id = str(row.get("id") or "").strip()
    label = str(row.get("label") or "").strip()
    href = str(row.get("href") or "").strip()
    if not ref_id or not label or not re.fullmatch(
        r"(ducky|profile|skill|subskill|mcp|plugin):[A-Za-z0-9_./:-]+", href
    ):
        return None
    out: dict[str, Any] = {
        "trigger": trigger,
        "id": ref_id,
        "label": label,
        "group": str(row.get("group") or "Plugins").strip() or "Plugins",
        "href": href,
        "plugin_id": plugin_id,
    }
    description = str(row.get("description") or "").strip()
    if description:
        out["description"] = description
    return out


def _dedupe_contrib_rows(
    rows: list[Any], *, key_fields: tuple[str, ...] = ("id",)
) -> list[Any]:
    """Keep first row per (plugin_id, …keys). Guards raced double-load after Store update."""
    seen: set[tuple[str, ...]] = set()
    out: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("plugin_id") or "").strip().lower()
        parts = tuple(str(row.get(f) or "").strip() for f in key_fields)
        key = (pid, *parts)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _plugin_icon_url(plugin_id: str) -> str:
    try:
        from backend.uefn_plugins.store import plugin_icon_data_url

        return plugin_icon_data_url(plugin_id) or ""
    except Exception:
        return ""


def _provider_shows_thinking(row: dict[str, Any]) -> bool:
    factory = _LLM_PROVIDER_FACTORIES.get(str(row.get("id") or "")) or {}
    if "shows_thinking_effort" in factory:
        return bool(factory.get("shows_thinking_effort"))
    if "shows_thinking_effort" in row:
        return bool(row.get("shows_thinking_effort"))
    return True


def image_generators() -> list[dict[str, Any]]:
    """Text to Image backends the loaded plugins declare (contributes.automations.image_generators)."""
    enabled = set(get_enabled_plugin_ids())
    with _LOCK:
        rows = _dedupe_contrib_rows(_CONTRIBUTIONS["automations_image_generators"])
    return [dict(row) for row in rows if row.get("plugin_id") in enabled]


def media_backends(node_type: str) -> list[dict[str, Any]]:
    """Backends the loaded plugins add to one image / 3D node (contributes.automations.media_backends)."""
    enabled = set(get_enabled_plugin_ids())
    with _LOCK:
        rows = _dedupe_contrib_rows(_CONTRIBUTIONS["automations_media_backends"], key_fields=("node", "id"))
    return [dict(row) for row in rows if row.get("plugin_id") in enabled and row.get("node") == node_type]


def image_generator_tools() -> set[str]:
    """Tool names of every declared image generator (no settings read: runs per tool call)."""
    with _LOCK:
        rows = list(_CONTRIBUTIONS["automations_image_generators"])
    return {str(row.get("tool") or "") for row in rows if isinstance(row, dict)} - {""}


def get_ui_contributions() -> dict[str, Any]:
    """Snapshot contrib registry for Settings UI — does not wait on register()."""
    enabled = list(get_enabled_plugin_ids())
    enabled_set = set(enabled)
    with _LOCK:
        out = {
            "settings_tabs": _dedupe_contrib_rows(_CONTRIBUTIONS["settings_tabs"]),
            "settings_sections": _dedupe_contrib_rows(_CONTRIBUTIONS["settings_sections"]),
            "dock_panels": _dedupe_contrib_rows(_CONTRIBUTIONS["dock_panels"]),
            "editor_kinds": _dedupe_contrib_rows(
                _CONTRIBUTIONS["editor_kinds"], key_fields=("kind",)
            ),
            "header_buttons": _dedupe_contrib_rows(_CONTRIBUTIONS["header_buttons"]),
            "header_menus": _dedupe_contrib_rows(_CONTRIBUTIONS["header_menus"]),
            "ui_panels": _dedupe_contrib_rows(_CONTRIBUTIONS["ui_panels"]),
            "chat_references": _dedupe_contrib_rows(
                _CONTRIBUTIONS["chat_references"], key_fields=("trigger", "id")
            ),
            "shell_boots": _dedupe_contrib_rows(
                _CONTRIBUTIONS["shell_boots"], key_fields=("entry",)
            ),
            "appearance_profiles": _dedupe_contrib_rows(_CONTRIBUTIONS["appearance_profiles"]),
            "appearance_css": _dedupe_contrib_rows(
                _CONTRIBUTIONS["appearance_css"], key_fields=("entry",)
            ),
            "appearance_effects": _dedupe_contrib_rows(_CONTRIBUTIONS["appearance_effects"]),
            "appearance_skins": _dedupe_contrib_rows(_CONTRIBUTIONS["appearance_skins"]),
            "sounds": _dedupe_contrib_rows(_CONTRIBUTIONS["sounds"]),
            "hooks": _dedupe_contrib_rows(_CONTRIBUTIONS["hooks"]),
            "automations_nodes": _dedupe_contrib_rows(_CONTRIBUTIONS["automations_nodes"]),
            "automations_triggers": _dedupe_contrib_rows(_CONTRIBUTIONS["automations_triggers"]),
            "automations_templates": _dedupe_contrib_rows(_CONTRIBUTIONS["automations_templates"]),
            "automations_image_generators": _dedupe_contrib_rows(_CONTRIBUTIONS["automations_image_generators"]),
            "verse_templates": sorted(
                _dedupe_contrib_rows(_CONTRIBUTIONS["verse_templates"]),
                key=lambda t: (int(t.get("order") or 100), str(t.get("name") or "")),
            ),
            "tts_voices": _dedupe_contrib_rows(_CONTRIBUTIONS["tts_voices"]),
            # Listers persist across reloads (like _REGISTERED); only surface the
            # ones whose plugin is currently enabled so the picker never polls a
            # disabled plugin.
            "tts_voice_plugins": sorted(p for p in _TTS_VOICE_LISTERS if p in enabled_set),
            "llm_providers": sorted(
                [
                    {
                        **row,
                        "shows_thinking_effort": _provider_shows_thinking(row),
                        "icon_data_url": _plugin_icon_url(str(row.get("plugin_id") or "")),
                    }
                    for row in _dedupe_contrib_rows(_CONTRIBUTIONS["llm_providers"])
                ],
                key=lambda p: (int(p.get("order") or 100), str(p.get("label") or "")),
            ),
            "llm_coding_agents": sorted(
                [
                    {
                        **row,
                        "shows_thinking_effort": bool(
                            (
                                _CODING_AGENT_FACTORIES.get(
                                    str(row.get("id") or "").replace("-", "_")
                                )
                                or {}
                            ).get("shows_thinking_effort")
                            or callable(
                                (
                                    _CODING_AGENT_FACTORIES.get(
                                        str(row.get("id") or "").replace("-", "_")
                                    )
                                    or {}
                                ).get("thinking_env")
                            )
                        ),
                    }
                    for row in _dedupe_contrib_rows(_CONTRIBUTIONS["llm_coding_agents"])
                ],
                key=lambda p: (int(p.get("order") or 100), str(p.get("label") or "")),
            ),
            "ide_hookups": _merged_ide_hookups(enabled_set),
            "agent_tools": dict(_CONTRIBUTIONS["agent_tools"]),
            "walkthroughs": list(_CONTRIBUTIONS.get("walkthroughs") or []),
            "enabled_ids": enabled,
        }
    # Every gateway list reads this: a plugin's gateway shows only while the plugin says so,
    # and says how its turns are paid (billing) so the UI never names a gateway.
    out["llm_providers"] = [
        {**row, **({"billing": billing} if (billing := provider_option(row.get("id"), "billing")) else {})}
        for row in out["llm_providers"]
        if provider_visible(row.get("id"))
    ]
    return out


def get_project_sources() -> list[dict[str, Any]]:
    """Projects-window tabs from enabled plugins (``contributes['project.sources']``)."""
    with _LOCK:
        rows = list(_CONTRIBUTIONS.get("project_sources") or [])
    return [r for r in rows if is_plugin_enabled(str(r.get("plugin_id") or ""))]


def get_reference_sources() -> list[dict[str, Any]]:
    """Live composer/search rows from enabled plugins (``contributes.chat.reference_sources``)."""
    with _LOCK:
        rows = list(_CONTRIBUTIONS.get("reference_sources") or [])
    return [r for r in rows if is_plugin_enabled(str(r.get("plugin_id") or ""))]


def get_contributions() -> dict[str, Any]:
    ensure_plugins_loaded()  # also repairs missing register() backends
    return get_ui_contributions()


def get_tts_synthesizer(plugin_id: str) -> Any | None:
    """Return the synthesizer callable registered by ``api.register_tts``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return None
    with _LOCK:
        return _TTS_SYNTHESIZERS.get(pid)


def register_tts_synthesizer(plugin_id: str, fn: Any) -> None:
    """Host-side register for plugin ``register(api).register_tts(fn)``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if not callable(fn):
        raise TypeError("TTS synthesizer must be callable")
    with _LOCK:
        _TTS_SYNTHESIZERS[pid] = fn


def get_tts_voices_lister(plugin_id: str) -> Any | None:
    """Return the dynamic-voices callable registered by ``api.register_tts_voices``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return None
    with _LOCK:
        return _TTS_VOICE_LISTERS.get(pid)


def register_tts_voices_lister(plugin_id: str, fn: Any) -> None:
    """Host-side register for plugin ``register(api).register_tts_voices(fn)``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if not callable(fn):
        raise TypeError("TTS voices lister must be callable")
    with _LOCK:
        _TTS_VOICE_LISTERS[pid] = fn


def register_media_generator(
    plugin_id: str, kind: str, fn: Any, describe: dict[str, Any] | None = None
) -> None:
    """Host-side register for ``api.register_media_generator(kind, fn, describe)``.

    ``fn(prompt, **options)`` returns the made media (``{path|url|b64, mime}``).
    ``describe``: ``{label, secret_key?, models?, ...}``; ``secret_key`` names the saved
    credential that must exist for the maker to be offered (none = always offered).
    """
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if kind not in MEDIA_KINDS:
        raise ValueError(f"media kind must be one of {', '.join(MEDIA_KINDS)}")
    if not callable(fn):
        raise TypeError("media generator must be callable")
    with _LOCK:
        _MEDIA_GENERATORS[(kind, pid)] = {"fn": fn, "describe": dict(describe or {})}


def media_generators(kind: str) -> list[dict[str, Any]]:
    """Makers for ``kind`` whose plugin is enabled and whose saved key is present."""
    from backend.agent.secrets import get_key

    with _LOCK:
        rows = [(pid, dict(r)) for (k, pid), r in _MEDIA_GENERATORS.items() if k == kind]
    out: list[dict[str, Any]] = []
    for pid, row in sorted(rows, key=lambda x: x[0]):
        desc = row["describe"]
        secret = str(desc.get("secret_key") or "").strip()
        if not is_plugin_enabled(pid) or (secret and not (get_key(secret) or "").strip()):
            continue
        out.append({"plugin_id": pid, "kind": kind, "fn": row["fn"], **desc})
    return out


def register_connection_probe(
    plugin_id: str,
    fn: Any,
    *,
    label: str = "",
    program: str = "",
) -> None:
    """Header Connections row. ``fn()`` must be cheap (no editor work)."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if not callable(fn):
        raise TypeError("connection probe must be callable")
    with _LOCK:
        _CONNECTION_PROBES[pid] = {
            "label": str(label or "").strip() or pid,
            "program": str(program or "").strip() or pid,
            "fn": fn,
        }
        _CONNECTION_CACHE.pop(pid, None)


def _normalize_connection_row(pid: str, spec: Mapping[str, Any], raw: Any) -> dict[str, Any]:
    label = str(spec.get("label") or pid)
    program = str(spec.get("program") or pid)
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            low = text.lower()
            online = not (low.startswith("error") or "disconnect" in low or "offline" in low)
            return {
                "id": pid,
                "program": program,
                "label": label,
                "online": online,
                "warn": False,
                "detail": text[:160] or ("Connected" if online else "Offline"),
            }
    if isinstance(data, Mapping):
        online = data.get("connected")
        if online is None:
            online = data.get("online")
        if online is None:
            online = data.get("ok")
        warn = bool(data.get("warn"))
        detail = str(data.get("detail") or data.get("hint") or data.get("state") or "")
        row_label = str(data.get("label") or label)
        return {
            "id": pid,
            "program": str(data.get("program") or program),
            "label": row_label,
            "online": bool(online),
            "warn": warn,
            "detail": detail[:160] or ("Connected" if online else "Offline"),
        }
    online = bool(data)
    return {
        "id": pid,
        "program": program,
        "label": label,
        "online": online,
        "warn": False,
        "detail": "Connected" if online else "Offline",
    }


def plugin_connection_rows() -> list[dict[str, Any]]:
    """Live plugin MCP rows for the Connections menu. Never raises."""
    now = time.monotonic()
    with _LOCK:
        items = list(_CONNECTION_PROBES.items())
    rows: list[dict[str, Any]] = []
    for pid, spec in items:
        if not is_plugin_enabled(pid):
            continue
        with _LOCK:
            cached = _CONNECTION_CACHE.get(pid)
        if cached and (now - cached[0]) < _CONNECTION_CACHE_TTL_S:
            rows.append(dict(cached[1]))
            continue
        fn = spec.get("fn")
        try:
            raw = fn() if callable(fn) else False
        except Exception as exc:  # noqa: BLE001 — a dead plugin must not blank the menu
            row = {
                "id": pid,
                "program": str(spec.get("program") or pid),
                "label": str(spec.get("label") or pid),
                "online": False,
                "warn": False,
                "detail": str(exc)[:160],
            }
            with _LOCK:
                _CONNECTION_CACHE[pid] = (now, row)
            rows.append(row)
            continue
        row = _normalize_connection_row(pid, spec, raw)
        with _LOCK:
            _CONNECTION_CACHE[pid] = (now, row)
        rows.append(row)
    return rows


def plugin_connection_for_program(program: str) -> dict[str, Any] | None:
    want = (program or "").strip()
    if not want:
        return None
    for row in plugin_connection_rows():
        if row.get("program") == want or row.get("id") == want:
            return row
    return None


def register_people_provider(plugin_id: str, fn: Any, *, label: str = "") -> None:
    """Header Online tab section. ``fn()`` must be cheap (cached data, no network wait)."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    if not callable(fn):
        raise TypeError("people provider must be callable")
    with _LOCK:
        _PEOPLE_PROVIDERS[pid] = {"label": str(label or "").strip() or pid, "fn": fn}
        _PEOPLE_CACHE.pop(pid, None)


def plugin_people_sections() -> list[dict[str, Any]]:
    """``[{id, label, people: [{id, name, detail, online}]}]`` from enabled plugins. Never raises."""
    now = time.monotonic()
    with _LOCK:
        items = list(_PEOPLE_PROVIDERS.items())
    sections: list[dict[str, Any]] = []
    for pid, spec in items:
        if not is_plugin_enabled(pid):
            continue
        with _LOCK:
            cached = _PEOPLE_CACHE.get(pid)
        if cached and (now - cached[0]) < _CONNECTION_CACHE_TTL_S:
            sections.append(cached[1])
            continue
        try:
            raw = spec["fn"]()
        except Exception:  # noqa: BLE001 — a dead plugin must not blank the menu
            raw = []
        people = []
        for row in raw if isinstance(raw, list) else []:
            name = str(row.get("name") or "").strip() if isinstance(row, Mapping) else ""
            if name:
                people.append({
                    "id": str(row.get("id") or name)[:120],
                    "name": name[:80],
                    "detail": str(row.get("detail") or "")[:160],
                    "online": bool(row.get("online")),
                })
        section = {"id": pid, "label": spec["label"], "people": people[:50]}
        with _LOCK:
            _PEOPLE_CACHE[pid] = (now, section)
        sections.append(section)
    return sections


def attach_plugin_connections(status: Mapping[str, Any]) -> dict[str, Any]:
    """The header status menu's plugin rows: Connections, and the Online tab's people."""
    out = dict(status)
    out["plugin_connections"] = plugin_connection_rows()
    out["plugin_people"] = plugin_people_sections()
    return out


def register_secret_tester(plugin_id: str, secret_key: str, test_fn: Any) -> None:
    """Register ``test_fn(api_key: str) -> {ok, detail}`` for a plugin secret field."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    key = str(secret_key or "").strip()
    if not key:
        raise ValueError("secret_key required")
    if not callable(test_fn):
        raise TypeError("secret test_fn must be callable")
    with _LOCK:
        _SECRET_TESTERS[key] = {"plugin_id": pid, "test_fn": test_fn}


def run_secret_test(
    plugin_id: str,
    secret_key: str,
    api_key: str = "",
) -> dict[str, Any]:
    """Call a registered secret tester. Empty api_key → use the saved credential."""
    from backend.agent.secrets import get_key
    from backend.uefn_plugins.store import (
        load_plugin_manifest,
        normalize_plugin_id,
    )

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    key = str(secret_key or "").strip()
    if not key:
        return {"ok": False, "detail": "secret key required"}
    manifest = load_plugin_manifest(pid)
    if manifest is None:
        return {"ok": False, "detail": f"UEFN plugin not found: {pid}"}
    raw = manifest.get("secret_keys") if isinstance(manifest.get("secret_keys"), list) else []
    allowed = {str(k).strip() for k in raw if str(k).strip()}
    if key not in allowed:
        return {"ok": False, "detail": f"Key {key!r} is not declared in {pid} secret_keys"}
    with _LOCK:
        row = _SECRET_TESTERS.get(key)
    if not row or row.get("plugin_id") != pid or not callable(row.get("test_fn")):
        return {"ok": False, "detail": "No API key test registered for this plugin"}
    candidate = str(api_key or "").strip() or (get_key(key) or "").strip()
    if not candidate:
        return {"ok": False, "detail": "Paste an API key first"}
    try:
        result = row["test_fn"](candidate)
    except Exception as exc:  # noqa: BLE001 — never raise across the JS bridge
        return {"ok": False, "detail": f"Test failed: {exc}"}
    if isinstance(result, dict):
        ok = bool(result.get("ok"))
        detail = str(result.get("detail") or ("OK" if ok else "Test failed"))
        return {"ok": ok, "detail": detail}
    return {"ok": bool(result), "detail": "OK" if result else "Test failed"}


def register_llm_provider_factory(
    plugin_id: str,
    provider_id: str,
    factory: Any,
    *,
    fetch_models: Any = None,
    test_key_model: str = "",
    test_key: Any = None,
    tool_schema: str = "",
    key_optional: bool = False,
    normalize_secret: Any = None,
    **extra: Any,
) -> None:
    """Host-side register for ``api.register_llm_provider``.

    Optional vendor hooks (no host if-vendor):
    - tool_schema: \"openai\" | \"anthropic\" | \"gemini\"
    - key_optional: True for URL gateways (Ollama)
    - normalize_secret: ``(raw) -> str`` before test/fetch
    - fetch_usage: ``(api_key, *, model=\"\") -> {windows, notice?}`` live quota sliders
    - read_cached_models: ``() -> list[ModelInfo]`` disk only, no Node or network
    """
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    provid = str(provider_id or "").strip().lower()
    if not provid:
        raise ValueError("provider id required")
    if not callable(factory):
        raise TypeError("LLM provider factory must be callable")
    row: dict[str, Any] = {
        "plugin_id": pid,
        "factory": factory,
        "fetch_models": fetch_models if callable(fetch_models) else None,
        "test_key_model": str(test_key_model or "").strip(),
        "test_key": test_key if callable(test_key) else None,
        "tool_schema": str(tool_schema or "").strip().lower(),
        "key_optional": bool(key_optional),
        "normalize_secret": normalize_secret if callable(normalize_secret) else None,
    }
    for k, v in extra.items():
        if v is not None:
            row[k] = v
    with _LOCK:
        _LLM_PROVIDER_FACTORIES[provid] = row


def register_coding_agent_factory(
    plugin_id: str,
    agent_id: str,
    factory: Any,
    *,
    complete_one_shot: Any = None,
    test_key: Any = None,
    resolve_api_fallback: Any = None,
    aliases: list[str] | tuple[str, ...] | None = None,
    skills_dir: Any = None,
    native_skills: bool = False,
    thinking_env: Any = None,
    normalize_model: Any = None,
    before_launch: Any = None,
    on_needs_login: Any = None,
    settings_defaults: dict[str, Any] | None = None,
    install_help: str = "",
    supports_batch_complete: bool = False,
    **extra: Any,
) -> None:
    """Host-side register for ``api.register_coding_agent``.

    Vendor behavior lives on the registration record — not ``if id == …`` in core.
    """
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    aid = str(agent_id or "").strip().lower().replace("-", "_")
    if not aid:
        raise ValueError("coding agent id required")
    if not callable(factory):
        raise TypeError("coding agent factory must be callable")
    alias_list = [
        str(a).strip().lower().replace("-", "_")
        for a in (aliases or ())
        if str(a).strip()
    ]
    row: dict[str, Any] = {
        "plugin_id": pid,
        "factory": factory,
        "complete_one_shot": complete_one_shot if callable(complete_one_shot) else None,
        "test_key": test_key if callable(test_key) else None,
        "resolve_api_fallback": resolve_api_fallback if callable(resolve_api_fallback) else None,
        "aliases": alias_list,
        "skills_dir": skills_dir,
        "native_skills": bool(native_skills),
        "thinking_env": thinking_env if callable(thinking_env) else None,
        "normalize_model": normalize_model if callable(normalize_model) else None,
        "before_launch": before_launch if callable(before_launch) else None,
        "on_needs_login": on_needs_login if callable(on_needs_login) else None,
        "settings_defaults": dict(settings_defaults or {}),
        "install_help": str(install_help or ""),
        "supports_batch_complete": bool(supports_batch_complete),
    }
    for k, v in extra.items():
        if v is not None:
            row[k] = v
    with _LOCK:
        _CODING_AGENT_FACTORIES[aid] = row


def get_llm_provider_registration(provider_id: str) -> dict[str, Any] | None:
    """Return registration for a provider id (may be disabled — caller gates).

    None while its plugin hides it (``visible``), so its models and meter never load."""
    provid = str(provider_id or "").strip().lower()
    if not provid or not provider_visible(provid):
        return None
    with _LOCK:
        row = _LLM_PROVIDER_FACTORIES.get(provid)
        return dict(row) if row else None


# Gateway options a plugin passes to register_llm_provider (core never names a gateway):
# - visible: () -> bool, False hides the gateway everywhere (an account without access)
# - fallback_model: the model a chat with no usable model takes, while visible
# - remote: True when the model runs on a server and cannot read this PC
# - billing: how its turns are paid, e.g. "account"
# - model_ids: its own model ids, so a bare id finds its gateway
# - model_aliases: {old id: current id}, so chats saved with an old id keep working
def provider_option(provider_id: Any, key: str, default: Any = None) -> Any:
    provid = str(provider_id or "").strip().lower()
    with _LOCK:
        row = _LLM_PROVIDER_FACTORIES.get(provid)
        return row.get(key, default) if row else default


def provider_visible(provider_id: Any) -> bool:
    check = provider_option(provider_id, "visible")
    if not callable(check):
        return True
    try:
        return bool(check())
    except Exception:
        return False


def registered_providers() -> list[str]:
    with _LOCK:
        return list(_LLM_PROVIDER_FACTORIES)


def provider_for_model(model: Any) -> str:
    """The gateway whose model_ids or model_aliases hold this bare model id ("" when none)."""
    mid = str(model or "").strip().lower()
    if not mid:
        return ""
    with _LOCK:
        rows = list(_LLM_PROVIDER_FACTORIES.items())
    for provid, row in rows:
        owned = {str(m).strip().lower() for m in row.get("model_ids") or ()}
        owned |= {str(m).strip().lower() for m in dict(row.get("model_aliases") or {})}
        if mid in owned:
            return provid
    return ""


def current_model_id(provider_id: Any, model: Any) -> str:
    """The id a gateway uses now for ``model`` (its model_aliases), else ``model`` as given."""
    mid = str(model or "").strip()
    aliases = provider_option(provider_id, "model_aliases") or {}
    return str(dict(aliases).get(mid.lower()) or dict(aliases).get(mid) or mid)


def _picked_account_key(provider_id: str, conv_id: str = "") -> str:
    """The key of the account to use now (the chat's or the default, or the next one while
    it's full); "" when unset."""
    try:
        from backend.agent import accounts

        picked = accounts.pick_account(provider_id, conv_id=conv_id)
        return str(accounts.account_key(provider_id, picked) or "").strip()
    except Exception:
        return ""


def resolve_gateway_credential(
    provider_id: str, raw: str = "", *, row: dict[str, Any] | None = None, conv_id: str = ""
) -> str:
    """Saved key, then plugin ``normalize_secret``, then URL default (Ollama).

    Hosted providers like UEFN Ducky keep the device key in ``duckyos_account``
    and resolve it here — they must not require a pasted LLM secret.

    ``row``: the gateway's ``llm_providers`` row when the caller already holds one, so
    this does not rebuild every plugin contribution just to find it.
    ``conv_id``: the chat asking, so a chat on its own account gets that account's key.
    """
    from backend.agent.secrets import get_key

    name = str(provider_id or "").strip().lower()
    if not name:
        return ""
    key = str(raw or "").strip() or _picked_account_key(name, conv_id) or str(get_key(name) or "").strip()
    reg = get_llm_provider_registration(name) or {}
    norm = reg.get("normalize_secret")
    if callable(norm):
        try:
            key = str(norm(key) or "").strip()
        except Exception:
            pass
    if key:
        return key
    if row is None:
        for candidate in get_contributions().get("llm_providers") or []:
            if not isinstance(candidate, dict):
                continue
            if str(candidate.get("id") or "").strip().lower() == name:
                row = candidate
                break
    kind = str((row or {}).get("kind") or "").strip().lower()
    if kind == "url" or (reg.get("key_optional") and kind != "secret"):
        return str((row or {}).get("default_url") or "").strip() or "http://localhost:11434"
    return ""


def get_coding_agent_registration(agent_id: str) -> dict[str, Any] | None:
    aid = str(agent_id or "").strip().lower().replace("-", "_")
    if not aid:
        return None
    with _LOCK:
        row = _CODING_AGENT_FACTORIES.get(aid)
        return dict(row) if row else None


def registered_llm_provider_ids() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_LLM_PROVIDER_FACTORIES.keys()))


def registered_coding_agent_ids() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_CODING_AGENT_FACTORIES.keys()))


def register_ide_hookup_factory(plugin_id: str, kind: str, *, label: str = "") -> None:
    """Bind an IDE MCP/skills Apply target to a Store gateway plugin."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    key = (kind or "").strip().lower().replace("-", "_")
    if not key or not key.replace("_", "").isalnum():
        raise ValueError(f"unsupported ide hookup kind: {kind!r}")
    with _LOCK:
        _IDE_HOOKUPS[key] = {
            "plugin_id": pid,
            "label": (label or key).strip() or key,
        }


def get_ide_hookup(kind: str) -> dict[str, Any] | None:
    key = (kind or "").strip().lower()
    with _LOCK:
        row = _IDE_HOOKUPS.get(key)
        if not row:
            return None
        out = dict(row)
    pid = str(out.get("plugin_id") or "")
    if pid and not is_plugin_enabled(pid):
        return None
    return out


def registered_ide_hookup_kinds() -> tuple[str, ...]:
    with _LOCK:
        return tuple(sorted(_IDE_HOOKUPS.keys()))


def _unregister_plugin_mcp_tools(tool_names: set[str]) -> None:
    """Remove a plugin's tools from the shared FastMCP server (best-effort).

    Without this, a reload leaves the old ``api.tool`` wrapper registered, and a
    reinstalled plugin keeps serving its previous tool code. Must hold ``_LOCK``.
    """
    if not tool_names:
        return
    try:
        from backend.server import mcp

        manager = getattr(mcp, "_tool_manager", None)
        if manager is None:
            return
        remove = getattr(manager, "remove_tool", None)
        for name in tool_names:
            try:
                if callable(remove):
                    remove(name)
                else:
                    getattr(manager, "_tools", {}).pop(name, None)
            except Exception:
                getattr(manager, "_tools", {}).pop(name, None)
    except Exception:
        _log.debug("could not unregister plugin MCP tools", exc_info=True)


def set_active_uefn_agent_plugin_ids(plugin_ids: list[str] | None) -> None:
    """Scope which UEFN app-plugin agent.tools are available for this run.

    Called by the embedded agent with the chat's override. None = unset (external
    MCP / no scope → all enabled plugins keep their tools). A list (even empty)
    means opt-in only for those plugin ids.
    """
    if plugin_ids is None:
        _ACTIVE_UEFN_AGENT_IDS.set(None)
        return
    from backend.uefn_plugins.store import normalize_plugin_id

    ids: list[str] = []
    for item in plugin_ids:
        try:
            pid = normalize_plugin_id(str(item))
        except ValueError:
            continue
        if pid not in ids:
            ids.append(pid)
    _ACTIVE_UEFN_AGENT_IDS.set(tuple(ids))


def uefn_agent_tools_allowed(plugin_id: str) -> bool:
    """True when the plugin is enabled and opted in for this run (or unscoped). The shared-
    spaces provider's tools are part of the app, like core tools: never opted out per chat."""
    if not is_plugin_enabled(plugin_id):
        return False
    active = _ACTIVE_UEFN_AGENT_IDS.get()
    if active is None:
        return True
    if plugin_id in active:
        return True
    from backend.uefn_plugins.spaces import provider_plugin

    return plugin_id == provider_plugin()


def _folder_plugin_call(pid: str, tool_name: str, current: Callable[..., Any]) -> Callable[..., Any] | None:
    """Before a tool call of a plugin that keeps declared folders: an agent's call first
    lands the team's changes waiting behind Update now (an agent never edits a stale
    base); then, when its folders moved since this process loaded it, it reloads here and
    the call goes to the reloaded tool, which is returned. None: run the tool as loaded."""
    from backend.uefn_plugins.scopes import caller

    actor, _ = caller("user" if is_app() else "agent")
    if actor == "agent":
        try:
            from backend.uefn_plugins import vcs_engine

            vcs_engine.apply_waiting(pid)
        except Exception:  # noqa: BLE001 — the call still runs; Update now stays on the bar
            _log.debug("landing %s's waiting changes before a call failed", pid, exc_info=True)
    if not reload_if_folders_moved(pid):
        return None
    from backend.server import mcp

    fn = getattr(mcp._tool_manager.get_tool(tool_name), "fn", None)
    if not callable(fn) or fn is current:
        raise RuntimeError(f"{pid} restarted after its files changed and no longer has the tool {tool_name}.")
    return fn


def _with_panel_note(plugin_id: str, result: Any) -> Any:
    """An agent's first call to a plugin whose panels are all off screen says so."""
    try:
        from backend.uefn_plugins.panel_presence import panel_note_once

        note = panel_note_once(plugin_id)
    except Exception:
        return result
    if not note:
        return result
    if isinstance(result, dict):
        return {**result, "panel_note": note} if "panel_note" not in result else result
    if isinstance(result, str):
        return f"{result}\n\n{note}"
    return result


def register_panel_rpc(plugin_id: str, name: str, fn: Any) -> None:
    """Register a PanelApi / bridge callable for ``plugin_call(plugin_id, name, params)``."""
    from backend.uefn_plugins.store import normalize_plugin_id

    pid = normalize_plugin_id(plugin_id)
    method = str(name or "").strip()
    if not method or not callable(fn):
        raise ValueError("register_panel_rpc requires a non-empty name and callable")
    with _LOCK:
        slot = _PANEL_RPC.setdefault(pid, {})
        slot[method] = fn


def call_panel_rpc(plugin_id: str, method: str, params: dict[str, Any] | None = None) -> Any:
    """Dispatch a plugin panel RPC. Raises ValueError when disabled / unknown.

    Never calls ``ensure_plugins_loaded()`` — that path repairs every enabled
    plugin (unity-mcp nested sync) and used to freeze Discord Settings on
    ``Loading…`` for minutes.
    """
    from backend.uefn_plugins.store import load_plugin_manifest, normalize_plugin_id, plugin_dir

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as e:
        raise ValueError(str(e)) from e
    if not is_plugin_enabled(pid):
        raise ValueError(f"Plugin '{pid}' is disabled — enable it in Settings → Store")
    name = str(method or "").strip()
    with _LOCK:
        fn = (_PANEL_RPC.get(pid) or {}).get(name)
    if fn is None:
        # Async Store enable may still be registering this plugin — wait for it,
        # then load only this plugin (never the full repair storm).
        wait_plugin_toggles(timeout=8.0)
        with _LOCK:
            fn = (_PANEL_RPC.get(pid) or {}).get(name)
        empty = not (_PANEL_RPC.get(pid) or {})
        if fn is None and (empty or pid not in _REGISTERED):
            # Registered-but-empty happens after invalidate + reload race.
            if empty:
                with _LOCK:
                    _REGISTERED.discard(pid)
            root = plugin_dir(pid)
            manifest = load_plugin_manifest(pid)
            # A plugin that failed to load fails the same way on every call: a panel
            # polling account.status reloaded Account every 2 s. Retry on the repair backoff.
            now = time.monotonic()
            failed_lately = pid in _LOAD_FAILURES and (
                now - float(_REPAIR_ATTEMPTS.get(pid) or 0.0) < _REPAIR_BACKOFF_S
            )
            if manifest is not None and root.is_dir() and not failed_lately:
                _REPAIR_ATTEMPTS[pid] = now
                _load_enabled_plugin(pid, root, manifest)
            with _LOCK:
                fn = (_PANEL_RPC.get(pid) or {}).get(name)
    if fn is None:
        failure = _LOAD_FAILURES.get(pid)
        if failure:
            raise ValueError(f"{pid} didn't load: {failure}")
        raise ValueError(f"Unknown panel RPC: {pid}.{name}")
    kwargs = dict(params or {})
    from backend.uefn_plugins.scopes import caller, write_group

    # What the call writes to a shared copy goes out as one change, the person's (or the
    # agent's whose run made the call).
    actor, ducky = caller("user")
    with write_group(pid, actor=actor, ducky=ducky, message=name):
        # Its folders moved since it loaded here (a switch, a pull, a project change in any
        # process): it reloads first, so the panel never saves the old copy's state into the new one.
        if pid in _FOLDER_EPOCH and reload_if_folders_moved(pid):
            with _LOCK:
                fn = (_PANEL_RPC.get(pid) or {}).get(name)
            if fn is None:
                raise ValueError(f"{pid} restarted after its files changed and no longer has {name}.")
        return fn(**kwargs)


def plugin_intent_rows() -> list[tuple[re.Pattern[str], frozenset[str]]]:
    ensure_plugins_loaded()
    active = _ACTIVE_UEFN_AGENT_IDS.get()
    with _LOCK:
        if active is None:
            return [(pat, tools) for _pid, pat, tools in _PLUGIN_INTENT_ROWS]
        allowed = set(active)
        return [(pat, tools) for pid, pat, tools in _PLUGIN_INTENT_ROWS if pid in allowed]


def plugin_tool_names() -> frozenset[str]:
    ensure_plugins_loaded()
    with _LOCK:
        return frozenset(_PLUGIN_TOOL_NAMES)


def store_plugin_tool_names() -> frozenset[str]:
    """Every tool a Store plugin registered, without waiting for plugins to load."""
    with _LOCK:
        return frozenset(_PLUGIN_TOOL_NAMES) | frozenset(_PLUGIN_TOOL_OWNER)


def plugin_for_tool(name: str) -> str:
    """Owning plugin id for an MCP tool, or ``ducky`` for core host tools."""
    n = (name or "").strip()
    if not n:
        return "ducky"
    with _LOCK:
        owner = _PLUGIN_TOOL_OWNER.get(n)
    if owner:
        return owner
    try:
        from backend.mcp_plugins.registry import parse_plugin_tool

        parsed = parse_plugin_tool(n)
        if parsed:
            return parsed[0]
    except Exception:
        pass
    return "ducky"


def plugin_plan_tools() -> frozenset[str]:
    """Union of explicit ``agent.tools.plan_tools`` from loaded desktop plugins."""
    ensure_plugins_loaded()
    out: set[str] = set()
    with _LOCK:
        out.update(_PLUGIN_PLAN_TOOLS)
        for meta in _CONTRIBUTIONS.get("agent_tools", {}).values():
            if not isinstance(meta, dict):
                continue
            for name in meta.get("plan_tools") or []:
                if isinstance(name, str) and name.strip():
                    out.add(name.strip())
    return frozenset(out)


def plugin_destructive_desktop_tools() -> frozenset[str]:
    """Union of ``agent.tools.destructive_tools`` from loaded desktop plugins."""
    ensure_plugins_loaded()
    out: set[str] = set()
    with _LOCK:
        out.update(_PLUGIN_DESTRUCTIVE_TOOLS)
        for meta in _CONTRIBUTIONS.get("agent_tools", {}).values():
            if not isinstance(meta, dict):
                continue
            for name in meta.get("destructive_tools") or []:
                if isinstance(name, str) and name.strip():
                    out.add(name.strip())
    return frozenset(out)


def sensitive_tool_plugin(name: str) -> str:
    """Owning plugin id when ``name`` is one of its ``agent.tools.sensitive`` tools, else ''."""
    n = (name or "").strip()
    with _LOCK:
        pid = _PLUGIN_TOOL_OWNER.get(n, "")
        meta = _CONTRIBUTIONS.get("agent_tools", {}).get(pid) if pid else None
    if not isinstance(meta, dict):
        return ""
    sensitive = meta.get("sensitive")
    if sensitive is True or (isinstance(sensitive, list) and n in sensitive):
        return pid
    return ""


def sensitive_tool_block_reason(name: str, *, listing: bool = False) -> str:
    """Why the calling run's model may not use this sensitive plugin tool ('' = it may)."""
    pid = sensitive_tool_plugin(name)
    if not pid:
        return ""
    from backend.uefn_plugins.model_gate import block_reason

    return block_reason(pid, name, label=_plugin_label(pid), listing=listing)


def filter_uefn_plugin_tools(tools: list[Any]) -> list[Any]:
    """Drop UEFN app-plugin tools that are Store-disabled or not in the run allowlist.

    ``active is None`` follows Store enable (all enabled plugins). Chat allowlists
    are not rewritten on disable — membership stays; this filter hides tools until
    the plugin is enabled again.
    """
    ensure_plugins_loaded()
    try:
        from backend.tools.support.plugin_gate import HOST_DISK_TOOLS as _host_disk
    except Exception:
        _host_disk = frozenset()
    active = _ACTIVE_UEFN_AGENT_IDS.get()
    with _LOCK:
        owner = dict(_PLUGIN_TOOL_OWNER)
        for pid, names in _API_TOOL_REGISTRY.items():
            for name in names:
                owner.setdefault(name, pid)
        for pid, _pat, names in _PLUGIN_INTENT_ROWS:
            for name in names:
                owner.setdefault(name, pid)
        # A disabled plugin's unload() drops its _PLUGIN_TOOL_OWNER rows on a
        # background thread; the last owner still hides its tools.
        for name, pid in _TOOL_LAST_OWNER.items():
            owner.setdefault(name, pid)
    if not owner:
        return list(tools)
    enabled: dict[str, bool] = {}  # once per plugin, not once per tool (a settings read each)
    out: list[Any] = []
    for t in tools:
        name = getattr(t, "name", None)
        if name not in owner:
            out.append(t)
            continue
        pid = owner[name]
        if name in _host_disk:
            out.append(t)
            continue
        if pid not in enabled:
            enabled[pid] = is_plugin_enabled(pid)
        if not enabled[pid]:
            try:
                from backend.tools.support.plugin_gate import heal_enable_store_plugin

                if heal_enable_store_plugin(pid) and is_plugin_enabled(pid):
                    out.append(t)
            except Exception:
                pass
            continue
        if active is not None and pid not in active:
            continue
        if sensitive_tool_block_reason(name, listing=True):
            continue
        out.append(t)
    return out


def _plugin_tool_names_for(pid: str, meta: dict[str, Any] | None = None) -> list[str]:
    names: set[str] = set()
    if isinstance(meta, dict):
        for name in meta.get("tools") or []:
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    with _LOCK:
        names.update(_API_TOOL_REGISTRY.get(pid) or ())
    return sorted(names)


def _rebuild_intent_row(pid: str, tool_names: set[str], intent_re: re.Pattern[str] | None = None) -> None:
    """Replace this plugin's intent row (must hold ``_LOCK``)."""
    _PLUGIN_INTENT_ROWS[:] = [row for row in _PLUGIN_INTENT_ROWS if row[0] != pid]
    if not tool_names:
        return
    if intent_re is None:
        raw = _API_INTENT_PATTERNS.get(pid)
        if raw:
            try:
                intent_re = re.compile(raw, re.I)
            except re.error:
                intent_re = None
    if intent_re is None:
        intent_re = re.compile(rf"\b{re.escape(pid)}\b", re.I)
    _PLUGIN_INTENT_ROWS.append((pid, intent_re, frozenset(tool_names)))


def _record_plugin_api_tool(
    pid: str,
    tool_name: str,
    *,
    intent: str | None = None,
    listener: bool = False,
) -> None:
    """Track a tool registered via ``api.tool()`` (survives contribution reloads)."""
    with _LOCK:
        _API_TOOL_REGISTRY.setdefault(pid, set()).add(tool_name)
        if isinstance(intent, str) and intent.strip():
            _API_INTENT_PATTERNS[pid] = intent.strip()
        meta = _CONTRIBUTIONS["agent_tools"].setdefault(pid, {"category": pid, "tools": []})
        tools = {str(t).strip() for t in (meta.get("tools") or []) if str(t).strip()}
        tools.add(tool_name)
        meta["tools"] = sorted(tools)
        _PLUGIN_TOOL_NAMES.add(tool_name)
        _PLUGIN_TOOL_OWNER[tool_name] = pid
        _TOOL_LAST_OWNER[tool_name] = pid
        if listener:
            _PLUGIN_HOST_ONLY_TOOLS.discard(tool_name)
        else:
            _PLUGIN_HOST_ONLY_TOOLS.add(tool_name)
        _rebuild_intent_row(pid, tools)


def is_plugin_host_only_tool(name: str) -> bool:
    """True when ``api.tool(listener=False)`` — no UEFN editor listener required."""
    n = (name or "").strip()
    if not n:
        return False
    ensure_plugins_loaded()
    with _LOCK:
        return n in _PLUGIN_HOST_ONLY_TOOLS


def plugin_tool_unavailable_reason(name: str) -> str:
    """Why a plugin tool this process served before is gone now ('' when it is served,
    or was never a plugin tool here)."""
    n = (name or "").strip()
    with _LOCK:
        if not n or n in _PLUGIN_TOOL_OWNER:
            return ""
        pid = _TOOL_LAST_OWNER.get(n, "")
        failure = _LOAD_FAILURES.get(pid, "")
    if not pid:
        return ""
    label = _plugin_label(pid)
    if failure:
        return (
            f"{n} belongs to the {label} plugin, which failed to load: {failure} "
            "Fix the plugin and reload it; until then none of its tools exist. "
            "This has nothing to do with the UEFN listener."
        )
    return f"{n} belongs to the {label} plugin, which is not loaded right now (reloading, turned off or removed)."


def plugin_load_failures() -> dict[str, str]:
    """{plugin label: error} for plugins whose last load in this process failed."""
    with _LOCK:
        failures = dict(_LOAD_FAILURES)
    return {_plugin_label(pid): err for pid, err in failures.items()}


def _plugin_label(pid: str) -> str:
    try:
        from backend.uefn_plugins.store import load_plugin_manifest

        return str((load_plugin_manifest(pid) or {}).get("label") or pid)
    except Exception:
        return pid


def is_uefn_agent_tool_plugin(plugin_id: str) -> bool:
    """True if an installed UEFN plugin contributes agent tools (manifest or api.tool)."""
    from backend.uefn_plugins.store import load_plugin_manifest, normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return False
    with _LOCK:
        if pid in _API_TOOL_REGISTRY and _API_TOOL_REGISTRY[pid]:
            return True
        if pid in _CONTRIBUTIONS["agent_tools"]:
            return True
    manifest = load_plugin_manifest(pid)
    if not manifest:
        return False
    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    tools_meta = contributes.get("agent.tools") or contributes.get("agent_tools")
    return bool(tools_meta)


def uefn_agent_tool_rows() -> list[dict[str, Any]]:
    """Tools & MCPs rows for enabled UEFN plugins that contribute agent.tools.

    Default-on for every ducky (deny-list opt-out on the profile). Schemas are
    still intent-gated per turn so they do not bloat the prompt until needed.
    """
    ensure_plugins_loaded()
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir, load_plugin_manifest

    rows: list[dict[str, Any]] = []
    with _LOCK:
        agent_tools = dict(_CONTRIBUTIONS["agent_tools"])
    for pid, meta in sorted(agent_tools.items()):
        if not is_plugin_enabled(pid):
            continue
        manifest = load_plugin_manifest(pid) or {}
        tool_list = _plugin_tool_names_for(pid, meta if isinstance(meta, dict) else None)
        n = len(tool_list)
        desc = str(manifest.get("description") or "").strip()
        if not desc:
            cat = str((meta or {}).get("category") or pid)
            desc = f"App plugin tools ({cat})" + (f" — {n} tools" if n else "")
        rows.append(
            {
                "id": pid,
                "label": str(manifest.get("label") or pid),
                "description": desc,
                "version": format_plugin_version(manifest.get("version")),
                "kind": "uefn_plugin",
                "transport": "builtin",
                "tags": ["app-plugin"],
                "enabled": True,
                "default_enabled": True,
                "requirements_note": "",
                "setup_steps": [],
                "tool_prefix": "",
                "tool_names": tool_list,
                "intents": [],
                "path": str(appdata_uefn_plugins_dir() / pid),
            }
        )
    return rows


def uefn_plugin_tool_group_rows() -> list[dict[str, Any]]:
    """Settings → MCPs rows for desktop plugins that extend the app MCP server."""
    ensure_plugins_loaded()
    from backend.uefn_plugins.store import list_uefn_plugins

    rows: list[dict[str, Any]] = []
    with _LOCK:
        agent_tools = dict(_CONTRIBUTIONS["agent_tools"])
        api_reg = {k: set(v) for k, v in _API_TOOL_REGISTRY.items()}

    for plug in list_uefn_plugins():
        pid = str(plug.get("id") or "").strip()
        if not pid:
            continue
        contrib = plug.get("contributes") if isinstance(plug.get("contributes"), dict) else {}
        tools_meta = contrib.get("agent.tools") or contrib.get("agent_tools")
        meta = agent_tools.get(pid) if isinstance(agent_tools.get(pid), dict) else None
        if meta is None and isinstance(tools_meta, dict):
            meta = {
                "category": str(tools_meta.get("category") or pid),
                "tools": [
                    str(n).strip()
                    for n in (tools_meta.get("tools") or [])
                    if isinstance(n, str) and n.strip()
                ],
            }
        elif meta is None and tools_meta is True:
            meta = {"category": pid, "tools": []}
        tool_list = _plugin_tool_names_for(pid, meta)
        if not tool_list and not tools_meta and pid not in api_reg:
            continue
        if not tool_list and pid in api_reg:
            tool_list = sorted(api_reg[pid])
        n = len(tool_list)
        desc = str(plug.get("description") or "").strip()
        if not desc:
            cat = str((meta or {}).get("category") or pid)
            desc = f"From plugin ({cat})" + (f" — {n} tools" if n else "")
        enabled = bool(plug.get("enabled"))
        rows.append(
            {
                "id": pid,
                "label": str(plug.get("label") or pid),
                "description": desc,
                "version": format_plugin_version(plug.get("version")),
                "kind": "uefn_plugin",
                "transport": "builtin",
                "tags": ["app-plugin", "from-plugin"],
                "enabled": enabled,
                "default_enabled": bool(plug.get("default_enabled", True)),
                "requirements_note": "",
                "setup_steps": [],
                "tool_prefix": "",
                "tool_names": tool_list,
                "intents": [],
                "path": str(plug.get("path") or ""),
            }
        )
    rows.sort(key=lambda r: str(r.get("label") or r.get("id") or "").lower())
    return rows


def count_uefn_plugin_tools(plugin_id: str) -> int:
    """Tool count for the MCPs tab Test button."""
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return 0
    ensure_plugins_loaded()
    with _LOCK:
        meta = _CONTRIBUTIONS["agent_tools"].get(pid)
    return len(_plugin_tool_names_for(pid, meta if isinstance(meta, dict) else None))


def _automation_contrib_row(row: Any, pid: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    nid = str(row.get("id") or row.get("type") or "").strip()
    if not nid:
        return None
    entry = dict(row)
    entry["id"] = nid
    entry["plugin_id"] = pid
    return entry


def _automation_template_row(row: Any, pid: str) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    tid = str(row.get("id") or "").strip()
    if not tid:
        return None
    from backend.automations.store import normalize_graph

    name = str(row.get("label") or row.get("name") or tid).strip() or tid
    req = [str(x).strip() for x in (row.get("requires_plugins") or []) if str(x).strip()]
    # A folder bundle: several workflows in nested folders (templates.bundle_of checks it).
    nested = row.get("bundle") if isinstance(row.get("bundle"), dict) else None
    is_bundle = nested is not None or row.get("kind") == "bundle" or isinstance(row.get("workflows"), list)
    out: dict[str, Any] = {
        "id": tid,
        "label": name,
        "name": name,
        "description": str(row.get("description") or ""),
        "icon": str(row.get("icon") or ("📁" if is_bundle else "⚡")),
        "category": str(row.get("category") or "").strip(),
        "graph": normalize_graph(row.get("graph")),
        "plugin_id": pid,
        "systems": row.get("systems"),
        "requires_plugins": req,
    }
    if is_bundle:
        src = nested or row
        out["bundle"] = {"root": src.get("root") or name, "folders": src.get("folders") or [], "workflows": src.get("workflows") or []}
    return out


def _image_generator_row(row: Any, pid: str) -> dict[str, Any] | None:
    """One Text to Image backend: the plugin's own MCP tool, what it is called and what it costs."""
    if not isinstance(row, dict):
        return None
    gid = str(row.get("id") or "").strip()
    tool = str(row.get("tool") or "").strip()
    if not gid or not tool:
        return None
    try:
        credits = max(0, int(row.get("credits") or 0))
    except (TypeError, ValueError):
        credits = 0
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    prompt_arg = str(row.get("prompt_arg") or "prompt").strip() or "prompt"
    out: dict[str, Any] = {
        "id": gid,
        "label": str(row.get("label") or gid).strip() or gid,
        "tool": tool,
        "credits": credits,
        "args": dict(args),
        "prompt_arg": prompt_arg,
        "plugin_id": pid,
    }
    # Optional: what it costs in words (shown instead of "~N credits"), whether it spends
    # even at 0 credits (Spend switch + confirm_spend), and its own settings (size, quality…)
    # whose values the node passes into the tool's arguments.
    cost = str(row.get("cost") or "").strip()
    if cost:
        out["cost"] = cost
    if row.get("paid") is True:
        out["paid"] = True
    # Optional: the tool argument a reference picture goes in; such a tool is offered on Edit Image too.
    image_arg = str(row.get("image_arg") or "").strip()
    if image_arg:
        out["image_arg"] = image_arg
    try:
        from backend.automations.catalog import _fields

        fields = [f for f in _fields(row.get("config_fields")) if f["id"] != prompt_arg]
    except Exception:
        fields = []
    if fields:
        out["config_fields"] = fields
    return out


def _media_backend_row(row: Any, pid: str) -> dict[str, Any] | None:
    """One backend for a built-in image / 3D node: the plugin's MCP tool and how the node's
    input pins map onto its arguments (``pins``; ``task_args`` when the wired file came from
    this plugin and carries its task id; ``task_only`` pins accept nothing else)."""
    base = _image_generator_row({**row, "prompt_arg": "__unused__"}, pid) if isinstance(row, dict) else None
    node = str(row.get("node") or "").strip() if isinstance(row, dict) else ""
    if base is None or not node or node == "image.generate":
        return None
    base.pop("prompt_arg", None)

    def names(raw: Any) -> dict[str, str]:
        return {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip() and str(v).strip()} if isinstance(raw, dict) else {}

    out: dict[str, Any] = {**base, "node": node, "pins": names(row.get("pins")), "task_args": names(row.get("task_args")),
                           "cfg": names(row.get("cfg"))}
    only = [str(p).strip() for p in row.get("task_only") or [] if str(p).strip()] if isinstance(row.get("task_only"), list) else []
    if only:
        out["task_only"] = only
    stage = str(row.get("stage") or "").strip()
    if stage:
        out["stage"] = stage
    return out


def _clear_node_handlers(pid: str) -> None:
    try:
        from backend.automations.plugin import clear_for_plugin

        clear_for_plugin(pid)
    except Exception:
        pass


def _strip_contributions_for(pid: str) -> None:
    """Remove contribution rows for one plugin (so a failed register can retry cleanly).

    Workflow node handlers stay: they come from register(), like tools, and are dropped
    only when register() runs again or the runtime goes (invalidate_plugin_runtime). A
    refresh of the rows skips register() for a plugin that is registered or registering,
    so clearing them here left its nodes with no handler while its tools kept working."""
    for key, val in _CONTRIBUTIONS.items():
        if key == "agent_tools":
            if isinstance(val, dict):
                val.pop(pid, None)
            continue
        if not isinstance(val, list):
            continue
        _CONTRIBUTIONS[key] = [
            row
            for row in val
            if not (isinstance(row, dict) and str(row.get("plugin_id") or "") == pid)
        ]


def _record_plugin_load_error(pid: str, exc: BaseException | str) -> None:
    """Persist last register failure so Settings can explain 'Backend not loaded'.

    A plain string is a notice for the person and is recorded as written.
    """
    from backend.util.privacy import scrub

    # Shown in Settings and kept for 30 days, so no folders, emails or keys in it.
    message = scrub(exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}")
    _LOAD_FAILURES[pid] = message
    try:
        from backend.store.switch import use_db

        if use_db("events"):
            import time as _t

            from backend.store.repos import events as _ev

            _ev.insert("plugin_load_error", ts=_t.time(), source=pid, message=message,
                       payload={"plugin_id": pid, "error": message})
            _ev.trim("plugin_load_error", older_than=_t.time() - 30 * 86400, keep=_LOAD_ERROR_LOG_KEEP)
            return
    except Exception:
        pass
    try:
        from frontend.settings import default_app_data_dir

        path = default_app_data_dir() / "uefn_plugin_load_errors.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        import time

        line = json.dumps(
            {"ts": time.time(), "plugin_id": pid, "error": message},
            ensure_ascii=False,
        )
        # Repair retries append the same failure every ensure_plugins_loaded() —
        # unrotated this reached 13.5k lines / 1.9MB. Keep the newest tail only.
        if path.is_file() and path.stat().st_size > _LOAD_ERROR_LOG_MAX_BYTES:
            kept = path.read_text(encoding="utf-8").splitlines()[-_LOAD_ERROR_LOG_KEEP:]
            path.write_text("\n".join(kept) + "\n", encoding="utf-8")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def tried_and_not_loaded() -> set[str]:
    """Plugins this process tried to load that are not loaded here: register() failed, or
    the plugin is paused (access to its audience lost). Their tools will not appear here on
    their own."""
    return (set(_LOAD_FAILURES) | set(_PAUSED_PRIVATE_PLUGINS)) - set(_REGISTERED)


def _ide_auto_apply_worker() -> None:
    """Drain queued IDE applies once the load pass is done. One thread — applies
    share skills roots, so they must not run concurrently."""
    global _IDE_APPLY_THREAD
    try:
        from frontend.ide_apply import apply_ide_bridge
        from frontend.ide_paths import IdeKind

        _LOAD_DONE.wait(timeout=180.0)  # let coding-agent skills_dir registrations land
        while True:
            with _IDE_APPLY_LOCK:
                if not _IDE_APPLY_PENDING:
                    # Clear under the same lock as the emptiness check, or a queue
                    # racing here would see a live thread and orphan its item.
                    _IDE_APPLY_THREAD = None
                    return
                kind, log = _IDE_APPLY_PENDING.pop(0)
            try:
                log(f"IDE auto-apply → {apply_ide_bridge(IdeKind(kind))}")
            except Exception as exc:  # noqa: BLE001 — IDE may be absent
                log(f"IDE auto-apply skipped ({kind}): {exc}")
    except Exception:  # noqa: BLE001 — never kill the app on a bad IDE import
        _log.exception("IDE auto-apply worker failed")
        with _IDE_APPLY_LOCK:
            _IDE_APPLY_THREAD = None
            _IDE_APPLY_PENDING.clear()


def _queue_ide_auto_apply(kind: str, log: Callable[[str], None]) -> None:
    key = (kind or "").strip().lower()
    if not key:
        return
    global _IDE_APPLY_THREAD
    with _IDE_APPLY_LOCK:
        if any(k == key for k, _ in _IDE_APPLY_PENDING):
            return
        _IDE_APPLY_PENDING.append((key, log))
        if _IDE_APPLY_THREAD is not None and _IDE_APPLY_THREAD.is_alive():
            return
        _IDE_APPLY_THREAD = threading.Thread(
            target=_ide_auto_apply_worker, daemon=True, name="uefn-ide-auto-apply"
        )
        _IDE_APPLY_THREAD.start()


_DEFAULT_ICON_FILES = (
    "assets/icon.svg",
    "assets/icon.png",
    "assets/icon.webp",
    "assets/icon.gif",
    "assets/icon.jpg",
    "assets/icon.jpeg",
)


def _inline_asset_icon(rel: str, root: Path) -> str | None:
    """Turn ``assets/icon.png`` into a data URL, or None if missing/unsafe."""
    from backend.uefn_plugins.webview import sanitize_entry

    entry = sanitize_entry(rel, root)
    if not entry:
        return None
    path = root / entry
    try:
        if not path.is_file() or path.stat().st_size > _ICON_ASSET_MAX_BYTES:
            return None
        mime = _ICON_MIME.get(path.suffix.lower())
        if not mime:
            return None
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    return f"data:{mime};base64,{b64}"


def _plugin_default_icon(root: Path) -> str | None:
    """First colored ``assets/icon.*`` present for this plugin."""
    for rel in _DEFAULT_ICON_FILES:
        url = _inline_asset_icon(rel, root)
        if url:
            return url
    return None


def _resolve_contrib_icon(icon: Any, root: Path) -> str | None:
    """Resolve settings/header icons: asset → data URL; named keys prefer color asset."""
    raw = str(icon or "").strip()
    if raw.startswith("assets/"):
        return _inline_asset_icon(raw, root)
    if raw.startswith("data:image/") or re.match(r"https?://", raw, re.I):
        return raw
    # Named flat keys (user/globe/…) or empty → use plugin color icon when shipped.
    if not raw or re.fullmatch(r"[a-z][a-z0-9_-]*", raw, re.I):
        return _plugin_default_icon(root) or (raw or None)
    return raw  # emoji / short label


def manifest_tool_concurrency(manifest: dict[str, Any]) -> int:
    raw = manifest.get("tool_concurrency")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return DEFAULT_TOOL_CONCURRENCY
    return min(raw, 64)


def _tool_slots(pid: str) -> tuple[threading.BoundedSemaphore, int]:
    with _LOCK:
        limit = _TOOL_CONCURRENCY.get(pid, DEFAULT_TOOL_CONCURRENCY)
        slots = _TOOL_SLOTS.get(pid)
        if slots is None:
            slots = _TOOL_SLOTS[pid] = threading.BoundedSemaphore(limit)
        return slots, limit


@contextlib.contextmanager
def plugin_tool_slot(pid: str) -> Iterator[None]:
    """Hold one of ``pid``'s tool slots for a call. A call from inside the same plugin's call
    (same thread) reuses its slot, so a limit of 1 cannot deadlock the plugin on itself."""
    held: set[str] = getattr(_HELD_SLOTS, "pids", None) or set()
    if pid in held:
        yield
        return
    slots, limit = _tool_slots(pid)
    if not slots.acquire(timeout=TOOL_SLOT_WAIT_S):
        raise ValueError(
            f"Plugin '{pid}' is busy ({limit} of its calls are running); try again in a moment."
        )
    _HELD_SLOTS.pids = held | {pid}
    try:
        yield
    finally:
        _HELD_SLOTS.pids = held
        slots.release()


def _bind_folders(pid: str) -> int | None:
    """A plugin that keeps declared folders, about to load: they point at the copy it reads
    now (:func:`team_folders.bind_plugin`). Returns the reload epoch it loads at (None: it
    keeps none). A bind that fails never stops the load; the next one tries again."""
    try:
        from backend.uefn_plugins import team_folders

        if not team_folders.plugin_folders(pid):
            return None
    except Exception:  # noqa: BLE001 — a manifest that can't be read: no folders
        return None
    try:
        out = team_folders.bind_plugin(pid)
        if not out.get("ok"):
            _log.warning("Plugin %s: its folders couldn't be pointed at its copy: %s", pid, out.get("error"))
    except Exception:  # noqa: BLE001
        _log.warning("Plugin %s: binding its folders failed", pid, exc_info=True)
    try:
        from backend.store.repos import folder_files

        return folder_files.reload_epoch(pid)
    except Exception:  # noqa: BLE001 — without an epoch it is never reloaded for a move
        _log.debug("Plugin %s: no reload epoch", pid, exc_info=True)
        return None


def bind_folders_after_engine() -> None:
    """The team-sync engine arrived (Ducky Account registered): plugins that loaded before
    it get their declared folders bound now, and a reload epoch to follow moves by."""
    for pid in list(_LOADED_SIGNATURE):
        if pid in _FOLDER_EPOCH:
            continue
        epoch = _bind_folders(pid)
        if epoch is not None:
            _FOLDER_EPOCH[pid] = epoch


def _folder_reload_lock(pid: str) -> threading.Lock:
    with _FOLDER_RELOAD_GUARD:
        return _FOLDER_RELOAD_LOCKS.setdefault(pid, threading.Lock())


def _folders_moved(pid: str) -> bool:
    """The plugin's folders moved (its reload epoch) since this process loaded it."""
    loaded = _FOLDER_EPOCH.get(pid)
    if loaded is None:
        return False
    from backend.store.repos import folder_files

    return folder_files.reload_epoch(pid) != loaded


def reload_if_folders_moved(pid: str) -> bool:
    """Before a tool call here: when the plugin's folders moved since this process loaded
    it, unload and load it again now (its caches belong to the old files). Returns
    whether its tools were registered anew (the call must run on the new ones). Raises
    with the reason when it can't load again."""
    if not _folders_moved(pid):
        return False
    with _folder_reload_lock(pid):
        if pid in _FOLDER_EPOCH and not _folders_moved(pid):
            return True  # another call reloaded it meanwhile
        from backend.uefn_plugins.store import load_plugin_manifest, plugin_dir

        label = pid
        try:
            invalidate_plugin_runtime(pid)
            manifest = load_plugin_manifest(pid)
            root = plugin_dir(pid)
            if manifest is None or not root.is_dir() or not is_plugin_enabled(pid):
                raise RuntimeError("it is no longer installed and on")
            label = str(manifest.get("label") or pid)
            _load_one(pid, root, manifest)
            if pid not in _REGISTERED:
                raise RuntimeError(_LOAD_FAILURES.get(pid) or "its register() didn't finish")
        except Exception as exc:  # noqa: BLE001 — the call says why instead of running stale code
            _record_plugin_load_error(pid, exc)
            raise RuntimeError(f"{label} couldn't restart after its files changed: {exc}") from exc
    _log.info("Plugin %s reloaded here: its folders moved", pid)
    return True


def reload_moved_plugins(now: float | None = None) -> list[str]:
    """The app process (each scheduler tick): a plugin loaded here whose folders moved (a
    pull, a switch or a project change made in any process) restarts once its epoch has
    stayed moved for :data:`FOLDER_RELOAD_GRACE_S` (a switch restarts it itself first)
    and none of its calls is running in any process; its panels then reload. Returns the
    plugins restarting."""
    if not _FOLDER_EPOCH:
        return []
    from backend.store.repos import folder_files
    from backend.uefn_plugins import team_folders

    now = time.time() if now is None else now
    out: list[str] = []
    for pid, loaded in list(_FOLDER_EPOCH.items()):
        epoch = folder_files.reload_epoch(pid)
        if epoch == loaded:
            _FOLDER_MOVED.pop(pid, None)
            continue
        seen = _FOLDER_MOVED.get(pid)
        if seen is None or seen[0] != epoch:
            _FOLDER_MOVED[pid] = (epoch, now)
            continue
        if now - seen[1] < FOLDER_RELOAD_GRACE_S or team_folders.live_calls(pid):
            continue
        _FOLDER_MOVED.pop(pid, None)
        _FOLDER_EPOCH.pop(pid, None)  # one restart; the load records the epoch again
        out.append(pid)
        threading.Thread(target=_restart_moved, args=(pid,), name=f"uefn-plugin-folders-moved-{pid}",
                         daemon=True).start()
    return out


def _restart_moved(pid: str) -> None:
    # Live, a teammate's pull restarts only the backend: the panels keep what is open and
    # merge it like any sync (backend.uefn_plugins.live); they reload once Live ends.
    live_pull = False
    try:
        from backend.store.repos import folder_files
        from backend.uefn_plugins import live

        live_pull = folder_files.reload_reason(pid) == "pull" and live.keeps_panels(pid)
    except Exception:  # noqa: BLE001 — a full restart
        _log.debug("live check of %s failed", pid, exc_info=True)
    try:
        reload_single_plugin(pid)
    except Exception:  # noqa: BLE001 — its next tool call reloads it
        _log.warning("Plugin %s: restarting after its folders moved failed", pid, exc_info=True)
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        if live_pull:
            from backend.uefn_plugins import live

            live.panels_kept(pid)
            push_ui_event({"type": "plugin_scope_changed", "plugins": [pid], "synced": True, "live": True})
        else:
            push_ui_event({"type": "plugin_scope_changed", "plugins": [pid], "switched": True})
    except Exception:  # noqa: BLE001 — its panels read the new files on their next open
        _log.debug("telling %s's panels about its restart failed", pid, exc_info=True)


# Crash guard: a plugin whose load takes the whole process down (a compiled backend
# crashing in native code) would otherwise do it again on every launch, and Ducky
# could never open. Each load leaves a marker named after this process; a marker
# whose process is gone means that load crashed Ducky, and that plugin version is
# skipped until it updates or the user turns it on again.
_LOAD_MARKERS = ".loading"
_CRASHED_FILE = "crashed.json"
_crash_lock = threading.Lock()
_crash_checked = False


def _markers_dir() -> Path:
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir

    return appdata_uefn_plugins_dir() / _LOAD_MARKERS


def _process_alive(os_pid: int) -> bool:
    try:
        from frontend.open_files import panel_process_alive

        return panel_process_alive(os_pid)
    except Exception:
        return True  # unknown: never blame a plugin on a guess


def _read_crashed() -> dict[str, str]:
    try:
        data = json.loads((_markers_dir() / _CRASHED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_crashed(crashed: dict[str, str]) -> None:
    path = _markers_dir() / _CRASHED_FILE
    try:
        if not crashed:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(crashed), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        _log.debug("could not save plugin crash record", exc_info=True)


def _promote_dead_markers() -> dict[str, str]:
    """Turn markers left by a process that died mid-load into crash records (once per process)."""
    global _crash_checked
    with _crash_lock:
        crashed = _read_crashed()
        if _crash_checked:
            return crashed
        _crash_checked = True
        folder = _markers_dir()
        if not folder.is_dir():
            return crashed
        changed = False
        for marker in folder.glob("*.json"):
            if marker.name == _CRASHED_FILE:
                continue
            try:
                info = json.loads(marker.read_text(encoding="utf-8"))
                owner = int(info.get("process") or 0)
            except (OSError, ValueError, TypeError, AttributeError):
                owner, info = 0, {}
            if owner and owner != os.getpid() and _process_alive(owner):
                continue  # another live Ducky process (the MCP bridge) is loading it now
            plugin = str(info.get("plugin") or "")
            if plugin:
                crashed[plugin] = str(info.get("version") or "")
                changed = True
                _log.warning("Plugin %s %s was loading when Ducky closed; skipping it", plugin, crashed[plugin])
            try:
                marker.unlink()
            except OSError:
                pass
        if changed:
            _write_crashed(crashed)
        return crashed


def plugin_crashed_ducky(pid: str, version: str) -> bool:
    """True when this exact plugin version took Ducky down while loading last time."""
    crashed = _promote_dead_markers()
    return pid in crashed and crashed[pid] == version


def clear_plugin_crash(pid: str) -> None:
    """The user turned the plugin on again (or it updated): let it load next time."""
    with _crash_lock:
        crashed = _read_crashed()
        if crashed.pop(pid, None) is not None:
            _write_crashed(crashed)


def heal_crashed_plugins() -> list[str]:
    """Update each plugin skipped for crashing Ducky when the Store has a newer version.

    The person can't be expected to find the Update button: the plugin that broke may be
    the one they sign in with, and the fix is usually already on the Store. The update
    installs and loads in this session (the Store's update path reloads the plugin).
    Returns the plugin ids that were updated.
    """
    crashed = _read_crashed()
    if not crashed:
        return []
    from backend.uefn_plugins.plugin_version import plugin_version_newer
    from backend.uefn_plugins.store import load_plugin_manifest

    still_skipped: dict[str, str] = {}
    for pid, bad in crashed.items():
        installed = str((load_plugin_manifest(pid) or {}).get("version") or "")
        if installed and installed == bad:
            still_skipped[pid] = bad
        else:
            clear_plugin_crash(pid)  # updated or removed since: the record is stale
    if not still_skipped:
        return []
    try:
        from frontend.duckyos_account import store_catalog, store_download_and_install

        catalog = store_catalog()
    except Exception:
        _log.debug("crashed-plugin update: Store catalog unavailable", exc_info=True)
        return []
    latest = {
        str(item.get("slug") or ""): str(item.get("latest_version") or "")
        for item in catalog.get("items") or []
        if isinstance(item, dict)
    }
    healed: list[str] = []
    for pid, bad in still_skipped.items():
        newer = latest.get(pid, "")
        if not newer or not plugin_version_newer(newer, bad):
            continue
        try:
            result = store_download_and_install(pid, replace=True, is_update=True)
        except Exception:
            _log.warning("Could not update %s after it crashed Ducky", pid, exc_info=True)
            continue
        if not (isinstance(result, dict) and result.get("ok")):
            _log.warning("Could not update %s after it crashed Ducky: %s", pid, (result or {}).get("error"))
            continue
        clear_plugin_crash(pid)
        healed.append(pid)
        _log.warning("Plugin %s %s crashed Ducky; updated it to %s", pid, bad, newer)
        try:
            from frontend.error_log import record_activity

            record_activity("plugins", f"{pid} {bad} closed Ducky while loading; updated it to {newer}")
        except Exception:
            pass
    if healed:
        _notify_uefn_plugins_changed()
    return healed


def _start_crash_heal() -> None:
    """Fetch fixed versions of crashed plugins off the boot path (the app window owns it)."""
    if not is_app() or not _read_crashed():
        return
    threading.Thread(target=heal_crashed_plugins, name="uefn-plugin-crash-heal", daemon=True).start()


def _load_marker_begin(pid: str, version: str) -> Path | None:
    path = _markers_dir() / f"{pid}.{os.getpid()}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"plugin": pid, "version": version, "process": os.getpid()}), encoding="utf-8")
        return path
    except OSError:
        return None


def _load_marker_end(path: Path | None) -> None:
    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _manifest_is_compiled(manifest: dict[str, Any]) -> bool:
    """True when plugin.json ships a compiled backend instead of .py source."""
    if bool(manifest.get("compiled")):
        return True
    if str(manifest.get("python_abi") or "").strip():
        return True
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    return bool(str(backend.get("compiled") or "").strip())


def _compiled_backend_pyd(root: Path, manifest: dict[str, Any], mod_name: str) -> Path | None:
    """Locate the compiled backend .pyd at the plugin root.

    Prefers the exact ``backend.compiled`` path from plugin.json; falls back to the
    newest ``<mod_name>*.pyd`` for this ABI so a rename or missing field still loads.
    """
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    rel = str(backend.get("compiled") or "").strip()
    if rel:
        p = root / rel
        if p.is_file():
            return p
    matches = sorted(
        (p for p in root.glob(f"{mod_name}*.pyd") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    return matches[-1] if matches else None


def _pyd_identity(pyd: Path) -> str:
    """Path + mtime: a newer build can land under the old file name."""
    try:
        return f"{pyd}|{pyd.stat().st_mtime_ns}"
    except OSError:
        return str(pyd)


def _import_compiled_backend(pid: str, root: Path, manifest: dict[str, Any]) -> Any:
    """Load a plugin's Nuitka-compiled backend extension module.

    The .pyd is loaded under ``uefn_plugin_<id>`` from the plugin root, so its
    ``__file__``-relative data paths resolve to the plugin folder exactly as the
    source package did. An ABI it was not built for is refused with a plain message.
    """
    from backend.uefn_plugins.store import plugin_dir

    module_name = backend_module_name(pid)
    loaded = _COMPILED_LOADED.get(module_name)
    if loaded is not None:
        mod, loaded_pyd = loaded
        pyd = _compiled_backend_pyd(root, manifest, module_name)
        if pyd is not None and _pyd_identity(pyd) != loaded_pyd:
            label = str(manifest.get("label") or pid)
            _RESTART_PENDING[pid] = f"Restart UEFN Ducky to finish updating {label}"
        sys.modules[module_name] = mod
        return mod
    if module_name in sys.modules:
        return sys.modules[module_name]

    abi = str(manifest.get("python_abi") or "").strip()
    if abi and abi != PY_ABI:
        raise RuntimeError(
            f"Update this plugin: it was built for {abi}, this Ducky runs {PY_ABI}."
        )

    # A newer version's .pyd may still be waiting in quarantine after a disable/update
    # while the old copy was mapped — restore it so the enabled plugin can load.
    _restore_compiled_from_quarantine(pid, plugin_dir(pid))
    pyd = _compiled_backend_pyd(root, manifest, module_name)
    if pyd is None:
        raise RuntimeError(f"compiled backend .pyd not found for {pid}")

    from importlib.machinery import ExtensionFileLoader

    loader = ExtensionFileLoader(module_name, str(pyd))
    spec = importlib.util.spec_from_file_location(module_name, str(pyd), loader=loader)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load compiled backend for {pid}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    _COMPILED_LOADED[module_name] = (mod, _pyd_identity(pyd))  # before exec: a failed init is still mapped
    spec.loader.exec_module(mod)
    return mod


def _import_backend(pid: str, root: Path, entry: str) -> Any:
    """Import plugin backend package from AppData (or plain module file)."""
    entry_path = root / entry
    module_name = backend_module_name(pid)
    if module_name in sys.modules:
        return sys.modules[module_name]

    if entry_path.is_dir() and (entry_path / "__init__.py").is_file():
        init_py = entry_path / "__init__.py"
        spec = importlib.util.spec_from_file_location(
            module_name,
            init_py,
            submodule_search_locations=[str(entry_path)],
        )
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod
        # Submodules resolve as uefn_plugin_<pid>.tools etc.
        spec.loader.exec_module(mod)
        return mod

    py_file = entry_path if entry_path.suffix == ".py" else Path(str(entry_path) + ".py")
    if not py_file.is_file():
        py_file = root / "backend" / "register.py"
    if not py_file.is_file():
        _log.warning("Plugin %s: backend entry not found (%s)", pid, entry)
        return None
    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _compiled_quarantine_dir() -> Path:
    return appdata_uefn_plugins_dir() / _COMPILED_QUARANTINE


def release_locked_compiled_backends(plugin_id: str, *, attempts: int = 12) -> list[Path]:
    """Move a plugin's compiled .pyd(s) out of its folder so the Store can overwrite it.

    A loaded extension stays mapped in-process and Windows locks the file, so the
    folder cannot be deleted in place. Renaming the file still works, so the old .pyd
    is moved into a quarantine folder; the next version installs under a new name and
    loads fresh, and the quarantined file is swept once Ducky restarts and unlocks it.
    Antivirus often holds a freshly loaded .pyd open for a moment, so a refused move is
    retried. Returns the .pyd files that still could not be moved (the caller must not
    wipe the folder then). Never raises into the Store install path.
    """
    import uuid

    from backend.uefn_plugins.store import _is_win_lock_error, normalize_plugin_id, plugin_dir

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return []
    root = plugin_dir(pid)
    if not root.is_dir():
        return []
    mod_name = backend_module_name(pid)
    quarantine = _compiled_quarantine_dir() / pid
    stuck: list[Path] = []
    for pyd in list(root.glob(f"{mod_name}*.pyd")):
        for i in range(max(1, attempts)):
            try:
                quarantine.mkdir(parents=True, exist_ok=True)
                # Prefix keeps the original name recoverable for restore; .bin so a swept
                # leftover is never mistaken for an installed module.
                dest = quarantine / f"{uuid.uuid4().hex}__{pyd.name}.bin"
                pyd.replace(dest)
                break
            except OSError as exc:
                if not _is_win_lock_error(exc) or i == attempts - 1:
                    _log.warning("Could not move compiled backend %s aside: %s", pyd, exc)
                    stuck.append(pyd)
                    break
                time.sleep(0.05 * (2 ** min(i, 5)))
    return stuck


def _restore_compiled_from_quarantine(pid: str, root: Path) -> None:
    """Recover a quarantined .pyd when the plugin folder has none. Best-effort.

    An install parks the mapped old .pyd in quarantine before overwriting the folder.
    If that overwrite was interrupted and no new .pyd was written, this brings the old
    one back so the plugin still loads. A completed install already wrote a new .pyd at
    the root, so this is a no-op then.
    """
    try:
        mod_name = backend_module_name(pid)
        if any(root.glob(f"{mod_name}*.pyd")):
            return
        quarantine = _compiled_quarantine_dir() / pid
        if not quarantine.is_dir():
            return
        parked = sorted(
            (p for p in quarantine.glob(f"*__{mod_name}*.pyd.bin") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        if not parked:
            return
        newest = parked[-1]
        # Recover the original file name (strip the "<uuid>__" prefix and ".bin").
        original = newest.name.split("__", 1)[1][: -len(".bin")]
        newest.replace(root / original)
    except OSError:
        _log.debug("Could not restore quarantined backend for %s", pid, exc_info=True)


def sweep_compiled_quarantine() -> None:
    """Delete quarantined .pyd files that have unlocked (previous run exited). Best-effort."""
    root = _compiled_quarantine_dir()
    if not root.is_dir():
        return
    for child in list(root.glob("*/*.pyd.bin")):
        try:
            child.unlink()
        except OSError:
            pass  # still mapped by a live process — try again next launch
    for pdir in list(root.glob("*")):
        try:
            if pdir.is_dir() and not any(pdir.iterdir()):
                pdir.rmdir()
        except OSError:
            pass


_ORPHAN_PYD_MIN_AGE_S = 600.0


def sweep_orphan_compiled_folders() -> None:
    """Delete plugin folders that hold nothing but an old compiled .pyd. Best-effort.

    An uninstall before the quarantine move existed deleted everything except the
    .pyd Windows still had locked, leaving a folder with no plugin.json that nothing
    lists or cleans. Only folders made up entirely of ``uefn_plugin_*.pyd`` files at
    least 10 minutes old go, so an install that is still unpacking is never touched.
    """
    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        return
    now = time.time()
    for pdir in list(root.iterdir()):
        try:
            if not pdir.is_dir() or pdir.name.startswith(".") or (pdir / "plugin.json").exists():
                continue
            files = list(pdir.iterdir())
            if not files or not all(
                f.is_file()
                and f.name.startswith("uefn_plugin_")
                and f.suffix == ".pyd"
                and now - f.stat().st_mtime >= _ORPHAN_PYD_MIN_AGE_S
                for f in files
            ):
                continue
            for f in files:
                f.unlink()
            pdir.rmdir()
        except OSError:
            pass  # still mapped by a live process — try again next launch


class _ChangesetApi:
    """Plug a desktop-plugin mutation into the Changes ledger.

    Two ways to record, same shape: return ``_ducky`` on a tool result, or call
    ``api.changeset.record(...)`` for a batch that is not one MCP call.
    """

    def __init__(self, api: "_PluginApi") -> None:
        self._api = api

    def record(
        self,
        *,
        command: str,
        kind: str,
        ident: str,
        facet: str = "",
        before: Any = None,
        inverse: list[dict[str, Any]] | None = None,
        created: list[dict[str, Any]] | None = None,
        targets: list[dict[str, Any]] | None = None,
        summary: str = "",
        revertable: str = "auto",
        reason: str = "",
        params: dict[str, Any] | None = None,
        slot: str = "",
    ) -> None:
        """Record one mutation. Never raises; never fails the tool."""
        try:
            from backend.workspace.editor_ops import slot_path
            from backend.workspace.editor_record import record as record_change

            prog = self._api.plugin_id
            sidecar = {
                "program": prog,
                "kind": kind,
                "facet": facet,
                "slot": slot or slot_path(kind, ident, facet, program=prog),
                "before": before,
                "inverse": list(inverse or []),
                "created": list(created or []),
                "targets": list(targets or [{"kind": kind, "id": ident, "label": ident, "path": ident}]),
                "revertable": revertable,
                "reason": reason,
                "summary": summary or command.replace("_", " "),
            }
            record_change(command, params or {}, {"_ducky": sidecar}, ok=True)
        except Exception:
            pass


class _CryptoApi:
    """AES-256-GCM from the app's own cryptography, so a plugin that seals data never
    depends on which modules the EXE happens to bundle (the shared-spaces provider died
    on every team change when 1.2.392 shipped without ``ciphers.aead``)."""

    @staticmethod
    def aead_seal(key: bytes, nonce: bytes, data: bytes, aad: bytes | None = None) -> bytes:
        """Ciphertext plus its 16-byte tag. ``key`` is 32 bytes, ``nonce`` 12."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(bytes(key)).encrypt(bytes(nonce), bytes(data), None if aad is None else bytes(aad))

    @staticmethod
    def aead_open(key: bytes, nonce: bytes, data: bytes, aad: bytes | None = None) -> bytes:
        """The plaintext. Raises cryptography's ``InvalidTag`` when the data, key, nonce or
        ``aad`` don't match, the same as importing cryptography directly."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(bytes(key)).decrypt(bytes(nonce), bytes(data), None if aad is None else bytes(aad))


class _PluginApi:
    """Narrow host surface passed to plugin register(api)."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def is_app(self) -> bool:
        """True in the app window's process. ``register()`` also runs in every agent host and
        tool server, so start threads, timers, servers and ports only ``if api.is_app()``."""
        return is_app()

    def log(self, message: str) -> None:
        """One line in Settings → Logs under this plugin (personal data is scrubbed out)."""
        _log.info("[%s] %s", self.plugin_id, message)
        try:
            from frontend.error_log import record_activity

            record_activity(self.plugin_id, message)
        except Exception:
            pass

    def is_enabled(self) -> bool:
        """True when this plugin is enabled in Settings → Store."""
        return is_plugin_enabled(self.plugin_id)

    def listener(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send one command to the live UEFN listener (sequencer / editor tools)."""
        from backend.bridge import REQUEST_TIMEOUT, send_command

        return send_command(
            command,
            params or {},
            timeout=REQUEST_TIMEOUT if timeout is None else float(timeout),
        )

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Call any Ducky MCP tool in-process, under the same approval rules the agent gets.

        Routes through the shared dispatcher (``execute_tool``), so a destructive or
        outward tool still shows its Allow/Deny card and a plan-mode mutator is still
        refused. Returns the tool's parsed result (dict/list when it is JSON, else the
        raw string) and raises ``ValueError`` on failure. Safe from a plugin's sync code.
        """
        import asyncio

        from backend.agent.tools import execute_tool

        tool_name = (name or "").strip()
        if not tool_name:
            raise ValueError("call_tool requires a tool name")
        args = arguments if isinstance(arguments, dict) else {}

        async def _run() -> Any:
            return await execute_tool(tool_name, args)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            result = asyncio.run(_run())
        else:
            # A loop is already running on this thread — run on a helper thread so we
            # never nest event loops.
            box: dict[str, Any] = {}

            def _worker() -> None:
                try:
                    box["result"] = asyncio.run(_run())
                except BaseException as exc:  # noqa: BLE001 — surfaced below
                    box["error"] = exc

            t = threading.Thread(target=_worker, name=f"plugin-call-tool-{self.plugin_id}", daemon=True)
            t.start()
            t.join()
            if "error" in box:
                raise box["error"]
            result = box.get("result")

        if getattr(result, "ok", False):
            data = getattr(result, "data", None)
            if isinstance(data, str):
                text = data.strip()
                if text.startswith(("{", "[")):
                    try:
                        return json.loads(text)
                    except ValueError:
                        return data
            return data
        raise ValueError(getattr(result, "error", None) or f"tool failed: {tool_name}")

    @property
    def changeset(self) -> "_ChangesetApi":
        """Record a mutation in the Changes ledger so Revert can unwind it."""
        return _ChangesetApi(self)

    @property
    def crypto(self) -> "_CryptoApi":
        """``aead_seal(key, nonce, data, aad)`` / ``aead_open(...)``: AES-256-GCM from the app."""
        return _CryptoApi()

    def local_dir(self, name: str = "") -> str:
        """A plain folder on this PC for files the plugin can rebuild: media proxies, thumbnails,
        caches. Not synced and not encrypted (``data`` is for the plugin's real records); play a
        file from it with ``media_url(path)``. Erased with the plugin's data."""
        from backend.uefn_plugins.panel_presence import plugin_local_dir

        return str(plugin_local_dir(self.plugin_id, name))

    def media_url(self, path: str) -> str:
        """A seekable link (HTTP Range) to a local media file, for this plugin's panel to play:
        an original the person picked with ``files.pick`` or a proxy the plugin made.
        '' when the file type can't be played in the panel. Loopback only: not on a phone."""
        from backend.automations.files import media_url

        return media_url(path)

    def panel_state(self) -> dict[str, Any]:
        """Whether this plugin's panel is open and on screen: ``{open, visible, since, panels}``.
        Panel code runs only while its tab is the visible one; a tool that needs it can say so."""
        from backend.uefn_plugins.panel_presence import panel_state

        return panel_state(self.plugin_id)

    @property
    def live(self) -> Any:
        """Live editing on a team copy: ``join(room)``, ``leave()``, ``send(topic, data,
        to="")``, ``on(topic, callback)``, ``state()``. Changes made through ``api.data`` go
        out with sync, and the group pulls each one as soon as it's committed. See
        ``backend.uefn_plugins.live``."""
        from backend.uefn_plugins.live import LiveApi

        return LiveApi(self.plugin_id)

    @property
    def data(self) -> Any:
        """Host data service: docs (one JSON doc per entity) and files in the active
        scope. Never pick your own folder: rows and files are per account and scope,
        and shared scopes sync. See ``backend.uefn_plugins.scopes.PluginData``."""
        from backend.uefn_plugins.scopes import PluginData

        _DATA_PLUGINS.add(self.plugin_id)
        return PluginData(self.plugin_id)

    @property
    def storage(self) -> Any:
        """The account's General storage, shared by the person and every plugin: ``put_file(name,
        bytes, folder="")``, ``put(name, value, folder="")`` (a note or any JSON), ``items(folder,
        query)``, ``get(id, with_data=False)``, ``find(name, folder)``, ``delete(id)``, ``where()``.
        Synced with Your team like plugin data; what a plugin keeps here stays when it goes. See
        ``backend.uefn_plugins.general_storage``."""
        from backend.uefn_plugins.general_storage import StorageApi

        return StorageApi(self.plugin_id)

    @property
    def search(self) -> Any:
        """Make this plugin's own records findable by meaning and keywords: ``upsert(id, title,
        text, meta)``, ``delete(id)``, ``clear()``, ``query(text, limit)``. Only this plugin's
        records in its active data scope; removed on uninstall. Expose them to agents through
        one of the plugin's own tools. See ``backend.search.records.SearchApi``."""
        from backend.search.records import SearchApi

        return SearchApi(self.plugin_id)

    def http_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float = 60,
    ) -> Any:
        """JSON HTTP call (stdlib urllib). Vendor URLs and status enums stay in the plugin."""
        from backend.util.http import http_json as _http_json

        return _http_json(
            method,
            url,
            headers=headers,
            json_body=json_body,
            timeout=timeout,
        )

    def poll(
        self,
        get_status: Callable[..., Any],
        *,
        done: Callable[..., bool],
        failed: Callable[..., bool],
        interval: float = 5,
        max_attempts: int = 120,
    ) -> Any:
        """Poll get_status until done/failed. Raises TimeoutError."""
        from backend.util.http import poll as _poll

        return _poll(
            get_status,
            done=done,
            failed=failed,
            interval=interval,
            max_attempts=max_attempts,
        )

    def encode_image(self, path: str) -> str:
        """Local image path → data URI."""
        from backend.util.http import encode_image as _encode_image

        return _encode_image(path)

    def resolve_image(self, image: str) -> str:
        """Pass through http(s)/data URIs; encode local paths."""
        from backend.util.http import resolve_image as _resolve_image

        return _resolve_image(image)

    def chat_reference(
        self,
        *,
        trigger: str,
        id: str,
        label: str,
        href: str,
        group: str = "",
        description: str = "",
    ) -> bool:
        """Add one @ or / row. A bad href is dropped. Same idea as ``tool()``."""
        entry = normalize_chat_reference(
            {
                "trigger": trigger,
                "id": id,
                "label": label,
                "href": href,
                "group": group,
                "description": description,
            },
            self.plugin_id,
        )
        if not entry:
            return False
        with _LOCK:
            _CONTRIBUTIONS["chat_references"].append(entry)
        return True

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        intent: str | None = None,
        listener: bool = False,
    ) -> Any:
        """Register an MCP tool on the app's shared FastMCP server.

        Auto-gates on plugin enable + per-chat opt-in. Tool names are tracked for
        intent unlock and the Settings → MCPs tab — no manifest listing required.

        Pass ``listener=True`` only when the tool cannot start without the UEFN editor open:
        it is then refused while UEFN is closed. Every other tool runs whatever UEFN's state
        (a call that does reach the editor still fails fast on its own).
        """

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = (name or getattr(func, "__name__", "") or "").strip()
            if not tool_name:
                raise ValueError("api.tool requires a function name or name=")

            from backend.server import mcp

            pid = self.plugin_id
            existing = mcp._tool_manager.get_tool(tool_name)
            if existing is not None:
                owner = _PLUGIN_TOOL_OWNER.get(tool_name)
                # Another plugin already owns this name — refuse.
                if owner is not None and owner != pid:
                    _log.error(
                        "[%s] tool name collision: %s — skipping registration",
                        self.plugin_id,
                        tool_name,
                    )
                    return func
                # Same plugin after reload_plugins() (MCP entry survives invalidate):
                # re-track ownership without double-registering on FastMCP.
                _record_plugin_api_tool(pid, tool_name, intent=intent, listener=listener)
                _log.debug("[%s] MCP tool already registered: %s", pid, tool_name)
                return func

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                if not uefn_agent_tools_allowed(pid):
                    raise ValueError(
                        f"Plugin '{pid}' tools are disabled — enable it in Settings → Store"
                    )
                blocked = sensitive_tool_block_reason(tool_name)
                if blocked:
                    raise ValueError(blocked)
                if pid in _DATA_PLUGINS:
                    try:  # a sync in the app process changed its data: its on_change hears it first
                        from backend.uefn_plugins.scopes import deliver_data_changes

                        deliver_data_changes(pid)
                    except Exception:  # noqa: BLE001 — the tool runs anyway
                        _log.debug("delivering data changes to %s failed", pid, exc_info=True)
                from backend.uefn_plugins.scopes import caller, write_group

                # One tool call is one change to a shared copy, made by the calling agent
                # (an MCP client of the bridge is an agent even without a ducky's name).
                actor, ducky = caller("user" if is_app() else "agent")
                with plugin_tool_slot(pid), write_group(pid, actor=actor, ducky=ducky, message=tool_name):
                    # A plugin that keeps declared folders: checked once its call marker is down
                    # (a move of its folders either waited for this call or finished first).
                    fresh = _folder_plugin_call(pid, tool_name, wrapper) if pid in _FOLDER_EPOCH else None
                    if fresh is not None:  # reloaded here: the call runs on the new plugin
                        return fresh(*args, **kwargs)
                    result = func(*args, **kwargs)
                try:
                    # A picture made in a chat shows inline in that turn's reply.
                    from frontend.ui_web.generated_images import note_tool_images

                    note_tool_images(tool_name, result)
                except Exception:
                    _log.debug("could not keep %s pictures for the chat", tool_name, exc_info=True)
                # Plugins on the host data service: say which copy the tool read or changed.
                # ponytail: dict results only; string results would need parsing.
                if pid in _DATA_PLUGINS and isinstance(result, dict) and "scope" not in result:
                    from backend.uefn_plugins.scopes import active_scope, scope_name

                    result = {**result, "scope": scope_name(active_scope(pid))}
                return _with_panel_note(pid, result)

            mcp.tool(name=tool_name)(wrapper)
            _record_plugin_api_tool(pid, tool_name, intent=intent, listener=listener)
            _log.debug("[%s] MCP tool registered: %s", pid, tool_name)
            return wrapper

        if fn is not None:
            return decorator(fn)
        return decorator

    def register_tts(self, synthesize_fn: Any) -> None:
        """Register ``synthesize(text, voice_id) -> {audio_base64, mime}`` for this plugin."""
        register_tts_synthesizer(self.plugin_id, synthesize_fn)
        self.log("TTS synthesizer registered")

    def register_media_generator(self, kind: str, fn: Any, describe: dict[str, Any] | None = None) -> None:
        """Register a ``kind`` (image | image_edit | video | music) maker: ``fn(prompt, **options)``.

        ``describe`` = ``{label, secret_key?}``; it is listed by ``media_generators(kind)``
        only while ``secret_key`` has a saved credential.
        """
        register_media_generator(self.plugin_id, kind, fn, describe)
        self.log(f"{kind} generator registered")

    def asset_pack_path(self, pack_id: str) -> str | None:
        """Folder of this plugin's unpacked asset pack ``pack_id`` (plugin.json
        ``asset_packs``), or None until it has downloaded, passed its sha256 and unpacked."""
        from backend.uefn_plugins.asset_packs import asset_pack_path

        path = asset_pack_path(self.plugin_id, pack_id)
        return str(path) if path else None

    def asset_packs(self) -> list[dict[str, Any]]:
        """Progress of this plugin's asset packs for its panel: one
        ``{id, bytes, state, done, total, error, path?}`` per pack this PC needs."""
        from backend.uefn_plugins.asset_packs import status

        return status(self.plugin_id)

    def ensure_asset_packs(self) -> bool:
        """Fetch missing packs from the Store in the background (resuming a partial
        download). False when none are missing or a fetch is already running."""
        from backend.uefn_plugins.asset_packs import start_install
        from backend.uefn_plugins.store import load_plugin_manifest

        manifest = load_plugin_manifest(self.plugin_id) or {}
        if all(row["state"] == "ready" for row in self.asset_packs()):
            return False
        return start_install(self.plugin_id, manifest, str(manifest.get("store_slug") or ""))

    def connection(self, fn: Any, *, label: str = "", program: str = "") -> None:
        """Show this plugin in the header Connections menu.

        ``fn()`` must be cheap (socket/health only). Return ``{online, detail}``
        or ``{connected, detail}``. Same row gates revert when the ledger
        program matches ``program`` (default: this plugin id).
        """
        register_connection_probe(self.plugin_id, fn, label=label, program=program)
        self.log("connection probe registered")

    def people(self, fn: Any, *, label: str = "") -> None:
        """Show who this plugin knows is around in the header's Online tab, under ``label``.

        ``fn()`` must be cheap (return cached data, never wait on the network). Return a
        list of ``{id, name, detail, online}``; online people count toward the header badge.
        """
        register_people_provider(self.plugin_id, fn, label=label)
        self.log("people provider registered")

    def register_secret_test(self, secret_key: str, test_fn: Any) -> None:
        """Register Settings → Test for a secret field (``test_fn(api_key) -> {ok, detail}``)."""
        register_secret_tester(self.plugin_id, secret_key, test_fn)
        self.log(f"Secret tester registered: {secret_key}")

    def register_automation_node(self, node_type: str, handler: Any = None) -> Any:
        """Register a handler for a contributed automations node type.

        ``api.register_automation_node("email.send", fn)`` or decorator form.
        """
        from backend.automations.plugin import register_node

        def decorator(fn: Any) -> Any:
            register_node(self.plugin_id, node_type, fn)
            _log.debug("[%s] Automation node registered: %s", self.plugin_id, node_type)
            return fn

        if handler is not None:
            return decorator(handler)
        return decorator

    def register_pipeline_node(self, node_type: str, handler: Any = None) -> Any:
        """Alias of register_automation_node — same handler table, both palettes."""
        return self.register_automation_node(node_type, handler)

    def emit_automation(self, trigger_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fire enabled workflows on this PC whose trigger matches ``trigger_id``
        (plugin API name kept from before Workflows)."""
        from backend.automations.runner import emit_trigger

        return emit_trigger(trigger_id, payload or {})

    def emit_hook(self, hook_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fire one of this plugin's ``contributes.hooks``: the sound the user put on it plays."""
        from backend.uefn_plugins.plugin_app_events import emit_hook

        return emit_hook(self.plugin_id, hook_id, payload)

    def set_appearance_profile(self, profile_id: str) -> dict[str, Any]:
        """Switch Appearance to one of this plugin's own ``appearance.profiles`` (by its id)."""
        from backend.uefn_plugins.plugin_app_events import set_appearance_profile

        return set_appearance_profile(self.plugin_id, profile_id)

    def spotlight(
        self,
        *,
        window: str = "uefn",
        box: dict[str, Any] | None = None,
        title: str = "",
        body: str = "",
        steps: list[dict[str, Any]] | None = None,
        click: bool = False,
        wait: bool = False,
    ) -> dict[str, Any]:
        """Spotlight a control in UEFN or another program. Same call as ``ducky_ui_show``.

        ``window`` is ``"uefn"``, a title pattern, or an hwnd. ``box`` is
        ``{x, y, w, h}`` as fractions of that window (0 to 1). The desktop dims
        and only the hole can be clicked. ``wait`` blocks until the user closes it
        and returns ``reason`` (``done``, ``close``, or ``esc``) and ``step``.
        """
        from backend.tools.panel.panel_ui import show

        return show(
            window=window, box=box, title=title, body=body, steps=steps, click=click, wait=wait,
        )

    def register_panel_rpc(self, name: str, fn: Any) -> None:
        """Register a PanelApi / bridge method: ``plugin_call(plugin_id, name, params)``.

        ``fn`` receives ``**params`` from the caller. Prefer returning plain dicts
        (never raise across the JS bridge unless the host should surface an error).
        """
        register_panel_rpc(self.plugin_id, name, fn)
        _log.debug("[%s] Panel RPC registered: %s", self.plugin_id, name)

    def register_tts_voices(self, list_voices_fn: Any) -> None:
        """Register ``list_voices() -> [{id, label}]`` — dynamic voices for the picker.

        Runs at runtime (e.g. fetch an account's cloned voices from a web API) and is
        merged on top of the static manifest ``tts.voices``. Keep it fast/cached — it is
        polled when a voice dropdown opens.
        """
        register_tts_voices_lister(self.plugin_id, list_voices_fn)
        self.log("TTS voices lister registered")

    def provide_spaces(self, impl: Any) -> None:
        """Provide shared spaces (a :class:`backend.uefn_plugins.spaces.Provider`): which
        spaces the account has, their sync, labels and web pages. One provider at a time,
        from a Store-signed install."""
        from backend.uefn_plugins import spaces

        spaces.register(self.plugin_id, impl)
        self.log("Shared spaces provider registered")

    def register_llm_provider(
        self,
        provider_id: str,
        factory: Any,
        *,
        fetch_models: Any = None,
        test_key_model: str = "",
        test_key: Any = None,
        tool_schema: str = "",
        key_optional: bool = False,
        normalize_secret: Any = None,
        **extra: Any,
    ) -> None:
        """Register an LLM provider factory for Settings → LLMs / make_provider."""
        register_llm_provider_factory(
            self.plugin_id,
            provider_id,
            factory,
            fetch_models=fetch_models,
            test_key_model=test_key_model,
            test_key=test_key,
            tool_schema=tool_schema,
            key_optional=key_optional,
            normalize_secret=normalize_secret,
            **extra,
        )
        self.log(f"LLM provider registered: {provider_id}")

    def register_coding_agent(
        self,
        agent_id: str,
        factory: Any,
        *,
        complete_one_shot: Any = None,
        test_key: Any = None,
        resolve_api_fallback: Any = None,
        aliases: list[str] | tuple[str, ...] | None = None,
        skills_dir: Any = None,
        native_skills: bool = False,
        thinking_env: Any = None,
        normalize_model: Any = None,
        before_launch: Any = None,
        on_needs_login: Any = None,
        settings_defaults: dict[str, Any] | None = None,
        install_help: str = "",
        supports_batch_complete: bool = False,
        **extra: Any,
    ) -> None:
        """Register a coding-agent adapter factory (detect/launch)."""
        register_coding_agent_factory(
            self.plugin_id,
            agent_id,
            factory,
            complete_one_shot=complete_one_shot,
            test_key=test_key,
            resolve_api_fallback=resolve_api_fallback,
            aliases=aliases,
            skills_dir=skills_dir,
            native_skills=native_skills,
            thinking_env=thinking_env,
            normalize_model=normalize_model,
            before_launch=before_launch,
            on_needs_login=on_needs_login,
            settings_defaults=settings_defaults,
            install_help=install_help,
            supports_batch_complete=supports_batch_complete,
            **extra,
        )
        self.log(f"Coding agent registered: {agent_id}")

    def register_ide_hookup(self, kind: str, *, label: str = "", auto_apply: bool = True) -> None:
        """Own IDE MCP + skills Apply for this gateway (Settings → LLMs detail).

        When ``auto_apply`` is True, best-effort write the UEFN MCP bridge and
        deploy skills into that IDE if its config path is reachable.
        """
        register_ide_hookup_factory(self.plugin_id, kind, label=label)
        self.log(f"IDE hookup registered: {kind}")
        if not auto_apply:
            return
        # Bridge stdio process must never rewrite mcp.json — Cursor reconnects and
        # cancels tools. Panel / ship_newest owns IDE apply.
        if not is_app():
            self.log(f"IDE auto-apply skipped ({kind}): not the app window")
            return
        from frontend.ide_apply import auto_apply_skip_reason

        reason = auto_apply_skip_reason()
        if reason:
            self.log(f"IDE auto-apply skipped ({kind}): {reason}")
            return
        # Off the plugin-load thread: config merge + skill deploy is disk-bound and
        # nothing in the UI needs it. Inline, three gateways serialized ~60s of
        # writes ahead of _LOADED, so the Store/Settings panes sat empty that long.
        _queue_ide_auto_apply(kind, self.log)


# Moved to backend.uefn_plugins.plugin_host; bound after every definition above so the
# host modules can import each other in any order. ``host.X`` still reads and patches
# every moved name (plugin_host.compat).
from backend.uefn_plugins.plugin_host import boot as _boot  # noqa: E402
from backend.uefn_plugins.plugin_host import compat as _compat  # noqa: E402
from backend.uefn_plugins.plugin_host import lifecycle as _lifecycle  # noqa: E402
from backend.uefn_plugins.plugin_host import loader as _loader  # noqa: E402
from backend.uefn_plugins.plugin_host.boot import (  # noqa: E402
    _notify_uefn_plugins_changed,
    ensure_plugins_loaded,
)
from backend.uefn_plugins.plugin_host.lifecycle import (  # noqa: E402
    _PAUSED_PRIVATE_PLUGINS,
    invalidate_plugin_runtime,
    is_plugin_enabled,
    reload_single_plugin,
    wait_plugin_toggles,
)
from backend.uefn_plugins.plugin_host.loader import (  # noqa: E402
    _load_enabled_plugin,
    _load_one,
    backend_module_name,
)

_compat.install(__name__, (_loader, _boot, _lifecycle))
