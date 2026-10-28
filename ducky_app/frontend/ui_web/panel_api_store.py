"""Account, Store, desktop plugins, skill packs, MCP catalog. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


def _cache_store_catalog(result: dict[str, Any], account: str = "") -> dict[str, Any]:
    """ADR 0003 phase 6: keep the last good catalog in ducky.db so the Store tab
    opens offline and after a WebView profile wipe (localStorage used to hold a
    copy without icons).

    Keyed by ``account``: the catalog carries the signed-in account's private
    items, so another account's copy is dropped, never served.
    """
    result["account"] = account
    try:
        from backend.store.repos import kv
        from backend.store.switch import use_db

        if not use_db("cache_docs"):
            return result
        cached = kv.get_doc("cache_docs", "store_catalog")
        if result.get("ok") and result.get("items"):
            # Every window polls every 10 minutes and nearly always gets the same
            # ~250 KB catalog back; only a changed one is worth a write.
            if not (isinstance(cached, dict) and cached.get("catalog") == result):
                kv.set_doc("cache_docs", "store_catalog", {"saved_at": _pa.time.time(), "catalog": result})
            return result
        if isinstance(cached, dict) and isinstance(cached.get("catalog"), dict):
            if cached["catalog"].get("account") != account:
                kv.delete_doc("cache_docs", "store_catalog")
                return result
            stale = dict(cached["catalog"])
            stale["stale"] = True
            stale["stale_error"] = str(result.get("error") or "")
            stale["saved_at"] = cached.get("saved_at")
            return stale
    except Exception:  # noqa: BLE001
        pass
    return result


# How long a scope switch waits for the round that sends the old space's queued changes.
SWITCH_SEND_TIMEOUT_S = 30.0


def _folder_offer(plugin: str, space: str) -> bool:
    """The bar offers to copy ``plugin``'s Local folders into ``space`` (a project the team
    has no copy of yet, or a link made before folders could be shared)."""
    try:
        from backend.uefn_plugins import team_folders

        return any(o["space"] == space for o in team_folders.offers(plugin))
    except Exception:  # noqa: BLE001 — no offer then
        return False


def _send_before_leaving(plugin: str, leaving: dict[str, Any], target: str) -> int | None:
    """A switch away from a shared space sends that copy's queued changes first, up to
    :data:`SWITCH_SEND_TIMEOUT_S` (after the switch nothing would send them). Returns how
    many of this plugin's changes are still queued there; None when there was nothing to
    send or nothing could send it."""
    import threading

    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins.spaces import has_provider
    from backend.uefn_plugins.sync_scheduler import SLOW_STATES, run_round

    account, space = leaving["account"], leaving["id"]
    if leaving["kind"] != "shared" or space == target or leaving["state"] in SLOW_STATES or not has_provider():
        return None
    if not repo.count_dirty(account, space, plugin):
        return None
    worker = threading.Thread(target=run_round, args=(account, space), kwargs={"force": True},
                              name="plugin-scope-send", daemon=True)
    worker.start()
    worker.join(SWITCH_SEND_TIMEOUT_S)
    return repo.count_dirty(account, space, plugin)


# Retry and Drop my change wait while a space's data is reset on the server: its copy
# stays as it is until the person picks Upload my copy or Clear my copy for all of it.
RESET_FIRST = "This space's data was reset on the server. Choose Upload my copy or Clear my copy first."


def _shared_scope_of(plugin: str) -> dict[str, Any]:
    """The shared copy ``plugin`` shows, for Retry and Drop my change (which wait while
    its space's data is reset on the server, whatever else the copy shows now)."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins.scopes import active_scope

    scope = active_scope(plugin)
    if scope["kind"] != "shared":
        raise ValueError("This plugin's data isn't shared.")
    if repo.sync_get(scope["account"], scope["id"])["state"] == "reset":
        raise ValueError(RESET_FIRST)
    return scope


def _retry_held(plugin: str, key: str) -> dict[str, Any]:
    """Refused and blocked changes of ``plugin`` go back in the queue: all of them, blocked
    folder units too; or only ``key``'s, with the rows blocked in the same write group (they
    go again together), or the blocked folder unit ``key`` names (``<folder>/<unit>``)."""
    from backend.store.repos import folder_files
    from backend.store.repos import plugin_data as repo
    from backend.store.repos import space_vcs

    scope = _shared_scope_of(plugin)
    account, space = scope["account"], scope["id"]

    def group_of(row: dict[str, Any]) -> str:
        r = space_vcs.row(None, account, space, plugin, row["kind"], row["key"])
        return str(r["group_id"] or "") if r and row["sync_state"] == "blocked" else ""

    held = [row for row in repo.state_items(account, space, plugin, limit=10_000)
            if row["sync_state"] in ("refused", "blocked")]
    groups = {group_of(row) for row in held if row["key"] == key} - {""} if key else set()
    retried = 0
    for row in held:
        if not key or row["key"] == key or (groups and group_of(row) in groups):
            retried += repo.set_state(account, space, plugin, row["kind"], row["key"], "")
    if not key:
        retried += folder_files.requeue_blocked(account, space, plugin)
    elif not retried and "/" in key:
        folder_id, unit = key.split("/", 1)
        retried = folder_files.requeue_blocked(account, space, plugin, folder_id, unit)
    if key and not retried:
        return {"ok": False, "error": "That change isn't waiting to be sent again."}
    return {"ok": True, "retried": retried}


def _drop_change(plugin: str, kind: str, key: str) -> dict[str, Any]:
    """This PC's refused or blocked change to one item goes: a never-sent item is just
    removed, an item the team has comes back on the next pull (the cursor goes back to its
    rev). Only that change: an edit made since, or a change being sent, stays."""
    from backend.store.repos import plugin_data as repo
    from backend.uefn_plugins import scopes

    scope = _shared_scope_of(plugin)
    account, space = scope["account"], scope["id"]
    if kind not in ("doc", "asset"):
        raise ValueError("invalid item kind")
    row = repo.get(account, space, plugin, kind, key)
    gone = {"ok": False, "error": "That change was already sent or dropped."}
    if not row or not row["dirty"] or row["sync_state"] not in ("refused", "blocked"):
        return gone
    target = scopes.asset_file(scope, plugin, key) if kind == "asset" else None
    if not repo.drop_held(account, space, plugin, kind, key, expect=row, target_path=target):
        return gone
    return {"ok": True, "dropped": 1}


class PanelApiStoreMixin:
    def duckyos_get_status(self) -> dict[str, Any]:
        # Store tab also reads status — do not gate on the Account plugin.
        from frontend.duckyos_account import get_status, refresh_status

        try:
            return refresh_status()
        except Exception:
            return get_status()

    def duckyos_agent_caps(self, include_catalog: bool = True) -> dict[str, Any]:
        from frontend.duckyos_account import agent_caps_status

        return agent_caps_status(include_catalog=bool(include_catalog))

    def duckyos_agent_caps_set(self, denied: Any = None, settings: Any = None) -> dict[str, Any]:
        from frontend.duckyos_account import set_agent_caps

        return set_agent_caps(denied, settings)

    def duckyos_login(self, base_url: str = "", email: str = "", password: str = "") -> dict[str, Any]:
        """Start browser-based login (email/password args ignored — kept for API compat)."""
        from frontend.duckyos_account import DuckyOSAccountError, start_browser_login

        _ = email, password
        try:
            return start_browser_login(str(base_url or ""))
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "logged_in": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error", "logged_in": False}

    def duckyos_cancel_login(self) -> dict[str, Any]:
        from frontend.duckyos_account import cancel_browser_login

        return cancel_browser_login()

    def duckyos_submit_code(self, code: str = "") -> dict[str, Any]:
        """Deprecated — email codes are completed in the browser now."""
        _ = code
        return {
            "ok": False,
            "error": "Use Log in — verification happens in the browser.",
            "code": "use_browser",
            "logged_in": False,
        }

    def duckyos_logout(self) -> dict[str, Any]:
        from frontend.duckyos_account import DuckyOSAccountError, logout

        try:
            return logout()
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "logged_in": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error", "logged_in": False}

    def duckyos_list_pcs(self) -> dict[str, Any]:
        from frontend.duckyos_account import DuckyOSAccountError, list_account_pcs

        try:
            return list_account_pcs()
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "devices": []}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error", "devices": []}

    # ---- Your other UEFN Duckies (frontend.ducky_link). This PC's windows only: OFF_PC_DENY.

    def ducky_link_list(self) -> dict[str, Any]:
        """The account's UEFN Duckies; ``mine`` is this one, ``live`` = reachable now."""
        from frontend.duckyos_account import DuckyOSAccountError
        from frontend.ducky_link import list_duckies

        try:
            return list_duckies()
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "devices": []}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "devices": []}

    def ducky_link_call(self, to: str = "", method: str = "", args: dict | None = None) -> dict[str, Any]:
        """Run one allowlisted call (list_all_conversations, load_messages, send_message…)
        on another of your UEFN Duckies."""
        from frontend.ducky_link import call

        return call(str(to or ""), str(method or ""), args if isinstance(args, dict) else {})

    def ducky_link_rtc_connect(self, args: dict | None = None) -> dict[str, Any]:
        """Handshake for a window showing another UEFN Ducky: the page's offer goes to
        that Ducky sealed; its answer comes back. ``{"answer", "version", "protocol"}``."""
        from frontend.ducky_link import call

        raw = args if isinstance(args, dict) else {}
        out = call(
            str(raw.get("to") or ""),
            "rtc_connect",
            {k: raw.get(k) for k in ("session", "offer", "ice", "protocol")},
            timeout=45.0,
        )
        if not out.get("ok"):
            raise RuntimeError(str(out.get("error") or "That UEFN Ducky didn't answer."))
        return out.get("result") if isinstance(out.get("result"), dict) else {}

    def ducky_link_open(self, key_id: str = "", name: str = "", chat_id: str = "") -> dict[str, Any]:
        """Open another of your UEFN Duckies in its own window here (at ``chat_id``, one of its chats)."""
        import re

        from frontend.ui_web.remote_ducky_window import open_remote_ducky

        key = str(key_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", key):
            return {"ok": False, "error": "Pick one of your UEFN Duckies."}
        chat = str(chat_id or "").strip()
        if chat and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", chat):
            return {"ok": False, "error": "That isn't one of its chats."}
        label = (str(name or "").strip() or "UEFN Ducky")[:64]
        # Like a focus window: pywebview marshals window creation onto its own thread.
        open_remote_ducky(key, label, chat)
        return {"ok": True}

    def ducky_sync_list(self) -> dict[str, Any]:
        """Your other UEFN Duckies' chats as copied here (no rows), for the sidebar."""
        from frontend.ducky_sync import mirror_list

        try:
            return {"ok": True, "duckies": mirror_list()}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "duckies": []}

    def ducky_sync_copy(self, key_id: str = "", chat_id: str = "") -> dict[str, Any]:
        """The read-only copy of one of another Ducky's chats: its newest rows."""
        from frontend.ducky_sync import mirror_rows

        copy = mirror_rows(str(key_id or ""), str(chat_id or ""))
        return {"ok": True, **copy} if copy else {"ok": False, "error": "No copy of that chat here yet."}

    def ducky_sync_now(self) -> dict[str, Any]:
        """Pull from your other UEFN Duckies now instead of at the next round."""
        from frontend import ducky_sync

        ducky_sync.start()
        ducky_sync.sync_now()
        return {"ok": True}

    def ducky_sync_forget(self, key_id: str = "") -> dict[str, Any]:
        """Remove one of your other UEFN Duckies: sign that PC out of the account and drop
        its chat copies here. Its own chats stay on that PC."""
        from frontend.ducky_sync import forget

        key = str(key_id or "").strip()
        if not key:
            return {"ok": False, "error": "Pick one of your UEFN Duckies."}
        revoked = self.duckyos_revoke_pc(key)
        dropped = forget(key)
        # Already gone from the account still clears the copies; report the revoke error otherwise.
        return {"ok": True, "dropped": dropped, "revoke_error": "" if revoked.get("ok", True) else str(revoked.get("error") or "")}

    def duckyos_revoke_pc(self, key_id: str = "") -> dict[str, Any]:
        from frontend.duckyos_account import DuckyOSAccountError, revoke_account_pc

        try:
            return revoke_account_pc(str(key_id or ""))
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "logged_in": False}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error", "logged_in": False}

    def duckyos_open_admin(self) -> None:
        import webbrowser

        from frontend.duckyos_account import get_status

        status = get_status()
        base = str(status.get("base_url") or "").rstrip("/")
        if base:
            webbrowser.open(f"{base}/admin")

    def duckyos_open_site(self, path: str = "/") -> dict[str, Any]:
        """Open a path of the signed-in account's website in the browser."""
        from frontend.duckyos_account import DuckyOSAccountError, open_site_path

        try:
            return open_site_path(str(path or "/"))
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error"}

    def remote_status(self) -> dict[str, Any]:
        from frontend.settings import PanelSettings
        from frontend.ui_web.panel_httpd import remote_session_count, remote_session_summaries

        s = PanelSettings.load()
        enabled = bool(getattr(s, "remote_access", False))
        # Remote Ducky connects peer to peer: nothing to start, so on is ready.
        return {
            "ok": True,
            "enabled": enabled,
            "running": enabled,
            "error": "",
            "site_update_pending": False,
            "sessions": remote_session_count(),
            "session_list": remote_session_summaries(),
        }

    def remote_set_enabled(self, enabled: bool = False) -> dict[str, Any]:
        from frontend.settings import PanelSettings
        from frontend.ui_web.panel_api import _save_panel_settings
        from frontend.duckyos_account import publish_device_presence

        s = PanelSettings.load()
        s.remote_access = bool(enabled)
        _save_panel_settings(s)
        if s.remote_access:
            publish_device_presence(live=True)
        else:
            from frontend.ui_web.panel_httpd import kick_all_remote

            kick_all_remote()
            publish_device_presence(live=False)
        return self.remote_status()

    def remote_sign_out_all(self) -> dict[str, Any]:
        from frontend.ui_web.panel_httpd import kick_all_remote

        kick_all_remote()
        return self.remote_status()

    def duckyos_store_catalog(self) -> dict[str, Any]:
        # Store is core — never gated by the Account plugin.
        from frontend.duckyos_account import DuckyOSAccountError, account_key, store_catalog

        account = account_key()
        try:
            result = store_catalog()
        except DuckyOSAccountError as exc:
            result = {"ok": False, "error": exc.message, "code": exc.code, "items": []}
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "code": "error", "items": []}
        if account_key() != account:
            # Signed in/out mid-fetch: this copy belongs to the previous account.
            account = account_key()
            result = {"ok": False, "error": "Account changed", "code": "account_changed", "items": []}
        return _cache_store_catalog(result, account)

    def duckyos_store_versions(self, slug: str, latest_version: str = "") -> dict[str, Any]:
        from frontend.duckyos_account import DuckyOSAccountError, store_item_versions

        try:
            return store_item_versions(str(slug or ""), str(latest_version or "") or None)
        except DuckyOSAccountError as exc:
            return {"ok": False, "error": exc.message, "code": exc.code, "versions": []}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "code": "error", "versions": []}

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
                replace_old=True,  # the person clicked Install/Update
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

    def starter_setup_status(self) -> dict[str, Any]:
        from frontend.starter_llm_gateways import starter_setup_status

        try:
            return starter_setup_status()
        except Exception as exc:
            return {"ok": False, "pending_first_run": False, "gateway_ids": [], "error": str(exc)}

    def ensure_starter_llm_gateways(self, force: bool = False) -> dict[str, Any]:
        from frontend.starter_llm_gateways import ensure_popular_plugins

        def _progress(payload: dict[str, Any]) -> None:
            self._push_panel(payload)
            if payload.get("setup_phase") in ("installed", "skipped"):
                self._push_panel({"type": "uefn_plugins_changed"})

        try:
            result = ensure_popular_plugins(force=bool(force), on_progress=_progress)
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

    def list_uefn_plugins(self) -> dict[str, Any]:
        try:
            from backend.uefn_plugins.store import list_uefn_plugins

            plugins = list_uefn_plugins()
            # The Store list never reads workflow nodes/templates (86% of a 752 KB list):
            # counts here; the catalog and template APIs serve the rows themselves.
            for row in plugins:
                autos = (row.get("contributes") or {}).get("automations")
                if isinstance(autos, dict):
                    row["contributes"] = {
                        **row["contributes"],
                        "automations": {k: len(v) if isinstance(v, (list, dict)) else v for k, v in autos.items()},
                    }
            return {"ok": True, "plugins": plugins}
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
            "chat_references": [],
            "sounds": [],
            "hooks": [],
            "automations_nodes": [],
            "automations_triggers": [],
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
            list_skill_packs,
            seed_skill_packs,
        )

        error = ""
        settings = _pa.PanelSettings.load()
        try:
            seed_skill_packs()
            # Catalog only — full markdown loads via get_skill_pack_files when a pack opens.
            packs = list_skill_packs(include_text=False)
        except Exception as exc:
            packs = []
            error = str(exc)
        # ponytail: the *_text / catalog keys stay (empty) for old readers; the combined
        # prompt was ~690 KB per call and nothing on screen read it.
        combined_text = ""
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
            "catalog": [],
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
            "get_mcp_server_tools",
            "get_workflow_tools_catalog",
            "run_workflow",  # a test run can take minutes; Stop must still get through
            "run_workflow_node",  # one image / 3D node can take minutes too
            "keep_workflow_preview",  # Use this: the steps after a Preview (a save, an import)
            "test_workflow_node",  # a Custom code test can wait on tools for minutes
            "test_mcp_plugin",
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

    def get_uefn_plugin_allowed_models(self, plugin_id: str) -> dict[str, Any]:
        """The models whose chats may use this plugin's sensitive tools (Settings picker)."""
        from backend.uefn_plugins.host import get_contributions
        from backend.uefn_plugins.model_gate import allowed_models
        from backend.uefn_plugins.store import normalize_plugin_id

        try:
            pid = normalize_plugin_id(str(plugin_id or ""))
        except ValueError as exc:
            return {"ok": False, "error": str(exc), "models": []}
        meta = (get_contributions().get("agent_tools") or {}).get(pid) or {}
        sensitive = meta.get("sensitive") if isinstance(meta, dict) else False
        return {
            "ok": True,
            "plugin_id": pid,
            "sensitive": bool(sensitive),
            "models": allowed_models(pid),
        }

    def set_uefn_plugin_allowed_models(self, plugin_id: str, models: list[str] | None = None) -> dict[str, Any]:
        """Save the person's Allowed models list. Host UI only: plugin panels cannot reach it."""
        from backend.uefn_plugins.model_gate import set_allowed_models
        from backend.uefn_plugins.store import normalize_plugin_id

        try:
            pid = normalize_plugin_id(str(plugin_id or ""))
            saved = set_allowed_models(pid, list(models or []))
        except (ValueError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        self._push_panel({"type": "uefn_plugins_changed"})
        return {"ok": True, "plugin_id": pid, "models": saved}

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

    # --- host data service: panels' data.* / files.* / scope.get

    def plugin_data(self, plugin_id: str, op: str, params: dict | None = None) -> dict[str, Any]:
        from backend.uefn_plugins.scopes import LockedOut, ReadOnlyScope, panel_call

        try:
            return panel_call(str(plugin_id or ""), str(op or ""), dict(params or {}))
        except LockedOut as exc:
            # Someone else holds the item: the panel's bar offers Ask to release.
            return {"ok": False, "error": str(exc), "code": "locked", "lock": dict(exc.lock)}
        except ReadOnlyScope as exc:
            return {"ok": False, "error": str(exc), "code": "read_only"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # -- live editing on a team copy (backend.uefn_plugins.live)

    def plugin_live_join(self, plugin_id: str, room: str = "") -> dict[str, Any]:
        """Start live (no room: a teammate's group, else this PC's own) or Join a group from a
        plugin: this PC goes live on its team copy, every plugin with it, and goes back in
        after a restart until Leave. A room of the plugin's own is for it alone."""
        from backend.uefn_plugins import live

        pid, room = str(plugin_id or ""), str(room or "")
        out = live.join(pid, room)
        team = live.team_of(pid)
        if out.get("ok") and team is not None and (not room or room.startswith(live.HOST_PREFIX)):
            live.remember_team(*team, room or live.START)
        return out

    def plugin_live_leave(self, plugin_id: str) -> dict[str, Any]:
        """Leave from a plugin: this PC leaves its team copy's group (a room of the plugin's
        own: only that)."""
        from backend.uefn_plugins import live

        return live.leave_live(str(plugin_id or ""))

    def live_overview(self) -> dict[str, Any]:
        """The title bar's Live menu: every team copy this PC has plugins on, who is live in
        which group of it, and where this PC is."""
        from backend.uefn_plugins import live

        try:
            return live.overview()
        except Exception as exc:
            return {"ok": False, "on": False, "teams": [], "error": str(exc)}

    def live_team_join(self, account: str = "", space: str = "", room: str = "") -> dict[str, Any]:
        """Start live (no room) or Join a teammate's group on a team copy: every plugin of it
        here goes in, and this PC goes back in after a restart until Leave."""
        from backend.uefn_plugins import live

        account, space, room = str(account or ""), str(space or ""), str(room or "")
        out = live.join_team(account, space, room)
        if out.get("ok"):
            live.remember_team(account, space, room or live.START)
        return out

    def live_team_invite(self, account: str = "", space: str = "", device: str = "") -> dict[str, Any]:
        """Invite a teammate who has the app open into this PC's group on a team copy (starts
        live first when this PC isn't): their app asks them to join for 30 s."""
        from backend.uefn_plugins import live

        try:
            return live.invite(str(account or ""), str(space or ""), str(device or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def live_team_leave(self, account: str = "", space: str = "") -> dict[str, Any]:
        from backend.uefn_plugins import live

        return live.leave_team(str(account or ""), str(space or ""))

    def plugin_live_send(self, plugin_id: str, topic: str, data: object = None, to: str = "",
                         fast: bool = False) -> dict[str, Any]:
        from backend.uefn_plugins import live

        return live.send(str(plugin_id or ""), str(topic or ""), data, to=str(to or ""), fast=bool(fast))

    def plugin_live_hold(self, plugin_id: str, anchor: str, on: bool = True, t: float = 0.0,
                         view: str = "") -> dict[str, Any]:
        """The page's live script presses or lets go of a control (every teammate greys it out)."""
        from backend.uefn_plugins import live

        return live.hold(str(plugin_id or ""), str(anchor or ""), bool(on), t, str(view or ""))

    def plugin_live_holds(self, plugin_id: str) -> dict[str, Any]:
        """What teammates hold in this plugin now (a page that just loaded)."""
        from backend.uefn_plugins import live

        return {"ok": True, "holds": live.holds_now(str(plugin_id or ""))}

    def plugin_live_set_my_state(self, plugin_id: str, state: object = None) -> dict[str, Any]:
        """A panel sets this PC's live state in its plugin (a camera, a tool in hand)."""
        from backend.uefn_plugins import live

        return live.set_my_state(str(plugin_id or ""), state)

    def plugin_live_share(self, plugin_id: str, key: str, value: object = None) -> dict[str, Any]:
        """A panel sets the group's value of one of its components (through the leader)."""
        from backend.uefn_plugins import live

        return live.share(str(plugin_id or ""), str(key or ""), value)

    def plugin_live_shared(self, plugin_id: str) -> dict[str, Any]:
        """The group's values of the plugin's components now (a page that just loaded)."""
        from backend.uefn_plugins import live

        return {"ok": True, "values": live.shared(str(plugin_id or ""))}

    def plugin_live_member_states(self, plugin_id: str) -> dict[str, Any]:
        """Every groupmate's live state in the plugin (a page that just loaded)."""
        from backend.uefn_plugins import live

        return {"ok": True, "states": live.member_states(str(plugin_id or ""))}

    def plugin_live_status(self, plugin_id: str) -> dict[str, Any]:
        from backend.uefn_plugins import live

        return live.status(str(plugin_id or ""))

    def plugin_scope_status(self, plugin_id: str) -> dict[str, Any]:
        """Where one plugin's data lives (scope bar, Plugins page)."""
        from backend.uefn_plugins.spaces import scope_status

        try:
            return scope_status(str(plugin_id or ""))
        except Exception as exc:
            return {"ok": False, "visible": False, "error": str(exc)}

    def plugin_scope_choices(self) -> dict[str, Any]:
        """Pickers only: asks the spaces provider (never on a timer)."""
        from backend.uefn_plugins.spaces import scope_choices

        try:
            return scope_choices("data")
        except Exception as exc:
            return {"ok": False, "choices": [], "error": str(exc)}

    def plugin_scope_set(self, plugin_id: str, scope_id: str, options: dict | None = None) -> dict[str, Any]:
        """Keep a plugin's data in Local or one shared space. Nothing moves: the plugin
        restarts on that scope's own copy, then its open panels reload (``switched``).
        Changes still queued for the space it leaves are sent first (up to 30 s);
        ``unsent`` is how many of them didn't go (they stay queued in that copy).

        A plugin that keeps its own folders: the old copy's changed units go out first,
        then the team's folder copy comes down while the plugin still reads its current one
        (a failed pull changes nothing). An empty team copy answers ``{ok: false, needs:
        "first_share", team, units, note}``: call again with the first share as the third
        argument, ``{units: [...]}`` (copied from Local and sent, then used) or
        ``{start_empty: true}`` (also accepted as ``{firstShare: {...}}``). The bar's "Copy
        Local into <team>" sends ``{pick: true}``: the answer asks which Local units to copy
        even when the team already has files, and the same call with the units copies them.
        A team that has no copy yet of a project opened since (its offer) goes through the
        switch too, even when the plugin is on that team already."""
        import threading

        from backend.uefn_plugins.scopes import active_scope
        from backend.uefn_plugins.spaces import link_scope, switch_folders, sync_active

        pid = str(plugin_id or "")
        target = str(scope_id or "")
        opts = dict(options or {}) if isinstance(options, dict) else {}
        first_share = opts.get("firstShare") if isinstance(opts.get("firstShare"), dict) else None
        if first_share is None and any(k in opts for k in ("units", "start_empty", "startEmpty", "pick")):
            first_share = opts
        try:
            leaving = active_scope(pid)
            before = leaving["id"]
            unsent = _send_before_leaving(pid, leaving, target)
            folders = switch_folders(pid, target, first_share=first_share) \
                if before != target or first_share or _folder_offer(pid, target) else None
            if folders is not None and not folders.get("ok"):
                return {**folders, "ok": False}
            out = link_scope(pid, target)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if unsent is not None:
            out = {**out, "unsent": unsent, "unsentLabel": str(leaving["label"])}
        # Its bar reads the new scope; its panels start over with the "switched" push below.
        self._push_panel({"type": "plugin_scope_changed", "plugins": [pid], "status": True})
        sync_active(pid, force=True, on_done=self._scope_synced)
        if out.get("scope", {}).get("spaceId", "") == ("" if before == "personal" else before) and not (
                folders and folders.get("changed")):
            return out  # same copy as before: nothing to restart

        def _restart() -> None:
            # Backend state (caches, threads, loaded docs) belongs to the old copy:
            # unload and register again, never a full plugin reload.
            try:
                from backend.uefn_plugins.host import reload_single_plugin

                reload_single_plugin(pid)
            except Exception:
                pass
            self._push_panel({"type": "plugin_scope_changed", "plugins": [pid], "switched": True})

        threading.Thread(target=_restart, name="plugin-scope-restart", daemon=True).start()
        return out

    def plugin_scope_sync(self, plugin_id: str, force: bool = False) -> dict[str, Any]:
        """Scope bar asks on plugin open, focus and each minute; the provider rate-limits."""
        from backend.uefn_plugins.spaces import sync_active

        try:
            return sync_active(str(plugin_id or ""), force=bool(force), on_done=self._scope_synced)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def plugin_scope_retry(self, plugin_id: str, key: str = "") -> dict[str, Any]:
        """Send a refused or blocked change again (every one without ``key``), then sync."""
        from backend.uefn_plugins.spaces import sync_active

        pid = str(plugin_id or "")
        try:
            out = _retry_held(pid, str(key or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if out.get("ok"):
            self._push_panel({"type": "plugin_scope_changed", "plugins": [pid], "status": True})
            sync_active(pid, force=True, on_done=self._scope_synced)
        return out

    def plugin_scope_drop(self, plugin_id: str, kind: str, key: str) -> dict[str, Any]:
        """Drop this PC's refused or blocked change: the team's copy of the item comes back
        on the next pull."""
        from backend.uefn_plugins.spaces import sync_active

        pid = str(plugin_id or "")
        try:
            out = _drop_change(pid, str(kind or ""), str(key or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not out.get("ok"):
            return out
        self._push_panel({"type": "plugin_scope_changed", "plugins": [pid]})
        sync_active(pid, force=True, on_done=self._scope_synced)
        return out

    def space_reset_settle(self, space_id: str, action: str) -> dict[str, Any]:
        """A space whose data was reset on the server: Upload my copy (``upload``) or Clear
        my copy (``clear``), for this PC's whole copy of it (every plugin's, its workflows
        too), then a round. After a clear, the plugins showing that space restart on the
        copy pulled again, like after a switch."""
        import threading

        from backend.uefn_plugins.scopes import account_id
        from backend.uefn_plugins.spaces import WORKFLOW_DOCS, settle_reset, sync_space

        space = str(space_id or "")
        try:
            out = settle_reset(space, str(action or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not out.get("ok"):
            return out
        plugins = sorted(out.pop("plugins", []))
        self._push_panel({"type": "plugin_scope_changed", "plugins": plugins})
        if WORKFLOW_DOCS in plugins:
            self._push_panel({"type": "graphs_changed"})
        sync_space(account_id(), space, force=True, on_done=self._scope_synced)
        if "cleared" not in out:
            return out

        def _restart() -> None:
            # Their backend state belongs to the copy that just went.
            from backend.store.repos import plugin_data as repo

            linked = sorted(p for p, s in repo.links(account_id()).items() if s == space)
            for pid in linked:
                try:
                    from backend.uefn_plugins.host import reload_single_plugin

                    reload_single_plugin(pid)
                except Exception:
                    pass
            self._push_panel({"type": "plugin_scope_changed", "plugins": linked, "switched": True})

        threading.Thread(target=_restart, name="space-reset-restart", daemon=True).start()
        return out

    # --- team copies (the new sync): the scope bar's drawer

    def plugin_scope_activity(self, plugin_id: str) -> dict[str, Any]:
        """The drawer's tables (people, locks, changes, conflicts, mode, head) with the rest
        of the status. A team space is looked at again right away (heads, presence)."""
        from backend.uefn_plugins.scopes import active_scope
        from backend.uefn_plugins.spaces import scope_status
        from backend.uefn_plugins.sync_scheduler import wake

        pid = str(plugin_id or "")
        try:
            scope = active_scope(pid)
            if scope["kind"] == "shared":
                wake(scope["account"], scope["id"])
            return scope_status(pid)
        except Exception as exc:
            return {"ok": False, "visible": False, "error": str(exc)}

    def _team_call(self, plugin_id: str, fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """One team call for the drawer: its answer, or ``{ok: false, error}``; the plugin's
        bar reads its status again after a change."""
        pid = str(plugin_id or "")
        try:
            out = fn(pid, *args, **kwargs)
        except (ValueError, PermissionError) as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        out = dict(out or {})
        out.setdefault("ok", not out.get("error"))
        return out

    def plugin_scope_lock(self, plugin_id: str, unit: str, name: str = "") -> dict[str, Any]:
        """Lock a unit by hand (kept until released)."""
        from backend.uefn_plugins.spaces import team_lock

        return self._team_call(plugin_id, team_lock, str(unit or ""), "acquire", name=str(name or ""))

    def plugin_scope_unlock(self, plugin_id: str, unit: str) -> dict[str, Any]:
        from backend.uefn_plugins.spaces import team_lock

        return self._team_call(plugin_id, team_lock, str(unit or ""), "release")

    def plugin_scope_break(self, plugin_id: str, unit: str) -> dict[str, Any]:
        """Break someone's lock (owners and admins, or anyone once its lease ran out)."""
        from backend.uefn_plugins.spaces import team_lock

        return self._team_call(plugin_id, team_lock, str(unit or ""), "break")

    def plugin_scope_ask(self, plugin_id: str, unit: str) -> dict[str, Any]:
        """Ask the holder to release a unit."""
        from backend.uefn_plugins.spaces import team_lock

        return self._team_call(plugin_id, team_lock, str(unit or ""), "ask")

    def plugin_scope_resolve(self, plugin_id: str, kind: str, key: str, choice: str) -> dict[str, Any]:
        """Settle a conflict: mine, theirs, or compare (``diff``, nothing changes). A folder
        unit (kind ``tree``, key ``<folder>/<unit>``): also both (Keep both), and for one
        changed here while someone else holds it keep_copy (Keep mine as a copy) or discard
        (Discard mine)."""
        from backend.uefn_plugins.spaces import team_resolve

        return self._team_call(plugin_id, team_resolve, str(kind or ""), str(key or ""), str(choice or ""))

    def plugin_scope_restore(self, plugin_id: str, request: dict | None = None) -> dict[str, Any]:
        """A new changeset on the server: ``items`` (kind, key, seq) back to that version,
        the plugin as of ``toSeq``, or changeset ``undoSeq`` undone. ``skipped``: items
        whose version is no longer kept."""
        from backend.uefn_plugins.spaces import team_restore

        req = dict(request or {})
        to_seq = req.get("toSeq")
        return self._team_call(plugin_id, team_restore, items=list(req.get("items") or []) or None,
                               to_seq=int(to_seq) if to_seq is not None else None,
                               undo_seq=int(req.get("undoSeq") or 0) or None, message=str(req.get("message") or ""))

    def plugin_scope_history(self, plugin_id: str, request: dict | None = None) -> dict[str, Any]:
        """Changesets newest first, or one item's versions (``kind`` + ``key``), before ``before``."""
        from backend.uefn_plugins.spaces import team_history

        req = dict(request or {})
        return self._team_call(plugin_id, team_history, kind=str(req.get("kind") or ""), key=str(req.get("key") or ""),
                               before=int(req.get("before") or 0) or None, limit=int(req.get("limit") or 50))

    def plugin_scope_mode(self, plugin_id: str, mode: str) -> dict[str, Any]:
        """Auto (rounds push and pull by themselves) or Manual (only Push and Pull do)."""
        from backend.uefn_plugins.spaces import team_mode

        out = self._team_call(plugin_id, team_mode, str(mode or ""))
        if out.get("ok"):
            self._push_panel({"type": "plugin_scope_changed", "plugins": [str(plugin_id or "")], "status": True})
        return out

    def plugin_scope_push(self, plugin_id: str, message: str = "") -> dict[str, Any]:
        """Send this PC's changes now (Manual mode's Push), with an optional message."""
        from backend.uefn_plugins.spaces import team_sync

        return self._team_call(plugin_id, team_sync, push=True, message=str(message or ""), on_done=self._scope_synced)

    def plugin_scope_pull(self, plugin_id: str, kind: str = "", key: str = "") -> dict[str, Any]:
        """Bring everyone's changes now (Pull, Update now); with ``kind`` + ``key``, that
        incoming item is downloaded again first."""
        from backend.uefn_plugins.spaces import team_sync

        return self._team_call(plugin_id, team_sync, pull=True, kind=str(kind or ""), key=str(key or ""),
                               on_done=self._scope_synced)

    def plugin_scope_save_unsent(self, plugin_id: str, action: str = "save") -> dict[str, Any]:
        """Lost access: copy this PC's changes to the space that never went out into Local
        (``save``), or let them go with the copy (``discard``)."""
        from backend.uefn_plugins.spaces import save_unsent

        return self._team_call(plugin_id, save_unsent, str(action or "save"))

    def plugin_data_copy_to_space(self, plugin_id: str, space_id: str) -> dict[str, Any]:
        """One way, on request: a plugin's Local data into one space's copy (theirs is kept)."""
        from backend.uefn_plugins.scopes import ReadOnlyScope, copy_local_into
        from backend.uefn_plugins.spaces import sync_active

        pid = str(plugin_id or "")
        try:
            out = copy_local_into(pid, str(space_id or ""))
        except (ReadOnlyScope, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        self._push_panel({"type": "plugin_scope_changed", "plugins": [pid]})
        sync_active(pid, force=True, on_done=self._scope_synced)
        return out

    def plugin_panel_error(
        self, plugin_id: str, panel_id: str = "", kind: str = "", message: str = "", stack: str = ""
    ) -> dict[str, Any]:
        """An error inside a plugin panel (bridge ``panel.error``): ducky_plugin_errors shows it."""
        from backend.tools.panel.panel_plugin_check import record_panel_error

        record_panel_error(str(plugin_id or ""), str(panel_id or ""), str(kind or ""), str(message or ""),
                           str(stack or ""))
        return {"ok": True}

    def plugin_data_open_web(self, plugin_id: str) -> dict[str, Any]:
        """The shared space's copy of this plugin's data on the web (the provider knows where)."""
        from backend.uefn_plugins.scopes import active_scope
        from backend.uefn_plugins.spaces import provider

        pid = str(plugin_id or "")
        scope = active_scope(pid)
        if scope["kind"] != "shared":
            return {"ok": False, "error": "This plugin's data isn't shared."}
        return provider().open_web(space_id=scope["id"], plugin=pid, page="plugins")

    def _scope_synced(self, result: dict[str, Any]) -> None:
        from backend.uefn_plugins.sync_scheduler import synced_event

        self._push_panel(synced_event(result))

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

    def reload_uefn_plugin(self, plugin_id: str) -> dict[str, Any]:
        """The agents' tool server installed a new copy of a plugin: load it here too,
        so the plugin's panels call the new code."""
        from backend.uefn_plugins.host import reload_single_plugin

        try:
            reload_single_plugin(str(plugin_id or ""))
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

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
        if result.get("ok"):
            # Re-publishing the tool catalog lists every server (~1 min): never on the switch.
            import threading

            def _publish() -> None:
                try:
                    from frontend.duckyos_account import publish_agent_catalog

                    publish_agent_catalog()
                except Exception:
                    pass

            threading.Thread(target=_publish, name="publish-agent-catalog", daemon=True).start()
        return result

    def get_mcp_server_tools(self, server_id: str) -> dict[str, Any]:
        """One server's tools for its Settings page (run as a bridge job)."""
        from frontend.ui_web.mcp_catalog import build_server_catalog

        return build_server_catalog(str(server_id or ""))

    def get_mcp_diagnostics(self, server_id: str = "", mode: str = "agent") -> dict[str, Any]:
        """Read cached observations; mode selects a policy preview, not a run mode."""
        from frontend.ui_web.mcp_catalog import build_mcp_diagnostics

        return build_mcp_diagnostics(server_id, mode)

    def test_mcp_plugin(self, plugin_id: str) -> dict[str, Any]:
        return self.test_mcp_server(plugin_id)

    def get_mcp_server_connection(self, server_id: str) -> dict[str, Any]:
        """A nested server's URL / command and its headers or env, values masked."""
        from backend.mcp_plugins.store import connection_view

        try:
            return connection_view(server_id)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

    def save_mcp_server_connection(
        self,
        server_id: str,
        transport: str,
        url: str = "",
        command: str = "",
        args: list[str] | None = None,
        values: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Save the editor's connection (new keys go into saved secrets), then drop the
        old connection so the next Test uses it."""
        from backend.mcp_plugins.client_pool import get_plugin_pool
        from backend.mcp_plugins.store import update_connection

        try:
            out = update_connection(server_id, transport=transport, url=url, command=command, args=args, values=values)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if out.get("ok"):
            try:
                get_plugin_pool().forget(str(out.get("server_id") or server_id).strip().lower())
            except Exception:
                pass
        return out

    def test_mcp_server(self, server_id: str) -> dict[str, Any]:
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
        pool = get_plugin_pool()
        return pool.run_sync(pool.test_plugin(server_id.strip()))

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
