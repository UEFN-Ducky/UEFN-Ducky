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


def test_auto_apply_store_updates_skips_local_and_unpaid() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    catalog = {
        "ok": True,
        "items": [
            {"slug": "openai", "kind": "plugin", "state": "update", "source": "store"},
            {"slug": "mine", "kind": "plugin", "state": "update", "source": "local"},
            {
                "slug": "paid-pack",
                "kind": "plugin",
                "state": "update",
                "source": "store",
                "paid": True,
                "owned": False,
            },
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
    assert "voice_create_realtime_token" in REMOTE_DENY
    assert "get_mcp_config" in REMOTE_DENY
    assert "set_uefn_plugin_secret" in REMOTE_DENY
    assert "test_key" in REMOTE_DENY
    assert "send_message" not in REMOTE_DENY
    assert "list_conversations" not in REMOTE_DENY


def test_store_item_versions_needs_slug() -> None:
    from frontend.duckyos_account import store_item_versions

    out = store_item_versions("  ")
    assert out["ok"] is False
    assert out["versions"] == []


if __name__ == "__main__":
    test_pkce_pair_s256()
    test_auto_apply_store_updates_skips_local_and_unpaid()
    test_store_item_versions_strips_empty_and_keeps_changelog()
    test_store_item_versions_needs_slug()
    test_dispatch_desktop_rpc_allowlist()
    test_call_panel_method_maps_kwargs_and_positional()
    test_remote_endpoint_shape_when_disabled()
    test_remote_endpoint_starting_when_tunnel_has_no_host()
    test_remote_deny_covers_native_and_secret_paths()
    print("ok")
