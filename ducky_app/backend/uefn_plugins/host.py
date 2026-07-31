"""Load enabled UEFN plugins: backend register() + UI contribution registry."""

from __future__ import annotations

import base64
import functools
import importlib.util
import logging
import re
import sys
import threading
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Callable

from backend.uefn_plugins.plugin_version import format_plugin_version, plugin_version_rank
from backend.uefn_plugins.store import (
    PLUGIN_MANIFEST,
    appdata_uefn_plugins_dir,
    get_enabled_plugin_ids,
    seed_uefn_plugins,
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
_LOADED = False
# True once plugin.json UI rows are applied — Settings can paint before register().
_UI_READY = False
# Single-flight first load: splash must not wait; agent paths wait on this Event.
_LOAD_DONE = threading.Event()
_LOAD_THREAD: threading.Thread | None = None
_LOAD_START_LOCK = threading.Lock()
_LOAD_CALLBACKS: list[Callable[[], None]] = []
_LOAD_CALLBACKS_LOCK = threading.Lock()
# Re-entrancy guard for _repair_missing_backends (per thread; _LOCK is an RLock).
_REPAIR_STATE = threading.local()
# Single-flight "scan missing backends" coordinator — never runs register() under _LOCK.
_REPAIR_THREAD: threading.Thread | None = None
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
    "ui_panels": [],
    "shell_boots": [],  # {plugin_id, entry}
    "appearance_profiles": [],  # {id, name, plugin_id, foundation?, overrides?, status_overrides?}
    "appearance_css": [],  # {plugin_id, entry}
    "appearance_effects": [],  # {id, label, plugin_id, entry}
    "appearance_skins": [],  # {id, label, plugin_id, entry, css?}
    "sounds": [],  # {id, label, file, plugin_id}
    "hooks": [],  # {id, label, plugin_id}
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
}
# plugin_id -> synthesize(text, voice_id) -> {ok?, audio_base64, mime}
_TTS_SYNTHESIZERS: dict[str, Any] = {}
# plugin_id -> list_voices() -> [{id, label}]  (dynamic voices fetched at runtime,
# e.g. an account's cloned voices — on top of the static manifest tts.voices).
_TTS_VOICE_LISTERS: dict[str, Any] = {}
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
# api.tool(listener=False) — host-cache / non-editor tools (skip UEFN listener gate).
_PLUGIN_HOST_ONLY_TOOLS: set[str] = set()
# secret_key → {plugin_id, test_fn} — Settings "Test" for plugin API keys (e.g. Meshy).
_SECRET_TESTERS: dict[str, dict[str, Any]] = {}
# plugin_id → {method_name → callable} — PanelApi.plugin_call / bridge plugin.call
_PANEL_RPC: dict[str, dict[str, Any]] = {}

# Per-run allowlist for UEFN app-plugin tools.
# None = follow Store enable; [] = none; non-empty = those plugin ids only.
_ACTIVE_UEFN_AGENT_IDS: ContextVar[tuple[str, ...] | None] = ContextVar(
    "uefn_ducky_active_uefn_agent_plugins", default=None
)


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        import json

        data = json.loads((path / PLUGIN_MANIFEST).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def is_plugin_enabled(plugin_id: str) -> bool:
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return False
    return pid in get_enabled_plugin_ids() and (appdata_uefn_plugins_dir() / pid / PLUGIN_MANIFEST).is_file()


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


def get_ui_contributions() -> dict[str, Any]:
    """Snapshot contrib registry for Settings UI — does not wait on register()."""
    enabled = list(get_enabled_plugin_ids())
    enabled_set = set(enabled)
    with _LOCK:
        return {
            "settings_tabs": _dedupe_contrib_rows(_CONTRIBUTIONS["settings_tabs"]),
            "settings_sections": _dedupe_contrib_rows(_CONTRIBUTIONS["settings_sections"]),
            "dock_panels": _dedupe_contrib_rows(_CONTRIBUTIONS["dock_panels"]),
            "editor_kinds": _dedupe_contrib_rows(
                _CONTRIBUTIONS["editor_kinds"], key_fields=("kind",)
            ),
            "header_buttons": _dedupe_contrib_rows(_CONTRIBUTIONS["header_buttons"]),
            "ui_panels": _dedupe_contrib_rows(_CONTRIBUTIONS["ui_panels"]),
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
                        "shows_thinking_effort": bool(
                            (_LLM_PROVIDER_FACTORIES.get(str(row.get("id") or "")) or {}).get(
                                "shows_thinking_effort"
                            )
                        ),
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
    """Return registration for a provider id (may be disabled — caller gates)."""
    provid = str(provider_id or "").strip().lower()
    if not provid:
        return None
    with _LOCK:
        row = _LLM_PROVIDER_FACTORIES.get(provid)
        return dict(row) if row else None


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


def invalidate_plugin_runtime(plugin_id: str, *, unload_timeout: float = 5.0) -> None:
    """Drop register()-side runtime for a plugin so the next load re-runs register().

    Call before Store install/update replaces AppData files. Keeps TTS/LLM factories
    from pointing at stale module objects after a zip overwrite.

    ``unload()`` runs on a helper thread and is joined with ``unload_timeout`` so a
    misbehaving plugin can never hang the Store bridge / uninstall path.
    """
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return
    mod_name = f"uefn_plugin_{pid}"
    # Let the plugin stop background threads before modules are dropped.
    mod = sys.modules.get(mod_name)
    if mod is not None:
        unload = getattr(mod, "unload", None)
        if callable(unload):
            err: list[BaseException] = []

            def _run_unload() -> None:
                try:
                    unload()
                except BaseException as exc:  # noqa: BLE001 — capture for log below
                    err.append(exc)

            t = threading.Thread(
                target=_run_unload,
                daemon=True,
                name=f"uefn-plugin-unload-{pid}",
            )
            t.start()
            t.join(timeout=max(0.0, float(unload_timeout)))
            if t.is_alive():
                _log.warning(
                    "Plugin %s unload() still running after %.1fs — continuing teardown",
                    pid,
                    float(unload_timeout),
                )
            elif err:
                _log.error("Plugin %s unload() failed: %s", pid, err[0], exc_info=err[0])
    with _LOCK:
        _REGISTERED.discard(pid)
        _TTS_SYNTHESIZERS.pop(pid, None)
        _TTS_VOICE_LISTERS.pop(pid, None)
        for key in [k for k, v in _LLM_PROVIDER_FACTORIES.items() if v.get("plugin_id") == pid]:
            _LLM_PROVIDER_FACTORIES.pop(key, None)
        for key in [k for k, v in _CODING_AGENT_FACTORIES.items() if v.get("plugin_id") == pid]:
            _CODING_AGENT_FACTORIES.pop(key, None)
        for key in [k for k, v in _IDE_HOOKUPS.items() if v.get("plugin_id") == pid]:
            _IDE_HOOKUPS.pop(key, None)
        for key in [k for k, v in _SECRET_TESTERS.items() if v.get("plugin_id") == pid]:
            _SECRET_TESTERS.pop(key, None)
        _PANEL_RPC.pop(pid, None)
        _API_TOOL_REGISTRY.pop(pid, None)
        _API_INTENT_PATTERNS.pop(pid, None)
        for name in [n for n, owner in _PLUGIN_TOOL_OWNER.items() if owner == pid]:
            _PLUGIN_TOOL_OWNER.pop(name, None)
        for key in list(sys.modules):
            if key == mod_name or key.startswith(mod_name + "."):
                del sys.modules[key]


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
    """True when the plugin is enabled and opted in for this run (or unscoped)."""
    if not is_plugin_enabled(plugin_id):
        return False
    active = _ACTIVE_UEFN_AGENT_IDS.get()
    if active is None:
        return True
    return plugin_id in active


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
        if fn is None and pid not in _REGISTERED:
            root = plugin_dir(pid)
            manifest = load_plugin_manifest(pid)
            if manifest is not None and root.is_dir():
                _load_enabled_plugin(pid, root, manifest)
            with _LOCK:
                fn = (_PANEL_RPC.get(pid) or {}).get(name)
    if fn is None:
        raise ValueError(f"Unknown panel RPC: {pid}.{name}")
    kwargs = dict(params or {})
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


def filter_uefn_plugin_tools(tools: list[Any]) -> list[Any]:
    """Drop UEFN app-plugin tools that are Store-disabled or not in the run allowlist.

    ``active is None`` follows Store enable (all enabled plugins). Chat allowlists
    are not rewritten on disable — membership stays; this filter hides tools until
    the plugin is enabled again.
    """
    ensure_plugins_loaded()
    active = _ACTIVE_UEFN_AGENT_IDS.get()
    with _LOCK:
        owner = dict(_PLUGIN_TOOL_OWNER)
        for pid, names in _API_TOOL_REGISTRY.items():
            for name in names:
                owner.setdefault(name, pid)
        for pid, _pat, names in _PLUGIN_INTENT_ROWS:
            for name in names:
                owner.setdefault(name, pid)
    if not owner:
        return list(tools)
    out: list[Any] = []
    for t in tools:
        name = getattr(t, "name", None)
        if name not in owner:
            out.append(t)
            continue
        pid = owner[name]
        if not is_plugin_enabled(pid):
            continue
        if active is not None and pid not in active:
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
    listener: bool = True,
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


def _notify_uefn_plugins_changed() -> None:
    """Tell React the Store plugin registry changed (HTTP bus — never evaluate_js)."""
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "uefn_plugins_changed"})
    except Exception:
        pass


