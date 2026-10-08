"""PKCE helpers and Store zip hash gate for DuckyOS desktop login."""

from __future__ import annotations

import base64
import hashlib

from frontend.duckyos_account import pkce_pair


def test_pkce_pair_s256() -> None:
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert challenge == expected
    other, _ = pkce_pair()
    assert other != verifier


def test_logout_disconnects_browser_access_and_removes_account() -> None:
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    from frontend import duckyos_account as acc

    events = []
    blob = {"base_url": "https://example.test", "device_key_id": "this-pc", "device_key": "secret"}
    kick = Mock(side_effect=lambda: events.append("sessions"))
    stop = Mock(side_effect=lambda **kwargs: events.append("tunnel"))
    with (
        patch.dict(sys.modules, {
            "frontend.ui_web.panel_httpd": SimpleNamespace(kick_all_remote=kick),
            "frontend.remote_tunnel": SimpleNamespace(stop_remote_tunnel=stop),
        }),
        patch.object(acc, "_load_blob", return_value=blob),
        patch.object(acc, "stop_presence_heartbeat"),
        patch.object(acc, "stop_rpc_waiter"),
        patch.object(acc, "_revoke_device_key", side_effect=lambda row: events.append("revoke")) as revoke,
        patch.object(acc, "_clear_blob", side_effect=lambda: events.append("clear")),
        patch.object(acc, "get_status", return_value={"logged_in": False}),
    ):
        assert acc.logout() == {"logged_in": False}
    stop.assert_called_once_with(deprovision=True)
    revoke.assert_called_once_with(blob)
    assert events == ["sessions", "tunnel", "revoke", "clear"]


def test_logout_clears_account_even_if_browser_cleanup_fails() -> None:
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    from frontend import duckyos_account as acc

    with (
        patch.dict(sys.modules, {
            "frontend.ui_web.panel_httpd": SimpleNamespace(kick_all_remote=Mock(side_effect=RuntimeError)),
            "frontend.remote_tunnel": SimpleNamespace(stop_remote_tunnel=Mock(side_effect=RuntimeError)),
        }),
        patch.object(acc, "_load_blob", return_value={}),
        patch.object(acc, "stop_presence_heartbeat"),
        patch.object(acc, "stop_rpc_waiter"),
        patch.object(acc, "_revoke_device_key") as revoke,
        patch.object(acc, "_clear_blob") as clear,
        patch.object(acc, "get_status", return_value={"logged_in": False}),
    ):
        assert acc.logout() == {"logged_in": False}
    revoke.assert_called_once()
    clear.assert_called_once()


def test_auto_apply_store_updates_skips_local() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    catalog = {
        "ok": True,
        "items": [
            {"slug": "openai", "kind": "plugin", "state": "update", "source": "store"},
            {"slug": "mine", "kind": "plugin", "state": "update", "source": "local"},
            {"slug": "fresh", "kind": "plugin", "state": "available", "source": "store"},
        ],
    }
    calls: list[str] = []

    def _install(slug: str, **_kw: object) -> dict:
        calls.append(slug)
        return {"ok": True, "slug": slug}

    with (
        patch.object(acc, "store_catalog", return_value=catalog),
        patch.object(acc, "store_download_and_install", side_effect=_install),
    ):
        out = acc.auto_apply_store_updates(force=True)
    assert out["ok"] is True
    assert out["updated"] == ["openai"]
    assert calls == ["openai"]


def test_store_item_versions_strips_empty_and_keeps_changelog() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    with patch.object(
        acc,
        "_store_collect",
        return_value={
            "versions": [
                {"version": "1.1.14", "changelog": "Bot tools.", "created_at": "2026-09-01T00:00:00Z"},
                {"version": "", "changelog": "skip me"},
                "nope",
            ]
        },
    ):
        out = acc.store_item_versions("discord")
    assert out["ok"] is True
    assert out["slug"] == "discord"
    assert out["versions"] == [
        {"version": "1.1.14", "changelog": "Bot tools.", "created_at": "2026-09-01T00:00:00Z"},
    ]


def test_dispatch_desktop_rpc_allowlist() -> None:
    from frontend.duckyos_account import dispatch_desktop_rpc

    denied = dispatch_desktop_rpc("execute_python", {"code": "1"})
    assert denied["ok"] is False
    assert "not allowed" in str(denied.get("error") or "")


