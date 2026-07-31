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


class PanelApi:
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

    def bind_tk_root(self, root: Any) -> None:
        self._tk_root = root

    def bind_window(self, window: Any, *, on_hide: Any, on_exit: Any) -> None:
        self._window = window
        self._hide_callback = on_hide
        self._exit_callback = on_exit
        from frontend.ui_web.agent_modes import set_panel_push

        set_panel_push(self._push)
        from frontend.ui_web.terminal import get_terminal_manager

        get_terminal_manager().set_push(self._push)
        self._flush_pending_panel_pushes()

    def _resolve_window(self) -> Any | None:
        try:
            import webview

            active = webview.active_window()
            if active is not None:
                return active
        except Exception:
            pass
        return self._window

    def _all_windows(self) -> list[Any]:
        try:
            from frontend.ui_web import focus_windows

            return focus_windows.all_windows()
        except Exception:
            return [self._window] if self._window else []

    def _evaluate_all(self, js: str) -> None:
        from frontend.ui_web.ui_dispatch import schedule_evaluate_js

        for w in self._all_windows():
            if w is None:
                continue
            schedule_evaluate_js(w, js)

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

    def get_window_bounds(self) -> dict[str, int | float]:
        w = self._resolve_window()
        if not w:
            return {"x": 0, "y": 0, "width": 920, "height": 640, "scale": 1.0}
        scale = 1.0
        if sys.platform == "win32":
            from frontend.ui_web.win_frameless import get_window_scale

            scale = get_window_scale(w)
        return {
            "x": int(w.x),
            "y": int(w.y),
            "width": int(w.width),
            "height": int(w.height),
            "scale": scale,
        }

    def set_window_bounds(self, x: int, y: int, width: int, height: int) -> None:
        w = self._resolve_window()
        if not w:
            return

        def apply() -> None:
            from frontend.ui_web import focus_windows
            from frontend.ui_web.window_layout import (
                MAIN_MIN_HEIGHT,
                MAIN_MIN_WIDTH,
                SIDEBAR_ONLY_MIN_WIDTH,
            )

            is_focus = focus_windows.is_focus_window(w)
            if is_focus:
                min_w, min_h = 360, SIDEBAR_ONLY_MIN_WIDTH
            elif self._sidebar_only_layout:
                min_w, min_h = SIDEBAR_ONLY_MIN_WIDTH, SIDEBAR_ONLY_MIN_WIDTH
            else:
                min_w, min_h = MAIN_MIN_WIDTH, MAIN_MIN_HEIGHT
            width_clamped = max(min_w, int(width))
            height_clamped = max(min_h, int(height))
            x_pos, y_pos = int(x), int(y)
            if sys.platform == "win32":
                from frontend.ui_web.win_frameless import set_window_bounds_hwnd

                if set_window_bounds_hwnd(w, x_pos, y_pos, width_clamped, height_clamped):
                    try:
                        w.x = x_pos
                        w.y = y_pos
                        w.width = width_clamped
                        w.height = height_clamped
                    except Exception:
                        pass
                    self._maximized = False
                    return
            w.move(x_pos, y_pos)
            w.resize(width_clamped, height_clamped)
            self._maximized = False

        native = getattr(w, "native", None)
        if native is not None and sys.platform == "win32":
            from frontend.ui_web.win_frameless import _run_on_form_ui

            _run_on_form_ui(native, apply)
        else:
            apply()

    def set_sidebar_only_layout(self, enabled: bool) -> None:
        """Toggle compact sidebar-only min size + native resize limits."""
        self._sidebar_only_layout = bool(enabled)
        w = self._resolve_window()
        if not w:
            return
        from frontend.ui_web.win_frameless import enter_sidebar_only_layout, exit_sidebar_only_layout

        if enabled:
            saved = enter_sidebar_only_layout(w)
            if saved:
                self._sidebar_only_saved_bounds = saved
        else:
            self._sidebar_only_saved_bounds = None
            exit_sidebar_only_layout(w)

    def get_sidebar_only_saved_bounds(self) -> dict[str, int | float] | None:
        return self._sidebar_only_saved_bounds

    def dock_focus_window_beside_main(self) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.dock_focus_window_beside_main()

    def uses_native_window_chrome(self) -> bool:
        return sys.platform == "win32"

    def begin_native_window_move(self) -> bool:
        w = self._resolve_window()
        if not w or sys.platform != "win32":
            return False
        from frontend.ui_web.win_frameless import begin_native_window_move

        return begin_native_window_move(w)

    def begin_native_window_resize(self, edge: str) -> bool:
        w = self._resolve_window()
        if not w or sys.platform != "win32":
            return False
        from frontend.ui_web.win_frameless import begin_native_window_resize

        return begin_native_window_resize(w, edge)

    def _read_maximized(self, w: Any) -> bool:
        """Prefer the real OS zoom state so native double-click maximise stays in sync."""
        if sys.platform == "win32":
            from frontend.ui_web.win_frameless import is_window_maximized

            return is_window_maximized(w)
        return self._maximized

    def is_window_maximized(self) -> bool:
        w = self._resolve_window()
        if not w:
            return False
        return self._read_maximized(w)

    def toggle_maximize(self) -> bool:
        w = self._resolve_window()
        if not w:
            return False
        # Decide from the actual window state, not a cached flag: the OS maximises the
        # window on a native caption double-click without routing through here, so a cached
        # bool drifts and the first button press becomes a wasted no-op.
        if self._read_maximized(w):
            w.restore()
            self._maximized = False
        else:
            w.maximize()
            self._maximized = True
        return self._read_maximized(w)

    def minimize_window(self) -> None:
        w = self._resolve_window()
        if w:
            w.minimize()

    def open_focus_window(self, focus_id: str, title: str, solo: bool = False) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.open_focus_window(focus_id, title, solo=bool(solo))

    def open_focus_window_group(self, tabs: list | None = None) -> None:
        """Move a batch of tabs into ONE focus window (sidebar-only hand-off)."""
        from frontend.ui_web import focus_windows

        focus_windows.open_focus_window_group(list(tabs or []))

    def list_focus_tab_ids(self) -> list[str]:
        """Tab ids currently hosted by any focus window."""
        from frontend.ui_web import focus_windows

        return focus_windows.list_focus_window_ids()

    def open_focus_window_at_point(self, focus_id: str, title: str, screen_x: int, screen_y: int) -> bool:
        """False when the drop landed back in the main window — caller keeps the tab."""
        from frontend.ui_web import focus_windows

        return focus_windows.open_focus_window_at_point(focus_id, title, int(screen_x), int(screen_y))

    def adopt_tab_into_this_focus_window(self, focus_id: str, title: str) -> None:
        from frontend.ui_web import focus_windows

        w = self._resolve_window()
        if w is None:
            raise RuntimeError("no window")
        focus_windows.adopt_tab_into_focus_window(focus_id, title, w)

    def raise_focus_window(self, focus_id: str) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.raise_focus_window(focus_id)

    # ── Browser panes (native WebView2 pinned inside a window; plugin web panes) ──

    def browser_pane_open(self, pane_id: str, url: str = "", wid: str = "") -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.open_pane(str(pane_id or ""), str(url or ""), str(wid or ""))

    def browser_pane_set_bounds(
        self,
        pane_id: str,
        x: float,
        y: float,
        width: float,
        height: float,
        viewport_w: float = 0.0,
        viewport_h: float = 0.0,
        visible: bool = True,
    ) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.set_bounds(
            str(pane_id or ""), float(x), float(y), float(width), float(height),
            float(viewport_w or 0.0), float(viewport_h or 0.0), bool(visible),
        )

    def browser_pane_navigate(self, pane_id: str, url: str) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.navigate(str(pane_id or ""), str(url or ""))

    def browser_pane_command(self, pane_id: str, command: str) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.command(str(pane_id or ""), str(command or ""))

    def browser_pane_state(self, pane_id: str) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.get_state(str(pane_id or ""))

    def browser_pane_close(self, pane_id: str) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.close_pane(str(pane_id or ""))

    def browser_pane_list(self) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        panes = browser_overlay.list_panes()
        return {"ok": True, "panes": panes, "pane_count": len(panes)}

    def browser_clear_browsing_data(self, kinds: str = "all") -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.clear_browsing_data(str(kinds or "all"))

    def browser_runtime_info(self) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.runtime_info()

    def browser_site_security(self, pane_id: str = "") -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        # Must stay sync+fast: pywebview may invoke this on the UI thread.
        # Cookie waits are skipped on that thread inside site_security_info.
        return browser_overlay.site_security_info(str(pane_id or ""))

    def browser_pane_hide_all(self) -> dict[str, Any]:
        from frontend.ui_web import browser_overlay

        return browser_overlay.hide_all_panes()

    def report_open_tabs(self, window_id: str = "", tab_ids: list | None = None) -> None:
        from frontend.ui_web import tab_registry

        tab_registry.report_open_tabs(window_id or "main", list(tab_ids or []))

    def focus_tab(self, tab_id: str, requesting_window: str = "") -> dict[str, object]:
        """VS Code single-tab rule: raise + activate the window that owns tab_id.

        Returns ok=False when no OTHER window owns it (caller opens locally + claims).
        """
        from frontend.ui_web import focus_windows, tab_registry

        owner = tab_registry.find_tab_owner(tab_id, exclude_window=requesting_window or "main")
        if not owner:
            return {"ok": False, "window_id": ""}
        try:
            if owner == "main":
                w = self._window
                if w is not None:
                    w.restore()
            else:
                # owner is an opaque wid (focus-<uuid>) — raise the OS window; the
                # tab itself activates via the tab_focus_request broadcast below.
                focus_windows.raise_window(owner)
        except Exception:
            pass
        self._push({"type": "tab_focus_request", "tab_id": tab_id, "window_id": owner})
        return {"ok": True, "window_id": owner}

    def claim_tab(self, tab_id: str, window_id: str = "") -> None:
        """Broadcast ownership; every other window holding tab_id closes its copy."""
        self._push({"type": "tab_claimed", "tab_id": tab_id, "window_id": window_id or "main"})

    def notify_focus_tab_active(self, focus_id: str, title: str) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.notify_focus_tab_active(focus_id, title)

    def report_focus_window_layout(self, birth_tab_id: str, layout: dict) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.report_focus_window_layout(birth_tab_id, layout if isinstance(layout, dict) else {})

    def return_tab_to_main(self, focus_id: str, title: str) -> bool:
        from frontend.ui_web import focus_windows

        return bool(focus_windows.return_tab_to_main(focus_id, title))

    def close_focus_window(self, focus_id: str) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.close_focus_window(focus_id)

    def close_all_focus_windows(self) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.close_all_focus_windows()

    def get_editor_workspace(self, slug: str = "") -> dict[str, Any]:
        from frontend.ui_web.editor_workspace import load_editor_workspace

        return load_editor_workspace(slug.strip() or None)

    def save_editor_workspace(self, payload: dict[str, Any]) -> None:
        from frontend.ui_web.editor_workspace import save_editor_workspace

        save_editor_workspace(payload)

    def get_workspace_dock(self, window_id: str = "main") -> dict[str, Any]:
        """Left/right rail layout (sides, widths, splits) — AppData durable store."""
        from frontend.ui_web.workspace_dock import load_window

        return load_window(window_id) or {}

    def save_workspace_dock(self, payload: dict[str, Any] | str | None) -> None:
        from frontend.ui_web.workspace_dock import save_window

        data = _coerce_mapping(payload, label="workspace dock")
        window_id = str(data.get("window_id") or "main").strip() or "main"
        snapshot = data.get("snapshot")
        if not isinstance(snapshot, dict):
            snapshot = {k: v for k, v in data.items() if k != "window_id"}
        if not snapshot:
            return
        save_window(window_id, snapshot)

    def restore_focus_windows(self, groups: list | None = None) -> None:
        from frontend.ui_web import focus_windows

        focus_windows.restore_groups(list(groups or []))

    def report_editor_state(self, relative_path: str, state: dict[str, Any]) -> None:
        from frontend.ui_web.verse_editor.editor_state_registry import report_state

        report_state(relative_path, state)

    def close_this_window(self) -> None:
        from frontend.ui_web import focus_windows

        w = self._resolve_window()
        if w is None or w is self._window:
            return
        focus_windows.close_window(w)

    def is_focus_window(self) -> bool:
        from frontend.ui_web import focus_windows

        w = self._resolve_window()
        return w is not None and focus_windows.is_focus_window(w)

    def get_version(self) -> str:
        return __version__

    def get_app_update_status(self) -> dict[str, Any]:
        from frontend.version_check import get_app_update_status

        return get_app_update_status()

    def get_install_info(self) -> dict[str, Any]:
        from frontend.install_info import get_install_info

        return get_install_info()

    def apply_update(self) -> dict[str, Any]:
        from frontend.updater import apply_update

        return apply_update()

    def get_update_progress(self) -> dict[str, Any]:
        from frontend.updater import get_update_progress

        return get_update_progress()

    def cancel_update(self) -> dict[str, Any]:
        from frontend.updater import cancel_update

        return cancel_update()

    def launch_uninstall(self) -> dict[str, Any]:
        from frontend.updater import launch_uninstall

        return launch_uninstall()

    def open_download_page(self) -> None:
        import webbrowser

        from frontend.version_check import download_page_url

        webbrowser.open(download_page_url())

    def open_devtools(self) -> dict[str, Any]:
        """Open WebView2 DevTools (Inspector) even in production builds."""
        w = self._resolve_window()
        if w is None:
            return {"ok": False, "error": "no window"}
        try:
            native = getattr(w, "native", None)
            browser = getattr(native, "browser", None) or getattr(native, "webview", None)
            # WinForms Edge: native.webview is the WebView2 control; CoreWebView2 on it.
            ctrl = getattr(native, "webview", None) or getattr(browser, "webview", None) or browser
            core = getattr(ctrl, "CoreWebView2", None)
            if core is None:
                return {"ok": False, "error": "CoreWebView2 unavailable"}
            settings = getattr(core, "Settings", None)
            if settings is not None:
                try:
                    settings.AreDevToolsEnabled = True
                    settings.AreDefaultContextMenusEnabled = True
                    settings.AreBrowserAcceleratorKeysEnabled = True
                except Exception:
                    pass
            core.OpenDevToolsWindow()
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def report_ui_crash(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Append a UI ErrorBoundary crash to AppData for support / debugging."""
        import json
        import time

        data = payload if isinstance(payload, dict) else {}
        try:
            from frontend.app_paths import resolve_app_data_dir

            path = resolve_app_data_dir(for_write=True) / "ui_crashes.jsonl"
            row = {
                "ts": time.time(),
                "version": __version__,
                "label": str(data.get("label") or ""),
                "message": str(data.get("message") or "")[:2000],
                "stack": str(data.get("stack") or "")[:8000],
                "componentStack": str(data.get("componentStack") or "")[:8000],
                "appVersion": str(data.get("appVersion") or __version__),
                "pluginId": str(data.get("pluginId") or "")[:128],
                "surface": str(data.get("surface") or "")[:128],
                "faultKind": str(data.get("faultKind") or "")[:32],
                "faultAction": str(data.get("faultAction") or "")[:32],
            }
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return {"ok": True, "path": str(path)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}

    def open_patreon_page(self) -> None:
        import webbrowser

        from frontend.version_check import PATREON_URL

        webbrowser.open(PATREON_URL)

    def open_external_url(self, url: str) -> None:
        """Open an https link in the user's default browser (settings help links)."""
        import webbrowser

        u = str(url or "").strip()
        if u.startswith("https://"):
            webbrowser.open(u)

    def duckyos_get_status(self) -> dict[str, Any]:
        # Store tab also reads status — do not gate on the Account plugin.
        from frontend.duckyos_account import get_status, refresh_status

        try:
            return refresh_status()
        except Exception:
            return get_status()

    def _require_account_plugin(self) -> dict[str, Any] | None:
        from backend.uefn_plugins.host import is_plugin_enabled

        if is_plugin_enabled("account"):
            return None
        return {
            "ok": False,
            "error": "Account plugin is disabled — enable it in Settings → Store",
            "code": "plugin_disabled",
            "logged_in": False,
            "plugin_disabled": True,
        }

    def duckyos_login(self, base_url: str = "", email: str = "", password: str = "") -> dict[str, Any]:
        """Start browser-based login (email/password args ignored — kept for API compat)."""
        blocked = self._require_account_plugin()
        if blocked:
            return blocked
        from frontend.duckyos_account import DuckyOSAccountError, start_browser_login

        _ = email, password
        try:
            return start_browser_login(str(base_url or ""))
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "logged_in": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error", "logged_in": False}

    def duckyos_cancel_login(self) -> dict[str, Any]:
        blocked = self._require_account_plugin()
        if blocked:
            return blocked
        from frontend.duckyos_account import cancel_browser_login

        return cancel_browser_login()

    def duckyos_submit_code(self, code: str = "") -> dict[str, Any]:
        """Deprecated — email codes are completed in the browser now."""
        blocked = self._require_account_plugin()
        if blocked:
            return blocked
        _ = code
        return {
            "ok": False,
            "error": "Use Sign in with browser — verification happens in the browser.",
            "code": "use_browser",
            "logged_in": False,
        }

    def duckyos_logout(self) -> dict[str, Any]:
        blocked = self._require_account_plugin()
        if blocked:
            return blocked
        from frontend.duckyos_account import DuckyOSAccountError, logout

        try:
            return logout()
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "logged_in": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error", "logged_in": False}

    def duckyos_open_admin(self) -> None:
        if self._require_account_plugin():
            return
        import webbrowser

        from frontend.duckyos_account import get_status

        status = get_status()
        base = str(status.get("base_url") or "").rstrip("/")
        if base:
            webbrowser.open(f"{base}/admin")

    def duckyos_teams_snapshot(self, stale_seconds: int = 120) -> dict[str, Any]:
        blocked = self._require_account_plugin()
        if blocked:
            return {
                "ok": False,
                "error": blocked["error"],
                "code": blocked["code"],
                "teams": [],
                "needs_team": True,
                "online": [],
            }
        from frontend.duckyos_account import DuckyOSAccountError, teams_snapshot

        try:
            return teams_snapshot(stale_seconds=int(stale_seconds or 120))
        except DuckyOSAccountError as exc:
            return {
                "ok": False,
                "error": exc.message,
                "code": exc.code,
                "teams": [],
                "needs_team": True,
                "online": [],
            }
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc),
                "code": "error",
                "teams": [],
                "needs_team": True,
                "online": [],
            }

    def duckyos_open_teams_site(self, path: str = "/teams") -> dict[str, Any]:
        blocked = self._require_account_plugin()
        if blocked:
            return {"ok": False, "error": blocked["error"], "code": blocked["code"]}
        from frontend.duckyos_account import DuckyOSAccountError, open_site_path

        try:
            return open_site_path(str(path or "/teams"))
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error"}

    def duckyos_store_catalog(self) -> dict[str, Any]:
        # Store is core — never gated by the Account plugin.
        from frontend.duckyos_account import DuckyOSAccountError, store_catalog

        try:
            return store_catalog()
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "items": []}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error", "items": []}

    def duckyos_store_download(
        self, slug: str, version: str = "", is_update: bool = False
    ) -> dict[str, Any]:
        from frontend.duckyos_account import DuckyOSAccountError, store_download_and_install

        try:
            result = store_download_and_install(
                str(slug or ""),
                version=str(version or "").strip() or None,
                replace=True,
                is_update=bool(is_update),
            )
            # Reload contributions in the live UI (no full app restart).
            if result.get("ok") and result.get("kind") == "plugin":
                self._push_panel({"type": "uefn_plugins_changed"})
            return result
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error"}

    def duckyos_store_checkout(self, slug: str) -> dict[str, Any]:
        from frontend.duckyos_account import DuckyOSAccountError, store_checkout
        import webbrowser

        try:
            result = store_checkout(str(slug or ""))
            url = str(result.get("url") or "")
            if url:
                webbrowser.open(url)
            return result
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error"}

    def duckyos_store_grant(self, session_id: str, slug: str = "") -> dict[str, Any]:
        from frontend.duckyos_account import DuckyOSAccountError, store_grant_purchase

        try:
            return store_grant_purchase(str(session_id or ""), slug=str(slug or "").strip() or None)
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error"}

    def list_uefn_plugins(self) -> dict[str, Any]:
        try:
            from backend.uefn_plugins.store import list_uefn_plugins, seed_uefn_plugins

            seed_uefn_plugins()
            return {"ok": True, "plugins": list_uefn_plugins()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "plugins": []}

    def get_uefn_plugin_contributions(self) -> dict[str, Any]:
        empty = {
            "settings_tabs": [],
            "settings_sections": [],
            "dock_panels": [],
            "editor_kinds": [],
            "header_buttons": [],
            "ui_panels": [],
            "sounds": [],
            "hooks": [],
            "agent_tools": {},
            "enabled_ids": [],
        }
        try:
            from backend.uefn_plugins.host import (
                ensure_plugins_loaded_async,
                get_contributions,
                get_ui_contributions,
                plugins_ready,
                plugins_ui_ready,
            )

            # Never sync-wait here — that re-blocks the UI for ~20s after paint.
            # UI contribs (plugin.json) land before slow register(); expose them early.
            if plugins_ready():
                return {"ok": True, **get_contributions()}
            ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
            if plugins_ui_ready():
                return {"ok": True, **get_ui_contributions()}
            return {"ok": False, "error": "plugins_loading", **empty}
        except Exception as exc:
            return {"ok": False, "error": str(exc), **empty}

    def set_skill_pack_enabled(self, pack_id: str, enabled: bool) -> dict[str, Any]:
        """Enable/disable a standalone skill pack (global deny-list). Store UI uses this."""
        from backend.tools.panel.panel_skills import set_pack_scoped

        result = set_pack_scoped(str(pack_id or ""), bool(enabled), scope="global")
        if result.get("ok"):
            return result
        return {"ok": False, "error": str(result.get("error") or "enable failed"), **result}

    def set_uefn_plugin_enabled(
        self, plugin_id: str, enabled: bool, trust_local: bool = False
    ) -> dict[str, Any]:
        try:
            from backend.uefn_plugins.store import set_uefn_plugin_enabled

            result = set_uefn_plugin_enabled(
                str(plugin_id or ""), bool(enabled), trust_local=bool(trust_local)
            )
            if result.get("ok"):
                _prune_model_caches_to_enabled_providers()
            self._push_panel({"type": "uefn_plugins_changed"})
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_uefn_plugin_secret_labels(self, plugin_id: str) -> dict[str, Any]:
        try:
            from backend.uefn_plugins.store import get_uefn_plugin_secret_labels

            return get_uefn_plugin_secret_labels(str(plugin_id or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc), "labels": [], "secret_keys": []}

    def uninstall_uefn_plugin(self, plugin_id: str, erase_data: bool = False) -> dict[str, Any]:
        try:
            from backend.uefn_plugins.store import uninstall_uefn_plugin

            result = uninstall_uefn_plugin(str(plugin_id or ""), erase_data=bool(erase_data))
            if result.get("ok"):
                _prune_model_caches_to_enabled_providers()
            self._push_panel({"type": "uefn_plugins_changed"})
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def install_uefn_plugin_bytes(self, b64: str, source: str = "local") -> dict[str, Any]:
        """Sideload or Store-install a plugin zip (base64). Never contains secrets."""
        import base64

        try:
            from backend.uefn_plugins.store import import_plugin_from_bytes

            raw = base64.b64decode(str(b64 or ""))
            result = import_plugin_from_bytes(raw, source=str(source or "local"), replace=True)
            if result.get("ok"):
                self._push_panel({"type": "uefn_plugins_changed"})
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def open_uefn_plugins_folder(self) -> None:
        from backend.uefn_plugins.store import appdata_uefn_plugins_dir, seed_uefn_plugins

        seed_uefn_plugins()
        folder = appdata_uefn_plugins_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.open_path_in_explorer(str(folder))

    def burst_desktop_confetti(self, client_x: float, client_y: float) -> None:
        """Fullscreen confetti burst at a point in the main window's client area."""
        if sys.platform != "win32" or not self._tk_root:
            return
        w = self._resolve_window()
        if not w:
            return
        screen_x = float(w.x) + float(client_x)
        screen_y = float(w.y) + float(client_y)
        from frontend.ui_web.confetti_overlay import schedule_desktop_confetti

        schedule_desktop_confetti(self._tk_root, screen_x, screen_y)

    def snip_screen(self) -> dict[str, Any]:
        """Open the Windows region snipper and attach the result to the chat.

        Accepted snips are saved under AppData chats/projects/<slug>/snips/ (panel
        storage) AND copied into the active UEFN project's Saved/DuckyCaptures/
        so agents can use the PNG without reading AppData.
        """
        if sys.platform != "win32":
            return {"ok": False, "reason": "unsupported"}
        from frontend.ui_web.snip_overlay import snip_screen_interactive

        result = snip_screen_interactive(self._tk_root)
        if result.get("ok") and result.get("data_base64"):
            try:
                import base64 as _b64
                from datetime import datetime

                from frontend.ui_web.project_chats import get_conversations_dir
                from frontend.ui_web.tool_captures import copy_png_to_ducky_captures

                raw = _b64.b64decode(str(result["data_base64"]))
                name = f"snip-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.png"

                # AppData panel copy (chat storage — agents should not rely on this path).
                snips_dir = get_conversations_dir().parent / "snips"
                snips_dir.mkdir(parents=True, exist_ok=True)
                appdata_path = snips_dir / name
                appdata_path.write_bytes(raw)
                result["name"] = name
                result["capture_path"] = str(appdata_path)

                # Project copy — primary path agents can read/import.
                project_path = copy_png_to_ducky_captures(raw, prefix="snip", filename=name)
                result["path"] = project_path or str(appdata_path)
            except Exception:
                pass  # disk copy is best-effort; the composer attachment still works
        return result

    def hide_window(self) -> None:
        if self._hide_callback:
            self._hide_callback()

    def list_folders(self) -> list[dict[str, str | float]]:
        ensure_group_folder_hubs()
        return [
            {
                "id": f.id,
                "name": f.name,
                "parent_id": f.parent_id,
                "sort_order": f.sort_order,
                "group_hub_id": getattr(f, "group_hub_id", "") or "",
            }
            for f in load_folders()
        ]

    def list_conversations(self, folder_id: str) -> list[dict[str, str | float | int]]:
        return [
            self._conversation_sidebar_row(c)
            for c in list_conversations(folder_id)
        ]

    def list_all_conversations(self) -> list[dict[str, str | float | int | bool]]:
        """All project chats in one call (metadata only) for sidebar grouping."""
        convs = list_all_conversation_metadata()
        group_ids = {c.id for c in convs if getattr(c, "is_group", False)}
        return [self._conversation_sidebar_row(c, group_ids=group_ids) for c in convs]

    def list_conversations_for_file(self, file_path: str) -> list[dict[str, str | float | int]]:
        return [
            self._conversation_sidebar_row(c)
            for c in list_conversations_for_file(file_path)
        ]

    @staticmethod
    def _sidebar_context_tokens(c: Any) -> int:
        """Cheap context-window size for sidebar hover totals (no recompute)."""
        from frontend.ui_web.token_usage import resolve_context_window_tokens

        stats = getattr(c, "coding_agent_stats", None)
        stored = 0
        num_turns = 0
        if isinstance(stats, dict):
            stored = int(stats.get("context_tokens") or 0)
            num_turns = int(stats.get("num_turns") or 0)
        last: dict[str, Any] | None = None
        usage = getattr(c, "token_usage", None)
        if isinstance(usage, dict):
            calls = usage.get("calls")
            if isinstance(calls, list) and calls and isinstance(calls[-1], dict):
                last = calls[-1]
        if last is not None:
            return resolve_context_window_tokens(
                stored_context_tokens=stored,
                input_tokens=int(last.get("input_tokens") or 0),
                cache_read_tokens=int(last.get("cache_read_tokens") or 0),
                cache_write_tokens=int(last.get("cache_write_tokens") or 0),
                num_turns=num_turns,
            )
        return max(0, stored)

    @staticmethod
    def _conversation_sidebar_row(
        c: Any,
        *,
        group_ids: set[str] | None = None,
    ) -> dict[str, str | float | int | bool]:
        del group_ids  # retained for call-site compat; subagents retired
        parent_id = (getattr(c, "parent_conv_id", None) or "").strip()
        return {
            "id": c.id,
            "title": c.title,
            "sort_order": c.sort_order,
            "updated": c.updated,
            "ducky_style": c.ducky_style,
            "ducky_name": c.ducky_name or "",
            "profile_id": (getattr(c, "profile_id", None) or "").strip(),
            "ducky_personality": c.ducky_personality or "",
            "tts_voice": getattr(c, "tts_voice", None) or "",
            "tts_speed": float(getattr(c, "tts_speed", 0) or 0.0),
            "file_path": c.file_path,
            "model": c.model or "",
            "provider": c.provider or "",
            "coding_agent": getattr(c, "coding_agent", None) or "ducky",
            "thinking_effort": getattr(c, "thinking_effort", None) or "",
            "terminal_session_id": getattr(c, "terminal_session_id", None) or "",
            "folder_id": getattr(c, "folder_id", None) or "",
            "parent_conv_id": parent_id,
            "is_group": bool(getattr(c, "is_group", False)),
            # Subagents retired — group members nest under hubs, never parent-linked seats.
            "is_subagent": False,
            "leader_conv_id": (getattr(c, "leader_conv_id", None) or "").strip(),
            "group_members": list(getattr(c, "group_members", None) or []),
            "tool_call_count": int(getattr(c, "tool_call_count", 0) or 0),
            "file_count": int(getattr(c, "file_count", 0) or 0),
            "context_tokens": PanelApi._sidebar_context_tokens(c),
        }

    def create_folder(self, name: str, parent_id: str = "") -> dict[str, str]:
        f = create_folder(name, parent_id)
        return {"id": f.id, "name": f.name}

    def rename_folder(self, folder_id: str, name: str) -> None:
        rename_folder(folder_id, name)

    def delete_folder(self, folder_id: str) -> list[str]:
        """Delete a folder. Returns the group hub chat ids it took with it."""
        # Deleting a group takes its nested groups too, so stop every runner in
        # the subtree first (same as Archive).
        hub_ids = group_hub_ids_in(folder_subtree_ids(folder_id))
        if hub_ids:
            from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

            for hub_id in hub_ids:
                for target_id in [hub_id, *conversation_descendant_ids(hub_id)]:
                    if is_agent_running(target_id):
                        cancel_agent(target_id)
        return delete_folder(folder_id)

    def create_conversation(
        self,
        folder_id: str,
        ducky_style: str | None = None,
        file_path: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        style = ducky_style if ducky_style is not None else default_bundled_style()
        settings = PanelSettings.load()
        cfg = config if isinstance(config, dict) else {}
        title = str(cfg.get("title") or "").strip()
        ducky_name = str(cfg.get("ducky_name") or "").strip()
        profile_id = str(cfg.get("profile_id") or "").strip()
        personality = str(cfg.get("ducky_personality") or "")
        tts_voice = str(cfg.get("tts_voice") or "").strip()
        tts_speed = float(cfg.get("tts_speed") or 0.0)
        thinking_effort = str(cfg.get("thinking_effort") or "").strip().lower()
        disabled_packs = cfg.get("disabled_packs")
        enabled_subskills = cfg.get("enabled_subskills")
        disabled_tool_ids = cfg.get("disabled_tool_ids")
        if isinstance(cfg.get("ducky_style"), str) and cfg.get("ducky_style").strip():
            style = str(cfg.get("ducky_style")).strip()

        from backend.agent.coding_agents.base import normalize_coding_agent
        from frontend.favorite_models import ResolveErr, ResolveOk

        coding_agent = "ducky"
        resolved_model = ""
        resolved_provider = ""

        if "favorite_models" in cfg:
            # Profile-driven create: the profile's model, else the global default.
            result = resolve_model_selection(cfg.get("favorite_models"), settings)
            if isinstance(result, ResolveErr):
                raise ValueError(result.message)
            coding_agent = result.coding_agent
            resolved_model = result.model
            resolved_provider = result.provider
        else:
            explicit_agent = str(cfg.get("coding_agent") or "").strip()
            coding_agent = normalize_coding_agent(explicit_agent or "ducky")
            # Branded chats (e.g. Duck-Tac-Toe) pass coding_agent="ducky" so a
            # Cursor/Codex Default Model must not hijack the conversation.
            force_ducky = bool(explicit_agent) and coding_agent == "ducky"
            if coding_agent == "ducky":
                # Blank chat — seed the global Default Model (Settings → LLMs) so
                # the composer opens with a working model. No default (or an
                # unavailable one) leaves the model empty for a manual pick.
                result = resolve_model_selection(None, settings)
                if isinstance(result, ResolveOk):
                    if force_ducky and result.coding_agent != "ducky":
                        api_pick = _first_available_api_model()
                        if api_pick:
                            resolved_provider, resolved_model = api_pick
                        # else: leave model empty — keep coding_agent ducky
                    else:
                        coding_agent = result.coding_agent
                        resolved_model = result.model
                        resolved_provider = result.provider

        explicit_agent = str(cfg.get("coding_agent") or "").strip()
        if explicit_agent and "favorite_models" in cfg:
            # Explicit coding_agent in config can only override when the model already
            # resolved to that agent (or ducky). Never invent a model for a forced agent.
            forced = normalize_coding_agent(explicit_agent)
            if forced != "ducky" and forced != coding_agent and not resolved_model:
                raise ValueError(
                    f"Cannot force coding agent {forced!r} without an exact model selection."
                )
            if forced != "ducky":
                coding_agent = forced

        conv = create_conversation(
            settings,
            folder_id,
            title=title,
            ducky_style=style,
            ducky_name=ducky_name,
            profile_id=profile_id,
            ducky_personality=personality,
            tts_voice=tts_voice,
            tts_speed=tts_speed,
            thinking_effort=thinking_effort,
            file_path=file_path or "",
            disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
            enabled_subskills=enabled_subskills if isinstance(enabled_subskills, dict) else None,
            disabled_tool_ids=disabled_tool_ids if isinstance(disabled_tool_ids, list) else None,
            model=resolved_model,
            provider=resolved_provider,
            coding_agent=coding_agent,
        )
        notify_chats_changed(conv.id, conv.title, folder_id)
        return {
            "id": conv.id,
            "title": conv.title,
            "ducky_style": conv.ducky_style,
            "ducky_name": conv.ducky_name or "",
            "profile_id": getattr(conv, "profile_id", None) or "",
            "ducky_personality": conv.ducky_personality,
            "tts_voice": getattr(conv, "tts_voice", None) or "",
            "tts_speed": float(getattr(conv, "tts_speed", 0) or 0.0),
            "thinking_effort": getattr(conv, "thinking_effort", None) or "",
            "file_path": conv.file_path,
            "model": conv.model or "",
            "provider": conv.provider or "",
            "coding_agent": getattr(conv, "coding_agent", None) or "ducky",
        }

    def apply_ducky_config(self, conv_id: str, config: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, dict):
            return {"ok": False, "error": "config must be an object"}
        disabled_packs = config.get("disabled_packs")
        enabled_subskills = config.get("enabled_subskills")
        disabled_tool_ids = config.get("disabled_tool_ids")
        title = config.get("title")
        ducky_name = config.get("ducky_name")
        profile_id = config.get("profile_id")
        apply_ducky_config(
            conv_id,
            ducky_style=str(config.get("ducky_style") or default_bundled_style()),
            ducky_name=str(ducky_name) if ducky_name is not None else None,
            profile_id=str(profile_id) if profile_id is not None else None,
            ducky_personality=str(config.get("ducky_personality") or ""),
            tts_voice=str(config["tts_voice"]) if "tts_voice" in config else None,
            tts_speed=float(config["tts_speed"]) if "tts_speed" in config else None,
            thinking_effort=str(config["thinking_effort"]) if "thinking_effort" in config else None,
            title=str(title) if title is not None else None,
            disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
            enabled_subskills=enabled_subskills if isinstance(enabled_subskills, dict) else None,
            disabled_tool_ids=disabled_tool_ids if isinstance(disabled_tool_ids, list) else None,
        )
        return {"ok": True}

    def group_create(self, name: str = "", folder_id: str = "") -> dict[str, Any]:
        """Create a group as a folder: folder click opens the group hub chat."""
        title = (name or "").strip() or "Group"
        settings = PanelSettings.load()
        # Parent folder_id here means "create the group-folder inside this folder".
        parent_folder = (folder_id or "").strip()
        hub_folder = create_folder(title, parent_folder)
        conv = create_conversation(
            settings,
            hub_folder.id,
            title=title,
            ducky_style=default_bundled_style(),
            ducky_name="Group",
        )
        conv.is_group = True
        conv.leader_conv_id = ""
        conv.group_members = []
        save_conversation(conv)
        # Link folder → hub so the sidebar treats the folder as the group.
        folders = load_folders()
        for f in folders:
            if f.id == hub_folder.id:
                f.group_hub_id = conv.id
                break
        save_folders(folders)
        notify_chats_changed(conv.id, conv.title, conv.folder_id)
        return {
            "ok": True,
            "id": conv.id,
            "title": conv.title,
            "is_group": True,
            "leader_conv_id": "",
            "group_members": [],
            "folder_id": hub_folder.id,
        }

    def group_invite(self, group_id: str, profile_id: str, model: str = "") -> dict[str, Any]:
        """Spawn an independent member chat from a ducky profile and add it to the group."""
        from frontend.agent_profiles import get_agent_profile
        from frontend.ducky_assets import ducky_style_label, normalize_ducky_style
        from frontend.favorite_models import ResolveErr
        from frontend.ui_web.group_orchestrator import group_members, member_color_for_index, normalize_member

        group = load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        profile = get_agent_profile(profile_id)
        if not profile:
            return {"ok": False, "error": f"Unknown ducky profile: {profile_id}"}
        existing = group_members(group)
        pid = str(profile.get("id") or profile_id).strip()
        if any(m.get("profile_id") == pid for m in existing):
            return {"ok": False, "error": "That ducky is already in this group"}
        settings = PanelSettings.load()
        override = (model or "").strip()
        favorites = [override] if override else profile.get("favorite_models")
        result = resolve_model_selection(favorites, settings)
        if isinstance(result, ResolveErr):
            return {"ok": False, "error": result.message}
        disabled_packs = profile.get("disabled_packs")
        disabled_tools = profile.get("disabled_tool_ids")
        enabled_subs = profile.get("enabled_subskills")
        style = normalize_ducky_style(str(profile.get("ducky_style") or ""))
        # Library profile name (Verse Coder) — not avatar style label (Artist).
        ducky_name = str(profile.get("name") or "").strip() or ducky_style_label(style)
        member = create_conversation(
            settings,
            group.folder_id or "",
            title=ducky_name,
            parent_conv_id=group_id,
            ducky_style=style,
            ducky_name=ducky_name,
            profile_id=pid,
            ducky_personality=str(profile.get("ducky_personality") or ""),
            tts_voice=str(profile.get("tts_voice") or "").strip(),
            tts_speed=float(profile.get("tts_speed") or 0.0),
            disabled_packs=disabled_packs if isinstance(disabled_packs, list) else None,
            enabled_subskills=enabled_subs if isinstance(enabled_subs, dict) else None,
            disabled_tool_ids=disabled_tools if isinstance(disabled_tools, list) else None,
            model=result.model,
            provider=result.provider or None,
            coding_agent=result.coding_agent,
        )
        row = normalize_member(
            {
                "member_conv_id": member.id,
                "profile_id": pid,
                "name": ducky_name,
                "ducky_name": ducky_name,
                "ducky_style": style,
                "model": result.selection.qualified,
                "coding_agent": result.coding_agent,
                "tts_voice": str(getattr(member, "tts_voice", None) or profile.get("tts_voice") or ""),
                "tts_speed": float(getattr(member, "tts_speed", None) or profile.get("tts_speed") or 0.0),
                "color": member_color_for_index(len(existing)),
            },
            index=len(existing),
        )
        group.group_members = [*existing, row]
        # First invited member becomes leader when none set yet.
        if not (getattr(group, "leader_conv_id", None) or "").strip():
            group.leader_conv_id = member.id
        save_conversation(group)
        # Reload sidebar only — do not notify the member id (that auto-opens a tab).
        notify_chats_changed(group.id, group.title, group.folder_id)
        return {
            "ok": True,
            "member": row,
            "group_members": group.group_members,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
        }

    def group_set_leader(self, group_id: str, member_conv_id: str) -> dict[str, Any]:
        """Designate the spokesperson for a group (cross-group / nested routing)."""
        from frontend.ui_web.group_orchestrator import group_members

        group = load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        mid = (member_conv_id or "").strip()
        if not mid:
            return {"ok": False, "error": "member_conv_id required"}
        members = group_members(group)
        if not any(m.get("member_conv_id") == mid for m in members):
            return {"ok": False, "error": "Member not in this group"}
        group.leader_conv_id = mid
        save_conversation(group)
        notify_chats_changed(group.id, group.title, group.folder_id)
        return {"ok": True, "leader_conv_id": mid, "group_members": members}

    def group_add_member(
        self, group_id: str, conv_id: str, as_leader: bool = False
    ) -> dict[str, Any]:
        """Move an existing chat into a group folder and roster (optional as_leader)."""
        from frontend.ui_web.group_orchestrator import (
            group_members,
            member_color_for_index,
            normalize_member,
            sync_group_members_from_folder,
        )
        from frontend.ui_web.project_chats import move_conversation

        group = load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        cid = (conv_id or "").strip()
        member = load_conversation(cid)
        if member is None:
            return {"ok": False, "error": "Conversation not found"}
        if getattr(member, "is_group", False):
            return {"ok": False, "error": "Cannot add a group hub as a leaf member"}
        folder_id = (group.folder_id or "").strip()
        if not folder_id:
            return {"ok": False, "error": "Group has no folder"}
        move_conversation(cid, folder_id)
        member = load_conversation(cid) or member
        member.parent_conv_id = group_id
        save_conversation(member)
        sync_group_members_from_folder(group)
        group = load_conversation(group_id) or group
        existing = group_members(group)
        if not any(m.get("member_conv_id") == cid for m in existing):
            name = (
                str(getattr(member, "ducky_name", None) or "").strip()
                or str(member.title or "").strip()
                or "Ducky"
            )
            row = normalize_member(
                {
                    "member_conv_id": cid,
                    "profile_id": str(getattr(member, "profile_id", None) or ""),
                    "name": name,
                    "ducky_name": str(getattr(member, "ducky_name", None) or ""),
                    "ducky_style": str(getattr(member, "ducky_style", None) or ""),
                    "model": str(getattr(member, "model", None) or ""),
                    "coding_agent": str(getattr(member, "coding_agent", None) or ""),
                    "tts_voice": str(getattr(member, "tts_voice", None) or ""),
                    "tts_speed": float(getattr(member, "tts_speed", None) or 0.0),
                    "color": member_color_for_index(len(existing)),
                },
                index=len(existing),
            )
            group.group_members = [*existing, row]
        if as_leader or not (getattr(group, "leader_conv_id", None) or "").strip():
            group.leader_conv_id = cid
        save_conversation(group)
        notify_chats_changed(group.id, group.title, group.folder_id)
        return {
            "ok": True,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
            "group_members": group_members(group),
        }

    def group_set_member_model(
        self, group_id: str, member_conv_id: str, model: str = ""
    ) -> dict[str, Any]:
        """Set the model a group member uses for their turns."""
        from frontend.favorite_models import ResolveErr
        from frontend.ui_web.group_orchestrator import group_members, normalize_member

        group = load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        mid = (member_conv_id or "").strip()
        members = group_members(group)
        if not any(m.get("member_conv_id") == mid for m in members):
            return {"ok": False, "error": "Member not in this group"}
        settings = PanelSettings.load()
        override = (model or "").strip()
        result = resolve_model_selection([override] if override else None, settings)
        if isinstance(result, ResolveErr):
            return {"ok": False, "error": result.message}
        agent_res = self.set_conversation_coding_agent(
            mid,
            result.coding_agent,
            result.model,
            result.provider or "",
        )
        if not agent_res.get("ok"):
            return {"ok": False, "error": agent_res.get("error") or "Failed to set model"}
        next_members = []
        for i, m in enumerate(members):
            row = dict(m)
            if row.get("member_conv_id") == mid:
                row["model"] = result.selection.qualified
                row["coding_agent"] = result.coding_agent
            next_members.append(normalize_member(row, index=i))
        group.group_members = next_members
        save_conversation(group)
        notify_chats_changed(group.id, group.title, group.folder_id)
        return {"ok": True, "group_members": next_members}

    def group_remove(self, group_id: str, member_conv_id: str) -> dict[str, Any]:
        from frontend.ui_web.group_orchestrator import group_members

        group = load_conversation(group_id)
        if not group or not getattr(group, "is_group", False):
            return {"ok": False, "error": "Not a group chat"}
        mid = (member_conv_id or "").strip()
        kept = [m for m in group_members(group) if m.get("member_conv_id") != mid]
        group.group_members = kept
        leader = (getattr(group, "leader_conv_id", None) or "").strip()
        if leader and leader == mid:
            group.leader_conv_id = str(kept[0].get("member_conv_id") or "") if kept else ""
        save_conversation(group)
        notify_chats_changed(group.id, group.title, group.folder_id)
        return {
            "ok": True,
            "group_members": kept,
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
        }

    def group_members(self, group_id: str) -> dict[str, Any]:
        from frontend.ui_web.group_orchestrator import (
            group_members,
            is_group_conversation,
            sync_group_members_from_folder,
        )

        group = load_conversation(group_id)
        if not group:
            return {"ok": False, "error": "Conversation not found", "members": []}
        if is_group_conversation(group):
            sync_group_members_from_folder(group)
            group = load_conversation(group_id) or group
        return {
            "ok": True,
            "is_group": is_group_conversation(group),
            "leader_conv_id": (getattr(group, "leader_conv_id", None) or "").strip(),
            "members": group_members(group) if is_group_conversation(group) else [],
        }

    def list_agent_profiles(self) -> dict[str, Any]:
        profiles = list_agent_profiles()
        return {
            "profiles": profiles,
            "template_profiles": list_bundled_agent_profile_templates(),
            "blank_profile_id": BLANK_PROFILE_ID,
        }

    def save_agent_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(profile, dict):
            raise ValueError("profile must be an object")
        saved = save_agent_profile(profile)
        self._sync_chats_for_profile(saved)
        return {"ok": True, "profile": saved}

    def save_agent_profile_override(self, bundled_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("patch must be an object")
        saved = save_agent_profile_override(bundled_id, patch)
        self._sync_chats_for_profile(saved)
        return {"ok": True, "profile": saved}

    def _sync_chats_for_profile(self, profile: dict[str, Any]) -> None:
        """Keep chat ducky_name in sync when a library profile is renamed (by profile_id)."""
        pid = str(profile.get("id") or "").strip()
        name = str(profile.get("name") or "").strip()
        if not pid or not name:
            return
        try:
            from frontend.ui_web.project_chats import (
                list_all_conversation_metadata,
                load_conversation,
                save_conversation,
            )

            for meta in list_all_conversation_metadata():
                if str(getattr(meta, "profile_id", "") or "").strip() != pid:
                    continue
                if str(getattr(meta, "ducky_name", "") or "").strip() == name:
                    continue
                conv = load_conversation(meta.id)
                if conv is None:
                    continue
                conv.ducky_name = name
                save_conversation(conv, touch_updated=False)
                notify_chats_changed(conv.id, conv.title, conv.folder_id)
        except Exception:
            logging.getLogger(__name__).exception("sync chats for profile %s failed", pid)

    def delete_agent_profile(self, profile_id: str) -> dict[str, Any]:
        delete_agent_profile(profile_id)
        return {"ok": True}

    def duplicate_agent_profile(self, profile_id: str) -> dict[str, Any]:
        duplicated = duplicate_agent_profile(profile_id)
        return {"ok": True, "profile": duplicated}

    def get_agent_profile_editor_catalog(self) -> dict[str, Any]:
        """Duckies editor catalog — never sync-wait on plugin register().

        Same non-blocking pattern as get_uefn_plugin_contributions: return packs/
        builtins/MCP immediately; fill UEFN agent tool rows once plugins_ready.
        """
        from backend.agent.builtin_toolsets import (
            builtin_group_rows,
            get_enabled_builtin_group_ids,
        )
        from backend.mcp_plugins.store import (
            get_enabled_plugin_ids,
            list_mcp_plugins,
            seed_mcp_plugins,
        )
        from backend.skills.store import default_selection_from_settings, list_skill_packs, seed_skill_packs
        from backend.uefn_plugins.host import (
            ensure_plugins_loaded_async,
            plugins_ready,
            uefn_agent_tool_rows,
        )
        from backend.uefn_plugins.store import seed_uefn_plugins
        from frontend.ui_web.project_chats import all_available_tool_ids

        seed_skill_packs()
        seed_mcp_plugins()
        seed_uefn_plugins()
        settings = PanelSettings.load()
        sel = default_selection_from_settings(settings)
        base = {
            "packs": list_skill_packs(),
            "default_disabled_packs": list(sel.disabled_packs),
            "default_disabled_tool_ids": [],
            "default_enabled_packs": sel.enabled_packs,  # derived (all − denied)
            "default_enabled_subskills": {},
        }
        if plugins_ready():
            return {
                **base,
                "tools": builtin_group_rows() + list_mcp_plugins() + uefn_agent_tool_rows(),
                "default_tool_ids": all_available_tool_ids(),
                "plugins_loading": False,
            }
        # Kick background load; UI refreshes on uefn_plugins_changed.
        ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
        return {
            **base,
            "tools": builtin_group_rows() + list_mcp_plugins(),
            "default_tool_ids": list(get_enabled_builtin_group_ids())
            + list(get_enabled_plugin_ids()),
            "plugins_loading": True,
        }

    def get_conversation_skills(self, conv_id: str) -> dict[str, Any]:
        from backend.skills.store import list_skill_packs, resolve_conversation_selection, seed_skill_packs
        from backend.mcp_plugins.store import seed_mcp_plugins

        seed_skill_packs()
        seed_mcp_plugins()
        conv = load_conversation(conv_id)
        settings = PanelSettings.load()
        packs = list_skill_packs()
        if conv:
            sel = resolve_conversation_selection(conv, settings)
        else:
            from backend.skills.store import default_selection_from_settings

            sel = default_selection_from_settings(settings)
        # For UI: packs with no stored allowlist show every toggleable root as on.
        enabled_subs = dict(sel.enabled_subskills)
        for pack in packs:
            pid = str(pack.get("id") or "")
            if not pid or pid in enabled_subs:
                continue
            if pid in sel.disabled_packs:
                continue
            toggleable = [
                str(s.get("id"))
                for s in (pack.get("subskills") or [])
                if isinstance(s, dict)
                and str(s.get("id") or "").strip()
                and not s.get("parent_id")
                and not s.get("always_on")
            ]
            enabled_subs[pid] = toggleable
        return {
            "disabled_packs": list(sel.disabled_packs),
            "enabled_packs": sel.enabled_packs,
            "enabled_subskills": enabled_subs,
            "enabled_skills": sel.enabled_packs,
            "packs": packs,
            "catalog": packs,
            "primary_filename": "uefn",
        }

    def set_conversation_skills(self, conv_id: str, filenames: list[str]) -> dict[str, Any]:
        enabled = set_conversation_enabled_skills(conv_id, filenames)
        return {"ok": True, "enabled_skills": enabled, "enabled_packs": enabled}

    def set_conversation_skill_selection(
        self,
        conv_id: str,
        enabled_packs: list[str],
        enabled_subskills: dict[str, list[str]],
    ) -> dict[str, Any]:
        packs, subs = set_conversation_skill_selection(conv_id, enabled_packs, enabled_subskills)
        return {
            "ok": True,
            "enabled_packs": packs,
            "enabled_subskills": subs,
            "enabled_skills": packs,
        }

    def set_default_enabled_skills(self, filenames: list[str]) -> dict[str, Any]:
        return self.set_default_skill_selection(filenames, {})

    def set_default_skill_selection(
        self,
        enabled_packs: list[str],
        enabled_subskills: dict[str, list[str]],
    ) -> dict[str, Any]:
        """Legacy API — clears the default deny-list (all packs available)."""
        del enabled_packs, enabled_subskills
        from backend.skills.store import merge_selection

        settings = PanelSettings.load()
        sel = merge_selection(disabled_packs=[])
        settings = replace(
            settings,
            default_disabled_packs=[],
            default_enabled_packs=[],
            default_enabled_subskills={},
            default_enabled_skills=[],
        )
        settings.save()
        return {
            "ok": True,
            "default_disabled_packs": [],
            "default_enabled_packs": sel.enabled_packs,
            "default_enabled_subskills": {},
            "default_enabled_skills": sel.enabled_packs,
        }

    def set_conversation_ducky_style(
        self,
        conv_id: str,
        ducky_style: str,
        ducky_personality: str | None = None,
    ) -> None:
        set_conversation_ducky_style(conv_id, ducky_style, ducky_personality=ducky_personality)

    def list_ducky_catalog(self) -> dict[str, Any]:
        from frontend.ui_web.panel_httpd import panel_ui_http_url

        catalog = _list_ducky_catalog()
        base = panel_ui_http_url()
        for item in catalog.get("builtin", []):
            if isinstance(item, dict):
                item["url"] = f"./duckies/{item.get('file', '')}"
        for item in catalog.get("custom", []):
            if isinstance(item, dict):
                duck_id = str(item.get("id", ""))
                slug = duck_id.removeprefix("custom:")
                item["url"] = f"{base}duckies/custom/{slug}.png"
        catalog["custom_base_url"] = base
        return catalog

    def upload_custom_ducky(self, filename: str, png_base64: str) -> dict[str, str]:
        entry = save_custom_ducky_png(filename, png_base64)
        from frontend.ui_web.panel_httpd import panel_ui_http_url

        base = panel_ui_http_url()
        slug = entry.id.removeprefix("custom:")
        return {
            "id": entry.id,
            "label": entry.label,
            "file": entry.file,
            "kind": entry.kind,
            "url": f"{base}duckies/custom/{slug}.png",
        }

    def delete_custom_ducky(self, style_id: str) -> dict[str, bool]:
        return {"ok": delete_custom_ducky(style_id)}

    def open_custom_duckies_folder(self) -> None:
        d = custom_duckies_dir(for_write=True)
        os.startfile(str(d))  # type: ignore[attr-defined]

    def list_custom_verse_templates(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in list_custom_verse_templates()]

    def get_custom_verse_template(self, template_id: str) -> dict[str, Any] | None:
        entry = get_custom_verse_template(template_id)
        return entry.to_dict() if entry else None

    def save_custom_verse_template(
        self,
        name: str,
        icon: str,
        content: str = "",
        template_id: str = "",
        folder: str = "",
        files_json: str = "",
    ) -> dict[str, Any]:
        """Create or update a user Verse template (never Store/plugin templates).

        files_json: optional JSON array of ``{path, content}`` for multi-file packs.
        Pass template_id to update an existing ``custom:…`` template.
        """
        files: Any = None
        raw = (files_json or "").strip()
        if raw:
            try:
                files = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid files_json: {exc}") from exc
        return save_custom_verse_template(
            name,
            icon,
            content,
            template_id=template_id or "",
            folder=folder or "",
            files=files,
        ).to_dict()

    def delete_custom_verse_template(self, template_id: str) -> dict[str, bool]:
        return {"ok": delete_custom_verse_template(template_id)}

    def rename_conversation(self, conv_id: str, title: str) -> None:
        conv = load_conversation(conv_id)
        if conv:
            conv.title = title.strip() or conv.title
            save_conversation(conv)

    def move_conversation(self, conv_id: str, folder_id: str) -> None:
        if is_archive_folder_id(folder_id):
            from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

            for target_id in [conv_id, *conversation_descendant_ids(conv_id)]:
                if is_agent_running(target_id):
                    cancel_agent(target_id)
        move_conversation(conv_id, folder_id or "")

    def delete_conversation(self, conv_id: str) -> None:
        from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

        for target_id in [conv_id, *conversation_descendant_ids(conv_id)]:
            if is_agent_running(target_id):
                cancel_agent(target_id)
        delete_conversation(conv_id)

    def apply_sidebar_layout(self, payload: dict[str, Any]) -> None:
        folders = payload.get("folders")
        chats = payload.get("chats")
        if not isinstance(folders, list) or not isinstance(chats, list):
            raise ValueError("layout payload must include folders and chats arrays")
        apply_sidebar_layout(folders=folders, chats=chats)

    def load_messages(self, conv_id: str) -> list[dict[str, Any]]:
        """The one message-load path: whole conversation as flat UI rows.

        Conversations are small local files (a handful of turns), so there is no
        pagination — `_messages_to_ui` is the single source of truth for how a
        stored conversation becomes the rows the chat renders.
        """
        conv = load_conversation(conv_id)
        if not conv:
            return []
        return _messages_to_ui(conv)

    def get_project_info(self) -> dict[str, str]:
        return get_panel_project_info()

    def list_recent_projects(self) -> list[dict[str, str | bool]]:
        return list_panel_projects()

    def set_project_root(self, path: str) -> dict[str, str]:
        self._listener_project_cache = None
        self._ping_fail_streak = 0
        # Central switch (shared with the ducky_set_project MCP tool). background_deploy
        # keeps the one-time deploy off the UI thread so the switch returns instantly.
        return switch_panel_project(
            path=path, push_ui=True, background_deploy=True, on_deploy_log=_log
        )

    def delete_recent_project(self, path: str) -> dict[str, str]:
        from frontend.ui_web.project_switch import delete_panel_project

        self._listener_project_cache = None
        self._ping_fail_streak = 0
        return delete_panel_project(path, push_ui=True)

    def list_project_files(self, relative_path: str = "") -> dict[str, object]:
        return list_project_files(relative_path)

    def list_workspace_roots(self) -> list[dict[str, object]]:
        return list_workspace_roots()

    def list_project_file_paths(self) -> list[dict[str, str]]:
        return list_project_file_paths()

    def search_workspace(
        self,
        query: str,
        scope: str = "both",
        case_sensitive: bool = False,
        whole_word: bool = False,
        max_results: int = 500,
    ) -> dict[str, object]:
        scope_val = scope if scope in ("files", "chats", "both") else "both"
        return _search_workspace(
            query,
            scope=scope_val,  # type: ignore[arg-type]
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            max_results=max_results,
        )

    def replace_workspace(
        self,
        query: str,
        replacement: str,
        case_sensitive: bool = False,
        whole_word: bool = False,
    ) -> dict[str, object]:
        """Replace in Verse files only; never modifies duckies or chat history."""
        return _replace_workspace(
            query,
            replacement,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
        )

    def open_project_file(self, relative_path: str) -> None:
        open_project_file(relative_path)

    def open_asset_in_uefn(self, relative_path: str) -> dict[str, object]:
        """Reveal a project asset in UEFN (Content Browser). Rich preview UI is the Store plugin."""
        from pathlib import Path

        from frontend.settings import PANEL_LISTENER_PORT

        rel = (relative_path or "").strip().replace("\\", "/")
        content_rel = rel[8:] if rel.lower().startswith("content/") else rel
        content_rel = Path(content_rel).with_suffix("").as_posix().lstrip("/")
        asset_path = f"/Game/{content_rel}"
        try:
            from backend.bridge import post_command_to_listener

            info = post_command_to_listener(PANEL_LISTENER_PORT, "get_project_info", {}, timeout=4.0)
            root = str((info or {}).get("content_root") or "").strip().rstrip("/")
            if root:
                asset_path = f"{root}/{content_rel}"
            res = post_command_to_listener(
                PANEL_LISTENER_PORT,
                "open_asset_in_uefn",
                {"asset_path": asset_path},
                timeout=15.0,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "asset_path": asset_path}
        ok = bool(res.get("success", True)) if isinstance(res, dict) else True
        opened = bool(res.get("opened")) if isinstance(res, dict) else None
        return {"ok": ok, "asset_path": asset_path, "opened": opened}

    def read_project_file(self, relative_path: str) -> dict[str, str]:
        return read_project_file(relative_path)

    def classify_project_file(self, relative_path: str) -> dict[str, object]:
        return classify_project_file(relative_path)

    def project_file_media_url(self, relative_path: str) -> dict[str, str]:
        """Return a loopback URL for rendering an image/model/audio/video file in an editor tab."""
        from frontend.ui_web.project_media import build_model_media_urls, build_project_media_url

        result = read_project_file(relative_path)
        kind = result.get("kind") or ""
        url = result.get("media_url") or ""
        if not url and kind == "image":
            url = build_project_media_url(result.get("path") or relative_path)
        out: dict[str, str] = {
            "path": result.get("path") or relative_path,
            "media_url": url,
            "mime": result.get("mime") or "",
            "kind": kind,
        }
        if kind == "model":
            if not url:
                urls = build_model_media_urls(result.get("path") or relative_path)
                out.update(urls)
            else:
                out["media_base_url"] = result.get("media_base_url") or ""
                out["media_filename"] = result.get("media_filename") or ""
        return out

    def stat_project_file(self, relative_path: str) -> dict[str, int | str | bool]:
        return stat_project_file(relative_path)

    def fingerprint_project_dirs(self, relative_paths: list[str]) -> dict[str, object]:
        return fingerprint_project_dirs(relative_paths)

    def create_project_folder(self, parent_relative: str, name: str) -> dict[str, str]:
        return create_project_folder(parent_relative, name)

    def copy_project_entry(self, source_relative: str, dest_parent_relative: str) -> dict[str, str]:
        return copy_project_entry(source_relative, dest_parent_relative)

    def move_project_entry(self, source_relative: str, dest_parent_relative: str) -> dict[str, str]:
        result = move_project_entry(source_relative, dest_parent_relative)
        remap_conversation_file_paths(source_relative, result["path"])
        # Moves are renames as far as tabs/diagnostics are concerned.
        self._push(
            {
                "type": "file_renamed",
                "old_path": source_relative.replace("\\", "/"),
                "new_path": str(result.get("path") or "").replace("\\", "/"),
            }
        )
        return result

    def delete_project_entry(self, relative_path: str) -> dict[str, str]:
        result = delete_project_entry(relative_path)
        # All windows purge diagnostics for the dead path and close the LSP document.
        self._push({"type": "file_deleted", "old_path": relative_path.replace("\\", "/")})
        return result

    def restore_trashed_entry(self, trash_token: str) -> dict[str, str]:
        """Ctrl+Z undo of a delete — move the trashed file/folder back into Content."""
        return restore_trashed_entry(trash_token)

    def rename_project_entry(self, source_relative: str, new_name: str) -> dict[str, str]:
        result = rename_project_entry(source_relative, new_name)
        remap_conversation_file_paths(source_relative, result["path"])
        # Open tabs must follow the rename (VS Code renames the tab in place) — every
        # window remaps its tab id/path/name instead of orphaning the old tab.
        self._push(
            {
                "type": "file_renamed",
                "old_path": source_relative.replace("\\", "/"),
                "new_path": str(result.get("path") or "").replace("\\", "/"),
            }
        )
        return result

    def create_project_verse_file(self, parent_relative: str, name: str, content: str = "") -> dict[str, str]:
        return create_project_verse_file(parent_relative, name, content)

    def create_project_file(self, parent_relative: str, name: str, content: str = "") -> dict[str, str]:
        return create_project_file(parent_relative, name, content)

    def set_import_drop_target(self, dest_parent_relative: str = "") -> None:
        """Sidebar reports the folder an external OS-file drag is currently over ("" = none)."""
        with self._import_drop_lock:
            self._import_drop_target = (dest_parent_relative or "").strip()

    def read_import_drop_target(self) -> str:
        with self._import_drop_lock:
            return self._import_drop_target

    def consume_pending_open_files(self) -> list[str]:
        """Absolute paths from Open-with / CLI / second-instance handoff (cold start)."""
        from frontend.open_files import take_pending_open_paths

        return take_pending_open_paths()

    def consume_pending_deep_links(self) -> list[str]:
        """``uefn-ducky://…`` URLs from a browser protocol launch (cold start)."""
        from frontend.open_files import take_pending_deep_links

        return take_pending_deep_links()

    def import_external_entries(
        self, dest_parent_relative: str, source_paths: list[str]
    ) -> dict[str, object]:
        return import_external_entries(dest_parent_relative, source_paths)

    def write_project_file(self, relative_path: str, content: str) -> dict[str, object]:
        # ext: = a dragged-in external file edited in place; write straight to its real
        # location, bypassing the project-scoped Content-only write path.
        if relative_path.strip().replace("\\", "/").lower().startswith(EXT_PATH_PREFIX):
            return write_external_file(relative_path, content)
        return self._verse_editor.write_file(relative_path, content)

    def record_external_file_change(
        self,
        relative_path: str,
        previous_content: str,
        new_content: str,
    ) -> dict[str, object]:
        return self._verse_editor.record_external_file_change(relative_path, previous_content, new_content)

    def list_file_history(self, relative_path: str) -> list[dict[str, Any]]:
        from frontend.ui_web.verse_editor import file_history

        return file_history.list_entries(relative_path)

    def read_file_history_entry(self, relative_path: str, entry_id: str) -> dict[str, str]:
        from frontend.ui_web.verse_editor import file_history

        return file_history.read_entry(relative_path, entry_id)

    def snapshot_file_history(self, relative_path: str, content: str) -> dict[str, str]:
        from frontend.ui_web.verse_editor import file_history

        return file_history.snapshot_editor_content(relative_path, content)

    def get_verse_lsp_status(self, client_id: str = "") -> dict[str, object]:
        return self._verse_editor.get_lsp_status(client_id or None)

    def start_verse_lsp(self, project_root: str = "", client_id: str = "") -> dict[str, object]:
        return self._verse_editor.start_lsp(project_root or None, client_id or None)

    def stop_verse_lsp(self, client_id: str = "") -> None:
        self._verse_editor.stop_lsp(client_id or None)

    def load_verse_diagnostics_cache(self, project_root: str = "") -> dict[str, object]:
        return self._verse_editor.load_verse_diagnostics_cache(project_root or None)

    def save_verse_file_cache(
        self,
        relative_path: str,
        errors: int,
        warnings: int,
        items: list | None = None,
        project_root: str = "",
    ) -> dict[str, object]:
        return self._verse_editor.save_verse_file_cache(
            relative_path,
            errors,
            warnings,
            items,
            project_root or None,
        )

    def scan_verse_diagnostics(self, project_root: str = "", full: bool = False) -> dict[str, object]:
        return self._verse_editor.scan_verse_diagnostics(project_root or None, full=full)

    def stop_verse_diagnostics_scan(self, project_root: str = "") -> dict[str, object]:
        return self._verse_editor.stop_verse_diagnostics_scan(project_root or None)

    def connect_verse_workflow(self) -> dict[str, object]:
        return self._verse_editor.connect_verse_workflow()

    def disconnect_verse_workflow(self) -> dict[str, object]:
        return self._verse_editor.disconnect_verse_workflow()

    def get_verse_workflow_status(self) -> dict[str, object]:
        return self._verse_editor.get_verse_workflow_status()

    def compile_verse_project(self) -> dict[str, object]:
        return self._verse_editor.compile_verse_project()

    def push_verse_changes(self, verse_only: bool = True) -> dict[str, object]:
        return self._verse_editor.push_verse_changes(verse_only)

    def _tester_project_root(self) -> str:
        from frontend.ui_web.verse_editor.lsp.project_root import normalize_verse_lsp_project_root

        return normalize_verse_lsp_project_root(PanelSettings.load().uefn_project_root.strip())

    def _require_tester_plugin(self) -> dict[str, Any] | None:
        """Tester dock + bridge require the Store tester plugin to be enabled."""
        try:
            from backend.uefn_plugins.host import is_plugin_enabled

            if is_plugin_enabled("tester"):
                return None
        except Exception:
            pass
        return {"ok": False, "error": "Tester plugin is disabled — enable it in Settings → Store"}

    def tester_list_devices(self) -> dict[str, Any]:
        """Device outliner for the Tester dock: live graph + workspace Verse sources."""
        blocked = self._require_tester_plugin()
        if blocked:
            return blocked
        from backend.testing.device_sim import device_graph_audit, scan_verse_devices_from_files

        root = self._tester_project_root()
        live: dict[str, Any] | None = None
        err: str | None = None
        try:
            from backend.bridge import send_command

            # Short timeout: offline probe must not block Sim / refresh for 30s.
            live = send_command(
                "device_graph_snapshot",
                {
                    "limit": 100,
                    "include_editables": True,
                    "include_events": True,
                },
                timeout=2.0,
            )
        except Exception as exc:
            err = str(exc)
        workspace = scan_verse_devices_from_files(root) if root else {"nodes": [], "count": 0}
        audit = device_graph_audit(live) if live else None
        return {
            "ok": True,
            "listener_online": live is not None,
            "live": live,
            "workspace": workspace,
            "audit": audit,
            "error": err,
        }

    def tester_simulate(
        self,
        device: str,
        event: str = "InteractedWithEvent",
        snapshot_json: str = "",
    ) -> dict[str, Any]:
        """Run offline event simulation. Prefer client-supplied snapshot (no listener hop)."""
        blocked = self._require_tester_plugin()
        if blocked:
            return {**blocked, "trace": [], "effects": []}
        import json

        from backend.testing.device_sim import simulate_device_event

        listener_online = False
        snapshot: dict[str, Any] | None = None
        raw = str(snapshot_json or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    snapshot = parsed
                    listener_online = bool(parsed.get("listener_online"))
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"invalid snapshot_json: {exc}", "trace": [], "effects": []}
        if snapshot is None:
            listed = self.tester_list_devices()
            snapshot = listed.get("live") or {"nodes": [], "edges": []}
            if not (snapshot.get("nodes") or []):
                snapshot = listed.get("workspace") or snapshot
            listener_online = bool(listed.get("listener_online"))
        # Normalize: UI may send {live, workspace} or a flat {nodes, edges}.
        if not (snapshot.get("nodes") or []) and (
            snapshot.get("live") is not None or snapshot.get("workspace") is not None
        ):
            live = snapshot.get("live") if isinstance(snapshot.get("live"), dict) else None
            workspace = (
                snapshot.get("workspace") if isinstance(snapshot.get("workspace"), dict) else None
            )
            listener_online = bool(snapshot.get("listener_online") or live)
            snapshot = live if live and (live.get("nodes") or []) else (workspace or {"nodes": [], "edges": []})
        try:
            result = simulate_device_event(
                snapshot, str(device or ""), str(event or "InteractedWithEvent")
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "trace": [], "effects": []}
        result["listener_online"] = listener_online
        return result

    def tester_list_tests(self) -> dict[str, Any]:
        blocked = self._require_tester_plugin()
        if blocked:
            return {**blocked, "tests": []}
        from backend.testing.verse_harness import list_harness_tests

        root = self._tester_project_root()
        if not root:
            return {"ok": False, "error": "No UEFN project root configured", "tests": []}
        tests = list_harness_tests(root)
        return {"ok": True, "tests": tests, "count": len(tests)}

    def tester_scaffold(self, overwrite: bool = False) -> dict[str, Any]:
        """Write the Verse/DuckyTests harness scaffold into the project."""
        blocked = self._require_tester_plugin()
        if blocked:
            return blocked
        import os

        from backend.bridge import resolve_workspace_path
        from backend.testing.verse_harness import scaffold_content

        rel = "Verse/DuckyTests/ducky_test_device.verse"
        try:
            path = resolve_workspace_path(rel)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if os.path.isfile(path) and not overwrite:
            return {
                "ok": True,
                "path": rel,
                "created": False,
                "note": "already exists — pass overwrite to replace",
            }
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        content = scaffold_content()
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return {"ok": True, "path": rel, "created": True}

    def tester_run_tests(self, start_session: bool = False) -> dict[str, Any]:
        """Compile + push Verse harness; optionally start a play session."""
        blocked = self._require_tester_plugin()
        if blocked:
            return blocked
        compile_data = self.compile_verse_project()
        push_data: dict[str, Any]
        try:
            push_data = dict(self.push_verse_changes(True))
        except Exception as exc:
            push_data = {"ok": False, "error": str(exc)}
        session: dict[str, Any] = {"started": False}
        if start_session:
            try:
                from backend.bridge import send_command

                session = send_command("play_in_editor", {})
                session["started"] = True
            except Exception as exc:
                session = {"started": False, "error": str(exc)}
        return {"ok": True, "compile": compile_data, "push": push_data, "session": session}

    def tester_results(self, last_n: int = 500, since_offset: int = 0) -> dict[str, Any]:
        """Parse [DUCKY-TEST] lines from the editor log."""
        blocked = self._require_tester_plugin()
        if blocked:
            return {**blocked, "results": []}
        from backend.testing.verse_harness import parse_test_results

        try:
            from backend.bridge import send_command

            log = send_command(
                "get_editor_log",
                {
                    "last_n": int(last_n or 500),
                    "since_offset": int(since_offset or 0),
                    "regex": r"\[DUCKY-TEST\]",
                },
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "results": []}
        parsed = parse_test_results(list(log.get("lines") or []))
        parsed["log_offset"] = log.get("offset")
        parsed["log_file"] = log.get("file")
        if log.get("error"):
            parsed["log_error"] = log["error"]
        return parsed

    def tester_create_chat(self, device_label: str = "") -> dict[str, Any]:
        """Spawn a Tester Ducky chat pre-prompted for the selected device."""
        blocked = self._require_tester_plugin()
        if blocked:
            return blocked
        from frontend.agent_profiles import list_bundled_agent_profile_templates

        profile = next(
            (p for p in list_bundled_agent_profile_templates() if str(p.get("id") or "") == "tester"),
            {},
        )
        label = str(device_label or "").strip()
        title = f"Test {label}" if label else "Tester"
        prompt = (
            f"Create and run tests for device `{label}`. "
            "Start with device_graph_snapshot + device_graph_audit, then simulate_device_event, "
            "then verse_test_scaffold / verse_test_run as needed."
            if label
            else (
                "Audit the level device graph, simulate key interaction chains, "
                "and scaffold/run Verse harness tests for leveling and movement."
            )
        )
        conv = self.create_conversation(
            "",
            str(profile.get("ducky_style") or "hacker"),
            None,
            {
                "title": title,
                "ducky_name": str(profile.get("name") or "Tester"),
                "ducky_personality": str(profile.get("ducky_personality") or ""),
                "ducky_style": str(profile.get("ducky_style") or "hacker"),
                "disabled_packs": list(profile.get("disabled_packs") or []),
                "disabled_tool_ids": list(profile.get("disabled_tool_ids") or []),
            },
        )
        return {"ok": True, "conversation": conv, "prompt": prompt, "device": label}

    def get_urc_status(self, project_root: str = "") -> dict[str, object]:
        return self._verse_editor.get_urc_status(project_root or None)

    def urc_commit(self, message: str = "", project_root: str = "") -> dict[str, object]:
        return self._verse_editor.urc_commit(message, project_root or None)

    def urc_push(self, project_root: str = "") -> dict[str, object]:
        return self._verse_editor.urc_push(project_root or None)

    def is_verse_editor_enabled(self) -> bool:
        return self._verse_editor.is_enabled()

    def get_settings(self) -> dict[str, str]:
        s = PanelSettings.load()
        from backend.agent.coding_agents.settings_helpers import coding_agents_dict

        return {
            "agent_provider": s.agent_provider,
            "agent_model": s.agent_model,
            "default_model": s.default_model,
            "uefn_project_root": s.uefn_project_root,
            "antigravity_config_path": s.antigravity_config_path,
            "verse_diagnostics_cache_enabled": s.verse_diagnostics_cache_enabled,
            "verse_diagnostics_auto_check": s.verse_diagnostics_auto_check,
            "show_hidden_project_files": s.show_hidden_project_files,
            "terminals_enabled": s.terminals_enabled,
            "prompt_caching_enabled": s.prompt_caching_enabled,
            "freeze_prompt_prefix": s.freeze_prompt_prefix,
            "anthropic_extended_cache_ttl": s.anthropic_extended_cache_ttl,
            "default_coding_agent": s.default_coding_agent or "ducky",
            "coding_agents": coding_agents_dict(s),
            "duckyos_base_url": s.duckyos_base_url or "",
            "voice_enabled": bool(s.voice_enabled),
            "voice_spoken_style": s.voice_spoken_style or "summary",
            "voice_summary_model": s.voice_summary_model or "",
            "voice_default_voice": s.voice_default_voice or "",
            "voice_default_speed": float(getattr(s, "voice_default_speed", 1.0) or 1.0),
            "voice_live_manual_send": bool(getattr(s, "voice_live_manual_send", False)),
            "voice_process_talk": float(getattr(s, "voice_process_talk", 0.7) or 0.0),
            "mic_permission": s.mic_permission if s.mic_permission in ("ask", "allow", "block") else "ask",
            "mic_device_id": s.mic_device_id or "",
            "output_device_id": getattr(s, "output_device_id", "") or "",
            "tts_volume": float(s.tts_volume) if s.tts_volume is not None else 1.0,
            "audio_muted": bool(s.audio_muted),
            "memory_auto_compress": bool(s.memory_auto_compress),
            "prompt_dedupe_exact_blocks": bool(getattr(s, "prompt_dedupe_exact_blocks", False)),
            "memory_keep_last_messages": int(s.memory_keep_last_messages or 20),
            "memory_compress_messages": int(s.memory_compress_messages or 40),
            "memory_compress_tokens": int(s.memory_compress_tokens or 80_000),
            "memory_index_max_chars": int(s.memory_index_max_chars or 2_500),
            "memory_summary_model": s.memory_summary_model or "",
            "walkthrough_completed": {
                str(k): bool(v) for k, v in (s.walkthrough_completed or {}).items() if v
            },
        }

    def get_appearance(self) -> dict[str, Any]:
        s = PanelSettings.load()
        if _migrate_legacy_built_in_appearance(s):
            s.validate()
            _save_panel_settings(s)
        return {
            "foundation": dict(s.appearance_foundation or {}),
            "overrides": dict(s.appearance_overrides or {}),
            "status_overrides": dict(s.appearance_status_overrides or {}),
            "profiles": _clean_appearance_profiles(s.appearance_profiles or []),
            "active_profile_id": _normalize_active_profile_id(s.appearance_active_profile_id),
            "effect_id": str(s.appearance_effect_id or ""),
            "effects_enabled": bool(s.appearance_effects_enabled),
            "skin_id": str(s.appearance_skin_id or ""),
            "profile_patches": dict(s.appearance_profile_patches or {}),
            "sounds": dict(s.appearance_sounds or {}),
        }

    def save_appearance(self, patch: dict[str, Any] | str | None) -> str:
        data = _coerce_mapping(patch, label="appearance patch")
        s = PanelSettings.load()
        foundation = data.get("foundation")
        if isinstance(foundation, dict):
            s.appearance_foundation = {str(k): str(v) for k, v in foundation.items() if v}
        overrides = data.get("overrides")
        if isinstance(overrides, dict):
            s.appearance_overrides = {str(k): str(v) for k, v in overrides.items() if v}
        status_overrides = data.get("status_overrides")
        if isinstance(status_overrides, dict):
            cleaned: dict[str, dict[str, str]] = {}
            for status_id, fields in status_overrides.items():
                if not isinstance(fields, dict):
                    continue
                row = {str(k): str(v) for k, v in fields.items() if v}
                if row:
                    cleaned[str(status_id)] = row
            s.appearance_status_overrides = cleaned
        profiles = data.get("profiles")
        if isinstance(profiles, list):
            s.appearance_profiles = _clean_appearance_profiles(profiles)
        if "active_profile_id" in data:
            s.appearance_active_profile_id = str(data.get("active_profile_id") or "")
        if "effect_id" in data:
            s.appearance_effect_id = str(data.get("effect_id") or "").strip()
        if "effects_enabled" in data:
            s.appearance_effects_enabled = bool(data.get("effects_enabled"))
        if "skin_id" in data:
            s.appearance_skin_id = str(data.get("skin_id") or "").strip()
        profile_patches = data.get("profile_patches")
        if isinstance(profile_patches, dict):
            cleaned_patches: dict[str, dict[str, Any]] = {}
            for pid, row in profile_patches.items():
                if not isinstance(row, dict):
                    continue
                cleaned_patches[str(pid)] = dict(row)
            s.appearance_profile_patches = cleaned_patches
        sounds = data.get("sounds")
        if isinstance(sounds, dict):
            mapping_raw = sounds.get("mapping")
            mapping: dict[str, str] = {}
            if isinstance(mapping_raw, dict):
                for hk, ref in mapping_raw.items():
                    if isinstance(hk, str) and isinstance(ref, str):
                        mapping[hk] = ref
            volume = sounds.get("volume")
            try:
                vol_f = float(volume) if volume is not None else 0.5
            except (TypeError, ValueError):
                vol_f = 0.5
            vol_f = max(0.0, min(1.0, vol_f))
            s.appearance_sounds = {
                "enabled": bool(sounds.get("enabled")),
                "volume": vol_f,
                "mapping": mapping,
            }
        s.validate()
        _save_panel_settings(s)
        self._push_appearance_changed()
        return "Appearance saved"

    def _push_appearance_changed(self) -> None:
        self._push_panel({"type": "appearance_changed", "appearance": self.get_appearance()})

    def push_appearance_live(self, data: dict[str, Any]) -> None:
        """Broadcast in-memory appearance to all windows without persisting."""
        if not isinstance(data, dict):
            return
        self._push_panel({"type": "appearance_changed", "appearance": data})

    def save_appearance_profile(self, name: str) -> dict[str, Any]:
        import uuid

        s = PanelSettings.load()
        profile_id = str(uuid.uuid4())
        profile = {
            "id": profile_id,
            "name": (name or "Untitled").strip() or "Untitled",
            "foundation": dict(s.appearance_foundation or {}),
            "overrides": dict(s.appearance_overrides or {}),
            "status_overrides": dict(s.appearance_status_overrides or {}),
        }
        profiles = list(s.appearance_profiles or [])
        profiles.append(profile)
        s.appearance_profiles = profiles
        s.appearance_active_profile_id = profile_id
        s.validate()
        s.save()
        return profile

    def load_appearance_profile(self, profile_id: str) -> dict[str, Any]:
        s = PanelSettings.load()
        pid = str(profile_id or "").strip()
        if is_built_in_appearance_profile(pid):
            if not apply_built_in_appearance(s, pid):
                raise ValueError(f"Profile not found: {profile_id}")
            s.validate()
            s.save()
            self._push_appearance_changed()
            return self.get_appearance()
        profile = next((p for p in (s.appearance_profiles or []) if p.get("id") == pid), None)
        if not profile:
            raise ValueError(f"Profile not found: {profile_id}")
        s.appearance_foundation = dict(profile.get("foundation") or {})
        s.appearance_overrides = dict(profile.get("overrides") or {})
        s.appearance_status_overrides = dict(profile.get("status_overrides") or {})
        s.appearance_active_profile_id = pid
        s.validate()
        s.save()
        self._push_appearance_changed()
        return self.get_appearance()

    def rename_appearance_profile(self, profile_id: str, name: str) -> dict[str, Any]:
        s = PanelSettings.load()
        pid = str(profile_id or "").strip()
        if is_built_in_appearance_profile(pid):
            raise ValueError("Built-in appearance profiles cannot be renamed")
        new_name = (name or "").strip()
        if not new_name:
            raise ValueError("Profile name is required")
        profiles = list(s.appearance_profiles or [])
        found = False
        for p in profiles:
            if p.get("id") == pid:
                p["name"] = new_name
                found = True
                break
        if not found:
            raise ValueError(f"Profile not found: {profile_id}")
        s.appearance_profiles = profiles
        s.validate()
        s.save()
        return next(p for p in profiles if p.get("id") == pid)

    def delete_appearance_profile(self, profile_id: str) -> str:
        s = PanelSettings.load()
        pid = str(profile_id or "").strip()
        if is_built_in_appearance_profile(pid):
            raise ValueError("Built-in appearance profiles cannot be deleted")
        profiles = [p for p in (s.appearance_profiles or []) if p.get("id") != pid]
        if len(profiles) == len(s.appearance_profiles or []):
            raise ValueError(f"Profile not found: {profile_id}")
        s.appearance_profiles = profiles
        was_active = str(s.appearance_active_profile_id or "").strip() == pid
        if was_active:
            # Dropping the active profile → snap working colors back to Default.
            apply_built_in_appearance(s, DEFAULT_APPEARANCE_PROFILE_ID)
            s.appearance_effect_id = ""
            s.appearance_effects_enabled = False
            s.appearance_skin_id = ""
        s.validate()
        s.save()
        if was_active:
            self._push_appearance_changed()
        return "Profile deleted"

    def update_active_appearance_profile(self) -> dict[str, Any] | None:
        """Update the active profile snapshot from current working appearance state."""
        s = PanelSettings.load()
        pid = (s.appearance_active_profile_id or "").strip()
        if not pid or is_built_in_appearance_profile(pid):
            return None
        profiles = list(s.appearance_profiles or [])
        updated = None
        for p in profiles:
            if p.get("id") == pid:
                p["foundation"] = dict(s.appearance_foundation or {})
                p["overrides"] = dict(s.appearance_overrides or {})
                p["status_overrides"] = dict(s.appearance_status_overrides or {})
                updated = dict(p)
                break
        if not updated:
            return None
        s.appearance_profiles = profiles
        s.validate()
        s.save()
        return updated

    def get_key_status(self) -> dict[str, bool]:
        """Key/gateway readiness — never sync-wait on plugin register().

        Feeds LLMs → Providers and the Default Model catalog. A hung register()
        used to pin this RPC and starve Appearance / create-conversation.
        """
        from backend.agent.secrets import has_key
        from backend.uefn_plugins.host import (
            ensure_plugins_loaded_async,
            get_contributions,
            get_llm_provider_registration,
            get_ui_contributions,
            plugins_ready,
            plugins_ui_ready,
        )

        # Built-in providers always available (no plugin wait).
        status: dict[str, bool] = {p: has_key(p) for p in all_providers()}
        status["cursor"] = has_key("cursor")

        if not plugins_ready():
            ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
            # plugin.json llm_providers land before register(); use them so
            # gateways appear while backends finish. Skip factory lookup.
            if plugins_ui_ready():
                for row in get_ui_contributions().get("llm_providers") or []:
                    if not isinstance(row, dict):
                        continue
                    pid = str(row.get("id") or "").strip().lower()
                    for key in (
                        str(row.get("secret_key") or "").strip(),
                        pid,
                    ):
                        if key:
                            status[key] = has_key(key)
                    if pid and str(row.get("kind") or "").strip().lower() == "url":
                        status[pid] = True
                        sk = str(row.get("secret_key") or "").strip()
                        if sk:
                            status[sk] = True
            return status

        # Include every enabled gateway secret_key (and provider id) so saved keys
        # still show after a Store Update even if factory registration races.
        for row in get_contributions().get("llm_providers") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("id") or "").strip().lower()
            for key in (
                str(row.get("secret_key") or "").strip(),
                pid,
            ):
                if key:
                    status[key] = has_key(key)
            # URL gateways (Ollama) work with default localhost — treat as ready
            # so the model catalog loads without requiring a saved secret.
            if pid:
                reg = get_llm_provider_registration(pid) or {}
                if reg.get("key_optional") or str(row.get("kind") or "").strip().lower() == "url":
                    status[pid] = True
                    sk = str(row.get("secret_key") or "").strip()
                    if sk:
                        status[sk] = True
        return status

    def has_any_api_key(self) -> bool:
        from backend.agent.secrets import has_key

        return any(has_key(p) for p in all_providers())

    # --- Discord group chat (Store plugin owns implementation) -----------------
    # Temporary shims: host React still calls discord_*; logic lives in
    # uefn-plugin-discord via api.register_panel_rpc. Removed once UI is Phase-2 HTML.

    def _discord_plugin_call(self, method: str, **params: Any) -> dict[str, Any]:
        from backend.uefn_plugins.host import is_plugin_enabled

        if not is_plugin_enabled("discord"):
            return {
                "ok": False,
                "plugin_disabled": True,
                "bots": [],
                "groups": [],
                "configured": False,
                "error": "Discord plugin is disabled — enable it in Settings → Store",
            }
        result = self.plugin_call("discord", method, params)
        if not isinstance(result, dict):
            return {"ok": False, "error": str(result), "bots": [], "groups": [], "configured": False}
        if result.get("ok") is not False:
            return result
        err = str(result.get("error") or "")
        # register() skipped panel RPCs or race — call handlers in-process.
        if "Unknown panel RPC" in err:
            try:
                return self._discord_panel_rpc_fallback(method, params)
            except Exception as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "bots": [],
                    "groups": [],
                    "configured": False,
                }
        return result

    def _discord_panel_rpc_fallback(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Invoke uefn-plugin-discord panel_rpc handlers without host registry."""
        import importlib
        import sys

        mod = sys.modules.get("uefn_plugin_discord")
        if mod is None:
            # Prefer AppData package already on sys.path via host import.
            try:
                mod = importlib.import_module("uefn_plugin_discord")
            except Exception:
                from backend.uefn_plugins.store import plugin_dir

                root = plugin_dir("discord")
                init_py = root / "backend" / "__init__.py"
                if not init_py.is_file():
                    raise FileNotFoundError("Discord plugin package not installed")
                from backend.uefn_plugins.host import _import_backend

                mod = _import_backend("discord", root, "backend")
                if mod is None:
                    raise RuntimeError("Discord plugin backend failed to import")
        panel = importlib.import_module("uefn_plugin_discord.panel_rpc")
        fn = getattr(panel, str(method or "").strip(), None)
        if not callable(fn):
            raise ValueError(f"Unknown Discord panel RPC: {method}")
        out = fn(**dict(params or {}))
        return out if isinstance(out, dict) else {"ok": True, "result": out}

    def discord_list_bots(self) -> dict[str, Any]:
        return self._discord_plugin_call("list_bots")

    def discord_save_bot(self, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create or update a bot profile. Optional ``token``; blank keeps existing."""
        return self._discord_plugin_call("save_bot", patch=patch if isinstance(patch, dict) else {})

    def discord_delete_bot(self, bot_id: str) -> dict[str, Any]:
        return self._discord_plugin_call("delete_bot", bot_id=str(bot_id or ""))

    def discord_status(self, bot_id: str = "") -> dict[str, Any]:
        return self._discord_plugin_call("status", bot_id=str(bot_id or ""))

    def discord_list_channels(self, bot_id: str = "") -> dict[str, Any]:
        return self._discord_plugin_call("list_channels", bot_id=str(bot_id or ""))

    def discord_debug(self, bot_id: str = "") -> dict[str, Any]:
        """Why aren't commands responding? Poller alive, watched channel, last message seen."""
        return self._discord_plugin_call("debug", bot_id=str(bot_id or ""))

    def discord_open_channel(self, channel_id: str, bot_id: str = "") -> dict[str, Any]:
        """Load a channel's recent history AND point the poller at it (one round trip)."""
        return self._discord_plugin_call(
            "open_channel",
            channel_id=str(channel_id or ""),
            bot_id=str(bot_id or ""),
        )

    def discord_open_portal(self, bot_id: str = "") -> dict[str, Any]:
        """Open the bot's Dev Portal page (name + avatar editing) in the browser."""
        return self._discord_plugin_call("open_portal", bot_id=str(bot_id or ""))

    def discord_send(self, channel_id: str, text: str, bot_id: str = "") -> dict[str, Any]:
        return self._discord_plugin_call(
            "send",
            channel_id=str(channel_id or ""),
            text=str(text or ""),
            bot_id=str(bot_id or ""),
        )

    def discord_list_members(self, bot_id: str = "") -> dict[str, Any]:
        """Server member sidebar — gateway cache when warm, else REST fallback."""
        return self._discord_plugin_call("list_members", bot_id=str(bot_id or ""))

    def save_agent_settings(self, patch: dict[str, Any]) -> str:
        from backend.agent.secrets import set_key

        s = PanelSettings.load()
        if patch.get("agent_provider"):
            s.agent_provider = str(patch["agent_provider"])
        if patch.get("agent_model"):
            s.agent_model = str(patch["agent_model"])
        if "default_model" in patch:
            # Qualified "backend:model_id"; blank clears it (no default).
            from frontend.favorite_models import parse_selection

            s.default_model = str(patch.get("default_model") or "").strip()
            # Keep legacy agent_model / agent_provider in sync for older send/context fallbacks.
            sel = parse_selection(s.default_model)
            if sel is None:
                s.agent_model = ""
            else:
                s.agent_model = sel.model_id
                if not sel.is_coding_agent and sel.provider:
                    s.agent_provider = sel.provider
                if sel.is_coding_agent:
                    from backend.agent.coding_agents.base import normalize_coding_agent

                    s.default_coding_agent = normalize_coding_agent(sel.coding_agent)
        if "uefn_project_root" in patch:
            raw = str(patch.get("uefn_project_root") or "").strip()
            if raw:
                s.uefn_project_root = str(resolve_uefn_project_root(Path(raw)))
            else:
                s.uefn_project_root = ""
        if "verse_diagnostics_cache_enabled" in patch:
            s.verse_diagnostics_cache_enabled = bool(patch.get("verse_diagnostics_cache_enabled"))
        if "verse_diagnostics_auto_check" in patch:
            s.verse_diagnostics_auto_check = bool(patch.get("verse_diagnostics_auto_check"))
        if "show_hidden_project_files" in patch:
            s.show_hidden_project_files = bool(patch.get("show_hidden_project_files"))
            from frontend.ui_web.project_files import _invalidate_file_paths_cache

            _invalidate_file_paths_cache()
        if "terminals_enabled" in patch:
            s.terminals_enabled = bool(patch.get("terminals_enabled"))
        if "prompt_caching_enabled" in patch:
            s.prompt_caching_enabled = bool(patch.get("prompt_caching_enabled"))
            from frontend.ui_web.project_chats import invalidate_all_conversation_caches

            invalidate_all_conversation_caches(s.uefn_project_root or None)
        if "freeze_prompt_prefix" in patch:
            s.freeze_prompt_prefix = bool(patch.get("freeze_prompt_prefix"))
            from frontend.ui_web.project_chats import invalidate_all_conversation_caches

            invalidate_all_conversation_caches(s.uefn_project_root or None)
        if "anthropic_extended_cache_ttl" in patch:
            s.anthropic_extended_cache_ttl = bool(patch.get("anthropic_extended_cache_ttl"))
        if "default_coding_agent" in patch:
            from backend.agent.coding_agents.base import normalize_coding_agent

            s.default_coding_agent = normalize_coding_agent(str(patch.get("default_coding_agent") or "ducky"))
        if "coding_agents" in patch and isinstance(patch.get("coding_agents"), dict):
            from backend.agent.coding_agents.settings_helpers import patch_coding_agents

            s.coding_agents = patch_coding_agents(s.coding_agents, patch["coding_agents"])
        if "voice_enabled" in patch:
            s.voice_enabled = bool(patch.get("voice_enabled"))
        if "voice_spoken_style" in patch:
            style = str(patch.get("voice_spoken_style") or "summary").strip()
            s.voice_spoken_style = style if style in ("summary", "speak_along") else "summary"
        if "voice_summary_model" in patch:
            s.voice_summary_model = str(patch.get("voice_summary_model") or "").strip()
        if "voice_default_voice" in patch:
            s.voice_default_voice = str(patch.get("voice_default_voice") or "").strip()
        if "voice_default_speed" in patch:
            try:
                s.voice_default_speed = max(0.25, min(4.0, float(patch.get("voice_default_speed"))))
            except (TypeError, ValueError):
                s.voice_default_speed = 1.0
        if "voice_live_manual_send" in patch:
            s.voice_live_manual_send = bool(patch.get("voice_live_manual_send"))
        if "voice_process_talk" in patch:
            try:
                s.voice_process_talk = max(0.0, min(1.0, float(patch.get("voice_process_talk"))))
            except (TypeError, ValueError):
                s.voice_process_talk = 0.7
        if "mic_permission" in patch:
            perm = str(patch.get("mic_permission") or "ask").strip()
            s.mic_permission = perm if perm in ("ask", "allow", "block") else "ask"
        if "mic_device_id" in patch:
            s.mic_device_id = str(patch.get("mic_device_id") or "").strip()
        if "output_device_id" in patch:
            s.output_device_id = str(patch.get("output_device_id") or "").strip()
        if "tts_volume" in patch:
            try:
                s.tts_volume = max(0.0, min(1.0, float(patch.get("tts_volume"))))
            except (TypeError, ValueError):
                s.tts_volume = 1.0
        if "audio_muted" in patch:
            s.audio_muted = bool(patch.get("audio_muted"))
        if "memory_auto_compress" in patch:
            s.memory_auto_compress = bool(patch.get("memory_auto_compress"))
        if "prompt_dedupe_exact_blocks" in patch:
            s.prompt_dedupe_exact_blocks = bool(patch.get("prompt_dedupe_exact_blocks"))
        if "memory_keep_last_messages" in patch:
            try:
                s.memory_keep_last_messages = max(1, min(100, int(patch.get("memory_keep_last_messages"))))
            except (TypeError, ValueError):
                s.memory_keep_last_messages = 20
        if "memory_compress_messages" in patch:
            try:
                s.memory_compress_messages = max(2, int(patch.get("memory_compress_messages")))
            except (TypeError, ValueError):
                s.memory_compress_messages = 40
        if "memory_compress_tokens" in patch:
            try:
                s.memory_compress_tokens = max(1000, int(patch.get("memory_compress_tokens")))
            except (TypeError, ValueError):
                s.memory_compress_tokens = 80_000
        if "memory_index_max_chars" in patch:
            try:
                s.memory_index_max_chars = max(200, min(20_000, int(patch.get("memory_index_max_chars"))))
            except (TypeError, ValueError):
                s.memory_index_max_chars = 2_500
        if "memory_summary_model" in patch:
            s.memory_summary_model = str(patch.get("memory_summary_model") or "").strip()
        if "walkthrough_completed" in patch:
            raw = patch.get("walkthrough_completed")
            if isinstance(raw, dict):
                s.walkthrough_completed = {
                    str(k): True for k, v in raw.items() if v
                }
            else:
                s.walkthrough_completed = {}
        s.validate()
        # Not a PanelSettings field: posting identity for Discord Ducky. Handled
        # apart from `keys` so a blank value CLEARS it (keys skips blanks).
        if "discord_name" in patch:
            set_key("discord_name", str(patch.get("discord_name") or "").strip())
        if "discord_allowed_ids" in patch:
            set_key("discord_allowed_ids", str(patch.get("discord_allowed_ids") or "").strip())
        keys = patch.get("keys") or {}
        if isinstance(keys, dict):
            for provider, val in keys.items():
                v = str(val or "").strip()
                if v and v != "••••••••":
                    set_key(str(provider), v)
        # Legacy Settings form → mirror into the Discord plugin's default bot profile.
        if (
            "discord_name" in patch
            or "discord_allowed_ids" in patch
            or any(str(k) in ("discord", "discord_guild") for k in (keys if isinstance(keys, dict) else {}))
        ):
            try:
                from backend.uefn_plugins.host import is_plugin_enabled

                if is_plugin_enabled("discord"):
                    tok = ""
                    if isinstance(keys, dict):
                        tok = str(keys.get("discord") or "").strip()
                    guild = ""
                    if isinstance(keys, dict):
                        guild = str(keys.get("discord_guild") or "").strip()
                    listed = self.plugin_call("discord", "list_bots", {})
                    bots = listed.get("bots") if isinstance(listed, dict) else None
                    create = not (isinstance(bots, list) and bots)
                    save_patch: dict[str, Any] = {
                        "id": "default",
                        "create": create,
                    }
                    if guild:
                        save_patch["guild_id"] = guild
                    if "discord_name" in patch:
                        save_patch["post_as"] = str(patch.get("discord_name") or "").strip()
                    if "discord_allowed_ids" in patch:
                        save_patch["allowed_ids"] = str(patch.get("discord_allowed_ids") or "").strip()
                    if tok:
                        save_patch["token"] = tok
                    self.plugin_call("discord", "save_bot", {"patch": save_patch})
            except Exception:
                pass
        s.save()
        # CLI paths / enabled flags / API keys just changed — drop the cached
        # coding-agent detection so the next list_coding_agents re-probes.
        from backend.agent.coding_agents.base import invalidate_detect_cache

        invalidate_detect_cache()
        apply_workspace_env(s.uefn_project_root)
        if s.uefn_project_root:
            root = Path(s.uefn_project_root)

            def _deploy() -> None:
                try:
                    lines = deploy_listener(root, PANEL_LISTENER_PORT)
                    for ln in lines:
                        _log(ln)
                except Exception as e:
                    record_error("deploy", str(e))

            threading.Thread(target=_deploy, daemon=True, name="deploy-listener").start()
        # Discord Ducky panel shows connected-state live — tell it settings changed.
        if (
            "discord_name" in patch
            or "discord_allowed_ids" in patch
            or any(str(k) in ("discord", "discord_guild") for k in (patch.get("keys") or {}))
        ):
            self._push_panel({"type": "discord_changed"})
        return "Saved — keys encrypted in credentials.dat"

    def list_coding_agents(self) -> dict[str, Any]:
        from backend.agent.coding_agents import detect_all

        # No explicit settings → uses the short-TTL cache in detect_all. The
        # panel polls this on every chat open/close and stream tick; each probe
        # does ~400ms of PATH scans, so uncached bursts spawn dozens of blocked
        # pywebview bridge threads and freeze the UI (see detect_all docstring).
        return detect_all()

    def set_conversation_coding_agent(
        self, conv_id: str, coding_agent: str, model: str = "", provider: str = ""
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.base import normalize_coding_agent
        from frontend.ui_web.project_chats import load_conversation, save_conversation

        conv = load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "conversation not found"}
        conv.coding_agent = normalize_coding_agent(coding_agent)
        # Always persist the requested model string (including empty) so switching
        # agents clears a prior chat model instead of leaking it.
        conv.model = (model or "").strip()
        if provider.strip():
            conv.provider = provider.strip()
        elif conv.coding_agent != "ducky":
            conv.provider = ""
        save_conversation(conv)
        notify_chats_changed(conv.id, conv.title, conv.folder_id, push=self._push)
        return {
            "ok": True,
            "coding_agent": conv.coding_agent,
            "model": conv.model or "",
            "provider": conv.provider or "",
        }

    def set_conversation_thinking_effort(self, conv_id: str, effort: str = "off") -> dict[str, Any]:
        from backend.agent.thinking_effort import normalize_thinking_effort
        from frontend.ui_web.project_chats import load_conversation, save_conversation

        conv = load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "conversation not found"}
        conv.thinking_effort = normalize_thinking_effort(effort)
        save_conversation(conv)
        return {"ok": True, "thinking_effort": conv.thinking_effort}

    def detect_coding_agent_cli(self, agent_id: str) -> dict[str, Any]:
        from backend.agent.coding_agents.base import (
            get_adapter,
            invalidate_detect_cache,
            normalize_coding_agent,
        )
        from backend.uefn_plugins.host import ensure_plugins_loaded, reload_plugins

        aid = normalize_coding_agent(agent_id)
        if aid == "ducky":
            return {"ok": True, "id": "ducky", "available": True, "status": "Embedded agent", "cli_path": ""}
        # Bound wait — a hung register() must not pin the panel HTTP connection.
        if not ensure_plugins_loaded(timeout=5.0):
            return {
                "ok": False,
                "error": "Plugins still loading — try Detect again in a moment.",
                "id": aid,
                "available": False,
                "status": "Plugins loading",
            }
        invalidate_detect_cache()
        adapter = get_adapter(aid)
        if adapter is None and aid not in self._coding_agent_reload_tried:
            # Last-resort repair, once per agent per session: a full reload
            # re-registers all 28 plugins (~2.6s) and the Settings tab polls
            # Detect — unguarded this fired ~100x a session.
            self._coding_agent_reload_tried.add(aid)
            try:
                reload_plugins()
                invalidate_detect_cache()
            except Exception:
                pass
            adapter = get_adapter(aid)
        if adapter is None:
            return {
                "ok": False,
                "error": (
                    f"Backend for {aid} is not loaded. Update Ducky to 1.0.527+, "
                    "fully quit and restart, then Detect again."
                ),
                "id": aid,
                "available": False,
                "status": "Backend not loaded",
            }
        info = adapter.detect(PanelSettings.load())
        return {"ok": True, **info.to_dict()}

    def list_tasks(self) -> dict[str, Any]:
        from backend.agent.coding_agents.epic import list_tasks

        s = PanelSettings.load()
        return {"tasks": list_tasks(s.uefn_project_root)}

    def create_task(self, title: str, goal: str = "", conv_ids: list[str] | None = None) -> dict[str, Any]:
        from backend.agent.coding_agents.epic import create_task

        s = PanelSettings.load()
        return create_task(title, goal=goal, conv_ids=conv_ids, project_root=s.uefn_project_root)

    def add_task_phase(self, task_id: str, title: str, plan: str = "") -> dict[str, Any]:
        from backend.agent.coding_agents.epic import add_phase

        s = PanelSettings.load()
        return add_phase(task_id, title, plan=plan, project_root=s.uefn_project_root)

    def write_task_artifact(self, task_id: str, name: str, content: str, kind: str = "spec") -> dict[str, Any]:
        from backend.agent.coding_agents.epic import write_artifact

        s = PanelSettings.load()
        return write_artifact(task_id, name, content, kind=kind, project_root=s.uefn_project_root)

    def build_task_handoff(self, task_id: str, phase_id: str = "") -> dict[str, Any]:
        from backend.agent.coding_agents.epic import build_handoff_prompt

        s = PanelSettings.load()
        return {"ok": True, "prompt": build_handoff_prompt(task_id, phase_id=phase_id, project_root=s.uefn_project_root)}

    def verify_task(
        self, task_id: str, phase_id: str = "", implementation_summary: str = ""
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.epic import verify_against_plan

        s = PanelSettings.load()
        return verify_against_plan(
            task_id,
            phase_id=phase_id,
            implementation_summary=implementation_summary,
            project_root=s.uefn_project_root,
        )

    def get_plan(self, chat_id: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import load_plan, outline_numbers, todo_progress

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        plan = load_plan(chat_id, project_root=root)
        outline = [
            {"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]}
            for lab, n in outline_numbers((plan or {}).get("nodes"))
        ]
        return {"ok": True, "plan": plan, "progress": todo_progress(plan), "outline": outline}

    def list_plans(self) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import list_plans

        return {"ok": True, "plans": list_plans()}

    def delete_plan(self, chat_id: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import delete_plan

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            deleted = delete_plan(chat_id, project_root=root)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "deleted": deleted}

    def list_plan_templates(self) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import list_templates

        return {"ok": True, "templates": list_templates()}

    def get_plan_template(self, template_id: str) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import load_template, outline_numbers

        doc = load_template(template_id)
        if not doc:
            return {"ok": False, "error": "template not found"}
        outline = [{"n": lab, "id": n["id"], "content": n["content"]} for lab, n in outline_numbers(doc.get("nodes"))]
        return {"ok": True, "template": doc, "outline": outline}

    def create_plan_template(
        self,
        title: str,
        overview: str = "",
        body_markdown: str = "",
        nodes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import create_template, outline_numbers

        try:
            doc = create_template(
                title=title,
                overview=overview,
                body_markdown=body_markdown,
                nodes=nodes,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        outline = [{"n": lab, "id": n["id"], "content": n["content"]} for lab, n in outline_numbers(doc.get("nodes"))]
        return {"ok": True, "template": doc, "outline": outline}

    def update_plan_template(
        self,
        template_id: str,
        title: str = "",
        overview: str = "",
        body_markdown: str = "",
        nodes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import outline_numbers, update_template

        try:
            doc = update_template(
                template_id,
                title=title if title else None,
                overview=overview if overview else None,
                body_markdown=body_markdown if body_markdown else None,
                nodes=nodes,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        outline = [{"n": lab, "id": n["id"], "content": n["content"]} for lab, n in outline_numbers(doc.get("nodes"))]
        return {"ok": True, "template": doc, "outline": outline}

    def delete_plan_template(self, template_id: str) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import delete_template

        try:
            deleted = delete_template(template_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "deleted": deleted}

    def instantiate_plan_template(
        self,
        template_id: str,
        chat_id: str,
        project_root: str | None = None,
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import (
            instantiate_template,
            outline_numbers,
            push_plan_updated,
            todo_progress,
        )

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            plan = instantiate_template(template_id, chat_id=chat_id, project_root=root)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        push_plan_updated(plan)
        outline = [
            {"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]}
            for lab, n in outline_numbers(plan.get("nodes"))
        ]
        return {"ok": True, "plan": plan, "progress": todo_progress(plan), "outline": outline}

    def save_plan_as_template(self, chat_id: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import save_plan_as_template

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            doc = save_plan_as_template(chat_id, project_root=root)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "template": doc}

    def plan_add_node(
        self,
        chat_id: str = "",
        content: str = "",
        parent_id: str = "",
        index: int | None = None,
        project_root: str | None = None,
        template_id: str = "",
        kind: str = "",
        body_markdown: str = "",
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import add_node, outline_numbers, push_plan_updated, todo_progress

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            plan = add_node(
                chat_id,
                content=content,
                parent_id=parent_id,
                index=index,
                kind=kind,
                body_markdown=body_markdown,
                project_root=root if not (template_id or "").strip() else None,
                template_id=(template_id or "").strip() or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if plan.get("kind") != "template":
            push_plan_updated(plan)
        return {
            "ok": True,
            "plan": plan,
            "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
            "outline": [
                {"n": lab, "id": n["id"], "content": n["content"], "status": n.get("status")}
                for lab, n in outline_numbers(plan.get("nodes"))
            ],
        }

    def plan_update_node(
        self,
        chat_id: str = "",
        node_id: str = "",
        content: str = "",
        status: str = "",
        project_root: str | None = None,
        template_id: str = "",
        kind: str = "",
        body_markdown: str | None = None,
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import outline_numbers, push_plan_updated, todo_progress, update_node

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            plan = update_node(
                chat_id,
                node_id,
                content=content if content else None,
                status=status if status else None,
                kind=kind if kind else None,
                body_markdown=body_markdown,
                project_root=root if not (template_id or "").strip() else None,
                template_id=(template_id or "").strip() or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if plan.get("kind") != "template":
            push_plan_updated(plan)
        return {
            "ok": True,
            "plan": plan,
            "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
            "outline": [
                {"n": lab, "id": n["id"], "content": n["content"], "status": n.get("status")}
                for lab, n in outline_numbers(plan.get("nodes"))
            ],
        }

    def plan_delete_node(
        self,
        chat_id: str = "",
        node_id: str = "",
        project_root: str | None = None,
        template_id: str = "",
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import delete_node, outline_numbers, push_plan_updated, todo_progress

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            plan = delete_node(
                chat_id,
                node_id,
                project_root=root if not (template_id or "").strip() else None,
                template_id=(template_id or "").strip() or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if plan.get("kind") != "template":
            push_plan_updated(plan)
        return {
            "ok": True,
            "plan": plan,
            "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
            "outline": [
                {"n": lab, "id": n["id"], "content": n["content"], "status": n.get("status")}
                for lab, n in outline_numbers(plan.get("nodes"))
            ],
        }

    def plan_move_node(
        self,
        chat_id: str = "",
        node_id: str = "",
        parent_id: str = "",
        index: int = 0,
        project_root: str | None = None,
        template_id: str = "",
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import move_node, outline_numbers, push_plan_updated, todo_progress

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            plan = move_node(
                chat_id,
                node_id,
                parent_id=parent_id,
                index=index,
                project_root=root if not (template_id or "").strip() else None,
                template_id=(template_id or "").strip() or None,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if plan.get("kind") != "template":
            push_plan_updated(plan)
        return {
            "ok": True,
            "plan": plan,
            "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
            "outline": [
                {"n": lab, "id": n["id"], "content": n["content"], "status": n.get("status")}
                for lab, n in outline_numbers(plan.get("nodes"))
            ],
        }

    # ---- Project memory (named entries, skills-style; see backend/project_memory.py) ----

    def list_memory_entries(self, project_root: str | None = None) -> dict[str, Any]:
        from backend.memory.project import list_entries, memory_dir

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        return {"ok": True, "entries": list_entries(root), "dir": str(memory_dir(root))}

    def get_memory_entry(self, name: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.memory.project import read_entry

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            entry = read_entry(name, root)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if entry is None:
            return {"ok": False, "error": f"No memory entry named {name!r}"}
        return {"ok": True, "entry": entry}

    def save_memory_entry(
        self,
        name: str,
        content: str,
        description: str = "",
        author: str = "",
        project_root: str | None = None,
    ) -> dict[str, Any]:
        from backend.memory.project import save_entry

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            result = save_entry(
                name, content, description=description, author=author, project_root=root
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "entry": result}

    def delete_memory_entry(self, name: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.memory.project import delete_entry

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            deleted = delete_entry(name, root)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "deleted": deleted}

    def get_memory_settings(self) -> dict[str, Any]:
        from backend.agent.context_memory import estimate_tokens
        from backend.memory.project import index_markdown

        s = PanelSettings.load()
        index = index_markdown(s.uefn_project_root, max_chars=int(s.memory_index_max_chars or 2_500))
        return {
            "ok": True,
            "memory_auto_compress": bool(s.memory_auto_compress),
            "prompt_dedupe_exact_blocks": bool(getattr(s, "prompt_dedupe_exact_blocks", False)),
            "memory_keep_last_messages": int(s.memory_keep_last_messages or 20),
            "memory_compress_messages": int(s.memory_compress_messages or 40),
            "memory_compress_tokens": int(s.memory_compress_tokens or 80_000),
            "memory_index_max_chars": int(s.memory_index_max_chars or 2_500),
            "memory_summary_model": s.memory_summary_model or "",
            "index_est_tokens": estimate_tokens(index),
            "index_chars": len(index),
        }

    def set_memory_settings(self, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        data = _coerce_mapping(patch, label="memory settings patch")
        # Reuse save_agent_settings field clamps; ignore its status string.
        self.save_agent_settings(data)
        return self.get_memory_settings()

    def get_chat_context_memory(self, conv_id: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.agent.context_memory import chat_context_memory_status

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        conv = load_conversation(str(conv_id or "").strip(), project_root=root)
        if conv is None:
            return {"ok": False, "error": f"Conversation not found: {conv_id!r}"}
        return chat_context_memory_status(conv)

    def compress_chat_context(
        self,
        conv_id: str,
        project_root: str | None = None,
        use_llm: bool = True,
    ) -> dict[str, Any]:
        from backend.agent.context_memory import compress_conversation

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        conv = load_conversation(str(conv_id or "").strip(), project_root=root)
        if conv is None:
            return {"ok": False, "error": f"Conversation not found: {conv_id!r}"}
        return compress_conversation(
            conv,
            project_root=root or "",
            force=True,
            use_llm=bool(use_llm),
        )

    def clear_chat_context_summary(
        self, conv_id: str, project_root: str | None = None
    ) -> dict[str, Any]:
        from backend.agent.context_memory import clear_conversation_summary

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        conv = load_conversation(str(conv_id or "").strip(), project_root=root)
        if conv is None:
            return {"ok": False, "error": f"Conversation not found: {conv_id!r}"}
        return clear_conversation_summary(conv, project_root=root or "")

    def copy_plan(
        self,
        source_chat_id: str,
        dest_chat_id: str,
        source_project_root: str | None = None,
        dest_project_root: str | None = None,
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import copy_plan, push_plan_updated, todo_progress

        src_root = (
            source_project_root
            if source_project_root is not None
            else PanelSettings.load().uefn_project_root
        )
        dest_root = (
            dest_project_root
            if dest_project_root is not None
            else PanelSettings.load().uefn_project_root
        )
        try:
            plan = copy_plan(
                source_chat_id=source_chat_id,
                dest_chat_id=dest_chat_id,
                source_project_root=src_root,
                dest_project_root=dest_root,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        push_plan_updated(plan)
        return {"ok": True, "plan": plan, "progress": todo_progress(plan)}

    def create_plan(
        self,
        chat_id: str,
        title: str,
        overview: str = "",
        body_markdown: str = "",
        todos: list[dict[str, Any]] | None = None,
        project_root: str | None = None,
        nodes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import create_plan, outline_numbers, push_plan_updated, todo_progress

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            plan = create_plan(
                chat_id,
                title=title,
                overview=overview,
                body_markdown=body_markdown,
                nodes=nodes,
                todos=todos,
                project_root=root,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        push_plan_updated(plan)
        outline = [
            {"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]}
            for lab, n in outline_numbers(plan.get("nodes"))
        ]
        return {"ok": True, "plan": plan, "progress": todo_progress(plan), "outline": outline}

    def update_plan(
        self,
        chat_id: str,
        todos: list[dict[str, Any]] | None = None,
        title: str = "",
        overview: str = "",
        body_markdown: str = "",
        merge: bool = True,
        status: str = "",
        project_root: str | None = None,
        nodes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import outline_numbers, push_plan_updated, todo_progress, update_plan

        root = project_root if project_root is not None else PanelSettings.load().uefn_project_root
        try:
            plan = update_plan(
                chat_id,
                title=title if title else None,
                overview=overview if overview else None,
                body_markdown=body_markdown if body_markdown else None,
                nodes=nodes,
                todos=todos,
                merge=merge,
                status=status if status else None,
                project_root=root,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        push_plan_updated(plan)
        outline = [
            {"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]}
            for lab, n in outline_numbers(plan.get("nodes"))
        ]
        return {"ok": True, "plan": plan, "progress": todo_progress(plan), "outline": outline}

    def test_key(self, provider: str, key: str = "") -> dict[str, Any]:
        import asyncio

        from backend.agent.coding_agents.base import invalidate_detect_cache
        from backend.agent.model_fetch import clear_model_cache, fetch_models
        from backend.agent.providers import gateway_providers, make_provider
        from backend.agent.secrets import get_key, set_key
        from backend.uefn_plugins.host import (
            get_coding_agent_registration,
            get_contributions,
            get_llm_provider_registration,
        )

        prov = (provider or "").strip().lower()
        draft = (key or "").strip()

        contrib_rows = get_contributions().get("llm_providers") or []
        contrib = next(
            (
                row
                for row in contrib_rows
                if isinstance(row, dict) and str(row.get("id") or "").strip().lower() == prov
            ),
            None,
        )
        prov_reg = get_llm_provider_registration(prov)
        agent_reg = get_coding_agent_registration(prov)
        if contrib is None and prov_reg is None and agent_reg is None:
            result = {
                "ok": False,
                "detail": "Gateway is not installed or enabled — Settings → Store → Gateways",
            }
            self._push_panel({"type": "key_test_done", "provider": prov, **result})
            return result

        # Plugin-owned custom test (e.g. Cursor format check).
        custom_test = None
        if agent_reg and callable(agent_reg.get("test_key")):
            custom_test = agent_reg["test_key"]
        elif prov_reg and callable(prov_reg.get("test_key")):
            custom_test = prov_reg["test_key"]
        if custom_test is not None:
            api_key = draft if draft and draft != "••••••••" else get_key(prov)
            try:
                result = dict(custom_test(api_key or ""))
            except Exception as exc:
                result = {"ok": False, "detail": str(exc)}
            if result.get("ok") and api_key:
                set_key(prov, str(api_key).strip())
                invalidate_detect_cache()
            self._push_panel({"type": "key_test_done", "provider": prov, **result})
            return result

        if prov_reg is None or prov not in gateway_providers():
            label = str((contrib or {}).get("label") or prov)
            result = {
                "ok": False,
                "detail": f"{label} gateway is not installed or enabled — Settings → Store → Gateways",
            }
            self._push_panel({"type": "key_test_done", "provider": prov, **result})
            return result

        kind = str((contrib or {}).get("kind") or "secret").strip().lower()
        if kind == "url":
            default_url = str((contrib or {}).get("default_url") or "http://localhost:11434")
            api_key = draft if draft and draft != "••••••••" else (get_key(prov) or default_url)
            norm = (prov_reg or {}).get("normalize_secret")
            if callable(norm):
                try:
                    api_key = str(norm(api_key) or api_key).strip()
                except Exception:
                    pass
            self._push_panel(
                {"type": "key_test_progress", "provider": prov, "detail": f"Contacting {api_key}…"}
            )
            try:
                models = fetch_models(prov, api_key)
            except Exception as e:
                result = {"ok": False, "detail": str(e)}
                self._push_panel({"type": "key_test_done", "provider": prov, **result})
                return result
            if not models:
                result = {
                    "ok": False,
                    "detail": "No models found — pull a model on that server first",
                }
                self._push_panel({"type": "key_test_done", "provider": prov, **result})
                return result
            test_model = models[0].id
            self._push_panel(
                {
                    "type": "key_test_progress",
                    "provider": prov,
                    "detail": (
                        f"{len(models)} model(s) found — running a test chat with {test_model}. "
                        "First run loads the model into memory and can take a few minutes…"
                    ),
                }
            )
        else:
            api_key = draft if draft and draft != "••••••••" else get_key(prov)
            if not api_key:
                return {"ok": False, "detail": "No key"}
            saved = PanelSettings.load().agent_model
            test_model = (
                str(prov_reg.get("test_key_model") or "").strip()
                or _TEST_KEY_MODELS.get(prov)
                or (saved.strip() if saved else "")
            )
            if not test_model:
                return {"ok": False, "detail": "Unknown provider"}

        try:
            p = make_provider(prov, api_key, test_model)
            ok, detail = asyncio.run(p.test_connection())
            if ok:
                set_key(prov, api_key)
                clear_model_cache(prov)
                invalidate_detect_cache()

                def _cache_models() -> None:
                    try:
                        models = fetch_models(prov, api_key)
                        if models:
                            _model_cache[prov] = list(models)
                            _save_model_cache_to_disk()
                    except Exception:
                        pass

                threading.Thread(target=_cache_models, daemon=True, name=f"models-{prov}").start()
            result = {"ok": ok, "detail": detail if not ok else "OK"}
            self._push_panel({"type": "key_test_done", "provider": prov, **result})
            return result
        except Exception as e:
            result = {"ok": False, "detail": str(e)}
            self._push_panel({"type": "key_test_done", "provider": prov, **result})
            return result

    def get_models(self, provider: str, refresh: bool = False) -> list[dict[str, Any]]:
        from backend.agent.model_fetch import fetch_models
        from backend.agent.providers import all_providers
        from backend.agent.secrets import get_key

        prov = provider or PanelSettings.load().agent_provider
        # Gateways (OpenAI / Ollama) only when their Store plugin is enabled —
        # never serve a stale disk cache for a removed/disabled gateway.
        if prov not in all_providers():
            _model_cache.pop(prov, None)
            return []
        cached = [] if refresh else _model_cache.get(prov, [])
        if not cached:
            key = get_key(prov)
            if key:
                try:
                    cached = fetch_models(prov, key)
                    _model_cache[prov] = list(cached)
                    _save_model_cache_to_disk()
                except Exception:
                    cached = _model_cache.get(prov, [])
        from frontend.agent_models import provider_label

        label = provider_label(prov) or PROVIDER_LABELS.get(prov, prov.title())
        return [
            {
                "provider": label,
                "id": m.id,
                "name": m.display_name or m.id,
                "supports_vision": m.supports_vision,
                "supports_tools": m.supports_tools,
                "supports_web_search": m.supports_web_search,
                "context_limit": m.context_limit or 0,
                "price_in": m.price_in,
                "price_out": m.price_out,
                "is_local": m.is_local,
            }
            for m in cached
        ]

    def set_model(self, model_id: str, provider: str = "") -> None:
        s = PanelSettings.load()
        s.agent_model = model_id
        if provider:
            s.agent_provider = provider
        s.validate()
        s.save()

    def send_message(
        self,
        conv_id: str,
        text: str,
        mode: str,
        model: str,
        file_path: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        from frontend.ui_web.group_orchestrator import (
            is_group_conversation,
            run_group_turn,
        )

        if file_path:
            before = load_conversation(conv_id)
            had_path = bool(before and before.file_path)
            ensure_conversation_file_path(conv_id, file_path)
            if not had_path:
                after = load_conversation(conv_id)
                if after and after.file_path:
                    notify_chats_changed(after.id, after.title, after.folder_id, push=self._push)
        # Composer model updates this conversation only — never global settings
        # or any Ducky profile favorite_models.
        turn_model = (model or "").strip()
        if turn_model and turn_model.lower() != "default":
            conv = load_conversation(conv_id)
            if conv is not None:
                from backend.agent.coding_agents.base import normalize_coding_agent
                from backend.agent.model_pricing import resolve_provider_for_model

                model_changed = (conv.model or "").strip() != turn_model
                if model_changed:
                    conv.model = turn_model
                if normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky") == "ducky":
                    resolved = resolve_provider_for_model(turn_model, conv.provider or "")
                    if resolved and resolved != (conv.provider or "").strip().lower():
                        conv.provider = resolved
                        model_changed = True
                if model_changed:
                    save_conversation(conv)
        conv = load_conversation(conv_id)
        # Subagents retired — every non-group chat is composable (group members included).
        if conv is not None and is_group_conversation(conv):
            # Group chats ignore attachments for now — members get a text prompt.
            run_id = run_group_turn(
                conv_id,
                text,
                mode=mode,
                model=model,
                push=self._push,
            )
            return {"run_id": run_id}
        run_id = run_message(conv_id, text, mode, model, push=self._push, attachments=attachments or [])
        return {"run_id": run_id}

    def resend_last_user_message(
        self,
        conv_id: str,
        text: str,
        mode: str,
        model: str,
        file_path: str = "",
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, str]:
        """Cursor-style edit + resend: rewind to the last user turn and rerun.

        Only the most recent user message is editable, so we never need to map a
        synthetic UI row id back to a stored message — we just drop the last
        ``role == "user"`` entry (and everything after it) and let the normal
        send path append the edited message and start a fresh run.
        """
        conv = load_conversation(conv_id)
        if not conv:
            return {"run_id": ""}
        # Cursor-style edit mid-answer: cancel the live run, then rewind + rerun.
        if conv_id in list_running_agents():
            cancel_agent(conv_id)
            wait_for_idle(conv_id, 2.0)
            if conv_id in list_running_agents():
                return {"run_id": ""}
        last_user = next(
            (i for i in range(len(conv.messages) - 1, -1, -1) if conv.messages[i].get("role") == "user"),
            None,
        )
        if last_user is not None:
            conv.messages = conv.messages[:last_user]
            save_conversation(conv)
        return self.send_message(conv_id, text, mode, model, file_path, attachments)

    def get_context_usage(
        self,
        conv_id: str,
        model: str,
        mode: str = "agent",
        draft: str = "",
        include_content: bool = False,
    ) -> dict[str, Any]:
        from frontend.ui_web.context_tokens import compute_context_usage

        try:
            return compute_context_usage(
                conv_id, model, mode=mode, draft_text=draft, include_content=include_content
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("get_context_usage failed for %s: %s", conv_id, exc)
            from frontend.ui_web.context_tokens import context_limit_for_model

            limit = context_limit_for_model(model) or 0
            return {
                "used_tokens": 0,
                "context_limit": limit,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "breakdown": [],
                "omitted": [],
                "error": str(exc),
            }

    def get_provider_usage(self, provider_id: str = "", days: int = 7) -> dict[str, Any]:
        """7-day (default) usage report for one provider / coding agent, or all."""
        from frontend.ui_web.provider_usage_log import usage_report

        try:
            return usage_report(provider_id=str(provider_id or ""), days=int(days or 7))
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("get_provider_usage failed: %s", exc)
            return {
                "provider": str(provider_id or ""),
                "days": int(days or 7),
                "call_count": 0,
                "total_input": 0,
                "total_output": 0,
                "total_tokens": 0,
                "total_cache_read": 0,
                "total_cache_write": 0,
                "cache_hit_rate": 0.0,
                "cost_usd": None,
                "by_day": [],
                "by_model": [],
                "error": str(exc),
            }

    def get_ducky_usage(
        self, ducky_name: str = "", profile_id: str = "", days: int = 7
    ) -> dict[str, Any]:
        """Per-ducky stats for the profile editor (chats + tokens, last N days)."""
        from frontend.agent_profiles import get_agent_profile
        from frontend.ui_web.provider_usage_log import ducky_usage_report

        pid = str(profile_id or "").strip()
        name = str(ducky_name or "").strip()
        if pid and not name:
            profile = get_agent_profile(pid)
            if profile:
                name = str(profile.get("name") or "").strip()
        try:
            return ducky_usage_report(name, profile_id=pid, days=int(days or 7))
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("get_ducky_usage failed: %s", exc)
            return {
                "ducky_name": name,
                "profile_id": pid,
                "days": int(days or 7),
                "chat_count": 0,
                "call_count": 0,
                "total_input": 0,
                "total_output": 0,
                "total_tokens": 0,
                "total_cache_read": 0,
                "total_cache_write": 0,
                "cost_usd": None,
                "chats": [],
                "error": str(exc),
            }

    def reset_context(self, conv_id: str, segments: list[str], mode: str = "agent", model: str = "") -> dict[str, Any]:
        from frontend.ui_web.context_control import reset_context

        return reset_context(conv_id, segments, model=model, mode=mode)

    def restore_context(self, conv_id: str, segments: list[str], mode: str = "agent", model: str = "") -> dict[str, Any]:
        from frontend.ui_web.context_control import restore_context

        return restore_context(conv_id, segments, model=model, mode=mode)

    def get_session_files(self, conv_id: str) -> list[dict[str, Any]]:
        from frontend.ui_web.session_files import compute_session_files

        return compute_session_files(conv_id)

    def cancel_agent(self, conv_id: str = "") -> bool:
        from frontend.ui_web.group_orchestrator import cancel_group_run, list_group_running_ids

        if conv_id:
            cancel_group_run(conv_id)
        else:
            for gid in list_group_running_ids():
                cancel_group_run(gid)
        cancel_agent(conv_id or None)
        return True

    def list_running_agents(self) -> list[str]:
        from frontend.ui_web.group_orchestrator import list_group_running_ids

        ids = list(list_running_agents())
        for gid in list_group_running_ids():
            if gid not in ids:
                ids.append(gid)
        return ids

    def wait_for_agent_idle(self, conv_id: str, timeout: float = 2.0) -> bool:
        return wait_for_idle(conv_id, timeout)

    def pick_project_path(self) -> str | None:
        # Prefer pywebview's native dialog: it runs on the GUI thread and is safe to call from
        # this JS-API worker thread. The Tk route can't (Tk is owned by the pump thread, and
        # cross-thread ``root.after`` raises "main thread is not in main loop").
        win = self._window
        if win is not None:
            return self._pick_project_path_webview(win)
        return self._pick_project_path_standalone()

    def _pick_project_path_webview(self, win: Any) -> str | None:
        import webview

        try:
            folder_type = webview.FileDialog.FOLDER
            open_type = webview.FileDialog.OPEN
        except AttributeError:  # older pywebview
            folder_type = getattr(webview, "FOLDER_DIALOG", 20)
            open_type = getattr(webview, "OPEN_DIALOG", 10)

        try:
            picked = win.create_file_dialog(folder_type)
            if picked:
                return str(resolve_uefn_project_root(Path(picked[0])))
            picked = win.create_file_dialog(
                open_type,
                file_types=("UEFN project (*.uefnproject)", "All files (*.*)"),
            )
            if picked:
                return str(resolve_uefn_project_root(Path(picked[0])))
        except Exception:
            return None
        return None

    def _pick_project_path_dialog(self, root: Any) -> str | None:
        from tkinter import filedialog

        d = filedialog.askdirectory(title="UEFN project folder", parent=root)
        if d:
            return str(resolve_uefn_project_root(Path(d)))
        f = filedialog.askopenfilename(
            title="Or pick a .uefnproject file",
            parent=root,
            filetypes=[("UEFN", "*.uefnproject"), ("All", "*.*")],
        )
        if f:
            return str(resolve_uefn_project_root(Path(f)))
        return None

    def _pick_project_path_standalone(self) -> str | None:
        try:
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            try:
                return self._pick_project_path_dialog(root)
            finally:
                root.destroy()
        except Exception:
            return None

    def import_sound_file(self) -> dict[str, Any]:
        """Pick an audio file and copy it into AppData/sounds/. Returns stored filename."""
        import shutil
        import uuid

        from backend.skills.store import appdata_dir

        allowed = {".mp3", ".wav", ".ogg", ".m4a", ".webm"}
        picked: str | None = None
        win = self._window
        if win is not None:
            try:
                import webview

                try:
                    open_type = webview.FileDialog.OPEN
                except AttributeError:
                    open_type = getattr(webview, "OPEN_DIALOG", 10)
                result = win.create_file_dialog(
                    open_type,
                    file_types=(
                        "Audio (*.mp3;*.wav;*.ogg;*.m4a;*.webm)",
                        "All files (*.*)",
                    ),
                )
                if result:
                    picked = str(result[0])
            except Exception:
                picked = None
        if not picked:
            try:
                import tkinter as tk
                from tkinter import filedialog

                root = tk.Tk()
                root.withdraw()
                try:
                    picked = filedialog.askopenfilename(
                        title="Choose sound file",
                        parent=root,
                        filetypes=[
                            ("Audio", "*.mp3 *.wav *.ogg *.m4a *.webm"),
                            ("All", "*.*"),
                        ],
                    ) or None
                finally:
                    root.destroy()
            except Exception:
                return {"ok": False, "error": "Could not open file dialog"}

        if not picked:
            return {"ok": False, "error": "cancelled"}

        src = Path(picked)
        if not src.is_file():
            return {"ok": False, "error": "File not found"}
        ext = src.suffix.lower()
        if ext not in allowed:
            return {"ok": False, "error": f"Unsupported audio type: {ext or '(none)'}"}

        sounds_dir = appdata_dir() / "sounds"
        sounds_dir.mkdir(parents=True, exist_ok=True)
        safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in src.stem)[:48] or "sound"
        filename = f"{safe_stem}_{uuid.uuid4().hex[:8]}{ext}"
        dest = sounds_dir / filename
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "filename": filename}

    def deploy(self, project_path: str = "") -> list[str]:
        path = (project_path or "").strip()
        if not path:
            path = PanelSettings.load().uefn_project_root.strip()
        if not path:
            picked = self.pick_project_path()
            if not picked:
                return [
                    "No project selected. Pick a project in the header (folder dropdown) "
                    "or Agent settings, then try again."
                ]
            path = picked
        try:
            root = resolve_uefn_project_root(Path(path))
        except ValueError as e:
            return [f"Invalid project: {e}"]
        except Exception as e:
            record_error("deploy", str(e))
            return [f"Deploy failed: {e}"]
        try:
            lines = deploy_listener(root, PANEL_LISTENER_PORT)
            from frontend.ui_web.recent_projects import add_recent_project

            add_recent_project(str(root))
            for ln in lines:
                _log(ln)
            return lines
        except Exception as e:
            record_error("deploy", str(e))
            return [f"Deploy failed: {e}"]

    def deploy_all_projects(self) -> list[str]:
        from frontend.ui_web.project_deploy import deploy_all_recent_projects

        lines = deploy_all_recent_projects(log=_log)
        if not lines:
            return ["No projects yet. Open a project from the header, then return here."]
        return lines

    def apply_ide(self, kind: str) -> str:
        # Host writes MCP+skills for every IDE (like pre-gateway peel). Gateway
        # plugins still register hookups for Settings UI grouping.
        key = (kind or "").strip().lower()
        ide = IdeKind(key)
        s = replace(PanelSettings.load(), port=PANEL_LISTENER_PORT)
        block = build_uefn_server_block(s)
        path = path_for_ide(ide, s.antigravity_config_path)
        merge_uefn_into_config(path, block, dry_run=False)
        for ln in sync_skill_for_ide(path):
            _log(ln)
        _log(f"Applied → {path}")
        return f"Applied → {path}"

    def test_ide(self, kind: str) -> dict[str, Any]:
        key = (kind or "").strip().lower()
        IdeKind(key)  # raises on unknown IDE kind
        s = replace(PanelSettings.load(), port=PANEL_LISTENER_PORT)
        block = build_uefn_server_block(s)
        cmd = block.get("command", "")
        args = list(block.get("args") or [])
        ok, detail = probe_stdio_mcp(cmd, args)
        _log(f"Test {key}: {detail[:500]}")
        return {"ok": ok, "detail": detail}

    def get_ide_statuses(self) -> dict[str, dict[str, Any]]:
        from frontend.ide_apply import verify_all_ide_bridges

        return verify_all_ide_bridges()

    def apply_all_ides(self) -> list[str]:
        """Apply MCP+skills for every host IDE (Cursor / Claude / Antigravity)."""
        from frontend.ide_apply import ALL_IDES

        out: list[str] = []
        for ide in ALL_IDES:
            kind = ide.value
            try:
                msg = self.apply_ide(kind)
                out.append(f"{kind}: {msg}")
            except Exception as e:
                out.append(f"{kind}: error: {e}")
        try:
            for ln in sync_skill_on_mcp_update(PanelSettings.load().antigravity_config_path):
                _log(ln)
                out.append(ln)
        except Exception as e:
            out.append(f"skill sync: error: {e}")
        return out

    def get_log(self) -> list[str]:
        return list(_log_history)

    def clear_log(self) -> list[str]:
        _log_history.clear()
        return []

    def get_errors(self) -> list[str]:
        trim_errors()
        lines: list[str] = []
        for e in read_errors():
            ts = e.get("ts", 0)
            try:
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
            except (ValueError, TypeError):
                stamp = "?"
            lines.append(f"[{stamp}] ({e.get('source', '?')}) {e.get('message', '')}")
        return lines

    def clear_errors(self) -> list[str]:
        clear_error_log()
        return []

    def pull_editor_log(self) -> None:
        try:
            from backend.bridge import post_command_to_listener

            res = post_command_to_listener(
                PANEL_LISTENER_PORT, "get_editor_log", {"last_n": 200, "filter_str": "Error"}, timeout=6.0
            )
            for line in res.get("lines", []) or []:
                record_error("editor", str(line))
        except Exception as e:
            record_error("panel", f"Pull editor log failed: {e}")

    def open_appdata(self) -> None:
        d = default_app_data_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # type: ignore[attr-defined]

    def get_skill_info(self) -> dict[str, Any]:
        from frontend.settings import default_app_data_dir
        from backend.skills.store import (
            appdata_skill_packs_dir,
            build_skill_prompt,
            default_selection_from_settings,
            list_skill_packs,
            seed_skill_packs,
        )

        error = ""
        settings = PanelSettings.load()
        try:
            seed_skill_packs()
            # Catalog only — full markdown loads via get_skill_pack_files when a pack opens.
            packs = list_skill_packs(include_text=False)
            sel = default_selection_from_settings(settings)
            combined_text = build_skill_prompt(sel)
        except Exception as exc:
            packs = []
            combined_text = ""
            error = str(exc)
        try:
            from backend.mcp_plugins.store import seed_mcp_plugins

            seed_mcp_plugins()
        except Exception:
            pass  # plugin seeding must never blank the skills list

        return {
            "text": combined_text,
            "primary_text": combined_text,
            "active_source": "appdata",
            "version": 0,
            "path": str(appdata_skill_packs_dir()),
            "appdata_dir": str(default_app_data_dir()),
            "skills_dir": str(appdata_skill_packs_dir()),
            "skill_packs_dir": str(appdata_skill_packs_dir()),
            "appdata_exists": appdata_skill_packs_dir().is_dir(),
            "appdata_text": combined_text,
            "appdata_version": 0,
            "bundled_path": "",
            "bundled_text": "",
            "bundled_version": 0,
            "is_custom": False,
            "files": [],
            "packs": packs,
            "primary_filename": "uefn",
            "catalog": packs,
            "default_disabled_packs": list(settings.default_disabled_packs or []) if not error else [],
            "default_enabled_packs": (
                [str(p.get("id")) for p in packs if p.get("id")]
                if not error
                else ["uefn", "ducky", "ponytail", "verse"]
            ),
            "default_enabled_subskills": {},
            "default_enabled_skills": (
                [str(p.get("id")) for p in packs if p.get("id")]
                if not error
                else ["uefn", "ducky", "ponytail", "verse"]
            ),
            "error": error,
        }

    def save_skill(self, text: str) -> dict[str, Any]:
        return self.save_subskill("uefn", "core", text)

    def save_skill_file(self, filename: str, text: str) -> dict[str, Any]:
        parts = filename.replace("\\", "/").split("/")
        if len(parts) >= 2:
            return self.save_subskill(parts[0], Path(parts[-1]).stem, text)
        return self.save_subskill("uefn", "core", text)

    def save_subskill(self, pack_id: str, subskill_id: str, text: str) -> dict[str, Any]:
        from frontend.skill_deploy import sync_skill_all_ides
        from backend.agent.prompt import clear_skill_cache
        from backend.skills.store import save_subskill

        path = save_subskill(pack_id, subskill_id, text)
        clear_skill_cache()
        logs = sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
        return {
            "ok": True,
            "path": str(path),
            "version": 0,
            "filename": f"{pack_id}/{path.name}",
            "logs": logs,
        }

    def create_skill(self, filename: str, text: str = "") -> dict[str, Any]:
        return self.create_skill_pack(filename, filename.replace("_", " ").title(), text)

    def create_skill_pack(self, pack_id: str, label: str, description: str = "") -> dict[str, Any]:
        from frontend.skill_deploy import sync_skill_all_ides
        from backend.agent.prompt import clear_skill_cache
        from backend.skills.store import create_skill_pack

        path = create_skill_pack(pack_id, label, description)
        clear_skill_cache()
        sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
        return {"ok": True, "path": str(path), "pack_id": path.name}

    def bridge_job_start(self, name: str, args: list[Any] | None = None) -> dict[str, Any]:
        """Run an allowlisted PanelApi method on a worker; returns job_id immediately.

        JS should poll with ``bridge_job_poll`` so Store / skill draft / key test /
        MCP catalog / Verse compile never freeze the file tree or chat.
        """
        from frontend.ui_web.bridge_jobs import job_start

        method = (name or "").strip()
        allow = {
            "duckyos_store_download",
            "draft_skill_pack",
            "draft_subskill",
            "test_key",
            "get_context_usage",
            "compile_verse_project",
            "get_mcp_tools_catalog",
            "test_mcp_plugin",
            "get_models",
            "voice_transcribe_audio",
            "voice_create_realtime_token",
            "voice_summarize_reply",
            "tester_list_devices",
            "tester_run_tests",
            "tester_results",
            "tester_simulate",
            "tester_create_chat",
            "apply_update",
        }
        if method not in allow:
            return {"ok": False, "error": f"bridge job not allowed: {method!r}"}
        fn = getattr(self, method, None)
        if not callable(fn):
            return {"ok": False, "error": f"unknown method: {method!r}"}
        raw_args = list(args or [])

        def _run() -> Any:
            return fn(*raw_args)

        return job_start(_run)

    def bridge_job_poll(self, job_id: str) -> dict[str, Any]:
        """Poll a ``bridge_job_start`` job (non-blocking)."""
        from frontend.ui_web.bridge_jobs import job_poll

        return job_poll(str(job_id or ""))

    def voice_transcribe_audio(self, b64_audio: str, mime: str = "audio/webm") -> dict[str, Any]:
        """Batch Whisper transcription (prefer via bridge_job_start)."""
        from backend.voice.transcription import transcribe_audio

        return transcribe_audio(str(b64_audio or ""), str(mime or "audio/webm"))

    def voice_create_realtime_token(self) -> dict[str, Any]:
        """Mint an ephemeral Realtime client secret for browser streaming STT."""
        from backend.voice.transcription import create_realtime_token

        return create_realtime_token()

    def voice_summarize_reply(self, assistant_text: str, model: str = "") -> dict[str, Any]:
        """Short spoken summary via the cheap voice model (prefer via bridge_job_start)."""
        from backend.voice.summary import summarize_for_speech

        return summarize_for_speech(str(assistant_text or ""), model=str(model or ""))

    def plugin_tts_start(self, plugin_id: str, text: str = "", voice_id: str = "") -> dict[str, Any]:
        """Start plugin TTS synthesize on a worker; poll with plugin_tts_poll."""
        from frontend.ui_web.plugin_host_api import tts_start

        return tts_start(str(plugin_id or ""), text=str(text or ""), voice_id=str(voice_id or ""))

    def plugin_tts_poll(self, job_id: str) -> dict[str, Any]:
        from frontend.ui_web.plugin_host_api import tts_poll

        return tts_poll(str(job_id or ""))

    def plugin_tts_voices_start(self, plugin_id: str) -> dict[str, Any]:
        """Start a dynamic-voice fetch on a worker; poll with plugin_tts_voices_poll."""
        from frontend.ui_web.plugin_host_api import tts_voices_start

        return tts_voices_start(str(plugin_id or ""))

    def plugin_tts_voices_poll(self, job_id: str) -> dict[str, Any]:
        from frontend.ui_web.plugin_host_api import tts_voices_poll

        return tts_voices_poll(str(job_id or ""))

    def set_uefn_plugin_secret(self, plugin_id: str, key: str, value: str) -> dict[str, Any]:
        """Store an encrypted secret (e.g. an API key) declared by a plugin manifest.

        Only keys listed in the plugin's ``secret_keys`` may be written — a plugin's
        settings UI cannot scribble over arbitrary app credentials.
        """
        from backend.agent.secrets import set_key
        from backend.uefn_plugins.store import (
            load_plugin_manifest,
            normalize_plugin_id,
        )

        try:
            pid = normalize_plugin_id(str(plugin_id or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        manifest = load_plugin_manifest(pid)
        if manifest is None:
            return {"ok": False, "error": f"UEFN plugin not found: {pid}"}
        raw = manifest.get("secret_keys") if isinstance(manifest.get("secret_keys"), list) else []
        allowed = {str(k).strip() for k in raw if str(k).strip()}
        k = str(key or "").strip()
        if k not in allowed:
            return {"ok": False, "error": f"Key {k!r} is not declared in {pid} secret_keys"}
        # Defense-in-depth: plugins must not overwrite the DuckyOS device key.
        # Store gateway ids (openai / anthropic / cursor / ollama / …) are owned by
        # their plugin and may be listed in that plugin's secret_keys.
        reserved = {"duckyos_account"}
        if k.lower() in reserved:
            return {"ok": False, "error": f"Key {k!r} is a reserved app credential and cannot be set by a plugin"}
        try:
            set_key(k, str(value or ""))
        except Exception as exc:  # noqa: BLE001 — never raise across the JS bridge
            return {"ok": False, "error": f"Could not save key: {exc}"}
        # Nudge the UI (voice pickers, key-status rows) to re-read after a key change.
        self._push_panel({"type": "uefn_plugins_changed"})
        return {"ok": True, "plugin_id": pid, "key": k, "set": bool(str(value or "").strip())}

    def get_uefn_plugin_secret_status(self, plugin_id: str, keys: list[str] | None = None) -> dict[str, Any]:
        """Report which of a plugin's secret keys are set (never returns the values)."""
        from backend.agent.secrets import has_key
        from backend.uefn_plugins.store import (
            load_plugin_manifest,
            normalize_plugin_id,
        )

        try:
            pid = normalize_plugin_id(str(plugin_id or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "status": {}}
        manifest = load_plugin_manifest(pid)
        if manifest is None:
            return {"ok": False, "error": f"UEFN plugin not found: {pid}", "status": {}}
        raw = manifest.get("secret_keys") if isinstance(manifest.get("secret_keys"), list) else []
        allowed = {str(k).strip() for k in raw if str(k).strip()}
        want = [str(k).strip() for k in (keys or []) if str(k).strip()] or sorted(allowed)
        status = {k: (k in allowed and has_key(k)) for k in want}
        return {"ok": True, "plugin_id": pid, "status": status}

    def test_uefn_plugin_secret(
        self, plugin_id: str, key: str, value: str = ""
    ) -> dict[str, Any]:
        """Hit the vendor API with a draft or saved plugin secret (Settings → Test)."""
        from backend.uefn_plugins.host import run_secret_test

        return run_secret_test(str(plugin_id or ""), str(key or ""), str(value or ""))

    def plugin_llm_start(
        self,
        plugin_id: str,
        system: str = "",
        user: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        """Start LLM on a worker thread; returns job_id immediately (bridge stays free)."""
        from frontend.ui_web.plugin_host_api import llm_start

        return llm_start(
            str(plugin_id or ""),
            system=str(system or ""),
            user=str(user or ""),
            model=str(model or ""),
        )

    def plugin_llm_poll(self, job_id: str) -> dict[str, Any]:
        """Poll a plugin_llm_start job (non-blocking)."""
        from frontend.ui_web.plugin_host_api import llm_poll

        return llm_poll(str(job_id or ""))

    def plugin_llm_cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a plugin_llm_start job (stops waiters; worker may still finish)."""
        from frontend.ui_web.plugin_host_api import llm_cancel

        return llm_cancel(str(job_id or ""))

    def plugin_translate_batch_start(
        self,
        plugin_id: str,
        language: str,
        strings: dict[str, Any] | list[Any] | str | None = None,
        model: str = "",
    ) -> dict[str, Any]:
        """Start UI translate batch (same pipeline as MCP translate_ui_batch)."""
        from frontend.ui_web.plugin_host_api import translate_batch_start

        return translate_batch_start(
            str(plugin_id or ""),
            language=str(language or ""),
            strings=strings if strings is not None else {},
            model=str(model or ""),
        )

    def plugin_translate_batch_poll(self, job_id: str) -> dict[str, Any]:
        """Poll plugin_translate_batch_start (non-blocking)."""
        from frontend.ui_web.plugin_host_api import translate_batch_poll

        return translate_batch_poll(str(job_id or ""))

    def plugin_llm_complete(
        self,
        plugin_id: str,
        system: str = "",
        user: str = "",
        model: str = "",
    ) -> dict[str, Any]:
        """Sync LLM complete — prefer plugin_llm_start + plugin_llm_poll (non-blocking)."""
        from frontend.ui_web.plugin_host_api import llm_complete

        return llm_complete(
            str(plugin_id or ""),
            system=str(system or ""),
            user=str(user or ""),
            model=str(model or ""),
        )

    def plugin_cache_get(self, plugin_id: str, key: str) -> dict[str, Any]:
        from frontend.ui_web.plugin_host_api import cache_get

        try:
            return {"ok": True, "data": cache_get(str(plugin_id or ""), str(key or ""))}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "data": {}}

    def plugin_cache_set(self, plugin_id: str, key: str, data: dict | None = None) -> dict[str, Any]:
        from frontend.ui_web.plugin_host_api import cache_set

        try:
            cache_set(str(plugin_id or ""), str(key or ""), dict(data or {}))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def plugin_cache_clear(self, plugin_id: str, key: str = "") -> dict[str, Any]:
        from frontend.ui_web.plugin_host_api import cache_clear

        try:
            return cache_clear(str(plugin_id or ""), str(key or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def plugin_prefs_get_all(self) -> dict[str, Any]:
        """Disk-backed plugin UI prefs (survives WebView localStorage wipes)."""
        from frontend.ui_web.plugin_host_api import prefs_all_get

        try:
            return {"ok": True, "prefs": prefs_all_get()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "prefs": {}}

    def plugin_prefs_set_all(self, prefs: dict | None = None) -> dict[str, Any]:
        from frontend.ui_web.plugin_host_api import prefs_all_set

        try:
            return prefs_all_set(dict(prefs or {}))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def plugin_prefs_set(self, plugin_id: str, prefs: dict | None = None) -> dict[str, Any]:
        from frontend.ui_web.plugin_host_api import prefs_plugin_set

        try:
            return prefs_plugin_set(str(plugin_id or ""), dict(prefs or {}))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def plugin_call(
        self,
        plugin_id: str,
        method: str,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Dispatch a panel RPC registered by ``api.register_panel_rpc`` in a plugin."""
        from backend.uefn_plugins.host import call_panel_rpc

        try:
            result = call_panel_rpc(
                str(plugin_id or ""),
                str(method or ""),
                dict(params or {}) if isinstance(params, dict) else {},
            )
            if isinstance(result, dict):
                return result
            return {"ok": True, "result": result}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def draft_skill_pack(self, description: str, model: str, provider: str = "") -> dict[str, Any]:
        from frontend.ui_web.skill_pack_draft import draft_skill_pack

        return draft_skill_pack(description, model, provider)

    def draft_subskill(
        self,
        pack_id: str,
        label: str,
        description: str,
        model: str,
        provider: str = "",
    ) -> dict[str, Any]:
        from frontend.ui_web.skill_pack_draft import draft_subskill

        return draft_subskill(pack_id, label, description, model, provider)

    def delete_skill(self, filename: str) -> dict[str, Any]:
        return self.delete_skill_pack(Path(filename).stem)

    def delete_skill_pack(self, pack_id: str) -> dict[str, Any]:
        from frontend.skill_deploy import sync_skill_all_ides
        from backend.agent.prompt import clear_skill_cache
        from backend.skills.store import delete_skill_pack

        try:
            ok = delete_skill_pack(pack_id)
        except ValueError as e:
            return {"ok": False, "error": str(e), "pack_id": pack_id}
        if ok:
            clear_skill_cache()
            sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
        return {"ok": ok, "pack_id": pack_id}

    def create_subskill(
        self,
        pack_id: str,
        subskill_id: str,
        label: str,
        description: str = "",
        parent_id: str = "",
        load_condition: str = "",
    ) -> dict[str, Any]:
        from backend.skills.store import create_subskill

        path = create_subskill(
            pack_id,
            subskill_id,
            label,
            description,
            parent_id=parent_id or None,
            load_condition=load_condition,
        )
        return {"ok": True, "path": str(path), "subskill_id": subskill_id}

    def create_skill_node(
        self,
        pack_id: str,
        subskill_id: str,
        label: str,
        description: str = "",
        parent_id: str = "",
        load_condition: str = "",
    ) -> dict[str, Any]:
        return self.create_subskill(
            pack_id, subskill_id, label, description, parent_id, load_condition
        )

    def save_pack_manifest(self, pack_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        from frontend.skill_deploy import sync_skill_all_ides
        from backend.agent.prompt import clear_skill_cache
        from backend.skills.store import save_pack_manifest

        try:
            path = save_pack_manifest(pack_id, patch or {})
        except Exception as e:
            return {"ok": False, "error": str(e), "pack_id": pack_id}
        clear_skill_cache()
        sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
        return {"ok": True, "path": str(path), "pack_id": pack_id}

    def get_skill_pack_graph(self, pack_id: str) -> dict[str, Any]:
        from backend.skills.store import get_skill_pack_graph

        try:
            return {"ok": True, **get_skill_pack_graph(pack_id)}
        except Exception as e:
            return {"ok": False, "error": str(e), "pack_id": pack_id}

    def get_skill_pack_files(self, pack_id: str) -> dict[str, Any]:
        from backend.skills.store import get_skill_pack_files

        try:
            return {"ok": True, **get_skill_pack_files(pack_id)}
        except Exception as e:
            return {"ok": False, "error": str(e), "pack_id": pack_id}

    def save_skill_node(self, pack_id: str, node_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        from frontend.skill_deploy import sync_skill_all_ides
        from backend.agent.prompt import clear_skill_cache
        from backend.skills.store import save_skill_node

        try:
            path = save_skill_node(pack_id, node_id, patch)
            clear_skill_cache()
            sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
            return {"ok": True, "path": str(path), "node_id": node_id}
        except Exception as e:
            return {"ok": False, "error": str(e), "node_id": node_id}

    def save_skill_layout(self, pack_id: str, layout: dict[str, Any]) -> dict[str, Any]:
        from backend.skills.store import save_skill_layout

        try:
            path = save_skill_layout(pack_id, layout)
            return {"ok": True, "path": str(path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _pick_save_file_webview(
        self,
        win: Any,
        *,
        default_name: str,
        file_types: tuple[str, ...],
    ) -> str | None:
        import webview

        try:
            save_type = webview.FileDialog.SAVE
        except AttributeError:
            save_type = getattr(webview, "SAVE_DIALOG", 30)
        try:
            picked = win.create_file_dialog(
                save_type,
                save_filename=default_name,
                file_types=file_types,
            )
            if picked:
                return str(picked if isinstance(picked, str) else picked[0])
        except ValueError:
            # Invalid file_types filter — retry with a safe fallback.
            try:
                picked = win.create_file_dialog(
                    save_type,
                    save_filename=default_name,
                    file_types=("All files (*.*)",),
                )
                if picked:
                    return str(picked if isinstance(picked, str) else picked[0])
            except Exception:
                return None
        except Exception:
            return None
        return None

    def _pick_open_file_webview(self, win: Any, *, file_types: tuple[str, ...]) -> str | None:
        import webview

        try:
            open_type = webview.FileDialog.OPEN
        except AttributeError:
            open_type = getattr(webview, "OPEN_DIALOG", 10)
        try:
            picked = win.create_file_dialog(open_type, file_types=file_types)
            if picked:
                return str(picked[0] if isinstance(picked, (list, tuple)) else picked)
        except ValueError:
            try:
                picked = win.create_file_dialog(open_type, file_types=("All files (*.*)",))
                if picked:
                    return str(picked[0] if isinstance(picked, (list, tuple)) else picked)
            except Exception:
                return None
        except Exception:
            return None
        return None

    def export_skill_pack(self, pack_id: str) -> dict[str, Any]:
        from backend.skills.store import export_skill_pack_bytes, normalize_pack_id

        pid = normalize_pack_id(pack_id)
        default_name = f"{pid}.ducky-skill-pack"
        try:
            payload = export_skill_pack_bytes(pid)
        except Exception as e:
            return {"ok": False, "error": str(e), "pack_id": pid}
        return {
            "ok": True,
            "pack_id": pid,
            "filename": payload.get("filename") or default_name,
            "data_base64": payload.get("data_base64"),
        }

    def prompt_save_skill_pack_export(self, filename: str, data_base64: str) -> dict[str, Any]:
        import base64
        from pathlib import Path

        win = self._resolve_window()
        if win is None:
            return {"ok": False, "error": "No window available"}

        # pywebview only allows \w in extensions — hyphens in *.ducky-skill-pack
        # raise ValueError before the dialog opens (looked like an instant cancel).
        dest = self._pick_save_file_webview(
            win,
            default_name=filename,
            file_types=(
                "Skill packs (*.zip)",
                "All files (*.*)",
            ),
        )
        if not dest:
            return {"ok": False, "cancelled": True}

        try:
            out = Path(dest)
            # Prefer the pack extension even when the dialog filter is *.zip.
            if out.suffix.lower() not in (".zip", ".ducky-skill-pack"):
                out = out.with_suffix(".ducky-skill-pack")
            elif out.suffix.lower() == ".zip" and str(filename or "").endswith(".ducky-skill-pack"):
                out = out.with_suffix(".ducky-skill-pack")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(base64.b64decode(data_base64 or ""))
            return {"ok": True, "path": str(out), "filename": out.name}
        except Exception as e:
            return {"ok": False, "error": f"Save failed: {e}"}

    def import_skill_pack(self, pack_id: str = "", replace: bool = False) -> dict[str, Any]:
        """Legacy path-based import (file dialog). Prefer import_skill_pack_bytes from the UI."""
        from pathlib import Path

        from backend.skills.store import import_skill_pack_from_zip

        win = self._window
        src: str | None = None
        if win is not None:
            src = self._pick_open_file_webview(
                win,
                file_types=(
                    "Skill packs (*.zip)",
                    "All files (*.*)",
                ),
            )
        if not src:
            return {"ok": False, "cancelled": True}
        try:
            result = import_skill_pack_from_zip(
                Path(src),
                pack_id=pack_id or None,
                replace=bool(replace),
            )
            if result.get("ok"):
                from frontend.skill_deploy import sync_skill_all_ides
                from backend.agent.prompt import clear_skill_cache

                clear_skill_cache()
                sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def import_skill_pack_bytes(
        self,
        filename: str,
        data_base64: str,
        pack_id: str = "",
        replace: bool = False,
    ) -> dict[str, Any]:
        import base64

        from backend.skills.store import import_skill_pack_from_bytes

        try:
            raw = base64.b64decode(data_base64 or "")
        except Exception as e:
            return {"ok": False, "error": f"Invalid file data: {e}"}
        if not raw:
            return {"ok": False, "error": "Empty file"}
        try:
            result = import_skill_pack_from_bytes(
                raw,
                pack_id=pack_id or None,
                replace=bool(replace),
            )
            if result.get("ok"):
                from frontend.skill_deploy import sync_skill_all_ides
                from backend.agent.prompt import clear_skill_cache

                clear_skill_cache()
                sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
            return result
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def delete_subskill(self, pack_id: str, subskill_id: str) -> dict[str, Any]:
        from backend.skills.store import delete_subskill

        try:
            ok = delete_subskill(pack_id, subskill_id)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": ok, "subskill_id": subskill_id}

    def reset_skill_pack(self, pack_id: str) -> dict[str, Any]:
        from frontend.skill_deploy import sync_skill_all_ides
        from backend.agent.prompt import clear_skill_cache
        from backend.skills.store import reset_skill_pack

        dest = reset_skill_pack(pack_id)
        clear_skill_cache()
        logs = sync_skill_all_ides(PanelSettings.load().antigravity_config_path)
        return {"ok": bool(dest), "path": str(dest) if dest else "", "logs": logs}

    def open_skills_folder(self) -> None:
        self.open_skill_packs_folder()

    def open_skill_packs_folder(self) -> None:
        from backend.skills.store import appdata_skill_packs_dir, seed_skill_packs

        seed_skill_packs()
        d = appdata_skill_packs_dir()
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # type: ignore[attr-defined]

    def open_path_in_explorer(self, path: str) -> None:
        import subprocess
        from pathlib import Path

        target = Path(path).expanduser()
        if not target.exists():
            target = target.parent
        if not target.exists():
            return
        if target.is_file():
            subprocess.run(["explorer", "/select,", str(target)], check=False)
        else:
            os.startfile(str(target))  # type: ignore[attr-defined]

    def open_project_path_in_explorer(self, relative_path: str) -> None:
        from frontend.ui_web.project_files import resolve_project_file_path

        self.open_path_in_explorer(resolve_project_file_path(relative_path))

    def open_skill_pack_folder(self, pack_id: str) -> None:
        from backend.skills.store import appdata_skill_packs_dir

        d = appdata_skill_packs_dir() / pack_id
        d.mkdir(parents=True, exist_ok=True)
        os.startfile(str(d))  # type: ignore[attr-defined]

    def reset_skill(self) -> dict[str, Any]:
        return self.reset_skill_pack("uefn")

    def get_mcp_tools_catalog(self) -> dict[str, Any]:
        from frontend.ui_web.mcp_catalog import build_mcp_catalog

        return build_mcp_catalog()

    def list_mcp_plugins(self) -> dict[str, Any]:
        return self.list_mcp_servers()

    def list_mcp_servers(self) -> dict[str, Any]:
        """MCP/tool catalog for Settings — never sync-wait on plugin register()."""
        from backend.agent.builtin_toolsets import builtin_group_rows
        from backend.mcp_plugins.store import list_mcp_servers, seed_mcp_plugins
        from backend.uefn_plugins.host import (
            ensure_plugins_loaded_async,
            plugins_ready,
            uefn_plugin_tool_group_rows,
        )
        from backend.uefn_plugins.store import seed_uefn_plugins

        seed_mcp_plugins()
        seed_uefn_plugins()
        servers = list_mcp_servers()
        builtins = builtin_group_rows()
        if plugins_ready():
            return {
                "ok": True,
                "plugins": builtins + servers + uefn_plugin_tool_group_rows(),
                "servers": servers,
            }
        ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
        return {
            "ok": True,
            "plugins": builtins + servers,
            "servers": servers,
            "plugins_loading": True,
        }

    def set_mcp_plugin_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        return self.set_mcp_server_enabled(plugin_id, enabled)

    def set_mcp_server_enabled(self, server_id: str, enabled: bool) -> dict[str, Any]:
        from frontend.ui_web.project_chats import invalidate_all_conversation_caches
        from backend.agent.builtin_toolsets import is_builtin_group, set_builtin_group_enabled
        from backend.mcp_plugins.store import set_mcp_server_enabled
        from backend.uefn_plugins.host import is_uefn_agent_tool_plugin
        from backend.uefn_plugins.store import set_uefn_plugin_enabled

        pid = server_id.strip().lower()
        if is_builtin_group(pid):
            result = set_builtin_group_enabled(pid, bool(enabled))
        elif is_uefn_agent_tool_plugin(pid):
            result = set_uefn_plugin_enabled(pid, bool(enabled), trust_local=False)
            if result.get("ok"):
                self._push_panel({"type": "uefn_plugins_changed"})
        else:
            result = set_mcp_server_enabled(server_id, bool(enabled))
        s = PanelSettings.load()
        invalidate_all_conversation_caches(s.uefn_project_root or None)
        return result

    def test_mcp_plugin(self, plugin_id: str) -> dict[str, Any]:
        return self.test_mcp_server(plugin_id)

    def test_mcp_server(self, server_id: str) -> dict[str, Any]:
        import asyncio

        from backend.agent.builtin_toolsets import count_builtin_group_tools, is_builtin_group
        from backend.mcp_plugins.client_pool import get_plugin_pool
        from backend.uefn_plugins.host import count_uefn_plugin_tools, is_uefn_agent_tool_plugin

        pid = server_id.strip().lower()
        if is_builtin_group(pid):
            count = count_builtin_group_tools(pid)
            return {
                "ok": True,
                "tool_count": count,
                "stages": [{"stage": "list_tools", "ok": True, "tool_count": count}],
            }
        if is_uefn_agent_tool_plugin(pid):
            count = count_uefn_plugin_tools(pid)
            return {
                "ok": True,
                "tool_count": count,
                "stages": [{"stage": "list_tools", "ok": True, "tool_count": count}],
            }
        return asyncio.run(get_plugin_pool().test_plugin(server_id.strip()))

    def create_mcp_plugin(
        self,
        plugin_id: str,
        label: str,
        description: str = "",
        command: str = "",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        tool_prefix: str = "",
        intents: list[str] | None = None,
        transport: str = "stdio",
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self.create_mcp_server(
            plugin_id,
            label,
            description,
            command,
            args,
            env,
            tool_prefix,
            intents,
            transport,
            url,
            headers,
        )

    def create_mcp_server(
        self,
        server_id: str,
        label: str,
        description: str = "",
        command: str = "",
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        tool_prefix: str = "",
        intents: list[str] | None = None,
        transport: str = "stdio",
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from backend.mcp_plugins.store import create_mcp_server, mcp_config_path

        path = create_mcp_server(
            server_id,
            label,
            description=description,
            command=command,
            args=args,
            env=env,
            tool_prefix=tool_prefix,
            intents=intents,
            transport=transport,
            url=url,
            headers=headers,
        )
        sid = server_id.strip().lower()
        return {"ok": True, "path": str(path), "plugin_id": sid, "server_id": sid, "mcp_config": str(mcp_config_path())}

    def delete_mcp_plugin(self, plugin_id: str) -> dict[str, Any]:
        return self.delete_mcp_server(plugin_id)

    def delete_mcp_server(self, server_id: str) -> dict[str, Any]:
        from backend.mcp_plugins.store import delete_mcp_server

        ok = delete_mcp_server(server_id)
        return {"ok": ok}

    def get_mcp_config(self) -> dict[str, Any]:
        from backend.mcp_plugins.store import get_mcp_config_text, mcp_config_path, seed_mcp_plugins

        seed_mcp_plugins()
        return {"ok": True, "path": str(mcp_config_path()), "text": get_mcp_config_text()}

    def set_mcp_config(self, text: str) -> dict[str, Any]:
        from backend.mcp_plugins.store import set_mcp_config_text

        try:
            path = set_mcp_config_text(text)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "path": str(path)}

    def get_chat_mcp_plugins(self, conv_id: str) -> dict[str, Any]:
        """Plugin catalog + this chat's override (null = follow global defaults).

        Built-in tool groups (UEFN / Ducky app) and UEFN app-plugin agent.tools
        ride along as pseudo-plugin rows; the override list mixes their ids
        with external MCP plugin ids. Never sync-wait on plugin register().
        """
        from backend.agent.builtin_toolsets import builtin_group_rows, get_enabled_builtin_group_ids
        from backend.mcp_plugins.store import get_enabled_plugin_ids, list_mcp_plugins
        from backend.uefn_plugins.host import (
            ensure_plugins_loaded_async,
            plugins_ready,
            uefn_agent_tool_rows,
        )
        from backend.uefn_plugins.store import seed_uefn_plugins

        seed_uefn_plugins()
        conv = load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "Conversation not found"}

        ready = plugins_ready()
        if not ready:
            ensure_plugins_loaded_async(on_done=self._notify_plugins_ready)
        uefn_rows = uefn_agent_tool_rows() if ready else []

        override: list[str] | None = None
        if (
            conv.mcp_plugins is not None
            or conv.builtin_toolsets is not None
            or conv.uefn_plugins is not None
        ):
            builtin_part = (
                conv.builtin_toolsets
                if conv.builtin_toolsets is not None
                else get_enabled_builtin_group_ids()
            )
            plugin_part = conv.mcp_plugins if conv.mcp_plugins is not None else get_enabled_plugin_ids()
            # App-plugin tools: None = follow Store enable (same as nested MCP).
            # While plugins load, skip unresolved Store defaults (empty uefn_part).
            uefn_part = (
                conv.uefn_plugins
                if conv.uefn_plugins is not None
                else [str(r.get("id") or "") for r in uefn_rows if r.get("id")]
            )
            override = list(builtin_part) + list(plugin_part) + list(uefn_part)
        return {
            "ok": True,
            "override": override,
            "plugins": builtin_group_rows() + list_mcp_plugins() + uefn_rows,
            "plugins_loading": not ready,
        }

    def set_chat_mcp_plugins(self, conv_id: str, plugin_ids: list[str] | None) -> dict[str, Any]:
        """Set this chat's MCP plugin override; null resets to global defaults.

        The list may mix builtin group ids, UEFN app-plugin ids, and MCP plugin
        ids — split into conv.builtin_toolsets / uefn_plugins / mcp_plugins.
        """
        from backend.agent.builtin_toolsets import is_builtin_group
        from backend.mcp_plugins.store import load_plugin_manifest as load_mcp_manifest
        from backend.mcp_plugins.store import normalize_plugin_id
        from backend.uefn_plugins.host import is_uefn_agent_tool_plugin

        conv = load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "Conversation not found"}
        if plugin_ids is None:
            conv.mcp_plugins = None
            conv.builtin_toolsets = None
            conv.uefn_plugins = None
            save_conversation(conv, touch_updated=False)
            return {"ok": True, "override": None}
        builtin_sel: list[str] = []
        plugin_sel: list[str] = []
        uefn_sel: list[str] = []
        for item in plugin_ids:
            pid = normalize_plugin_id(str(item))
            if is_builtin_group(pid):
                if pid not in builtin_sel:
                    builtin_sel.append(pid)
                continue
            if is_uefn_agent_tool_plugin(pid):
                if pid not in uefn_sel:
                    uefn_sel.append(pid)
                continue
            if load_mcp_manifest(pid) is None:
                return {"ok": False, "error": f"MCP plugin not found: {pid}"}
            if pid not in plugin_sel:
                plugin_sel.append(pid)
        conv.builtin_toolsets = builtin_sel
        conv.mcp_plugins = plugin_sel
        conv.uefn_plugins = uefn_sel
        save_conversation(conv, touch_updated=False)
        return {"ok": True, "override": builtin_sel + plugin_sel + uefn_sel}

    def open_mcp_plugins_folder(self) -> None:
        """Reveal AppData folder that holds mcp.json (legacy name)."""
        import os
        import subprocess

        from backend.mcp_plugins.store import mcp_config_path, seed_mcp_plugins
        from backend.skills.store import appdata_dir

        seed_mcp_plugins()
        path = mcp_config_path()
        folder = appdata_dir()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if path.is_file() and sys.platform == "win32":
                subprocess.run(["explorer", "/select,", str(path)], check=False)
                return
        except Exception:
            pass
        os.startfile(str(folder))  # type: ignore[attr-defined]

    def terminal_spawn(
        self,
        shell: str = "bash",
        cwd: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        workdir = _normalize_project_path(cwd) if cwd.strip() else ""
        return get_terminal_manager().spawn(shell=shell, cwd=workdir or None, title=title)

    def terminal_kill(self, session_id: str) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().kill(session_id.strip())

    def terminal_busy(self, session_id: str) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().busy_state(session_id.strip())

    def terminal_list(self) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return {"sessions": get_terminal_manager().list_sessions()}

    def terminal_write(self, session_id: str, data: str) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().write(session_id.strip(), data)

    def terminal_resize(self, session_id: str, cols: int, rows: int) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().resize(session_id.strip(), cols, rows)

    def terminal_request_command(
        self,
        session_id: str,
        command: str,
        source: str = "",
        conv_id: str = "",
    ) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().request_command(
            session_id.strip(),
            command,
            source=source,
            conv_id=conv_id,
        )

    def terminal_approve_command(self, request_id: str) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().approve_command(request_id.strip())

    def terminal_reject_command(self, request_id: str, reason: str = "") -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().reject_command(request_id.strip(), reason=reason or "rejected by user")

    def terminal_read_output(self, session_id: str, max_chars: int = 8000) -> dict[str, Any]:
        from frontend.ui_web.terminal import get_terminal_manager

        return get_terminal_manager().read_output(session_id.strip(), max_chars=max_chars)

    def exit_all(self) -> bool:
        from frontend.ui_web.shutdown import abort_and_quit

        holder = getattr(self, "_window_holder", None)
        if not isinstance(holder, dict):
            holder = {}
        abort_and_quit(api=self, tk_root=self._tk_root, window_holder=holder)
        return True
