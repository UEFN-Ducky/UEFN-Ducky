"""PyWebView JS API — thin pass-through to existing frontend modules."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from frontend import __version__
from frontend.agent_models import PROVIDER_LABELS
from backend.agent.providers import all_providers
from frontend.ducky_assets import (
    custom_duckies_dir,
    default_bundled_style,
    delete_custom_ducky,
    list_ducky_catalog as _list_ducky_catalog,
    save_custom_ducky_png,
)
from frontend.verse_template_assets import (
    delete_custom_verse_template,
    get_custom_verse_template,
    list_custom_verse_templates,
    save_custom_verse_template,
)
from frontend.agent_profiles import (
    BLANK_PROFILE_ID,
    delete_agent_profile,
    duplicate_agent_profile,
    list_agent_profiles,
    list_bundled_agent_profile_templates,
    save_agent_profile,
    save_agent_profile_override,
)
from frontend.archive_folder import is_archive_folder_id
from frontend.ui_web.project_chats import (
    apply_ducky_config,
    apply_sidebar_layout,
    create_conversation,
    create_folder,
    conversation_descendant_ids,
    delete_conversation,
    delete_folder,
    ensure_conversation_file_path,
    ensure_group_folder_hubs,
    folder_subtree_ids,
    group_hub_ids_in,
    list_all_conversation_metadata,
    list_conversations,
    list_conversations_for_file,
    load_conversation,
    load_folders,
    move_conversation,
    remap_conversation_file_paths,
    rename_folder,
    save_conversation,
    save_folders,
    set_conversation_ducky_style,
    set_conversation_enabled_skills,
    set_conversation_skill_selection,
)
from frontend.deploy import deploy_listener, resolve_uefn_project_root
from frontend.error_log import clear_errors as clear_error_log, read_errors, record_error, trim as trim_errors
from frontend.ide_paths import IdeKind, path_for_ide
from frontend.merge import merge_uefn_into_config
from frontend.mcp_block import build_uefn_server_block
from frontend.appearance_builtin_profiles import (
    DEFAULT_APPEARANCE_PROFILE_ID,
    apply_built_in_appearance,
    is_built_in_appearance_profile,
)
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings, apply_workspace_env, default_app_data_dir
from frontend.skill_deploy import sync_skill_for_ide, sync_skill_on_mcp_update
from frontend.stdio_probe import probe_stdio_mcp
from frontend.ui_web.agent_modes import cancel_agent, list_running_agents, notify_chats_changed, run_message, wait_for_idle
from frontend.ui_web.project_files import (
    content_entry_exists,
    content_entry_needs_uefn_delete,
    content_package_rel,
    copy_project_entry,
    create_project_file,
    create_project_folder,
    create_project_verse_file,
    delete_project_entry,
    fingerprint_project_dirs,
    import_external_entries,
    list_project_files,
    list_project_file_paths,
    list_workspace_roots,
    move_project_entry,
    open_project_file,
    read_project_file,
    restore_trashed_entry,
    stat_project_file,
    rename_project_entry,
    write_external_file,
    EXT_PATH_PREFIX,
)
from frontend.ui_web.file_kinds import classify_project_file
from frontend.ui_web.project_switch import (
    get_panel_project_info,
    list_panel_projects,
    normalize_project_path as _normalize_project_path,
    switch_panel_project,
)
from frontend.ui_web.workspace_search import replace_workspace as _replace_workspace
from frontend.ui_web.workspace_search import search_workspace as _search_workspace
from frontend.ui_web.verse_editor.api import VerseEditorApi

_MAX_LOG = 2500
_log_history: list[str] = []
_model_cache: dict[str, list[Any]] = {}
_MODELS_CACHE_FILE = "models_cache.json"


def _load_model_cache_from_disk() -> None:
    """Seed _model_cache from the last session so first chat open never waits on provider APIs.

    Do not call ``all_providers()`` here — that sync-loads every Store plugin and
    kept the splash up for 20s+. Prune against live gateways after plugins load
    (``_warm_model_cache`` / ``_prune_model_caches_to_enabled_providers``).
    """
    from backend.agent.model_fetch import ModelInfo, _cache_provider_models

    try:
        raw = json.loads((default_app_data_dir() / _MODELS_CACHE_FILE).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        for prov, rows in raw.items():
            if not isinstance(prov, str) or not isinstance(rows, list):
                continue
            try:
                models = [ModelInfo(**row) for row in rows if isinstance(row, dict)]
            except Exception:
                continue
            if models:
                _model_cache[prov] = models
                _cache_provider_models(prov, models)
    except Exception:
        pass


def _save_model_cache_to_disk() -> None:
    from dataclasses import asdict

    from backend.agent.providers import all_providers

    try:
        path = default_app_data_dir() / _MODELS_CACHE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        # Never persist models for gateways that are no longer installed/enabled.
        allowed = set(all_providers())
        payload = {
            prov: [asdict(m) for m in models]
            for prov, models in _model_cache.items()
            if prov in allowed
        }
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass


def _prune_model_caches_to_enabled_providers() -> None:
    """Drop in-memory + disk model lists for gateways that left (disable/uninstall).

    Keep-data uninstall leaves the API key on disk; models must still disappear
    until that Store gateway is installed again.
    """
    from backend.agent.model_fetch import clear_model_cache
    from backend.agent.providers import all_providers

    allowed = set(all_providers())
    stale = [p for p in list(_model_cache) if p not in allowed]
    if not stale:
        return
    for prov in stale:
        _model_cache.pop(prov, None)
        try:
            clear_model_cache(prov)
        except Exception:
            pass
    _save_model_cache_to_disk()


def _all_cached_model_ids() -> list[str]:
    from backend.agent.providers import all_providers

    allowed = set(all_providers())
    ids: list[str] = []
    seen: set[str] = set()
    for prov, models in _model_cache.items():
        if prov not in allowed:
            continue
        for item in models:
            mid = item.id if hasattr(item, "id") else str(item)
            if mid and mid not in seen:
                seen.add(mid)
                ids.append(mid)
    return ids


def _coding_agent_favorite_ids() -> frozenset[str]:
    from backend.agent.coding_agents.base import CODING_AGENT_IDS

    return frozenset(aid for aid in CODING_AGENT_IDS if aid != "ducky")


def resolve_model_selection(favorite_models: Any, settings: PanelSettings):
    """ResolveOk | ResolveErr for the profile's model, falling back to settings.default_model."""
    from frontend.favorite_models import resolve_model_strict

    return resolve_model_strict(favorite_models, settings)