def test_dispatch_remote_release_clears_sessions() -> None:
    from unittest.mock import patch

    from frontend.duckyos_account import dispatch_desktop_rpc

    with patch("frontend.ui_web.panel_httpd.sign_out_all_remote") as drop:
        out = dispatch_desktop_rpc("remote_release", {})
    assert out == {"ok": True, "result": True}
    drop.assert_called_once_with()
    gone = dispatch_desktop_rpc("kick_all_remote", {})
    assert gone["ok"] is False


class _StubApi:
    def list_folders(self, parent_id: str = "") -> list[str]:
        return [parent_id or "root"]

    def send_message(self, conv_id: str, text: str, mode: str = "ask") -> dict[str, str]:
        return {"conv_id": conv_id, "text": text, "mode": mode}


def test_call_panel_method_maps_kwargs_and_positional() -> None:
    from frontend.duckyos_account import call_panel_method

    api = _StubApi()
    assert call_panel_method(api, "list_folders", {"parent_id": "a"}) == ["a"]
    assert call_panel_method(api, "list_folders", []) == ["root"]
    assert call_panel_method(api, "send_message", ["c1", "hi"])["text"] == "hi"


def test_remote_endpoint_shape_when_disabled() -> None:
    from frontend.duckyos_account import _remote_endpoint
    from frontend.settings import PanelSettings
    from unittest.mock import patch

    s = PanelSettings()
    s.remote_access = False
    with patch("frontend.settings.PanelSettings.load", return_value=s):
        out = _remote_endpoint()
    assert out == {"enabled": False}


def test_remote_endpoint_waits_until_tunnel_registers() -> None:
    from frontend.duckyos_account import _remote_endpoint
    from frontend.settings import PanelSettings
    from unittest.mock import patch

    s = PanelSettings()
    s.remote_access = True
    with (
        patch("frontend.settings.PanelSettings.load", return_value=s),
        patch("frontend.remote_tunnel.start_remote_tunnel"),
        patch(
            "frontend.remote_tunnel.remote_tunnel_status",
            return_value={
                "hostname": "u-abc.uefnducky.org",
                "mode": "named",
                "running": False,
                "error": "",
            },
        ),
    ):
        out = _remote_endpoint()
    assert out["starting"] is True
    assert out["login_url"] == ""


def test_remote_endpoint_starting_when_tunnel_has_no_host() -> None:
    from frontend.duckyos_account import _remote_endpoint
    from frontend.settings import PanelSettings
    from unittest.mock import patch

    s = PanelSettings()
    s.remote_access = True
    with (
        patch("frontend.settings.PanelSettings.load", return_value=s),
        patch("frontend.remote_tunnel.start_remote_tunnel"),
        patch(
            "frontend.remote_tunnel.remote_tunnel_status",
            return_value={"hostname": "", "mode": "starting", "error": ""},
        ),
    ):
        out = _remote_endpoint()
    assert out["enabled"] is True
    assert out["login_url"] == ""
    assert out["starting"] is True
    assert out["mode"] == "starting"


def test_remote_deny_covers_native_and_secret_paths() -> None:
    from frontend.duckyos_account import REMOTE_DENY

    assert "pick_project_path" in REMOTE_DENY
    assert "minimize_window" in REMOTE_DENY
    assert "set_window_bounds" in REMOTE_DENY
    assert "voice_create_realtime_token" not in REMOTE_DENY
    assert "get_mcp_config" in REMOTE_DENY
    assert "set_uefn_plugin_secret" in REMOTE_DENY
    assert "test_key" in REMOTE_DENY
    assert "rtc_signal" in REMOTE_DENY
    assert "window_input" in REMOTE_DENY
    assert "window_box" in REMOTE_DENY
    assert "snip_screen" in REMOTE_DENY
    assert "send_message" not in REMOTE_DENY
    assert "list_conversations" not in REMOTE_DENY
    assert "launch_uefn" not in REMOTE_DENY
    assert "launch_uefn_project" not in REMOTE_DENY
    assert "close_uefn" not in REMOTE_DENY
    assert "restart_uefn_project" not in REMOTE_DENY


