"""Appearance, keys, Discord, coding agents, plans, memory, models. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


class PanelApiSettingsMixin:
    def get_settings(self) -> dict[str, Any]:
        s = _pa.PanelSettings.load()
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
            "chat_auto_title": bool(getattr(s, "chat_auto_title", True)),
            "chat_title_model": getattr(s, "chat_title_model", "") or "",
            "follow_code_enabled": bool(getattr(s, "follow_code_enabled", True)),
            "follow_code_speed": (
                s.follow_code_speed
                if getattr(s, "follow_code_speed", "normal")
                in ("slow", "normal", "fast", "instant")
                else "normal"
            ),
            "follow_code_split_beside_chat": bool(
                getattr(s, "follow_code_split_beside_chat", True)
            ),
            "walkthrough_completed": {
                str(k): bool(v) for k, v in (s.walkthrough_completed or {}).items() if v
            },
        }

    def get_appearance(self) -> dict[str, Any]:
        s = _pa.PanelSettings.load()
        if _pa._migrate_legacy_built_in_appearance(s):
            s.validate()
            _pa._save_panel_settings(s)
        return {
            "foundation": dict(s.appearance_foundation or {}),
            "overrides": dict(s.appearance_overrides or {}),
            "status_overrides": dict(s.appearance_status_overrides or {}),
            "profiles": _pa._clean_appearance_profiles(s.appearance_profiles or []),
            "active_profile_id": _pa._normalize_active_profile_id(s.appearance_active_profile_id),
            "effect_id": str(s.appearance_effect_id or ""),
            "effects_enabled": bool(s.appearance_effects_enabled),
            "skin_id": str(s.appearance_skin_id or ""),
            "profile_patches": dict(s.appearance_profile_patches or {}),
            "sounds": dict(s.appearance_sounds or {}),
        }

    def save_appearance(self, patch: dict[str, Any] | str | None) -> str:
        data = _pa._coerce_mapping(patch, label="appearance patch")
        s = _pa.PanelSettings.load()
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
            s.appearance_profiles = _pa._clean_appearance_profiles(profiles)
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
        _pa._save_panel_settings(s)
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

        s = _pa.PanelSettings.load()
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
        s = _pa.PanelSettings.load()
        pid = str(profile_id or "").strip()
        if _pa.is_built_in_appearance_profile(pid):
            if not _pa.apply_built_in_appearance(s, pid):
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
        s = _pa.PanelSettings.load()
        pid = str(profile_id or "").strip()
        if _pa.is_built_in_appearance_profile(pid):
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
        s = _pa.PanelSettings.load()
        pid = str(profile_id or "").strip()
        if _pa.is_built_in_appearance_profile(pid):
            raise ValueError("Built-in appearance profiles cannot be deleted")
        profiles = [p for p in (s.appearance_profiles or []) if p.get("id") != pid]
        if len(profiles) == len(s.appearance_profiles or []):
            raise ValueError(f"Profile not found: {profile_id}")
        s.appearance_profiles = profiles
        was_active = str(s.appearance_active_profile_id or "").strip() == pid
        if was_active:
            # Dropping the active profile → snap working colors back to Default.
            _pa.apply_built_in_appearance(s, _pa.DEFAULT_APPEARANCE_PROFILE_ID)
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
        s = _pa.PanelSettings.load()
        pid = (s.appearance_active_profile_id or "").strip()
        if not pid or _pa.is_built_in_appearance_profile(pid):
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
        status: dict[str, bool] = {p: has_key(p) for p in _pa.all_providers()}
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

        return any(has_key(p) for p in _pa.all_providers())

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

        mod = _pa.sys.modules.get("uefn_plugin_discord")
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

        s = _pa.PanelSettings.load()
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
                s.uefn_project_root = str(_pa.resolve_uefn_project_root(_pa.Path(raw)))
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
        if "chat_auto_title" in patch:
            s.chat_auto_title = bool(patch.get("chat_auto_title"))
        if "chat_title_model" in patch:
            s.chat_title_model = str(patch.get("chat_title_model") or "").strip()
        if "follow_code_enabled" in patch:
            s.follow_code_enabled = _pa._patch_bool(patch.get("follow_code_enabled"))
        if "follow_code_speed" in patch:
            speed = str(patch.get("follow_code_speed") or "normal").strip()
            s.follow_code_speed = (
                speed if speed in ("slow", "normal", "fast", "instant") else "normal"
            )
        if "follow_code_split_beside_chat" in patch:
            s.follow_code_split_beside_chat = _pa._patch_bool(
                patch.get("follow_code_split_beside_chat")
            )
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
        _pa.apply_workspace_env(s.uefn_project_root)
        if s.uefn_project_root:
            root = _pa.Path(s.uefn_project_root)

            def _deploy() -> None:
                try:
                    lines = _pa.deploy_listener(root, _pa.PANEL_LISTENER_PORT)
                    for ln in lines:
                        _pa._log(ln)
                except Exception as e:
                    _pa.record_error("deploy", str(e))

            _pa.threading.Thread(target=_deploy, daemon=True, name="deploy-listener").start()
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

        conv = _pa.load_conversation(conv_id)
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
        _pa.save_conversation(conv)
        _pa.notify_chats_changed(conv.id, conv.title, conv.folder_id, push=self._push)
        return {
            "ok": True,
            "coding_agent": conv.coding_agent,
            "model": conv.model or "",
            "provider": conv.provider or "",
        }

    def set_conversation_thinking_effort(self, conv_id: str, effort: str = "off") -> dict[str, Any]:
        from backend.agent.thinking_effort import normalize_thinking_effort
        from frontend.ui_web.project_chats import load_conversation, save_conversation

        conv = _pa.load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "conversation not found"}
        conv.thinking_effort = normalize_thinking_effort(effort)
        _pa.save_conversation(conv)
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
        info = adapter.detect(_pa.PanelSettings.load())
        return {"ok": True, **info.to_dict()}

    def list_tasks(self) -> dict[str, Any]:
        from backend.agent.coding_agents.epic import list_tasks

        s = _pa.PanelSettings.load()
        return {"tasks": list_tasks(s.uefn_project_root)}

    def create_task(self, title: str, goal: str = "", conv_ids: list[str] | None = None) -> dict[str, Any]:
        from backend.agent.coding_agents.epic import create_task

        s = _pa.PanelSettings.load()
        return create_task(title, goal=goal, conv_ids=conv_ids, project_root=s.uefn_project_root)

    def add_task_phase(self, task_id: str, title: str, plan: str = "") -> dict[str, Any]:
        from backend.agent.coding_agents.epic import add_phase

        s = _pa.PanelSettings.load()
        return add_phase(task_id, title, plan=plan, project_root=s.uefn_project_root)

    def write_task_artifact(self, task_id: str, name: str, content: str, kind: str = "spec") -> dict[str, Any]:
        from backend.agent.coding_agents.epic import write_artifact

        s = _pa.PanelSettings.load()
        return write_artifact(task_id, name, content, kind=kind, project_root=s.uefn_project_root)

    def build_task_handoff(self, task_id: str, phase_id: str = "") -> dict[str, Any]:
        from backend.agent.coding_agents.epic import build_handoff_prompt

        s = _pa.PanelSettings.load()
        return {"ok": True, "prompt": build_handoff_prompt(task_id, phase_id=phase_id, project_root=s.uefn_project_root)}

    def verify_task(
        self, task_id: str, phase_id: str = "", implementation_summary: str = ""
    ) -> dict[str, Any]:
        from backend.agent.coding_agents.epic import verify_against_plan

        s = _pa.PanelSettings.load()
        return verify_against_plan(
            task_id,
            phase_id=phase_id,
            implementation_summary=implementation_summary,
            project_root=s.uefn_project_root,
        )

    def get_plan(self, chat_id: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.agent.coding_agents.plans import load_plan, outline_numbers, todo_progress

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
        return {"ok": True, "entries": list_entries(root), "dir": str(memory_dir(root))}

    def get_memory_entry(self, name: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.memory.project import read_entry

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
        try:
            result = save_entry(
                name, content, description=description, author=author, project_root=root
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "entry": result}

    def delete_memory_entry(self, name: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.memory.project import delete_entry

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
        try:
            deleted = delete_entry(name, root)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "deleted": deleted}

    def get_memory_settings(self) -> dict[str, Any]:
        from backend.agent.context_memory import estimate_tokens
        from backend.memory.project import index_markdown

        s = _pa.PanelSettings.load()
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
        data = _pa._coerce_mapping(patch, label="memory settings patch")
        # Reuse save_agent_settings field clamps; ignore its status string.
        self.save_agent_settings(data)
        return self.get_memory_settings()

    def get_chat_context_memory(self, conv_id: str, project_root: str | None = None) -> dict[str, Any]:
        from backend.agent.context_memory import chat_context_memory_status

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
        conv = _pa.load_conversation(str(conv_id or "").strip(), project_root=root)
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
        conv = _pa.load_conversation(str(conv_id or "").strip(), project_root=root)
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
        conv = _pa.load_conversation(str(conv_id or "").strip(), project_root=root)
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
            else _pa.PanelSettings.load().uefn_project_root
        )
        dest_root = (
            dest_project_root
            if dest_project_root is not None
            else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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

        root = project_root if project_root is not None else _pa.PanelSettings.load().uefn_project_root
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
            saved = _pa.PanelSettings.load().agent_model
            test_model = (
                str(prov_reg.get("test_key_model") or "").strip()
                or _pa._TEST_KEY_MODELS.get(prov)
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
                            _pa._model_cache[prov] = list(models)
                            _pa._save_model_cache_to_disk()
                    except Exception:
                        pass

                _pa.threading.Thread(target=_cache_models, daemon=True, name=f"models-{prov}").start()
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

        prov = provider or _pa.PanelSettings.load().agent_provider
        # Gateways (OpenAI / Ollama) only when their Store plugin is enabled —
        # never serve a stale disk cache for a removed/disabled gateway.
        if prov not in _pa.all_providers():
            _pa._model_cache.pop(prov, None)
            return []
        cached = [] if refresh else _pa._model_cache.get(prov, [])
        if not cached:
            key = get_key(prov)
            if key:
                try:
                    cached = fetch_models(prov, key)
                    _pa._model_cache[prov] = list(cached)
                    _pa._save_model_cache_to_disk()
                except Exception:
                    cached = _pa._model_cache.get(prov, [])
        from frontend.agent_models import provider_label

        label = provider_label(prov) or _pa.PROVIDER_LABELS.get(prov, prov.title())
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
        s = _pa.PanelSettings.load()
        s.agent_model = model_id
        if provider:
            s.agent_provider = provider
        s.validate()
        s.save()

    def get_provider_usage(self, provider_id: str = "", days: int = 7) -> dict[str, Any]:
        """7-day (default) usage report for one provider / coding agent, or all."""
        from frontend.ui_web.provider_usage_log import usage_report

        try:
            return usage_report(provider_id=str(provider_id or ""), days=int(days or 7))
        except Exception as exc:
            import logging

            _pa.logging.getLogger(__name__).exception("get_provider_usage failed: %s", exc)
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

            _pa.logging.getLogger(__name__).exception("get_ducky_usage failed: %s", exc)
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

        src = _pa.Path(picked)
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

    def get_log(self) -> list[str]:
        return list(_pa._log_history)

    def clear_log(self) -> list[str]:
        _pa._log_history.clear()
        return []

    def get_errors(self) -> list[str]:
        _pa.trim_errors()
        lines: list[str] = []
        for e in _pa.read_errors():
            ts = e.get("ts", 0)
            try:
                stamp = _pa.time.strftime("%Y-%m-%d %H:%M:%S", _pa.time.localtime(float(ts)))
            except (ValueError, TypeError):
                stamp = "?"
            lines.append(f"[{stamp}] ({e.get('source', '?')}) {e.get('message', '')}")
        return lines

    def clear_errors(self) -> list[str]:
        _pa.clear_error_log()
        return []

    def pull_editor_log(self) -> None:
        try:
            from backend.bridge import post_command_to_listener

            res = post_command_to_listener(
                _pa.PANEL_LISTENER_PORT, "get_editor_log", {"last_n": 200, "filter_str": "Error"}, timeout=6.0
            )
            for line in res.get("lines", []) or []:
                _pa.record_error("editor", str(line))
        except Exception as e:
            _pa.record_error("panel", f"Pull editor log failed: {e}")