def _first_available_api_model() -> tuple[str, str] | None:
    """First keyed API provider model (provider, model_id) — not a coding-agent CLI."""
    from frontend.favorite_models import _available_api_models

    catalog = _available_api_models()
    for provider in sorted(catalog):
        ids = sorted(catalog.get(provider) or [])
        if ids:
            return provider, ids[0]
    return None


# Legacy fallback only — prefer test_key_model from api.register_llm_provider.
_TEST_KEY_MODELS: dict[str, str] = {}


def _log(msg: str) -> None:
    global _log_history
    _log_history.append(msg)
    if len(_log_history) > _MAX_LOG:
        _log_history = _log_history[-_MAX_LOG:]


def _tool_result_text(result: dict[str, Any] | None) -> str:
    if not result:
        return ""
    if result.get("data"):
        return str(result["data"])
    if result.get("error"):
        return str(result["error"])
    return json.dumps(result, ensure_ascii=False)


def _messages_to_ui(conv, project_root: str | None = None) -> list[dict[str, Any]]:
    from frontend.settings import PanelSettings
    from frontend.ui_web.conversation_attachments import hydrate_attachment_dicts
    from frontend.ui_web.project_chats import get_conversations_dir

    root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
    conv_dir = get_conversations_dir(root)
    out: list[dict[str, Any]] = []
    i = 0
    for m in conv.messages:
        role = m.get("role", "user")
        if role == "user":
            row: dict[str, Any] = {"id": i, "role": "user", "text": m.get("text", m.get("content", ""))}
            attachments = m.get("attachments")
            if attachments:
                row["attachments"] = hydrate_attachment_dicts(attachments, conv.id, conv_dir, root)
            out.append(row)
            i += 1
            continue
        if role == "assistant":
            for block in m.get("blocks") or []:
                btype = block.get("type")
                if btype == "thinking":
                    # Per-step reasoning, shown inline between tools (Cursor-style).
                    seg = str(block.get("text") or "")
                    if seg.strip():
                        out.append({"id": i, "role": "assistant", "text": "", "thinking": seg})
                        i += 1
                    continue
                if btype == "text":
                    # Narration the model spoke between tool calls — kept in order.
                    seg = str(block.get("text") or "")
                    if seg.strip():
                        out.append({"id": i, "role": "assistant", "text": seg})
                        i += 1
                    continue
                if btype != "tool_call":
                    continue
                name = block.get("name", "?")
                args = block.get("arguments") or {}
                args_short = json.dumps(args, ensure_ascii=False)
                if len(args_short) > 120:
                    args_short = args_short[:117] + "..."
                tool_line = f"⚙ {name}({args_short})"
                out.append(
                    {
                        "id": i,
                        "role": "tool",
                        "text": tool_line,
                        "tool": {
                            "name": name,
                            "arguments": args,
                            "status": "pending",
                        },
                    }
                )
                i += 1
                status = block.get("status", "?")
                ms = int(block.get("duration_ms", 0) or 0)
                done_role = "success" if status == "success" else "error"
                block_result = block.get("result") if isinstance(block.get("result"), dict) else {}
                tool_done: dict[str, Any] = {
                    "name": name,
                    "arguments": args,
                    "status": status,
                    "durationMs": ms,
                    "result": _tool_result_text(block_result),
                    "hint": str(block_result.get("hint") or ""),
                }
                llm_tokens_val = block.get("llm_tokens")
                if isinstance(llm_tokens_val, int) and llm_tokens_val > 0:
                    tool_done["llmTokens"] = llm_tokens_val
                stored_edit = block.get("file_edit")
                if isinstance(stored_edit, dict):
                    tool_done["fileEdit"] = stored_edit
                else:
                    try:
                        from frontend.ui_web.verse_editor.agent_sync import build_file_edit_meta

                        file_edit = build_file_edit_meta(name, args, block_result)
                        if file_edit:
                            tool_done["fileEdit"] = file_edit
                    except Exception:
                        pass
                out.append(
                    {
                        "id": i,
                        "role": done_role,
                        "text": f"⚙ {tool_line[2:]} · {status} · {ms}ms",
                        "tool": tool_done,
                    }
                )
                i += 1
            content = m.get("content", "")
            thinking = m.get("thinking")
            author = m.get("author") if isinstance(m.get("author"), dict) else None
            if content or (isinstance(thinking, str) and thinking.strip()):
                row_asst: dict[str, Any] = {"id": i, "role": "assistant", "text": content}
                if isinstance(thinking, str) and thinking.strip():
                    row_asst["thinking"] = thinking
                if author:
                    row_asst["author"] = {
                        "name": str(author.get("name") or "").strip(),
                        "member_conv_id": str(author.get("member_conv_id") or "").strip(),
                        "tts_voice": str(author.get("tts_voice") or "").strip(),
                        "tts_speed": float(author.get("tts_speed") or 0.0),
                        "color": str(author.get("color") or "").strip(),
                        "profile_id": str(author.get("profile_id") or "").strip(),
                    }
                if m.get("incomplete"):
                    row_asst["incomplete"] = True
                    if isinstance(m.get("error"), str) and m["error"].strip():
                        row_asst["error"] = m["error"]
                out.append(row_asst)
                i += 1
            elif m.get("incomplete"):
                # Crashed turn whose final step produced no answer/reasoning of
                # its own (it's all in the interleaved blocks above) — still show
                # the interruption so it never silently disappears.
                err = m.get("error")
                row_inc: dict[str, Any] = {
                    "id": i,
                    "role": "assistant",
                    "text": "",
                    "incomplete": True,
                    **({"error": err} if isinstance(err, str) and err.strip() else {}),
                }
                if author:
                    row_inc["author"] = {
                        "name": str(author.get("name") or "").strip(),
                        "member_conv_id": str(author.get("member_conv_id") or "").strip(),
                        "tts_voice": str(author.get("tts_voice") or "").strip(),
                        "tts_speed": float(author.get("tts_speed") or 0.0),
                        "color": str(author.get("color") or "").strip(),
                        "profile_id": str(author.get("profile_id") or "").strip(),
                    }
                out.append(row_inc)
                i += 1
    return out