def test_device_login_polls_until_token() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    calls: list[str] = []

    def _collect(plugin_id: str, event: str, body=None, **_kw):
        calls.append(event)
        if event == "desktop-device-start":
            return {"user_code": "ABCD-EFGH", "device_code": "a" * 32}
        if event == "desktop-device-poll":
            if calls.count("desktop-device-poll") < 2:
                return {"status": "pending"}
            return {"status": "approved", "token": "dky_v1_secret", "keyId": "k1", "email": "a@b.co"}
        raise AssertionError(event)

    with (
        patch.object(acc, "_plugin_collect", side_effect=_collect),
        patch.object(acc, "_save_blob"),
        patch.object(acc, "start_presence_heartbeat"),
        patch.object(acc, "start_rpc_waiter"),
        patch.object(acc, "fetch_agent_caps"),
        patch.object(acc, "publish_agent_catalog"),
        patch.object(acc, "_persist_base_url"),
        patch.object(
            acc,
            "get_status",
            return_value={"logged_in": True, "email": "a@b.co", "device_key_active": True},
        ),
        patch.object(acc, "resolve_base_url", return_value="https://uefnducky.org"),
        patch("time.sleep"),
        patch("frontend.remote_tunnel.start_remote_tunnel"),
    ):
        out = acc.start_browser_login("https://uefnducky.org", timeout_secs=30)
    assert out["ok"] is True
    assert "desktop-device-start" in calls
    assert calls.count("desktop-device-poll") >= 2
    assert "desktop-exchange" not in calls


def test_start_browser_login_reattaches_when_lock_held() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    acc._DEVICE_LOGIN.clear()
    acc._DEVICE_LOGIN["user_code"] = "4FQG-UYNX"
    assert acc._BROWSER_LOGIN_LOCK.acquire(blocking=False)
    try:
        with (
            patch.object(acc, "_persist_base_url"),
            patch.object(acc, "resolve_base_url", return_value="https://uefnducky.org"),
            patch.object(
                acc,
                "get_status",
                return_value={
                    "logged_in": False,
                    "user_code": "4FQG-UYNX",
                    "browser_pending": True,
                },
            ),
        ):
            out = acc.start_browser_login("https://uefnducky.org", timeout_secs=30)
        assert out["ok"] is True
        assert out["user_code"] == "4FQG-UYNX"
        assert out["browser_pending"] is True
    finally:
        acc._DEVICE_LOGIN.clear()
        acc._BROWSER_LOGIN_LOCK.release()


def test_plugin_collect_404_never_mentions_plugin() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    with patch.object(acc, "api_request", return_value=(404, {}, "")):
        try:
            acc._plugin_collect(
                "uefn-ducky",
                "desktop-device-start",
                {},
                unavailable_code="auth_unavailable",
                unavailable_msg="Desktop login plugin is not active on this tenant yet.",
                error_code="device_start_failed",
                allow_anonymous=True,
            )
        except acc.DuckyOSAccountError as exc:
            assert "plugin" not in exc.message.lower()
            assert "tenant" not in exc.message.lower()
            assert exc.code == "auth_unavailable"
        else:
            raise AssertionError("expected DuckyOSAccountError")


def test_plugin_collect_posts_v1_only() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    seen: list[str] = []

    def _req(method, path, body=None, **_kw):
        seen.append(path)
        return (200, {"payload": {"ok": True}}, "")

    with (
        patch.object(acc, "api_request", side_effect=_req),
        patch.object(acc, "_load_blob", return_value={"base_url": "https://uefnducky.org"}),
    ):
        acc._plugin_collect(
            "uefn-ducky",
            "desktop-device-start",
            {},
            unavailable_code="auth_unavailable",
            unavailable_msg="",
            error_code="device_start_failed",
            allow_anonymous=True,
        )
    assert seen == ["/api/v1/plugins/uefn-ducky/collect/desktop-device-start"]
    assert all("/api/v1/plugins/" in p for p in seen)
    assert not any(p.startswith("/api/plugins/") for p in seen)


