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


def _evaluate_pane(reply: object) -> object:
    from types import SimpleNamespace

    class _Task:
        def __init__(self, value: object) -> None:
            self.value = value

        def GetAwaiter(self) -> object:
            return SimpleNamespace(GetResult=lambda: self.value)

    calls: list[tuple[str, str]] = []

    def call(method: str, params: str) -> _Task:
        calls.append((method, params))
        return _Task(reply)

    core = SimpleNamespace(CallDevToolsProtocolMethodAsync=call)
    window = SimpleNamespace(native=None)
    return calls, SimpleNamespace(window=window, control=SimpleNamespace(CoreWebView2=core))


def test_evaluate_awaits_the_page_promise_over_devtools() -> None:
    """Plugins read a page (a video frame) through the control's own DevTools channel."""
    import json

    from frontend.ui_web import browser_overlay as bo

    calls, fake = _evaluate_pane(json.dumps({"result": {"type": "object", "value": {"ok": True, "w": 1080}}}))
    pane = bo._Pane(pane_id="ev", window=fake.window, control=fake.control, ready=True,
                    url="http://127.0.0.1:8123/")
    bo._panes["ev"] = pane
    try:
        assert bo.evaluate("ev", "(async () => ({ok: true}))()") == {"ok": True, "value": {"ok": True, "w": 1080}}
        method, params = calls[0]
        assert method == "Runtime.evaluate"
        assert json.loads(params) == {"expression": "(async () => ({ok: true}))()", "awaitPromise": True,
                                      "returnByValue": True}
        bo._panes["ev"] = bo._Pane(pane_id="ev", window=fake.window, control=_evaluate_pane(json.dumps(
            {"exceptionDetails": {"text": "Uncaught", "exception": {"description": "ReferenceError: x"}}}))[1].control,
            ready=True, url="https://www.youtube.com/watch?v=x")
        assert bo.evaluate("ev", "x") == {"ok": False, "error": "ReferenceError: x"}
        assert bo.evaluate("missing", "1")["error"] == "pane not open"
    finally:
        bo._panes.pop("ev", None)


def test_page_tabs_belong_to_their_browser_tab() -> None:
    """Agents get the page tab on screen; closing the Browser tab closes its page tabs."""
    from frontend.ui_web import browser_overlay as bo

    app = "plugin:browser:browser"
    panes = {
        f"{app}::t1": bo._Pane(pane_id=f"{app}::t1", window=None, visible=False),
        f"{app}::t2": bo._Pane(pane_id=f"{app}::t2", window=None, visible=True, css_bounds=(0, 0, 800, 600)),
        "plugin:other:x": bo._Pane(pane_id="plugin:other:x", window=None),
    }
    saved = dict(bo._panes)
    bo._panes.clear()
    bo._panes.update(panes)
    try:
        assert bo.pick_pane_id(app, allow_fallback=False) == f"{app}::t2"
        assert bo.pick_pane_id(f"{app}::t1") == f"{app}::t1"
        assert bo.pick_pane_id("plugin:browser:gone", allow_fallback=False) == ""
        assert bo._state_dict(panes[f"{app}::t2"])["visible"] is True
        assert bo.close_pane(app) == {"ok": True, "closed": True}
        assert list(bo._panes) == ["plugin:other:x"]
    finally:
        bo._panes.clear()
        bo._panes.update(saved)