def _warm_model_cache() -> None:
    from backend.agent.model_fetch import fetch_models
    from backend.agent.secrets import get_key, has_key

    changed = False
    for provider in all_providers():
        if not has_key(provider):
            continue
        try:
            models = fetch_models(provider, get_key(provider))
            if models:
                _model_cache[provider] = list(models)
                changed = True
        except Exception:
            pass
    if changed:
        _save_model_cache_to_disk()


def _normalize_active_profile_id(profile_id: str) -> str:
    pid = str(profile_id or "").strip()
    # Light / Hacker moved to Store plugins — fall back to Default.
    if pid in ("__light__", "__hacker__"):
        return DEFAULT_APPEARANCE_PROFILE_ID
    if not pid or is_built_in_appearance_profile(pid):
        return pid or DEFAULT_APPEARANCE_PROFILE_ID
    return pid


def _migrate_legacy_built_in_appearance(s: Any) -> bool:
    """Light/Hacker are Store plugins now — reset saved legacy built-in selection to Default."""
    pid = str(getattr(s, "appearance_active_profile_id", "") or "").strip()
    if pid not in ("__light__", "__hacker__"):
        return False
    apply_built_in_appearance(s, DEFAULT_APPEARANCE_PROFILE_ID)
    if pid == "__hacker__" and str(getattr(s, "appearance_effect_id", "") or "").strip() == "matrix":
        s.appearance_effect_id = ""
        s.appearance_effects_enabled = False
    return True