def test_publish_device_presence_offline_when_remote_off() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc
    from frontend.settings import PanelSettings

    seen: list[dict] = []

    def _collect(_plugin_id: str, event: str, body=None, **_kw):
        seen.append({"event": event, "body": dict(body or {})})
        return {"ok": True}

    s = PanelSettings()
    s.remote_access = False
    with (
        patch.object(acc, "_load_blob", return_value={"device_key_id": "k1"}),
        patch.object(acc, "_plugin_collect", side_effect=_collect),
        patch("frontend.settings.PanelSettings.load", return_value=s),
    ):
        acc.publish_device_presence()
        acc.publish_device_presence(live=True)
        acc.publish_device_presence(live=False)
    assert seen[0] == {"event": "desktop-device-heartbeat", "body": {"keyId": "k1", "live": False}}
    assert seen[1] == {"event": "desktop-device-heartbeat", "body": {"keyId": "k1"}}
    assert seen[2]["body"]["live"] is False


def test_rpc_waiter_idle_while_remote_access_off() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    acc.stop_rpc_waiter()
    if acc._RPC_THREAD is not None:
        acc._RPC_THREAD.join(timeout=10)
    polls: list[int] = []
    with (
        patch.object(acc, "_load_blob", return_value={"device_key": "dky_v1_x"}),
        patch.object(acc, "_remote_access_on", return_value=False),
        patch.object(acc, "_poll_desktop_rpc_once", side_effect=lambda: polls.append(1)),
    ):
        acc.start_rpc_waiter()
        acc._RPC_STOP.wait(0.3)
        acc.stop_rpc_waiter()
        acc._RPC_THREAD.join(timeout=10)
    assert polls == []


def test_name_allowed_matches_filter() -> None:
    from frontend.duckyos_account import _role_has, name_allowed

    assert name_allowed("Classic")
    assert not name_allowed("shit crew")
    assert _role_has([], "owner", "manage_roles")
    assert not _role_has(
        [{"id": "lead", "perms": ["invite", "manage_roles"]}],
        "lead",
        "manage_roles",
    )
    assert _role_has([{"id": "lead", "perms": ["invite"]}], "lead", "invite")


def test_store_item_versions_needs_slug() -> None:
    from frontend.duckyos_account import store_item_versions

    out = store_item_versions("  ")
    assert out["ok"] is False
    assert out["versions"] == []


def test_set_agent_caps_stays_on_desktop() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    blob: dict = {}
    settings = SimpleNamespace(
        allow_settings_write=True,
        allow_agent_clicks=False,
        allow_see_uefn=True,
        allow_see_other_programs=True,
    )
    settings.save = lambda: None
    pushed: list = []

    def _save(data):
        blob.clear()
        blob.update(data)

    def _collect(plugin_id, event, body=None, **_kwargs):
        pushed.append((plugin_id, event, body or {}))
        return {"ok": True}

    catalog = {"categories": [{"id": "p", "label": "P", "tools": [{"name": "keep_me"}]}]}
    with (
        patch.object(acc, "_load_blob", lambda: dict(blob)),
        patch.object(acc, "_save_blob", _save),
        patch.object(acc, "_plugin_collect", _collect),
        patch("frontend.settings.PanelSettings.load", return_value=settings),
        patch("frontend.ui_web.mcp_catalog.build_caps_catalog", return_value=catalog),
    ):
        out = acc.set_agent_caps(
            ["deny_me"],
            {
                "allow_settings_write": False,
                "allow_agent_clicks": True,
                "allow_see_uefn": False,
                "allow_see_other_programs": False,
            },
        )
        assert out["denied"] == ["deny_me"]
        assert out["settings"]["allow_settings_write"] is False
        assert out["settings"]["allow_see_uefn"] is False
        assert out["settings"]["allow_see_other_programs"] is False
        assert "catalog" not in out
        assert settings.allow_settings_write is False
        assert settings.allow_agent_clicks is True
        assert settings.allow_see_uefn is False
        assert settings.allow_see_other_programs is False
        assert blob["agent_caps_local"] is True
        assert pushed == []
        blob["device_key"] = "k"
        acc.set_agent_caps(["deny_me"], {"allow_settings_write": False})
        assert pushed[-1][1] == "agent-caps-set"
        assert acc.fetch_agent_caps() is not None
        assert pushed[-1][1] == "agent-caps-set"

    assert acc.sanitize_denied_names(["ok_tool", "ok_tool", "", "bad name", 1]) == ["ok_tool"]
    assert acc.effective_allow_settings_write(True) is True
    assert acc.effective_allow_agent_clicks(False) is False