def test_devtools_refuses_sites_the_person_uses(monkeypatch) -> None:
    """No script or capture on a signed-in site (Gmail, X) until the person allows it."""
    from frontend.ui_web import browser_overlay as bo
    from frontend.ui_web.panel_httpd import PANEL_UI_HTTP_PORT

    calls, fake = _evaluate_pane("{}")
    monkeypatch.setattr(bo, "agent_sites", lambda: [])
    try:
        for url in ("https://mail.google.com/", "https://x.com/home", "https://evilyoutube.com/",
                    f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/", "about:blank", ""):
            bo._panes["ev"] = bo._Pane(pane_id="ev", window=fake.window, control=fake.control, ready=True, url=url)
            assert "only runs on" in bo.evaluate("ev", "document.cookie")["error"], url
        assert calls == []
        assert bo.script_allowed("https://consent.youtube.com/m")
        assert bo.script_allowed("http://localhost:3000/")
        monkeypatch.setattr(bo, "agent_sites", lambda: ["x.com"])
        assert bo.script_allowed("https://x.com/home") and not bo.script_allowed("https://mail.google.com/")
        assert bo.site_of("https://www.x.com/home") == "x.com"
        monkeypatch.setattr(bo, "agent_sites", lambda: ["*"])
        assert bo.script_allowed("https://www.linkedin.com/feed/")
        assert not bo.script_allowed("https://accounts.google.com/signin")  # account pages stay the person's
        assert not bo.script_allowed("https://myaccount.google.com/security")
        assert not bo.script_allowed(f"http://127.0.0.1:{PANEL_UI_HTTP_PORT}/")  # never Ducky's own UI
    finally:
        bo._panes.pop("ev", None)


def test_render_behind_draws_a_hidden_pane_under_the_app_page(monkeypatch) -> None:
    """An agent's pane whose tab is off screen draws full size under Ducky's own page, never on
    top and never focused, and hides again when its time is up."""
    import sys
    import types
    from types import SimpleNamespace

    import frontend.ui_web.browser_overlay as bo

    drawing = types.ModuleType("System.Drawing")
    drawing.Point = lambda x, y: (x, y)
    drawing.Size = lambda w, h: SimpleNamespace(Width=w, Height=h)
    forms = types.ModuleType("System.Windows.Forms")
    forms.AnchorStyles = SimpleNamespace(Top=1, Left=4)
    for name, module in {"System": types.ModuleType("System"), "System.Drawing": drawing,
                         "System.Windows": types.ModuleType("System.Windows"), "System.Windows.Forms": forms}.items():
        monkeypatch.setitem(sys.modules, name, module)
    timers: list[float] = []
    monkeypatch.setattr(bo.threading, "Timer", lambda s, fn: SimpleNamespace(daemon=True, start=lambda: timers.append(s)))

    class _Control:
        def __init__(self) -> None:
            self.Visible = False
            self.Size = SimpleNamespace(Width=1, Height=1)
            self.Location = (0, 0)
            self.calls: list[str] = []

        def Focus(self) -> None:
            self.calls.append("focus")

        def BringToFront(self) -> None:
            self.calls.append("front")

        def SendToBack(self) -> None:
            self.calls.append("back")

    class _Controls(list):
        def SetChildIndex(self, control: object, index: int) -> None:
            pass

    control = _Control()
    host = SimpleNamespace(ClientSize=SimpleNamespace(Width=1600, Height=900), Left=0, Top=0)
    form = SimpleNamespace(webview=host, Visible=True, WindowState="Normal", Controls=_Controls([host, control]))
    pane = bo._Pane(pane_id="p", window=SimpleNamespace(native=form), control=control, ready=True)
    pane.visible, pane.css_bounds, pane.last_size = False, (0.0, 0.0, 0.0, 0.0), (1200, 700)
    bo._panes["p"] = pane
    try:
        assert bo.render_behind("p", 30)["on_screen"] is False
        assert control.Visible is True and (control.Size.Width, control.Size.Height) == (1200, 700)
        assert control.calls == ["back"] and timers == [30.5]

        pane.behind_until = 0.0  # time is up: hidden again
        bo._apply_bounds(pane)
        assert control.Visible is False and "focus" not in control.calls

        pane.visible, pane.css_bounds, pane.css_viewport = True, (10.0, 10.0, 800.0, 600.0), (1600.0, 900.0)
        bo._apply_bounds(pane)  # the person shows the tab: on top as before
        assert control.Visible is True and control.calls[-2:] == ["front", "focus"] and pane.last_size == (800, 600)
        assert bo.render_behind("missing")["ok"] is False
    finally:
        bo._panes.pop("p", None)