def _reset_appearance_to_defaults(s: Any) -> None:
    apply_built_in_appearance(s, DEFAULT_APPEARANCE_PROFILE_ID)


def _patch_bool(value: Any) -> bool:
    """Coerce a settings patch value. ``bool("false")`` is True — never use that."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def _coerce_mapping(value: Any, *, label: str = "object") -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        parsed = json.loads(stripped)
        if not isinstance(parsed, dict):
            raise ValueError(f"{label} must be a JSON object")
        return parsed
    try:
        return dict(value)
    except (TypeError, ValueError):
        pass
    raise ValueError(f"{label} must be an object")


def _save_panel_settings(s: PanelSettings) -> None:
    """Atomic settings write with short retries for Windows AppData races."""
    last_err: OSError | None = None
    for attempt in range(4):
        try:
            s.save()
            return
        except OSError as e:
            last_err = e
            time.sleep(0.04 * (attempt + 1))
    if last_err is not None:
        raise last_err


def _clean_appearance_profiles(raw: list[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        if not pid or not name or is_built_in_appearance_profile(pid):
            continue
        foundation = item.get("foundation")
        overrides = item.get("overrides")
        status_overrides = item.get("status_overrides")
        cleaned.append(
            {
                "id": pid,
                "name": name,
                "foundation": {str(k): str(v) for k, v in foundation.items() if v}
                if isinstance(foundation, dict)
                else {},
                "overrides": {str(k): str(v) for k, v in overrides.items() if v}
                if isinstance(overrides, dict)
                else {},
                "status_overrides": {
                    str(sid): {str(k): str(v) for k, v in fields.items() if v}
                    for sid, fields in status_overrides.items()
                    if isinstance(fields, dict)
                }
                if isinstance(status_overrides, dict)
                else {},
            }
        )
    return cleaned


_AGENT_PUSH_BATCH_DELAY_S = 0.025


def _coalesce_agent_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Preserve event order while joining adjacent high-frequency deltas.

    pywebview ``evaluate_js`` is a synchronous native/WebView boundary. Sending
    every token delta through it separately can starve both UI threads during a
    busy coding-agent run. Tool lifecycle events remain distinct; only adjacent
    text/thinking chunks and superseded status lines for the same run collapse.
    """
    out: list[dict[str, Any]] = []
    for event in events:
        current = dict(event)
        kind = str(current.get("type") or "")
        if out:
            previous = out[-1]
            same_run = (
                previous.get("conv_id") == current.get("conv_id")
                and previous.get("run_id") == current.get("run_id")
            )
            if same_run and kind in ("text_delta", "thinking") and previous.get("type") == kind:
                previous["text"] = str(previous.get("text") or "") + str(current.get("text") or "")
                continue
            if same_run and kind == "status" and previous.get("type") == "status":
                out[-1] = current
                continue
        out.append(current)
    return out


# Mixins import this module as `_pa` for helpers/globals. Load them after those
# names exist so the circular import sees a complete helper surface.
from frontend.ui_web.panel_api_window import PanelApiWindowMixin  # noqa: E402
from frontend.ui_web.panel_api_store import PanelApiStoreMixin  # noqa: E402
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin  # noqa: E402
from frontend.ui_web.panel_api_project import PanelApiProjectMixin  # noqa: E402
from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin  # noqa: E402