def test_tool_blocked_by_caps_see_toggles() -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    settings = SimpleNamespace(
        allow_settings_write=True,
        allow_agent_clicks=False,
        allow_see_uefn=False,
        allow_see_other_programs=False,
    )
    with patch("frontend.settings.PanelSettings.load", return_value=settings):
        assert acc.tool_blocked_by_caps("take_high_res_screenshot")
        assert acc.tool_blocked_by_caps("blender_get_viewport_screenshot")
        assert acc.tool_blocked_by_caps("unity_call")
        assert acc.tool_blocked_by_caps("workspace_read_file") is None
    settings.allow_see_uefn = True
    settings.allow_see_other_programs = True
    with patch("frontend.settings.PanelSettings.load", return_value=settings):
        assert acc.tool_blocked_by_caps("take_high_res_screenshot") is None
        assert acc.tool_blocked_by_caps("blender_status") is None


def test_list_account_pcs_marks_mine() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    def _collect(_plugin, event, body=None, **_kw):
        assert event == "desktop-devices"
        return {
            "devices": [
                {"keyId": "k1", "name": "UEFN Ducky on Win32", "live": False, "last_seen": 1},
                {"keyId": "k2", "name": "Other", "live": True, "last_seen": 2},
            ]
        }

    with (
        patch.object(acc, "_load_blob", return_value={"device_key_id": "k1"}),
        patch.object(acc, "_plugin_collect", side_effect=_collect),
    ):
        out = acc.list_account_pcs()
    assert out["ok"] is True
    assert out["devices"][0]["mine"] is True
    assert out["devices"][1]["mine"] is False


def test_revoke_account_pc_clears_this_device() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    blob = {"device_key": "tok", "device_key_id": "k1", "base_url": "https://uefnducky.org"}
    saved: list[dict] = []

    with (
        patch.object(acc, "_load_blob", return_value=blob),
        patch.object(acc, "_save_blob", side_effect=saved.append),
        patch.object(acc, "_plugin_collect", return_value={"ok": True}),
        patch.object(acc, "stop_presence_heartbeat"),
        patch.object(acc, "stop_rpc_waiter"),
        patch.object(acc, "get_status", return_value={"logged_in": True, "device_key_active": False}),
        patch("frontend.remote_tunnel.stop_remote_tunnel"),
        patch.object(acc, "publish_device_presence"),
    ):
        out = acc.revoke_account_pc("k1")
    assert out["ok"] is True
    assert "device_key" not in saved[-1]
    assert "device_key_id" not in saved[-1]


def test_session_route_401_keeps_the_paired_pc() -> None:
    """Profile and Store 401s want a website cookie. They must not unpair the PC."""
    import io
    import urllib.error
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    blob = {
        "device_key": "dky_v1_x",
        "device_key_id": "k1",
        "base_url": "https://uefnducky.org",
        "email": "a@b.co",
    }
    unpaired: list[str] = []

    def _401(url: str):
        return urllib.error.HTTPError(url, 401, "unauthorized", hdrs=None, fp=io.BytesIO(b"{}"))

    with (
        patch.object(acc, "_load_blob", return_value=dict(blob)),
        patch.object(acc, "_unpair_this_pc", side_effect=lambda: unpaired.append("unpair")),
        patch.object(acc, "_clear_expired_auth", side_effect=lambda: unpaired.append("clear")),
        patch.object(acc, "_drop_website_session"),
        patch("urllib.request.urlopen", side_effect=_401("https://uefnducky.org/api/v1/auth/me")),
    ):
        try:
            acc.api_request("GET", "/api/v1/auth/me", prefer_bearer=False)
            raised = None
        except acc.DuckyOSAccountError as exc:
            raised = exc
    assert raised is not None and raised.code == "session_required"
    assert unpaired == []

    with (
        patch.object(acc, "_load_blob", return_value=dict(blob)),
        patch.object(acc, "_unpair_this_pc", side_effect=lambda: unpaired.append("unpair")),
        patch("urllib.request.urlopen", side_effect=_401("https://uefnducky.org/api/v1/plugins/uefn-ducky/collect/desktop-devices")),
    ):
        try:
            acc.api_request("POST", "/api/v1/plugins/uefn-ducky/collect/desktop-devices", {})
            raised = None
        except acc.DuckyOSAccountError as exc:
            raised = exc
    assert raised is not None and raised.code == "device_unpaired"
    assert unpaired == ["unpair"]


