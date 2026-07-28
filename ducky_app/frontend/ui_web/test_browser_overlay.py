"""Self-check: browser overlay URL policy + bounds scale guard."""

from __future__ import annotations

from unittest.mock import patch


def test_normalize_url() -> None:
    from frontend.ui_web.browser_overlay import normalize_url

    assert normalize_url("") == ""
    assert normalize_url("  ") == ""
    assert normalize_url("https://duckduckgo.com") == "https://duckduckgo.com"
    assert normalize_url("http://example.com/path") == "http://example.com/path"
    assert normalize_url("about:blank") == "about:blank"
    assert normalize_url("duckduckgo.com") == "https://duckduckgo.com"
    assert normalize_url("not a url") == ""
    assert normalize_url("no dots here") == ""


def test_is_app_ui_url_panel_port() -> None:
    from frontend.ui_web.browser_overlay import is_app_ui_url
    from frontend.ui_web.panel_httpd import PANEL_UI_HTTP_PORT

    assert is_app_ui_url(f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/")
    assert is_app_ui_url(f"http://localhost:{PANEL_UI_HTTP_PORT}/plugin-ui/browser/")
    assert is_app_ui_url("http://127.0.0.1:5173/")
    assert is_app_ui_url("http://localhost:5173/vite/")
    assert not is_app_ui_url("https://duckduckgo.com")
    assert not is_app_ui_url("http://example.com:5173/")


def test_is_app_ui_url_fallback_port() -> None:
    from frontend.ui_web import browser_overlay as bo

    with patch.object(bo, "urlparse", side_effect=RuntimeError("boom")):
        assert not bo.is_app_ui_url("http://127.0.0.1:1/")


def test_sane_scale_rejects_bad_values() -> None:
    from frontend.ui_web.browser_overlay import sane_scale

    assert sane_scale(1.0, 1.0) == (1.0, 1.0)
    assert sane_scale(0.5, 0.5) == (0.5, 0.5)
    assert sane_scale(2.0, 2.0) == (2.0, 2.0)
    assert sane_scale(0.1, 0.1) == (1.0, 1.0)
    assert sane_scale(5.0, 5.0) == (1.0, 1.0)
    assert sane_scale(1.0, 2.0) == (1.0, 1.0)


def test_is_allowed_url_blocks_app_ui() -> None:
    from frontend.ui_web.browser_overlay import _is_allowed_url
    from frontend.ui_web.panel_httpd import PANEL_UI_HTTP_PORT

    assert _is_allowed_url("https://duckduckgo.com")
    assert _is_allowed_url("http://example.com")
    assert _is_allowed_url("about:blank")
    assert not _is_allowed_url(f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/")
    assert not _is_allowed_url("http://localhost:5173/")
    assert not _is_allowed_url("file:///etc/passwd")
    assert not _is_allowed_url("javascript:alert(1)")


def test_await_cookie_task_never_blocks_on_hang() -> None:
    """Padlock used to GetResult() on the UI thread and deadlocked → bridge timeout."""
    import time

    from frontend.ui_web.browser_overlay import _await_cookie_task

    class _HangTask:
        @property
        def Result(self) -> None:
            time.sleep(10)
            return None

    t0 = time.perf_counter()
    rows, err = _await_cookie_task(_HangTask(), timeout_s=0.2)
    elapsed = time.perf_counter() - t0
    assert rows == []
    assert "timed out" in err
    assert elapsed < 2.0


def test_cookie_rows_skips_bad_entries() -> None:
    from frontend.ui_web.browser_overlay import _cookie_rows

    class _C:
        def __init__(self, name: str, domain: str = ".example.com") -> None:
            self.Name = name
            self.Domain = domain
            self.Path = "/"
            self.IsSecure = True
            self.IsHttpOnly = False
            self.IsSession = True

    class _Bad:
        @property
        def Name(self) -> str:
            raise RuntimeError("boom")

    rows = _cookie_rows([_C("a"), _Bad(), _C("b", ".x.test")])
    assert [r["name"] for r in rows] == ["a", "b"]
    assert rows[0]["secure"] is True


def test_hide_all_panes_empty() -> None:
    from frontend.ui_web.browser_overlay import hide_all_panes

    assert hide_all_panes() == {"ok": True, "hidden": 0}


def test_cdp_off_by_default(monkeypatch) -> None:
    from frontend.ui_web import browser_overlay as bo

    monkeypatch.delenv("UEFN_DUCKY_BROWSER_CDP", raising=False)
    monkeypatch.setattr(
        "frontend.ui_web.plugin_host_api.prefs_plugin_get",
        lambda _pid: {},
    )
    assert bo.cdp_enabled() is False
    assert bo.additional_browser_arguments() == ""


def test_cdp_enabled_via_env(monkeypatch) -> None:
    from frontend.ui_web import browser_overlay as bo

    monkeypatch.setenv("UEFN_DUCKY_BROWSER_CDP", "1")
    assert bo.cdp_enabled() is True
    assert "remote-debugging-port=0" in bo.additional_browser_arguments()


def test_clear_refuses_disk_wipe_while_profile_locked(monkeypatch) -> None:
    """MCP clear while WebView2 holds Cookies was freezing the WinForms UI."""
    from frontend.ui_web import browser_overlay as bo

    _reset_overlay_env_state(bo)
    monkeypatch.setattr(bo, "profile_locked", lambda: True)
    try:
        res = bo.clear_browsing_data("cookies")
        assert res["ok"] is False
        assert "locked" in (res.get("error") or "").lower()
    finally:
        _reset_overlay_env_state(bo)


def test_ui_sync_times_out_instead_of_deadlock() -> None:
    import time

    from frontend.ui_web.browser_overlay import _ui_sync

    class _Native:
        InvokeRequired = True

        def BeginInvoke(self, _action) -> None:
            # Never run the action — simulates a wedged UI pump.
            return None

    class _Window:
        native = _Native()

    t0 = time.perf_counter()
    try:
        _ui_sync(_Window(), lambda: "ok", timeout_s=0.2)
        raise AssertionError("expected timeout")
    except RuntimeError as exc:
        assert "timed out" in str(exc).lower()
    assert time.perf_counter() - t0 < 2.0


def _reset_overlay_env_state(bo) -> None:
    bo._shared_env = None
    bo._pending_env_panes.clear()
    bo._panes.clear()
    bo._env_bootstrap_started = False
    bo._ensure_inflight = False


def test_second_pane_queues_while_first_boots() -> None:
    """Two WebView2s on one UserDataFolder without a shared Environment freeze WinForms."""
    from frontend.ui_web import browser_overlay as bo

    class _FakeWindow:
        native = None

    first = bo._Pane(pane_id="a", window=_FakeWindow(), control=object(), ready=False)
    second = bo._Pane(pane_id="b", window=_FakeWindow())
    _reset_overlay_env_state(bo)
    bo._panes["a"] = first
    bo._panes["b"] = second
    try:
        bo._create_control(second)
        assert second.control is None
        assert second in bo._pending_env_panes
    finally:
        _reset_overlay_env_state(bo)


def test_second_pane_queues_while_first_ready_without_env() -> None:
    """Ready sibling still owns the profile — never open CreationProperties again."""
    from frontend.ui_web import browser_overlay as bo

    class _FakeWindow:
        native = None

    first = bo._Pane(pane_id="a", window=_FakeWindow(), control=object(), ready=True)
    second = bo._Pane(pane_id="b", window=_FakeWindow())
    _reset_overlay_env_state(bo)
    bo._panes["a"] = first
    bo._panes["b"] = second
    try:
        bo._create_control(second)
        assert second.control is None
        assert second in bo._pending_env_panes
    finally:
        _reset_overlay_env_state(bo)


def test_second_pane_queues_while_ensure_inflight() -> None:
    """Serialize EnsureCoreWebView2Async even when Environment is already shared."""
    from frontend.ui_web import browser_overlay as bo

    class _FakeWindow:
        native = None

    second = bo._Pane(pane_id="b", window=_FakeWindow())
    _reset_overlay_env_state(bo)
    bo._shared_env = object()
    bo._ensure_inflight = True
    bo._panes["b"] = second
    try:
        bo._create_control(second)
        assert second.control is None
        assert second in bo._pending_env_panes
    finally:
        _reset_overlay_env_state(bo)