class PanelApi(
    PanelApiWindowMixin,
    PanelApiStoreMixin,
    PanelApiChatsMixin,
    PanelApiProjectMixin,
    PanelApiSettingsMixin,
):
    def __init__(self) -> None:
        self._window: Any = None
        self._hide_callback: Any = None
        self._exit_callback: Any = None
        self._maximized = False
        self._sidebar_only_layout = False
        self._sidebar_only_saved_bounds: dict[str, int | float] | None = None
        self._tk_root: Any = None
        from backend.bridge.status import ListenerStatusState

        self._listener_status_state = ListenerStatusState()
        self._last_listener_status: dict[str, Any] | None = None
        self._listener_status_lock = threading.Lock()
        self._listener_was_online = False
        # Folder the sidebar reports an external OS-file drag is over; read by the
        # pywebview drop handler (see file_drop_import.py).
        self._import_drop_target = ""
        self._import_drop_lock = threading.Lock()
        self._agent_push_lock = threading.Lock()
        # Coding agents whose missing-backend repair already ran (see detect_coding_agent_cli).
        self._coding_agent_reload_tried: set[str] = set()
        self._agent_push_pending: list[dict[str, Any]] = []
        self._agent_push_timer: threading.Timer | None = None
        # uefn_plugins_changed (etc.) can fire before WebView exists — flush on bind.
        self._pending_panel_pushes: list[dict[str, Any]] = []
        self._pending_panel_push_lock = threading.Lock()
        self._verse_editor = VerseEditorApi()
        _load_model_cache_from_disk()
        # Plugins off the splash critical path — window paints, then contribs arrive.
        self._start_plugins_load_async()
        threading.Thread(target=_warm_model_cache, daemon=True, name="warm-models").start()

    def _notify_plugins_ready(self) -> None:
        try:
            _prune_model_caches_to_enabled_providers()
        except Exception:
            pass
        self._push_panel({"type": "uefn_plugins_changed"})

    def _start_plugins_load_async(self) -> None:
        try:
            from backend.uefn_plugins.host import ensure_plugins_loaded_async

            ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
        except Exception:
            pass

    def _flush_agent_pushes(self) -> None:
        with self._agent_push_lock:
            pending = self._agent_push_pending
            self._agent_push_pending = []
            self._agent_push_timer = None
        if not pending or not self._all_windows():
            return
        try:
            oldest_age_ms = 0.0
            now = time.time()
            for ev in pending:
                stamped = float(ev.pop("_push_ts", 0) or 0)
                if stamped:
                    oldest_age_ms = max(oldest_age_ms, (now - stamped) * 1000.0)
            t0 = time.perf_counter()
            coalesced = _coalesce_agent_events(pending)
            payload = json.dumps(coalesced, ensure_ascii=False)
            # Stream through the panel's loopback HTTP server. Calling
            # WebView2.evaluate_js for every agent batch can deadlock against
            # pywebview's own JS-API response callbacks under multi-agent load.
            from frontend.ui_web.panel_httpd import publish_panel_events

            publish_panel_events(coalesced)
            try:
                from frontend.perf_trace import trace

                trace(
                    "push_flush",
                    "agent_push",
                    (time.perf_counter() - t0) * 1000.0,
                    event_count=len(pending),
                    coalesced_count=len(coalesced),
                    payload_bytes=len(payload),
                    oldest_event_age_ms=round(oldest_age_ms, 3),
                    event_types=[str(e.get("type") or "") for e in coalesced],
                    conv_ids=list({str(e.get("conv_id") or "") for e in coalesced if e.get("conv_id")}),
                )
            except Exception:
                pass
        except Exception:
            pass

    def _push(self, event: dict[str, Any]) -> None:
        if not self._all_windows():
            return
        stamped = dict(event)
        stamped["_push_ts"] = time.time()
        with self._agent_push_lock:
            self._agent_push_pending.append(stamped)
            if self._agent_push_timer is not None:
                return
            timer = threading.Timer(_AGENT_PUSH_BATCH_DELAY_S, self._flush_agent_pushes)
            timer.daemon = True
            self._agent_push_timer = timer
            timer.start()

    def report_ui_perf(self, entries: list[dict[str, Any]] | None = None) -> bool:
        """Receive longtask / frame-stall samples from the React perf monitor."""
        try:
            from frontend.perf_trace import trace

            for entry in entries or []:
                if not isinstance(entry, dict):
                    continue
                kind = str(entry.get("kind") or "ui_stall")
                name = str(entry.get("name") or kind)
                duration_ms = float(entry.get("duration_ms") or 0)
                meta = {
                    k: v
                    for k, v in entry.items()
                    if k not in ("kind", "name", "duration_ms") and v is not None
                }
                trace(kind, name, duration_ms, **meta)
            return True
        except Exception:
            return False

    def _flush_pending_panel_pushes(self) -> None:
        with self._pending_panel_push_lock:
            pending = list(self._pending_panel_pushes)
            self._pending_panel_pushes.clear()
        for event in pending:
            self._push_panel(event)

    def _push_panel(self, event: dict[str, Any]) -> None:
        """Notify React of panel-level changes without WebView2 evaluate_js.

        Store enable/disable/install/uninstall call this *inside* a pywebview JS→Python
        API handler. evaluate_js from that path deadlocks WebView2 against the API
        return (UI stuck on Uninstalling… / install at ~14% until app restart). Agent
        streaming already uses the loopback HTTP bus for the same reason — join it.
        """
        try:
            from frontend.ui_web.panel_httpd import publish_panel_events

            publish_panel_events([dict(event)])
            return
        except Exception:
            pass
        # Fallback only when HTTP bus is unavailable (very early boot).
        if not self._all_windows():
            with self._pending_panel_push_lock:
                if event.get("type") == "uefn_plugins_changed":
                    self._pending_panel_pushes = [
                        e for e in self._pending_panel_pushes if e.get("type") != "uefn_plugins_changed"
                    ]
                self._pending_panel_pushes.append(dict(event))
            return
        try:
            payload = json.dumps(event, ensure_ascii=False)
            self._evaluate_all(f"window.__uefnPanelPush && window.__uefnPanelPush({payload})")
        except Exception:
            pass

    def ui_rpc_respond(self, request_id: str, payload: dict[str, Any] | None = None) -> bool:
        """React answers a ui_rpc_request (navigate / list targets / spotlight click).

        Wakes the loopback /__panel_rpc handler blocked on this request so the
        calling tool receives the result. Returns False for an unknown/expired id.
        """
        from frontend.ui_web import ui_rpc

        return ui_rpc.respond(str(request_id or ""), payload or {})

    def get_listener_status(self) -> dict[str, Any]:
        if not self._listener_status_lock.acquire(blocking=False):
            if self._last_listener_status is not None:
                return dict(self._last_listener_status)
            # Another poll is in flight and we have no cache yet — do not claim Offline.
            return {
                "online": False,
                "wedged": False,
                "busy": False,
                "version": __version__,
                "uptime_sec": 0,
                "status_text": "Checking…",
                "uefn_project_dir": "",
                "uefn_project_name": "",
                "project_match": True,
                "port": PANEL_LISTENER_PORT,
            }
        try:
            result = self._fetch_listener_status()
            self._last_listener_status = result
            self._maybe_ship_on_listener_online(result)
            return result
        finally:
            self._listener_status_lock.release()

    def _maybe_ship_on_listener_online(self, status: dict[str, Any]) -> None:
        """When UEFN listener flips offline→online, re-ship newest listener/skills/IDE bridges."""
        online = bool(status.get("online")) and not bool(status.get("wedged"))
        was = self._listener_was_online
        self._listener_was_online = online
        if online and not was:
            try:
                from frontend.ship_newest import ship_newest_everywhere_async

                ship_newest_everywhere_async(apply_ides=True, force_skills=False)
            except Exception:
                pass

    def _fetch_listener_status(self) -> dict[str, Any]:
        try:
            from backend.bridge.status import fetch_listener_status

            settings = PanelSettings.load()
            return fetch_listener_status(
                PANEL_LISTENER_PORT,
                state=self._listener_status_state,
                version=__version__,
                selected_project_root=settings.uefn_project_root,
            )
        except Exception as e:
            record_error("listener", str(e))
            return {
                "online": False,
                "version": __version__,
                "status_text": "Offline",
                "uefn_project_dir": "",
                "uefn_project_name": "",
                "project_match": True,
            }