def test_unpair_this_pc_keeps_session() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    blob = {
        "device_key": "tok",
        "device_key_id": "k1",
        "session_value": "sess",
        "email": "a@b.co",
        "base_url": "https://uefnducky.org",
    }
    saved: list[dict] = []

    with (
        patch.object(acc, "_load_blob", return_value=blob),
        patch.object(acc, "_save_blob", side_effect=saved.append),
        patch.object(acc, "stop_presence_heartbeat"),
        patch.object(acc, "stop_rpc_waiter"),
        patch("frontend.remote_tunnel.stop_remote_tunnel"),
        patch.object(acc, "publish_device_presence"),
    ):
        acc._unpair_this_pc()
    assert "device_key" not in saved[-1]
    assert "device_key_id" not in saved[-1]
    assert saved[-1].get("session_value") == "sess"
    assert saved[-1].get("email") == "a@b.co"


def test_revoke_device_key_also_collects() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    seen: list[str] = []

    def _collect(_plugin, event, body=None, **_kw):
        seen.append(event)
        return {"ok": True}

    blob = {"device_key_id": "k1", "device_key": "tok", "base_url": "https://uefnducky.org"}
    with patch.object(acc, "_plugin_collect", side_effect=_collect):
        acc._revoke_device_key(blob)
    assert seen == ["desktop-device-revoke"]