def _spawn_toggle_thread(target: Callable[[], None], *, name: str) -> None:
    t = threading.Thread(target=target, daemon=True, name=name)
    with _TOGGLE_THREADS_LOCK:
        _TOGGLE_THREADS[:] = [x for x in _TOGGLE_THREADS if x.is_alive()]
        _TOGGLE_THREADS.append(t)
    t.start()


def wait_plugin_toggles(timeout: float = 30.0) -> None:
    """Join background Store enable/disable workers (reload / tests)."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        with _TOGGLE_THREADS_LOCK:
            alive = [t for t in _TOGGLE_THREADS if t.is_alive()]
        if not alive:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        alive[0].join(timeout=min(0.05, remaining))


def apply_plugin_enabled_change(plugin_id: str, *, enabled: bool) -> None:
    """Fast Store enable/disable: touch only this plugin (no full reload).

    Returns quickly so the pywebview/MCP bridge never hangs on Discord
    ``register()`` / ``unload()`` or a stuck first-boot ``ensure_plugins_loaded()``.

    - Disable: strip UI contribs immediately; ``unload()`` / runtime drop on a daemon.
    - Enable: apply plugin.json UI rows immediately; ``register()`` on a daemon.
    Settings saves ``enabled_uefn_plugins`` *before* this runs, so a restart still
    restores the toggle even if background register is slow.
    """
    from backend.uefn_plugins.store import load_plugin_manifest, normalize_plugin_id, plugin_dir

    pid = normalize_plugin_id(plugin_id)
    # Kick boot if needed, but never wait — Store toggles must not block the UI thread.
    if not plugins_ready() and not plugins_ui_ready():
        ensure_plugins_loaded_async()

    if not enabled:
        with _LOCK:
            _strip_contributions_for(pid)

        def _disable_bg() -> None:
            try:
                # Re-enabled while we were queued — leave runtime alone.
                if is_plugin_enabled(pid):
                    return
                invalidate_plugin_runtime(pid)
                try:
                    from frontend.deploy import overlay_plugin_listeners_to_appdata

                    overlay_plugin_listeners_to_appdata(reload=True)
                except Exception:
                    _log.debug("Plugin %s listener overlay on disable failed", pid, exc_info=True)
            except Exception:
                _log.exception("Plugin %s disable unload failed", pid)
            finally:
                if not is_plugin_enabled(pid):
                    _notify_uefn_plugins_changed()

        _spawn_toggle_thread(_disable_bg, name=f"uefn-plugin-disable-{pid}")
        return

    root = plugin_dir(pid)
    manifest = load_plugin_manifest(pid)
    if manifest is None or not root.is_dir():
        return
    # Fast path: Settings tab / dock / header from plugin.json (no backend import).
    try:
        _load_one(pid, root, manifest, register=False)
    except Exception as exc:  # noqa: BLE001 — never kill Store toggle
        _log.exception("UEFN plugin %s contrib load failed: %s", pid, exc)
        _record_plugin_load_error(pid, exc)

    def _enable_bg() -> None:
        try:
            if not is_plugin_enabled(pid):
                return
            _load_enabled_plugin(pid, root, manifest)
            try:
                from frontend.deploy import overlay_plugin_listeners_to_appdata

                overlay_plugin_listeners_to_appdata(reload=True)
            except Exception:
                _log.debug("Plugin %s listener overlay on enable failed", pid, exc_info=True)
            try:
                from backend.agent.coding_agents.base import invalidate_detect_cache

                invalidate_detect_cache()
            except Exception:
                pass
        finally:
            # Enable raced with disable — drop whatever register() just attached.
            if not is_plugin_enabled(pid):
                try:
                    invalidate_plugin_runtime(pid)
                except Exception:
                    _log.exception("Plugin %s enable-race cleanup failed", pid)
                with _LOCK:
                    _strip_contributions_for(pid)
            _notify_uefn_plugins_changed()

    _spawn_toggle_thread(_enable_bg, name=f"uefn-plugin-enable-{pid}")


def reload_single_plugin(plugin_id: str) -> None:
    """Reload one plugin after Store install/update — never full reload_plugins().

    Full reload re-registers every plugin and races concurrent updates into
    duplicate header buttons / settings tabs. Single-plugin path is enough for
    zip overwrite + register().

    Waits are bounded so a wedged first-boot load cannot freeze install/uninstall
    (or hold ``_STORE_INSTALL_LOCK`` / host ``_LOCK`` indefinitely).

    ``register()`` runs on a toggle daemon — a hung plugin backend must not stick
    the Store install bridge on "Refreshing store…".
    """
    from backend.uefn_plugins.store import load_plugin_manifest, normalize_plugin_id, plugin_dir

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return
    wait_plugin_toggles(timeout=5.0)
    # If boot load is still running, skip repair and only touch this plugin —
    # the loader will honor final enabled settings when it finishes.
    ensure_plugins_loaded(timeout=10.0)
    invalidate_plugin_runtime(pid)
    root = plugin_dir(pid)
    manifest = load_plugin_manifest(pid)
    if manifest is None or not root.is_dir() or pid not in set(get_enabled_plugin_ids()):
        with _LOCK:
            _strip_contributions_for(pid)
        return
    with _LOCK:
        _strip_contributions_for(pid)
    # Fast path: UI contribs from plugin.json so install can paint before register().
    try:
        _load_one(pid, root, manifest, register=False)
    except Exception as exc:  # noqa: BLE001 — never kill Store install
        _log.exception("UEFN plugin %s contrib reload failed: %s", pid, exc)
        _record_plugin_load_error(pid, exc)

    def _reload_bg() -> None:
        try:
            if not is_plugin_enabled(pid):
                return
            _load_enabled_plugin(pid, root, manifest)
            try:
                from frontend.deploy import overlay_plugin_listeners_to_appdata

                overlay_plugin_listeners_to_appdata(reload=True)
            except Exception:
                _log.debug("Plugin %s listener overlay on reload failed", pid, exc_info=True)
            try:
                from backend.agent.coding_agents.base import invalidate_detect_cache

                invalidate_detect_cache()
            except Exception:
                pass
        finally:
            if not is_plugin_enabled(pid):
                try:
                    invalidate_plugin_runtime(pid)
                except Exception:
                    _log.exception("Plugin %s reload-race cleanup failed", pid)
                with _LOCK:
                    _strip_contributions_for(pid)
            _notify_uefn_plugins_changed()

    _spawn_toggle_thread(_reload_bg, name=f"uefn-plugin-reload-{pid}")


def reload_plugins() -> None:
    # Let Store toggle workers finish so we don't invalidate mid-register().
    wait_plugin_toggles()
    # Capture who was registered / enabled before wiping contribution lists so we
    # can drop stale module objects and re-run register() (Store Update replaces
    # AppData files; keeping _REGISTERED skipped register and left detect/models broken).
    try:
        previously = set(get_enabled_plugin_ids()) | set(_REGISTERED)
    except Exception:
        previously = set(_REGISTERED)
    with _LOAD_START_LOCK:
        global _LOADED, _UI_READY, _LOAD_THREAD
        # Wait for any in-flight first load before wiping (avoid racing the worker).
        if _LOAD_THREAD is not None and _LOAD_THREAD.is_alive():
            _LOAD_DONE.wait(timeout=120.0)
        with _LOCK:
            _LOADED = False
            _UI_READY = False
            _LOAD_DONE.clear()
            _LOAD_THREAD = None
            _CONTRIBUTIONS["settings_tabs"] = []
            _CONTRIBUTIONS["settings_sections"] = []
            _CONTRIBUTIONS["dock_panels"] = []
            _CONTRIBUTIONS["editor_kinds"] = []
            _CONTRIBUTIONS["header_buttons"] = []
            _CONTRIBUTIONS["ui_panels"] = []
            _CONTRIBUTIONS["shell_boots"] = []
            _CONTRIBUTIONS["appearance_profiles"] = []
            _CONTRIBUTIONS["appearance_css"] = []
            _CONTRIBUTIONS["appearance_effects"] = []
            _CONTRIBUTIONS["appearance_skins"] = []
            _CONTRIBUTIONS["sounds"] = []
            _CONTRIBUTIONS["hooks"] = []
            _CONTRIBUTIONS["verse_templates"] = []
            _CONTRIBUTIONS["tts_voices"] = []
            _CONTRIBUTIONS["llm_providers"] = []
            _CONTRIBUTIONS["llm_coding_agents"] = []
            _CONTRIBUTIONS["ide_hookups"] = []
            _CONTRIBUTIONS["agent_tools"] = {}
            _CONTRIBUTIONS["walkthroughs"] = []
            _PLUGIN_INTENT_ROWS.clear()
            _PLUGIN_TOOL_NAMES.clear()
            _PLUGIN_PLAN_TOOLS.clear()
            _PLUGIN_DESTRUCTIVE_TOOLS.clear()
    for pid in sorted(previously):
        try:
            invalidate_plugin_runtime(pid)
        except Exception:
            pass
    ensure_plugins_loaded()
    # Store install/enable can add Codex / etc. — drop stale CLI detect cache.
    try:
        from backend.agent.coding_agents.base import invalidate_detect_cache

        invalidate_detect_cache()
    except Exception:
        pass


def _strip_contributions_for(pid: str) -> None:
    """Remove contribution rows for one plugin (so a failed register can retry cleanly)."""
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


def _record_plugin_load_error(pid: str, exc: BaseException) -> None:
    """Persist last register failure so Settings can explain 'Backend not loaded'."""
    try:
        from frontend.settings import default_app_data_dir

        path = default_app_data_dir() / "uefn_plugin_load_errors.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        import json
        import time

        line = json.dumps(
            {"ts": time.time(), "plugin_id": pid, "error": f"{type(exc).__name__}: {exc}"},
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


def _load_enabled_plugin(pid: str, root: Path, manifest: dict[str, Any]) -> None:
    """Load one enabled plugin. Keep plugin.json UI rows even if register() fails."""
    try:
        _load_one(pid, root, manifest)
    except Exception as exc:  # noqa: BLE001 — never kill app startup
        _log.exception("UEFN plugin %s failed to load: %s", pid, exc)
        _record_plugin_load_error(pid, exc)
        try:
            invalidate_plugin_runtime(pid)
        except Exception:
            pass
        # ponytail: leave llm_providers / coding_agents / ide.hookups so LLMs
        # still lists the gateway; repair retries register() without wiping UI.


def _repair_missing_backends_async() -> None:
    """Kick a single-flight background pass that retries unfinished register().

    Never blocks the caller and never runs under ``_LOCK`` — a hung plugin
    ``register()`` (e.g. unity-mcp nested MCP sync) used to freeze every Store
    enable/disable/uninstall that needed the host lock.
    """
    global _REPAIR_THREAD
    with _REPAIR_THREAD_LOCK:
        if _REPAIR_THREAD is not None and _REPAIR_THREAD.is_alive():
            return
        _REPAIR_THREAD = threading.Thread(
            target=_repair_missing_backends_worker,
            daemon=True,
            name="uefn-plugins-repair",
        )
        _REPAIR_THREAD.start()


def _repair_missing_backends_worker() -> None:
    try:
        _repair_missing_backends()
    except Exception:  # noqa: BLE001 — never kill the app on repair
        _log.exception("UEFN plugin backend repair failed")


def _repair_missing_backends() -> None:
    """Re-register enabled Store plugins that contributed UI but never finished register().

    First-boot import blips used to leave llm_providers rows with no factories —
    Default Model empty + Coding Agents 'Backend not loaded' until full restart.

    Safe to call from the repair worker only — never from a thread holding ``_LOCK``.
    """
    # A plugin whose register() reaches back into get_contributions() /
    # ensure_plugins_loaded() re-enters here, and it is not in _REGISTERED yet
    # (that happens only after register returns) — so it repairs itself forever.
    # That RecursionError killed verse + vfx on every load and made every
    # ensure_plugins_loaded() call pay a failed re-import.
    if getattr(_REPAIR_STATE, "active", False):
        return
    _REPAIR_STATE.active = True
    try:
        _repair_missing_backends_once()
    finally:
        _REPAIR_STATE.active = False


def _repair_missing_backends_once() -> None:
    """Scan missing backends; kick each register() on its own daemon (not under _LOCK).

    Per-plugin backoff prevents a permanently hung ``register()`` from being
    re-kicked every sidebar poll. Each register runs off the coordinator so one
    hung plugin cannot block repair of the others.
    """
    enabled = set(get_enabled_plugin_ids())
    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        return
    now = time.monotonic()
    for pid in sorted(enabled):
        if pid in _REGISTERED:
            _REPAIR_ATTEMPTS.pop(pid, None)
            continue
        last = float(_REPAIR_ATTEMPTS.get(pid) or 0.0)
        if now - last < _REPAIR_BACKOFF_S:
            continue
        child = root / pid
        if not child.is_dir():
            continue
        manifest = _read_manifest(child)
        if not manifest:
            continue
        # Record *before* register so a hung call is not retried every poll.
        _REPAIR_ATTEMPTS[pid] = now

        def _repair_one(
            repair_pid: str = pid,
            repair_root: Path = child,
            repair_manifest: dict[str, Any] = manifest,
        ) -> None:
            try:
                if repair_pid in _REGISTERED or not is_plugin_enabled(repair_pid):
                    return
                # _load_one refreshes contribs; do not wipe first or a failed register
                # leaves LLMs empty until the next app update.
                _load_enabled_plugin(repair_pid, repair_root, repair_manifest)
                if repair_pid in _REGISTERED:
                    _REPAIR_ATTEMPTS.pop(repair_pid, None)
                    _notify_uefn_plugins_changed()
            except Exception:  # noqa: BLE001 — never kill repair coordinator siblings
                _log.exception("Plugin %s repair register failed", repair_pid)

        # Not a toggle thread — wait_plugin_toggles must not join hung repairs.
        threading.Thread(
            target=_repair_one,
            daemon=True,
            name=f"uefn-plugin-repair-{pid}",
        ).start()


def plugins_ready() -> bool:
    """True once the first ensure_plugins_loaded pass has finished."""
    return _LOADED


def plugins_ui_ready() -> bool:
    """True once plugin.json UI contributions are applied (backends may still register)."""
    return _UI_READY or _LOADED


def wait_plugins_loaded(timeout: float | None = None) -> bool:
    """Block until the first plugin load finishes.

    Returns True when ready. If ``timeout`` is set and elapses first, returns False
    (callers may still proceed with whatever tools are already registered).
    """
    if _LOADED:
        return True
    ensure_plugins_loaded_async()
    if timeout is None:
        _LOAD_DONE.wait()
        return bool(_LOADED)
    return bool(_LOAD_DONE.wait(timeout=float(timeout)))


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


def _flush_load_callbacks() -> None:
    with _LOAD_CALLBACKS_LOCK:
        callbacks = list(_LOAD_CALLBACKS)
        _LOAD_CALLBACKS.clear()
    for cb in callbacks:
        try:
            cb()
        except Exception:  # noqa: BLE001 — never kill plugin load on UI notify
            _log.exception("plugin load on_done callback failed")


def _boot_trace_plugins_load(duration_ms: float) -> None:
    try:
        from frontend.perf_trace import ensure_started, trace

        ensure_started()
        payload: dict[str, Any] = {}
        import os
        import time as _time

        raw = os.environ.get("UEFN_DUCKY_BOOT_T0")
        if raw:
            try:
                payload["total_ms"] = round((_time.perf_counter() - float(raw)) * 1000.0, 1)
            except ValueError:
                pass
        trace("boot", "plugins_load", duration_ms=duration_ms, **payload)
    except Exception:
        pass


def _warm_plugin_backend(pid: str, root: Path, manifest: dict[str, Any]) -> None:
    """Pre-import backend module (slow). Safe across threads — unique module names."""
    if pid in _REGISTERED:
        return
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    entry = str(backend.get("entry") or "backend").strip() or "backend"
    try:
        _import_backend(pid, root, entry)
    except Exception:  # noqa: BLE001 — register path records the real failure
        _log.debug("UEFN plugin %s pre-import failed", pid, exc_info=True)


def _run_first_plugin_load() -> None:
    """Import + register every enabled plugin. Caller owns single-flight.

    Two-phase so Settings can show Installed tabs before slow register() work
    (Blender addon deploy, Discord presence, gateway factories, …) finishes.
    """
    global _LOADED, _UI_READY
    try:
        seed_uefn_plugins()
    except Exception as exc:  # noqa: BLE001 — fail-soft
        _log.warning("seed_uefn_plugins failed: %s", exc)
    enabled = set(get_enabled_plugin_ids())
    root = appdata_uefn_plugins_dir()
    if not root.is_dir():
        with _LOCK:
            _UI_READY = True
            _LOADED = True
        return
    jobs: list[tuple[str, Path, dict[str, Any]]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        manifest = _read_manifest(child)
        if not manifest:
            continue
        pid = str(manifest.get("id") or child.name).strip().lower()
        if pid not in enabled:
            continue
        jobs.append((pid, child, manifest))
    # Phase 1 — plugin.json UI rows only (fast). Settings polls / paints Installed.
    for pid, child, manifest in jobs:
        try:
            _load_one(pid, child, manifest, register=False)
        except Exception as exc:  # noqa: BLE001 — never kill app startup
            _log.exception("UEFN plugin %s contrib load failed: %s", pid, exc)
            _record_plugin_load_error(pid, exc)
    with _LOCK:
        _UI_READY = True
    # Do not flush load callbacks here — PanelApi still needs one notify after
    # register() so LLM/coding-agent factories refresh. Settings paints via
    # get_ui_contributions() + frontend poll while backends finish.
    # Parallel pre-import: exec_module dominates boot; register stays serial after.
    if len(jobs) > 1:
        from concurrent.futures import ThreadPoolExecutor

        workers = min(8, len(jobs))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="uefn-plugin-import") as pool:
            list(pool.map(lambda j: _warm_plugin_backend(j[0], j[1], j[2]), jobs))
    # Phase 2 — register() backends (may be slow; UI already has contribs).
    for pid, child, manifest in jobs:
        _load_enabled_plugin(pid, child, manifest)
    with _LOCK:
        _UI_READY = True
        _LOADED = True


def _plugin_load_worker() -> None:
    import time

    global _LOADED
    t0 = time.perf_counter()
    try:
        if not _LOADED:
            _run_first_plugin_load()
    except Exception:  # noqa: BLE001 — mark done so waiters never hang
        _log.exception("UEFN plugin background load failed")
        with _LOCK:
            _LOADED = True
    finally:
        _LOAD_DONE.set()
        _boot_trace_plugins_load((time.perf_counter() - t0) * 1000.0)
        _flush_load_callbacks()


def ensure_plugins_loaded_async(on_done: Callable[[], None] | None = None) -> None:
    """Start first plugin load on a daemon thread (no-op if already loaded/in flight)."""
    if on_done is not None:
        with _LOAD_CALLBACKS_LOCK:
            _LOAD_CALLBACKS.append(on_done)
    if _LOADED:
        _flush_load_callbacks()
        return
    global _LOAD_THREAD
    with _LOAD_START_LOCK:
        if _LOADED:
            _flush_load_callbacks()
            return
        if _LOAD_THREAD is not None and _LOAD_THREAD.is_alive():
            return
        _LOAD_DONE.clear()
        _LOAD_THREAD = threading.Thread(
            target=_plugin_load_worker,
            daemon=True,
            name="uefn-plugins-load",
        )
        _LOAD_THREAD.start()


def ensure_plugins_loaded(timeout: float | None = None) -> bool:
    """Block until plugins are loaded (agent/MCP). Safe if async load already started.

    When ``timeout`` is set and the first load has not finished, return False without
    kicking repair — Store install/uninstall must not wait forever on a hung plugin
    ``register()`` at boot. Agent/MCP callers pass ``timeout=None`` (default) and
    still wait until ready.

    Missing-backend repair is always kicked on a background thread and never runs
    under ``_LOCK`` (a hung ``register()`` must not freeze Store toggles).
    """
    if not _LOADED:
        ensure_plugins_loaded_async()
        if timeout is None:
            _LOAD_DONE.wait()
        elif not _LOAD_DONE.wait(timeout=max(0.0, float(timeout))):
            return False
    if _LOADED:
        # Never hold _LOCK across register() — repair is async + per-plugin backoff.
        _repair_missing_backends_async()
        return True
    if timeout is not None:
        # Boot still in flight (or never marked ready) — caller asked not to hang.
        return False
    # Should not happen; fail-soft so callers never hang forever.
    _run_first_plugin_load()
    _LOAD_DONE.set()
    _repair_missing_backends_async()
    return True


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


def _load_one(pid: str, root: Path, manifest: dict[str, Any], *, register: bool = True) -> None:
    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    # Refresh this plugin's contrib rows (safe to re-run after a failed register).
    _strip_contributions_for(pid)

    for tab in contributes.get("settings.tabs") or contributes.get("settings_tabs") or []:
        if isinstance(tab, dict) and tab.get("id"):
            entry = dict(tab)
            entry["plugin_id"] = pid
            resolved = _resolve_contrib_icon(entry.get("icon"), root)
            if resolved:
                entry["icon"] = resolved
            elif "icon" in entry and str(entry.get("icon") or "").startswith("assets/"):
                entry.pop("icon", None)
            _CONTRIBUTIONS["settings_tabs"].append(entry)

    for section in contributes.get("settings.sections") or contributes.get("settings_sections") or []:
        if isinstance(section, dict) and section.get("id") and section.get("tab"):
            props = section.get("properties")
            if not isinstance(props, list) or not props:
                continue
            entry = dict(section)
            entry["plugin_id"] = pid
            entry["properties"] = [dict(p) for p in props if isinstance(p, dict) and p.get("id")]
            if entry["properties"]:
                _CONTRIBUTIONS["settings_sections"].append(entry)

    for panel in contributes.get("dock.panels") or contributes.get("dock_panels") or []:
        if isinstance(panel, dict) and panel.get("id"):
            entry = dict(panel)
            entry["plugin_id"] = pid
            _CONTRIBUTIONS["dock_panels"].append(entry)

    for kind in contributes.get("editor.kinds") or contributes.get("editor_kinds") or []:
        if isinstance(kind, dict) and kind.get("kind"):
            entry = dict(kind)
            entry["plugin_id"] = pid
            _CONTRIBUTIONS["editor_kinds"].append(entry)

    for btn in contributes.get("header.buttons") or contributes.get("header_buttons") or []:
        if isinstance(btn, dict) and btn.get("id") and btn.get("action"):
            entry = dict(btn)
            entry["plugin_id"] = pid
            resolved = _resolve_contrib_icon(entry.get("icon"), root)
            if resolved:
                entry["icon"] = resolved
            elif "icon" in entry and str(entry.get("icon") or "").startswith("assets/"):
                entry.pop("icon", None)
            _CONTRIBUTIONS["header_buttons"].append(entry)

    wt = contributes.get("walkthrough")
    if isinstance(wt, dict) and isinstance(wt.get("steps"), list) and wt.get("steps"):
        entry = dict(wt)
        entry["plugin_id"] = pid
        if not entry.get("id"):
            entry["id"] = pid
        _CONTRIBUTIONS["walkthroughs"].append(entry)

    # Plugin webview panels (Phase 2) — logic lives in webview.py.
    from backend.uefn_plugins.webview import merge_ui_panels, sanitize_entry

    ver_raw = manifest.get("version")
    ver = plugin_version_rank(ver_raw) if ver_raw is not None else None
    _CONTRIBUTIONS["ui_panels"].extend(merge_ui_panels(contributes, pid, root, version=ver))

    for boot in contributes.get("shell.boot") or contributes.get("shell_boot") or []:
        if not isinstance(boot, dict):
            continue
        entry = sanitize_entry(str(boot.get("entry") or "").strip(), root)
        if not entry:
            continue
        _CONTRIBUTIONS["shell_boots"].append({"plugin_id": pid, "entry": entry})

    for profile in contributes.get("appearance.profiles") or contributes.get("appearance_profiles") or []:
        if not isinstance(profile, dict):
            continue
        local_id = str(profile.get("id") or "").strip()
        name = str(profile.get("name") or "").strip()
        if not local_id or not name:
            continue
        foundation = profile.get("foundation") if isinstance(profile.get("foundation"), dict) else {}
        overrides = profile.get("overrides") if isinstance(profile.get("overrides"), dict) else {}
        status = (
            profile.get("status_overrides")
            if isinstance(profile.get("status_overrides"), dict)
            else {}
        )
        _CONTRIBUTIONS["appearance_profiles"].append(
            {
                "id": local_id,
                "name": name,
                "plugin_id": pid,
                "foundation": {str(k): str(v) for k, v in foundation.items() if v},
                "overrides": {str(k): str(v) for k, v in overrides.items() if v},
                "status_overrides": {
                    str(sid): {str(fk): str(fv) for fk, fv in fields.items() if fv}
                    for sid, fields in status.items()
                    if isinstance(fields, dict)
                },
            }
        )

    for css in contributes.get("appearance.css") or contributes.get("appearance_css") or []:
        if not isinstance(css, dict):
            continue
        entry = sanitize_entry(str(css.get("entry") or "").strip(), root)
        if not entry:
            continue
        _CONTRIBUTIONS["appearance_css"].append({"plugin_id": pid, "entry": entry})

    for effect in contributes.get("appearance.effects") or contributes.get("appearance_effects") or []:
        if not isinstance(effect, dict):
            continue
        eid = str(effect.get("id") or "").strip()
        label = str(effect.get("label") or eid).strip()
        entry = sanitize_entry(str(effect.get("entry") or "").strip(), root)
        if not eid or not entry:
            continue
        _CONTRIBUTIONS["appearance_effects"].append(
            {"id": eid, "label": label or eid, "plugin_id": pid, "entry": entry}
        )

    for skin in contributes.get("appearance.skin") or contributes.get("appearance_skins") or []:
        if not isinstance(skin, dict):
            continue
        sid = str(skin.get("id") or "").strip()
        label = str(skin.get("label") or sid).strip()
        entry = sanitize_entry(str(skin.get("entry") or "").strip(), root)
        if not sid or not entry:
            continue
        css_raw = str(skin.get("css") or "").strip()
        css_entry = sanitize_entry(css_raw, root) if css_raw else None
        row: dict[str, Any] = {
            "id": sid,
            "label": label or sid,
            "plugin_id": pid,
            "entry": entry,
        }
        if css_entry:
            row["css"] = css_entry
        _CONTRIBUTIONS["appearance_skins"].append(row)

    for sound in contributes.get("sounds") or []:
        if not isinstance(sound, dict):
            continue
        sid = str(sound.get("id") or "").strip()
        file_entry = sanitize_entry(str(sound.get("file") or "").strip(), root)
        if not sid or not file_entry:
            continue
        label = str(sound.get("label") or sid).strip()
        _CONTRIBUTIONS["sounds"].append(
            {"id": sid, "label": label or sid, "file": file_entry, "plugin_id": pid}
        )

    for tmpl in contributes.get("verse.templates") or contributes.get("verse_templates") or []:
        if not isinstance(tmpl, dict):
            continue
        tid = str(tmpl.get("id") or "").strip()
        name = str(tmpl.get("name") or tid).strip()
        if not tid or not name:
            continue
        icon = str(tmpl.get("icon") or "📄").strip() or "📄"
        description = str(tmpl.get("description") or "").strip()
        folder = str(tmpl.get("folder") or "").strip()
        content = str(tmpl.get("content") or "")
        file_entry: str | None = None
        pack_files: list[dict[str, str]] = []
        raw_files = tmpl.get("files")
        if isinstance(raw_files, list) and raw_files:
            ok_pack = True
            for item in raw_files:
                if not isinstance(item, dict):
                    ok_pack = False
                    break
                rel_path = str(item.get("path") or "").strip().replace("\\", "/")
                src_raw = str(item.get("file") or item.get("content_file") or "").strip()
                inline = str(item.get("content") or "")
                if not rel_path or ".." in rel_path.split("/") or rel_path.startswith("/"):
                    ok_pack = False
                    break
                body = inline
                if src_raw:
                    src_entry = sanitize_entry(src_raw, root)
                    if not src_entry:
                        ok_pack = False
                        break
                    try:
                        body = (root / src_entry).read_text(encoding="utf-8")
                    except OSError:
                        _log.warning(
                            "verse template %s:%s missing pack file %s", pid, tid, src_entry
                        )
                        ok_pack = False
                        break
                # ponytail: 200 KiB per file; upgrade = stream via /plugin-ui if packs grow
                if len(body) > 200_000:
                    _log.warning(
                        "verse template %s:%s file %s too large (%d)",
                        pid,
                        tid,
                        rel_path,
                        len(body),
                    )
                    ok_pack = False
                    break
                pack_files.append({"path": rel_path, "content": body})
            if not ok_pack or not pack_files:
                continue
            if not content and pack_files:
                content = pack_files[0]["content"]
        else:
            file_raw = str(tmpl.get("file") or "").strip()
            if file_raw:
                file_entry = sanitize_entry(file_raw, root)
                if not file_entry:
                    continue
                try:
                    content = (root / file_entry).read_text(encoding="utf-8")
                except OSError:
                    _log.warning("verse template %s:%s missing file %s", pid, tid, file_entry)
                    continue
            if len(content) > 200_000:
                _log.warning(
                    "verse template %s:%s content too large (%d)", pid, tid, len(content)
                )
                continue
        try:
            order = int(tmpl.get("order") if tmpl.get("order") is not None else 100)
        except (TypeError, ValueError):
            order = 100
        connects = tmpl.get("connects") or tmpl.get("connects_to") or []
        if not isinstance(connects, list):
            connects = []
        connects_clean = [str(c).strip() for c in connects if str(c).strip()]
        row: dict[str, Any] = {
            "id": tid,
            "name": name,
            "icon": icon,
            "description": description,
            "content": content,
            "order": order,
            "plugin_id": pid,
        }
        if folder:
            row["folder"] = folder
        if file_entry:
            row["file"] = file_entry
        if pack_files:
            row["files"] = pack_files
        if connects_clean:
            row["connects"] = connects_clean
        _CONTRIBUTIONS["verse_templates"].append(row)

    for hook in contributes.get("hooks") or []:
        if not isinstance(hook, dict):
            continue
        hid = str(hook.get("id") or "").strip()
        if not hid:
            continue
        label = str(hook.get("label") or hid).strip()
        _CONTRIBUTIONS["hooks"].append(
            {"id": hid, "label": label or hid, "plugin_id": pid}
        )

    for voice in contributes.get("tts.voices") or contributes.get("tts_voices") or []:
        if not isinstance(voice, dict):
            continue
        vid = str(voice.get("id") or "").strip()
        if not vid:
            continue
        label = str(voice.get("label") or vid).strip()
        _CONTRIBUTIONS["tts_voices"].append(
            {"id": vid, "label": label or vid, "plugin_id": pid}
        )

    for prov in contributes.get("llm.providers") or contributes.get("llm_providers") or []:
        if not isinstance(prov, dict):
            continue
        provid = str(prov.get("id") or "").strip().lower()
        if not provid:
            continue
        kind = str(prov.get("kind") or "secret").strip().lower() or "secret"
        if kind not in ("secret", "url"):
            kind = "secret"
        try:
            order = int(prov.get("order") if prov.get("order") is not None else 100)
        except (TypeError, ValueError):
            order = 100
        entry = {
            "id": provid,
            "label": str(prov.get("label") or provid).strip() or provid,
            "kind": kind,
            "secret_key": str(prov.get("secret_key") or provid).strip() or provid,
            "default_url": str(prov.get("default_url") or "").strip(),
            "order": order,
            "plugin_id": pid,
        }
        _CONTRIBUTIONS["llm_providers"].append(entry)

    for agent in contributes.get("llm.coding_agents") or contributes.get("llm_coding_agents") or []:
        if not isinstance(agent, dict):
            continue
        aid = str(agent.get("id") or "").strip().lower().replace("-", "_")
        if not aid:
            continue
        try:
            order = int(agent.get("order") if agent.get("order") is not None else 100)
        except (TypeError, ValueError):
            order = 100
        _CONTRIBUTIONS["llm_coding_agents"].append(
            {
                "id": aid,
                "label": str(agent.get("label") or aid).strip() or aid,
                "order": order,
                "plugin_id": pid,
            }
        )

    for hook in contributes.get("ide.hookups") or contributes.get("ide_hookups") or []:
        if not isinstance(hook, dict):
            continue
        kind = str(hook.get("kind") or "").strip().lower().replace("-", "_")
        if not kind or not kind.replace("_", "").isalnum():
            continue
        _CONTRIBUTIONS["ide_hookups"].append(
            {
                "kind": kind,
                "label": str(hook.get("label") or kind).strip() or kind,
                "plugin_id": pid,
            }
        )

    tools_meta = contributes.get("agent.tools") or contributes.get("agent_tools")
    tool_names: set[str] = set()
    plan_names: set[str] = set()
    destructive_names: set[str] = set()
    declared_tools: set[str] = set()
    intent_re = None
    if isinstance(tools_meta, dict):
        for name in tools_meta.get("tools") or []:
            if isinstance(name, str) and name.strip():
                declared_tools.add(name.strip())
                tool_names.add(name.strip())
        for name in tools_meta.get("plan_tools") or []:
            if isinstance(name, str) and name.strip():
                plan_names.add(name.strip())
        for name in tools_meta.get("destructive_tools") or []:
            if isinstance(name, str) and name.strip():
                destructive_names.add(name.strip())
        raw_pat = tools_meta.get("intent_pattern")
        if isinstance(raw_pat, str) and raw_pat.strip():
            try:
                intent_re = re.compile(raw_pat, re.I)
            except re.error:
                intent_re = None
        category = str(tools_meta.get("category") or pid)
    elif tools_meta is True:
        category = pid
    else:
        category = pid

    # Tools previously registered via api.tool() survive reload.
    api_tools = set(_API_TOOL_REGISTRY.get(pid) or ())
    tool_names.update(api_tools)
    # plan_tools ⊆ declared tools when the manifest lists tools; otherwise keep as-is
    # (api.tool packs often omit tools[] and register at runtime).
    if declared_tools:
        plan_names = {n for n in plan_names if n in declared_tools or n in api_tools}
        destructive_names = {
            n for n in destructive_names if n in declared_tools or n in api_tools
        }

    if tool_names or tools_meta or plan_names or destructive_names:
        _CONTRIBUTIONS["agent_tools"][pid] = {
            "category": category,
            "tools": sorted(tool_names),
            "plan_tools": sorted(plan_names),
            "destructive_tools": sorted(destructive_names),
        }
    if plan_names:
        _PLUGIN_PLAN_TOOLS.update(plan_names)
    if destructive_names:
        _PLUGIN_DESTRUCTIVE_TOOLS.update(destructive_names)
    if tool_names:
        _PLUGIN_TOOL_NAMES.update(tool_names)
        for name in tool_names:
            _PLUGIN_TOOL_OWNER[name] = pid
        _rebuild_intent_row(pid, tool_names, intent_re)

    if not register or pid in _REGISTERED:
        return
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    entry = str(backend.get("entry") or "backend").strip() or "backend"
    register_name = str(backend.get("register") or "register").strip() or "register"
    mod = _import_backend(pid, root, entry)
    if mod is None:
        return
    register_fn = getattr(mod, register_name, None)
    if callable(register_fn):
        register_fn(_PluginApi(pid))
    _REGISTERED.add(pid)


def _import_backend(pid: str, root: Path, entry: str) -> Any:
    """Import plugin backend package from AppData (or plain module file)."""
    entry_path = root / entry
    module_name = f"uefn_plugin_{pid}"
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


class _PluginApi:
    """Narrow host surface passed to plugin register(api)."""

    def __init__(self, plugin_id: str) -> None:
        self.plugin_id = plugin_id

    def log(self, message: str) -> None:
        _log.info("[%s] %s", self.plugin_id, message)

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

    def tool(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        intent: str | None = None,
        listener: bool = True,
    ) -> Any:
        """Register an MCP tool on the app's shared FastMCP server.

        Auto-gates on plugin enable + per-chat opt-in. Tool names are tracked for
        intent unlock and the Settings → MCPs tab — no manifest listing required.

        Pass ``listener=False`` for host-only tools (plugin cache, Discord, …) so
        the agent does not require the UEFN editor listener.
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
                self.log(f"MCP tool already registered: {tool_name}")
                return func

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                if not uefn_agent_tools_allowed(pid):
                    raise ValueError(
                        f"Plugin '{pid}' tools are disabled — enable it in Settings → Store"
                    )
                return func(*args, **kwargs)

            mcp.tool(name=tool_name)(wrapper)
            _record_plugin_api_tool(pid, tool_name, intent=intent, listener=listener)
            self.log(f"MCP tool registered: {tool_name}")
            return wrapper

        if fn is not None:
            return decorator(fn)
        return decorator

    def register_tts(self, synthesize_fn: Any) -> None:
        """Register ``synthesize(text, voice_id) -> {audio_base64, mime}`` for this plugin."""
        register_tts_synthesizer(self.plugin_id, synthesize_fn)
        self.log("TTS synthesizer registered")

    def register_secret_test(self, secret_key: str, test_fn: Any) -> None:
        """Register Settings → Test for a secret field (``test_fn(api_key) -> {ok, detail}``)."""
        register_secret_tester(self.plugin_id, secret_key, test_fn)
        self.log(f"Secret tester registered: {secret_key}")

    def register_panel_rpc(self, name: str, fn: Any) -> None:
        """Register a PanelApi / bridge method: ``plugin_call(plugin_id, name, params)``.

        ``fn`` receives ``**params`` from the caller. Prefer returning plain dicts
        (never raise across the JS bridge unless the host should surface an error).
        """
        register_panel_rpc(self.plugin_id, name, fn)
        self.log(f"Panel RPC registered: {name}")

    def register_tts_voices(self, list_voices_fn: Any) -> None:
        """Register ``list_voices() -> [{id, label}]`` — dynamic voices for the picker.

        Runs at runtime (e.g. fetch an account's cloned voices from a web API) and is
        merged on top of the static manifest ``tts.voices``. Keep it fast/cached — it is
        polled when a voice dropdown opens.
        """
        register_tts_voices_lister(self.plugin_id, list_voices_fn)
        self.log("TTS voices lister registered")

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
        import os

        if os.environ.get("UEFN_DUCKY_MCP_BRIDGE") == "1":
            self.log(f"IDE auto-apply skipped ({kind}): MCP bridge process")
            return
        # Off the plugin-load thread: config merge + skill deploy is disk-bound and
        # nothing in the UI needs it. Inline, three gateways serialized ~60s of
        # writes ahead of _LOADED, so the Store/Settings panes sat empty that long.
        _queue_ide_auto_apply(kind, self.log)
