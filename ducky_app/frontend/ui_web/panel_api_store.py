"""Account, Store, desktop plugins, skill packs, MCP catalog. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


class PanelApiStoreMixin:
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

    def starter_llm_onboard_pending(self) -> dict[str, Any]:
        from frontend.starter_llm_gateways import starter_llm_onboard_pending

        try:
            return starter_llm_onboard_pending()
        except Exception as exc:
            return {"ok": False, "pending": False, "error": str(exc)}

    def ensure_starter_llm_gateways(self) -> dict[str, Any]:
        from frontend.starter_llm_gateways import ensure_starter_llm_gateways

        try:
            result = ensure_starter_llm_gateways()
            if result.get("installed"):
                self._push_panel({"type": "uefn_plugins_changed"})
            return result
        except Exception as exc:
            return {
                "ok": False,
                "first_run": False,
                "error": str(exc),
                "installed": [],
                "skipped": [],
                "errors": [],
            }

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
            from backend.uefn_plugins.store import list_uefn_plugins

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
                _pa._prune_model_caches_to_enabled_providers()
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
                _pa._prune_model_caches_to_enabled_providers()
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
        from backend.uefn_plugins.store import appdata_uefn_plugins_dir

        folder = appdata_uefn_plugins_dir()
        folder.mkdir(parents=True, exist_ok=True)
        self.open_path_in_explorer(str(folder))

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
        settings = _pa.PanelSettings.load()
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
            "appdata_dir": str(_pa.default_app_data_dir()),
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
            return self.save_subskill(parts[0], _pa.Path(parts[-1]).stem, text)
        return self.save_subskill("uefn", "core", text)

    def save_subskill(self, pack_id: str, subskill_id: str, text: str) -> dict[str, Any]:
        from frontend.skill_deploy import sync_skill_all_ides
        from backend.agent.prompt import clear_skill_cache
        from backend.skills.store import save_subskill

        path = save_subskill(pack_id, subskill_id, text)
        clear_skill_cache()
        logs = sync_skill_all_ides(_pa.PanelSettings.load().antigravity_config_path)
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
        sync_skill_all_ides(_pa.PanelSettings.load().antigravity_config_path)
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
            "ensure_starter_llm_gateways",
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
        return self.delete_skill_pack(_pa.Path(filename).stem)

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
            sync_skill_all_ides(_pa.PanelSettings.load().antigravity_config_path)
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
        sync_skill_all_ides(_pa.PanelSettings.load().antigravity_config_path)
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
            sync_skill_all_ides(_pa.PanelSettings.load().antigravity_config_path)
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
            out = _pa.Path(dest)
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
                _pa.Path(src),
                pack_id=pack_id or None,
                replace=bool(replace),
            )
            if result.get("ok"):
                from frontend.skill_deploy import sync_skill_all_ides
                from backend.agent.prompt import clear_skill_cache

                clear_skill_cache()
                sync_skill_all_ides(_pa.PanelSettings.load().antigravity_config_path)
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
                sync_skill_all_ides(_pa.PanelSettings.load().antigravity_config_path)
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
        logs = sync_skill_all_ides(_pa.PanelSettings.load().antigravity_config_path)
        return {"ok": bool(dest), "path": str(dest) if dest else "", "logs": logs}

    def open_skills_folder(self) -> None:
        self.open_skill_packs_folder()

    def open_skill_packs_folder(self) -> None:
        from backend.skills.store import appdata_skill_packs_dir, seed_skill_packs

        seed_skill_packs()
        d = appdata_skill_packs_dir()
        d.mkdir(parents=True, exist_ok=True)
        _pa.os.startfile(str(d))  # type: ignore[attr-defined]

    def open_skill_pack_folder(self, pack_id: str) -> None:
        from backend.skills.store import appdata_skill_packs_dir

        d = appdata_skill_packs_dir() / pack_id
        d.mkdir(parents=True, exist_ok=True)
        _pa.os.startfile(str(d))  # type: ignore[attr-defined]

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
        seed_mcp_plugins()
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
        s = _pa.PanelSettings.load()
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
        conv = _pa.load_conversation(conv_id)
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

        conv = _pa.load_conversation(conv_id)
        if not conv:
            return {"ok": False, "error": "Conversation not found"}
        if plugin_ids is None:
            conv.mcp_plugins = None
            conv.builtin_toolsets = None
            conv.uefn_plugins = None
            _pa.save_conversation(conv, touch_updated=False)
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
        _pa.save_conversation(conv, touch_updated=False)
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
            if path.is_file() and _pa.sys.platform == "win32":
                subprocess.run(["explorer", "/select,", str(path)], check=False)
                return
        except Exception:
            pass
        _pa.os.startfile(str(folder))  # type: ignore[attr-defined]