def test_ducky_ai_exists_only_for_an_account_holding_its_permission() -> None:
    """Owner rule 2026-10-08: without the Ducky AI permission the app shows no Ducky AI at
    all (gateway, model, meter, notice, saved default). The site refuses brain-status then."""
    import io
    import urllib.error
    from unittest.mock import Mock, patch

    from backend.agent import model_fetch
    from backend.agent.model_fetch import ModelInfo
    from backend.uefn_plugins import host
    from frontend import duckyos_account as acc
    from frontend.settings import PanelSettings
    from frontend.ui_web.panel_api import serialize_model_rows

    blob = {"device_key": "dky_v1_x", "device_key_id": "k1", "base_url": "https://uefnducky.org", "email": "a@b.co"}
    gateway = {"id": "uefn_ducky", "label": "UEFN Ducky", "kind": "secret", "plugin_id": "account"}
    other = {"id": "openai", "label": "OpenAI", "kind": "secret", "plugin_id": "openai"}
    meter = {"windows": [{"id": "period", "label": "This period", "used": 40, "limit": 100}]}
    reg = {"factory": object, "key_optional": True, "fetch_usage": lambda *_a, **_k: meter}

    class _Granted(io.BytesIO):
        status = 200

    def refused() -> urllib.error.HTTPError:
        body = io.BytesIO(b'{"error":"permission denied: uefn-ducky.ai","request_id":"r"}')
        return urllib.error.HTTPError(acc._BRAIN_STATUS, 400, "Bad Request", hdrs=None, fp=body)

    def surfaces() -> tuple:
        model_fetch.reset_usage_cache()
        rows = serialize_model_rows("uefn_ducky", [ModelInfo(id="ducky-brain", display_name="Ducky AI")])
        return (
            sorted(r["id"] for r in host.get_ui_contributions()["llm_providers"]),
            host.get_llm_provider_registration("uefn_ducky") is not None,
            [r["id"] for r in rows],
            model_fetch.fetch_usage("uefn_ducky", ""),
        )

    hidden = (["openai"], False, [], {"windows": []})
    shown = (["openai", "uefn_ducky"], True, ["ducky-brain"], meter)
    saved = PanelSettings.load()
    before = (saved.default_model, saved.agent_provider, saved.agent_model)
    saved.default_model, saved.agent_provider, saved.agent_model = "uefn_ducky:ducky-brain", "uefn_ducky", "ducky-brain"
    saved.save()
    changed = Mock()
    try:
        with (
            patch.object(acc, "_load_blob", return_value=dict(blob)),
            patch.object(acc, "_ducky_ai_changed", changed),
            patch.dict(acc._AI, {"who": "", "at": float("-inf"), "ok": False, "refused": False, "busy": False}),
            patch.dict(host._CONTRIBUTIONS, {"llm_providers": [gateway, other]}),
            patch.dict(host._LLM_PROVIDER_FACTORIES, {"uefn_ducky": reg}),
            patch("frontend.agent_models.provider_label", return_value="UEFN Ducky"),
        ):
            who = acc.account_key()
            # Refused: nothing shows, and the Ducky AI default the plugin saved is gone.
            with patch("urllib.request.urlopen", side_effect=refused()):
                acc._check_ducky_ai(who)
            assert surfaces() == hidden
            s = PanelSettings.load()
            assert (s.default_model, s.agent_provider, s.agent_model) == ("", "", "")
            assert changed.call_count == 1
            # A refusal stands: the site is not asked again (no polling).
            assert acc.ducky_ai_allowed() is False and acc._AI["busy"] is False
            # Granted (a 2xx answer): every surface shows.
            with patch("urllib.request.urlopen", return_value=_Granted(b'{"ok":true,"payload":{"subscribed":true}}')):
                acc._check_ducky_ai(who)
            assert surfaces() == shown and changed.call_count == 2
            # Offline or any error: hidden again (fail closed).
            with patch("urllib.request.urlopen", side_effect=OSError("offline")):
                acc._check_ducky_ai(who)
            assert surfaces() == hidden and changed.call_count == 3
            # No answer is asked again only after the retry wait, not on every look.
            assert acc.ducky_ai_allowed() is False and acc._AI["busy"] is False
            # Another account on this PC starts hidden and is asked about at once.
            with patch("urllib.request.urlopen", return_value=_Granted(b'{"ok":true}')):
                acc._check_ducky_ai(who)
            assert acc.ducky_ai_allowed() is True
            acc._load_blob.return_value = {**blob, "email": "b@b.co"}
            with patch.object(acc, "_check_ducky_ai"):
                assert acc.ducky_ai_allowed() is False and acc._AI["busy"] is True
            # A website sign-in carries the permission list: the site is never asked.
            with patch("urllib.request.urlopen", side_effect=AssertionError("no request")):
                acc._load_blob.return_value = {**blob, "session_value": "s", "permissions": ["uefn-ducky.brain"]}
                assert acc.ducky_ai_allowed() is True
                acc._load_blob.return_value = {**blob, "session_value": "s", "permissions": ["uefn-ducky.app"]}
                assert acc.ducky_ai_allowed() is False
            # Signed out: hidden, and no request goes out.
            acc._load_blob.return_value = {}
            with patch("urllib.request.urlopen", side_effect=AssertionError("no request")):
                assert surfaces() == hidden
    finally:
        s = PanelSettings.load()
        s.default_model, s.agent_provider, s.agent_model = before
        s.save()


if __name__ == "__main__":
    test_pkce_pair_s256()
    test_device_login_polls_until_token()
    test_auto_apply_store_updates_skips_local_and_unpaid()
    test_store_item_versions_strips_empty_and_keeps_changelog()
    test_store_item_versions_needs_slug()
    test_set_agent_caps_stays_on_desktop()
    test_tool_blocked_by_caps_see_toggles()
    test_name_allowed_matches_filter()
    test_dispatch_desktop_rpc_allowlist()
    test_dispatch_remote_release_clears_sessions()
    test_call_panel_method_maps_kwargs_and_positional()
    test_remote_endpoint_shape_when_disabled()
    test_remote_endpoint_starting_when_tunnel_has_no_host()
    test_remote_endpoint_waits_until_tunnel_registers()
    test_remote_deny_covers_native_and_secret_paths()
    test_publish_device_presence_offline_when_remote_off()
    test_plugin_collect_posts_v1_only()
    test_list_account_pcs_marks_mine()
    test_revoke_account_pc_clears_this_device()
    test_session_route_401_keeps_the_paired_pc()
    test_unpair_this_pc_keeps_session()
    test_revoke_device_key_also_collects()
    print("ok")
