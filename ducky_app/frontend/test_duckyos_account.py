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
    with (
        patch.dict(sys.modules, {
            "frontend.ui_web.panel_httpd": SimpleNamespace(kick_all_remote=kick),
        }),
        patch.object(acc, "_load_blob", return_value=blob),
        patch.object(acc, "stop_presence_heartbeat"),
        patch.object(acc, "stop_rpc_waiter"),
        patch.object(acc, "_revoke_device_key", side_effect=lambda row: events.append("revoke")) as revoke,
        patch.object(acc, "_clear_blob", side_effect=lambda: events.append("clear")),
        patch.object(acc, "get_status", return_value={"logged_in": False}),
    ):
        assert acc.logout() == {"logged_in": False}
    revoke.assert_called_once_with(blob)
    assert events == ["sessions", "revoke", "clear"]


def test_logout_clears_account_even_if_browser_cleanup_fails() -> None:
    import sys
    from types import SimpleNamespace
    from unittest.mock import Mock, patch

    from frontend import duckyos_account as acc

    with (
        patch.dict(sys.modules, {
            "frontend.ui_web.panel_httpd": SimpleNamespace(kick_all_remote=Mock(side_effect=RuntimeError)),
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
    assert "capture_list_shield" in REMOTE_DENY
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


def _sign_in_start(answers: list):
    """Run desktop-device-start (retries=2) against canned api_request answers; no sleeps."""
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    calls: list[int] = []

    def _req(*_a, **_k):
        calls.append(1)
        nxt = answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    with (
        patch.object(acc, "api_request", side_effect=_req),
        patch.object(acc.time, "sleep", lambda _s: None),
        patch.object(acc, "_note_site_failure", lambda *_a: None),
    ):
        try:
            out = acc._plugin_collect(
                "uefn-ducky",
                "desktop-device-start",
                {},
                unavailable_code="auth_unavailable",
                unavailable_msg="",
                error_code="device_start_failed",
                allow_anonymous=True,
                retries=2,
            )
        except acc.DuckyOSAccountError as exc:
            return len(calls), exc
    return len(calls), out


def test_sign_in_retries_a_cloudflare_challenge_then_names_it() -> None:
    from frontend.net_access import Answer

    page = "<!DOCTYPE html><html><title>Just a moment...</title></html>"
    blocked = Answer((403, None, page), {"cf-mitigated": "challenge", "server": "cloudflare"})
    n, exc = _sign_in_start([blocked, blocked, blocked])
    assert n == 3
    assert "firewall blocked this PC (HTTP 403)" in exc.message
    assert exc.code == "device_start_failed"


def test_sign_in_names_a_site_outage_after_retries() -> None:
    n, exc = _sign_in_start([(502, None, "Bad gateway")] * 3)
    assert n == 3
    assert "having trouble (HTTP 502)" in exc.message


def test_sign_in_404_then_200_succeeds() -> None:
    n, out = _sign_in_start([(404, None, ""), (200, {"payload": {"user_code": "AB"}}, "")])
    assert n == 2
    assert out == {"user_code": "AB"}


def test_sign_in_html_200_is_not_a_success() -> None:
    n, exc = _sign_in_start([(200, None, "<html>wifi login</html>")] * 3)
    assert n == 3
    assert "web page instead of an answer" in exc.message


def test_sign_in_timeout_is_named() -> None:
    from frontend import duckyos_account as acc

    t = acc.DuckyOSAccountError("Network error: timed out", code="network")
    n, exc = _sign_in_start([t, t, t])
    assert n == 3
    assert "didn't answer in time" in exc.message


def test_sign_in_json_refusal_is_not_retried() -> None:
    n, exc = _sign_in_start([(400, {"error": "Too many codes"}, "")])
    assert n == 1
    assert exc.message == "Too many codes"


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


def test_unchanged_presence_waits_only_where_the_site_allows() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    posts: list[str] = []
    live = {"value": None}

    def _request(_method: str, path: str, _body=None, **_kw):
        posts.append(path)
        payload = {"ok": True} if live["value"] is None else {"liveSeconds": live["value"]}
        return 200, {"payload": payload}, b""

    def _collect(*_a, **_kw):
        return {} if live["value"] is None else {"liveSeconds": live["value"]}

    acc._PRESENCE_LIVE_S.clear()
    acc._PRESENCE_LAST.update(fingerprint=None, at=0.0)
    with (
        patch.object(acc, "_load_blob", return_value={"device_key": "dk", "device_key_id": "k1", "email": "a@b.c"}),
        patch.object(acc, "api_request", side_effect=_request),
        patch.object(acc, "_plugin_collect", side_effect=_collect),
        patch.object(acc, "_presence_project_label", return_value="Island"),
        patch.object(acc, "_presence_uefn_online", return_value=True),
        patch.object(acc, "_remote_access_on", return_value=True),
    ):
        # Older plugins say nothing: every 90 s beat still reports.
        acc.send_presence_heartbeat(only_if_changed=True)
        assert acc._presence_quiet_s() == acc._PRESENCE_INTERVAL_S
        # The next beat meets plugins that count a PC online for 330 s: an unchanged
        # report waits one beat, under a third of that (it waited 300 s, past 330 s at the next wake).
        live["value"] = 330
        acc._PRESENCE_LAST["at"] -= 100.0
        acc.send_presence_heartbeat(only_if_changed=True)
        assert acc._presence_quiet_s() < 330 / 3
        sent = len(posts)
        assert acc.send_presence_heartbeat(only_if_changed=True) is True
        assert len(posts) == sent, "unchanged and still online: skipped"
        # A change goes out at once.
        with patch.object(acc, "_presence_project_label", return_value="Other island"):
            acc.send_presence_heartbeat(only_if_changed=True)
        assert len(posts) == sent + 1
    acc._PRESENCE_LIVE_S.clear()
    acc._PRESENCE_LAST.update(fingerprint=None, at=0.0)


def test_ten_minutes_of_beats_with_failures_never_go_past_the_site_window() -> None:
    """The real report path on a fake clock: 1 in 4 POSTs fail, the site counts 330 s."""
    import random
    from unittest.mock import patch

    from frontend import duckyos_account as acc
    from frontend.ui_web.remote_link.presence import PresenceBeat

    clock = {"t": 1000.0}
    seen: list[float] = []
    rng = random.Random(11)

    def _request(_method: str, _path: str, _body=None, **_kw):
        clock["t"] += 0.5
        if rng.random() < 0.25:
            raise acc.DuckyOSAccountError("Can't connect right now.", code="x")
        seen.append(clock["t"])
        return 200, {"payload": {"liveSeconds": 330}}, b""

    acc._PRESENCE_LIVE_S.clear()
    acc._PRESENCE_LAST.update(fingerprint=None, at=0.0)
    with (
        patch.object(acc.time, "monotonic", side_effect=lambda: clock["t"]),
        patch.object(acc, "_load_blob", return_value={"device_key": "dk", "device_key_id": "k1", "email": "a@b.c"}),
        patch.object(acc, "api_request", side_effect=_request),
        patch.object(acc, "_plugin_collect", return_value={"liveSeconds": 330}),
        patch.object(acc, "_presence_project_label", return_value="Island"),
        patch.object(acc, "_presence_uefn_online", return_value=True),
        patch.object(acc, "_remote_access_on", return_value=True),
    ):
        beat = PresenceBeat(lambda: acc.send_presence_heartbeat(only_if_changed=True), acc._presence_live_s,
                            clock=lambda: clock["t"])
        while clock["t"] < 1600.0:
            clock["t"] += beat.beat_once()
    acc._PRESENCE_LIVE_S.clear()
    acc._PRESENCE_LAST.update(fingerprint=None, at=0.0)
    gaps = [b - a for a, b in zip(seen, seen[1:])]
    assert len(seen) >= 5 and max(gaps) < 330, gaps


def test_a_slow_mailbox_job_never_holds_the_next_command() -> None:
    import threading
    import time as _time
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    queue = [{"pending": True, "id": "slow1", "method": "rtc_connect"},
             {"pending": True, "id": "fast1", "method": "list_folders"}]
    posted: dict[str, float] = {}
    gate = threading.Event()

    def collect(_plugin: str, action: str, body, **_kw):
        if action == "desktop-rpc-wait":
            return queue.pop(0) if queue else {"pending": False}
        posted[body["id"]] = _time.monotonic()
        return {}

    def dispatch(method: str, _args):
        if method == "rtc_connect":
            gate.wait(5.0)
        return {"ok": True, "result": method}

    t0 = _time.monotonic()
    with patch.object(acc, "_plugin_collect", side_effect=collect), patch.object(acc, "dispatch_desktop_rpc", side_effect=dispatch):
        while acc._poll_desktop_rpc_once():
            pass
        for _ in range(100):
            if "fast1" in posted:
                break
            _time.sleep(0.01)
        assert "fast1" in posted and posted["fast1"] - t0 < 1.0
        assert "slow1" not in posted
        gate.set()
        for _ in range(200):
            if "slow1" in posted:
                break
            _time.sleep(0.01)
    assert "slow1" in posted


def test_mailbox_backoff_never_passes_10_s() -> None:
    from frontend.ui_web.remote_link import mailbox

    b = mailbox.BACKOFF_MIN_S
    for _ in range(10):
        b = mailbox.next_backoff(b)
    assert b == 10.0


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


def test_mailbox_is_read_at_start_and_then_only_when_the_channel_tells() -> None:
    """The PC asked the site's mailbox every second all day; now it waits on a channel."""
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    acc.stop_rpc_waiter()
    if acc._RPC_THREAD is not None:
        acc._RPC_THREAD.join(timeout=10)
    events: list[str] = []
    told = iter([(False, 4), (True, 5), (False, 5)])
    cursors: list[int | None] = []

    def wait(cursor: int | None) -> tuple[bool, int | None]:
        cursors.append(cursor)
        try:
            answer = next(told)
        except StopIteration:
            acc._RPC_STOP.set()
            return False, cursor
        events.append("told" if answer[0] else "quiet")
        return answer

    asks = iter([False, True, False])

    def poll() -> bool:
        events.append("ask")
        return next(asks, False)

    with (
        patch.object(acc, "_load_blob", return_value={"device_key": "dky_v1_x"}),
        patch.object(acc, "_remote_access_on", return_value=True),
        patch.object(acc, "wait_desktop_rpc", side_effect=wait),
        patch.object(acc, "_poll_desktop_rpc_once", side_effect=poll),
    ):
        acc.start_rpc_waiter()
        acc._RPC_THREAD.join(timeout=10)
    acc.stop_rpc_waiter()
    assert not acc._RPC_THREAD.is_alive()
    # One ask at start (a command sent before the wait began), none while quiet,
    # and when told it answers every pending command, then waits again.
    assert events == ["ask", "quiet", "told", "ask", "ask", "quiet"]
    assert cursors[:4] == [None, 4, 5, 5], "each wait passes back the cursor"


def test_waiting_on_the_channel_sends_the_cursor_and_reads_the_answer() -> None:
    from unittest.mock import patch

    import pytest

    from frontend import duckyos_account as acc

    seen: list[str] = []

    def answer(status: int, body: dict | None):
        def request(method: str, path: str, *_a: object, **_k: object):
            seen.append(f"{method} {path}")
            return status, body, ""

        return request

    with patch.object(acc, "api_request", side_effect=answer(200, {"messages": [{"id": 7}], "cursor": 7})):
        assert acc.wait_desktop_rpc(3) == (True, 7)
    with patch.object(acc, "api_request", side_effect=answer(200, {"messages": [], "cursor": 3})):
        assert acc.wait_desktop_rpc(None) == (False, 3)
    assert seen == [
        "GET /api/v1/plugins/uefn-ducky/channels/desktop-rpc?after=3",
        "GET /api/v1/plugins/uefn-ducky/channels/desktop-rpc",
    ]
    with patch.object(acc, "api_request", side_effect=answer(503, None)):
        with pytest.raises(acc.DuckyOSAccountError):
            acc.wait_desktop_rpc(3)


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
        patch.object(acc, "_site_urlopen", side_effect=_401("https://uefnducky.org/api/v1/auth/me")),
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
        patch.object(acc, "_site_urlopen", side_effect=_401("https://uefnducky.org/api/v1/plugins/uefn-ducky/collect/desktop-devices")),
    ):
        try:
            acc.api_request("POST", "/api/v1/plugins/uefn-ducky/collect/desktop-devices", {})
            raised = None
        except acc.DuckyOSAccountError as exc:
            raised = exc
    assert raised is not None and raised.code == "device_unpaired"
    assert unpaired == ["unpair"]


def test_a_body_cut_off_mid_read_is_a_network_error_not_an_escape() -> None:
    """http.client errors (IncompleteRead, a bad status line) aren't OSErrors: they used to
    escape api_request and kill a team sync round instead of reading as offline."""
    import http.client
    from unittest.mock import MagicMock, patch

    from frontend import duckyos_account as acc

    blob = {"device_key": "dky_v1_x", "device_key_id": "k1", "base_url": "https://uefnducky.org", "email": "a@b.co"}
    cut = MagicMock()
    cut.__enter__.return_value = cut
    cut.read.side_effect = http.client.IncompleteRead(b'{"changes": [', 4096)
    for opened in ({"return_value": cut}, {"side_effect": http.client.BadStatusLine("HTTP/1.1 ???")}):
        with patch.object(acc, "_load_blob", return_value=dict(blob)), patch.object(acc, "_site_urlopen", **opened):
            try:
                acc.api_request("POST", "/api/v1/plugins/uefn-ducky-store/collect/team-data-sync", {"teamId": "t"})
                raised = None
            except acc.DuckyOSAccountError as exc:
                raised = exc
        assert raised is not None and raised.code == "network"


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


def test_a_gateway_its_plugin_hides_is_hidden_everywhere() -> None:
    """A plugin's gateway (here an account plugin's hosted model) shows on no surface while
    its plugin's ``visible`` option says no: gateway list, registration, models, meter."""
    from unittest.mock import patch

    from backend.agent import model_fetch
    from backend.agent.model_fetch import ModelInfo
    from backend.uefn_plugins.plugin_host import gateways, state, ui_registry
    from frontend.ui_web.panel_api import serialize_model_rows

    gateway = {"id": "hosted", "label": "Hosted", "kind": "secret", "plugin_id": "account"}
    other = {"id": "openai", "label": "OpenAI", "kind": "secret", "plugin_id": "openai"}
    meter = {"windows": [{"id": "period", "label": "This period", "used": 40, "limit": 100}]}
    allowed = {"on": False}
    reg = {"factory": object, "key_optional": True, "fetch_usage": lambda *_a, **_k: meter,
           "visible": lambda: allowed["on"], "billing": "account"}

    def surfaces() -> tuple:
        model_fetch.reset_usage_cache()
        rows = serialize_model_rows("hosted", [ModelInfo(id="hosted-1", display_name="Hosted AI")])
        listed = ui_registry.get_ui_contributions()["llm_providers"]
        return (
            sorted(r["id"] for r in listed),
            gateways.get_llm_provider_registration("hosted") is not None,
            [r["id"] for r in rows],
            model_fetch.fetch_usage("hosted", ""),
            [r.get("billing") for r in listed if r["id"] == "hosted"],
        )

    with (
        patch.dict(state._CONTRIBUTIONS, {"llm_providers": [gateway, other]}),
        patch.dict(state._LLM_PROVIDER_FACTORIES, {"hosted": reg}),
        patch("frontend.agent_models.provider_label", return_value="Hosted"),
    ):
        assert surfaces() == (["openai"], False, [], {"windows": []}, [])
        allowed["on"] = True
        assert surfaces() == (["hosted", "openai"], True, ["hosted-1"], meter, ["account"])


def test_plugins_hear_every_sign_in_sign_out_and_refresh() -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    heard: list[tuple[str, str]] = []
    acc.add_account_listener(lambda before, after: heard.append((before, after)))
    blob = {"device_key": "dky_v1_x", "base_url": "https://uefnducky.org", "email": "a@b.co"}
    try:
        acc._note_login_change({}, blob)
        with patch.object(acc, "_load_blob", return_value=dict(blob)):
            acc.refresh_status()
        acc._note_login_change(blob, {})
    finally:
        acc._ACCOUNT_LISTENERS.clear()
    who = acc.account_key(blob)
    assert heard == [("", who), (who, who), (who, "")]


def test_mailbox_polls_reuse_one_connection() -> None:
    """Remote access asks the site's mailbox every ~2 s all day. Each ask used to
    open a new TCP + TLS connection (about 1,500 handshakes an hour)."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    connections: list[int] = []
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def setup(self) -> None:
            super().setup()
            connections.append(1)

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            paths.append(self.path)
            body = b'{"ok":true,"payload":{"pending":false}}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    blob = {"base_url": f"http://127.0.0.1:{server.server_port}", "device_key": "dky_v1_x"}
    try:
        with patch.object(acc, "_load_blob", return_value=dict(blob)):
            results = [acc._poll_desktop_rpc_once() for _ in range(30)]
    finally:
        server.shutdown()
        server.server_close()
    assert results == [False] * 30
    assert len(paths) == 30 and all(p.endswith("/collect/desktop-rpc-wait") for p in paths)
    assert len(connections) == 1, f"{len(connections)} connections for 30 polls"


def test_a_connection_the_site_closed_is_replaced_not_an_error() -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = b'{"ok":true,"payload":{"pending":false}}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True  # hang up without saying so

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    blob = {"base_url": f"http://127.0.0.1:{server.server_port}", "device_key": "dky_v1_x"}
    try:
        with patch.object(acc, "_load_blob", return_value=dict(blob)):
            assert [acc._poll_desktop_rpc_once() for _ in range(5)] == [False] * 5
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    test_pkce_pair_s256()
    test_device_login_polls_until_token()
    test_store_item_versions_strips_empty_and_keeps_changelog()
    test_store_item_versions_needs_slug()
    test_set_agent_caps_stays_on_desktop()
    test_tool_blocked_by_caps_see_toggles()
    test_dispatch_desktop_rpc_allowlist()
    test_call_panel_method_maps_kwargs_and_positional()
    test_remote_deny_covers_native_and_secret_paths()
    test_publish_device_presence_offline_when_remote_off()
    test_unchanged_presence_waits_only_where_the_site_allows()
    test_plugin_collect_posts_v1_only()
    test_list_account_pcs_marks_mine()
    test_revoke_account_pc_clears_this_device()
    test_session_route_401_keeps_the_paired_pc()
    test_unpair_this_pc_keeps_session()
    test_revoke_device_key_also_collects()
    print("ok")


def test_a_refused_download_shows_the_stores_reason() -> None:
    import pytest

    from frontend.duckyos_account import DuckyOSAccountError, store_zip_bytes

    reason = "Ducky Account 1.0.50 needs UEFN Ducky 1.2.354 or newer. Update UEFN Ducky, then install it."
    with pytest.raises(DuckyOSAccountError) as refused:
        store_zip_bytes({"error": reason})
    assert refused.value.message == reason and refused.value.code == "store_refused"
    with pytest.raises(DuckyOSAccountError) as empty:
        store_zip_bytes({})
    assert empty.value.code == "store_empty"


def test_store_update_retry_loop_runs_early_then_periodically() -> None:
    from frontend import duckyos_account as acc

    waits: list[float] = []
    runs: list[int] = []

    def wait(seconds: float) -> bool:
        waits.append(seconds)
        return len(waits) >= 6  # the app quits on the sixth wait

    passes = acc._store_update_retry_loop(wait, lambda: runs.append(1), delays=(1.0, 2.0, 3.0), period=9.0)
    assert waits == [1.0, 2.0, 3.0, 9.0, 9.0, 9.0]
    assert len(runs) == passes == 5  # three early passes, then two periodic ones


def test_store_update_retries_start_once(monkeypatch) -> None:
    import threading

    from frontend import duckyos_account as acc

    started: list[str] = []

    class FakeThread:
        def __init__(self, *a, **kw):
            started.append(kw.get("name", ""))

        def start(self):
            pass

    monkeypatch.setattr(acc, "_retry_thread_started", False)
    monkeypatch.setattr(threading, "Thread", FakeThread)
    acc.start_store_update_retries()
    acc.start_store_update_retries()
    assert started == ["store-update-retries"]


def test_the_store_pass_moves_a_pc_with_old_uefn_parts_onto_uefn() -> None:
    from unittest.mock import patch

    from backend.uefn_plugins import store
    from frontend import duckyos_account as acc

    store.plugin_dir("materials").mkdir(parents=True)  # an old UEFN part, and no UEFN
    calls: list[tuple[str, dict]] = []

    def run(items: list) -> dict:
        with (
            patch.object(acc, "store_catalog", return_value={"ok": True, "items": items}),
            patch.object(acc, "store_download_and_install", side_effect=lambda s, **kw: calls.append((s, kw)) or {"ok": True}),
        ):
            return acc.auto_apply_store_updates(force=True)

    # No UEFN yet: the pass installs it, allowed to take the parts' place (they go once it lands).
    assert run([])["updated"] == ["uefn"]
    assert calls[-1] == ("uefn", {"replace": True, "is_update": False, "replace_old": True})
    # UEFN installed: an update to it may replace the parts too; nobody has to click anything.
    store.plugin_dir("uefn").mkdir(parents=True)
    (store.plugin_dir("uefn") / "plugin.json").write_text('{"id": "uefn", "version": "1.0.53"}', encoding="utf-8")
    update = {"slug": "uefn", "kind": "plugin", "state": "update", "source": "store", "latest_version": "1.1.1"}
    assert run([update])["updated"] == ["uefn"] and calls[-1][1]["replace_old"] is True


def test_the_store_pass_puts_back_a_plugin_whose_install_stopped_halfway() -> None:
    from unittest.mock import patch

    from backend.uefn_plugins import store
    from frontend import duckyos_account as acc

    calls: list[tuple[str, dict]] = []
    available = {"slug": "uefn", "kind": "plugin", "state": "available", "latest_version": "1.1.1"}

    def run(enabled: list[str]) -> dict:
        with (
            patch.object(acc, "store_catalog", return_value={"ok": True, "items": [available]}),
            patch.object(acc, "store_download_and_install", side_effect=lambda s, **kw: calls.append((s, kw)) or {"ok": True}),
            patch.object(store, "get_enabled_plugin_ids", return_value=enabled),
        ):
            return acc.auto_apply_store_updates(force=True)

    # Still on, but all that's left is the old .pyd an install died around (or nothing, once
    # the launch sweep took that): the pass puts it back.
    store.plugin_dir("uefn").mkdir(parents=True)
    (store.plugin_dir("uefn") / "uefn_plugin_uefn.v1000053.cp313-win_amd64.pyd").write_bytes(b"")
    assert run(["uefn"])["updated"] == ["uefn"]
    assert calls[-1] == ("uefn", {"replace": True, "is_update": True, "replace_old": True})
    # Uninstalled (an uninstall turns it off first): left alone.
    calls.clear()
    assert run([])["updated"] == [] and calls == []


def test_auto_apply_records_an_activity_line(monkeypatch) -> None:
    from unittest.mock import patch

    from frontend import duckyos_account as acc

    lines: list[tuple[str, str]] = []
    catalog = {"ok": True, "items": [
        {"slug": "openai", "kind": "plugin", "state": "update", "source": "store"},
        {"slug": "broken", "kind": "plugin", "state": "update", "source": "store"},
    ]}

    def install(slug, **_kw):
        return {"ok": slug == "openai", "error": None if slug == "openai" else "zip missing"}

    with (
        patch.object(acc, "store_catalog", return_value=catalog),
        patch.object(acc, "store_download_and_install", side_effect=install),
        patch("frontend.error_log.record_activity", side_effect=lambda s, m: lines.append((s, m))),
    ):
        out = acc.auto_apply_store_updates(force=True)
    assert out["updated"] == ["openai"]
    assert lines and lines[0][0] == "store"
    assert "updated openai" in lines[0][1] and "broken: zip missing" in lines[0][1]


def test_a_test_bed_cache_keeps_linked_store_zips_by_hash(tmp_path, monkeypatch) -> None:
    """DUCKY_STORE_CACHE (test beds only): a second fresh profile skips the download,
    and a cached file that doesn't match still fails the hash check."""
    import hashlib

    import pytest

    from frontend import duckyos_account as acc

    cache = tmp_path / "zips"
    raw = b"PK\x03\x04 plugin bytes"
    sha = hashlib.sha256(raw).hexdigest()
    payload = {"url": "https://example.test/p.zip", "size": len(raw), "sha256": sha}
    fetched: list[str] = []
    monkeypatch.setattr(acc, "_fetch_store_zip_url", lambda url, size: fetched.append(url) or raw)

    monkeypatch.delenv("DUCKY_STORE_CACHE", raising=False)
    assert acc.store_zip_bytes(payload) == raw and not cache.exists()  # off by default

    monkeypatch.setenv("DUCKY_STORE_CACHE", str(cache))
    assert acc.store_zip_bytes(payload) == raw
    assert acc.store_zip_bytes(payload) == raw
    assert len(fetched) == 2  # once uncached, once to fill the cache, never again
    (cache / f"{sha}.zip").write_bytes(b"tampered!!!!!!!!!!!!!")
    with pytest.raises(acc.DuckyOSAccountError):
        acc.store_zip_bytes(payload)


def test_the_catalog_gets_a_minute_and_a_test_bed_keeps_it(tmp_path, monkeypatch) -> None:
    """The catalog carries every icon inline: a slow link needs more than 20 s (a new install
    showed no icons). A test bed (DUCKY_STORE_CACHE) asks the Store once per hour."""
    from frontend import duckyos_account as acc

    calls: list[float] = []

    def fake_collect(event, body, *, allow_anonymous=False, timeout=0.0):
        assert event == "catalog"
        calls.append(timeout)
        return {"ok": True, "items": [{"slug": "openai", "icon_data_url": "data:image/png;base64,AA"}]}

    monkeypatch.setattr(acc, "_store_collect", fake_collect)
    monkeypatch.delenv("DUCKY_STORE_CACHE", raising=False)
    acc._store_catalog_payload()
    acc._store_catalog_payload()
    assert calls == [60.0, 60.0]  # a real install always asks

    monkeypatch.setenv("DUCKY_STORE_CACHE", str(tmp_path / "store"))
    first = acc._store_catalog_payload()
    again = acc._store_catalog_payload()
    assert len(calls) == 3 and again == first and again["items"][0]["slug"] == "openai"


def test_store_zips_download_even_when_the_certificate_cant_be_verified(monkeypatch) -> None:
    from frontend import duckyos_account as acc
    from frontend import update_network

    seen: dict = {}

    def fake(url, out, **kwargs):
        seen.update(kwargs)
        out.write(b"zip")

    monkeypatch.setattr(update_network, "download_resumable", fake)
    assert acc._fetch_store_zip_url("https://bucket.example/p.zip", 3) == b"zip"
    assert seen["content_checked"] is True  # store_zip_bytes checks the Store's sha256
