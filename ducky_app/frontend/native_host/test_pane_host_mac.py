"""Browser panes on macOS (cocoa_panes + browser_overlay) against fake AppKit / WebKit objects:
the pane is a WKWebView in the window's contentView, framed bottom-up like Cocoa does."""

from __future__ import annotations

import sys
import time
from types import ModuleType, SimpleNamespace

import pytest

from frontend.native_host import cocoa_panes as cp
from frontend.ui_web import browser_overlay as bo


def _rect(x, y, w, h):
    return SimpleNamespace(origin=SimpleNamespace(x=x, y=y), size=SimpleNamespace(width=w, height=h))


class _Alloc:
    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self


class FakeView(_Alloc):
    def __init__(self, frame=None):
        self._frame, self._parent, self._subs = frame or _rect(0, 0, 1, 1), None, []
        self.hidden, self.loaded, self.delegate, self.config = False, [], None, None

    def initWithFrame_configuration_(self, frame, config):  # noqa: N802
        self._frame, self.config = frame, config
        return self

    def frame(self):
        return self._frame

    def setFrame_(self, frame):  # noqa: N802
        self._frame = frame

    def setHidden_(self, hidden):  # noqa: N802
        self.hidden = bool(hidden)

    def superview(self):
        return self._parent

    def subviews(self):
        return list(self._subs)

    def addSubview_positioned_relativeTo_(self, view, where, other):  # noqa: N802
        if view._parent is not None:
            view.removeFromSuperview()
        at = len(self._subs) if other is None else self._subs.index(other)
        if where == cp._ABOVE and other is not None:
            at += 1
        if where == cp._BELOW and other is None:
            at = 0
        self._subs.insert(at, view)
        view._parent = self

    def removeFromSuperview(self):
        if self._parent is not None:
            self._parent._subs.remove(self)
            self._parent = None

    def setNavigationDelegate_(self, d):  # noqa: N802
        self.delegate = d

    def loadRequest_(self, req):  # noqa: N802
        self.loaded.append(req)

    def canGoBack(self):  # noqa: N802
        return False


class FakeWKWebView(FakeView):
    pass


class FakeConfig(_Alloc):
    store = None

    def setWebsiteDataStore_(self, store):  # noqa: N802
        self.store = store


class FakeStore:
    made: list = []

    @classmethod
    def nonPersistentDataStore(cls):  # noqa: N802
        cls.made.append("memory")
        return ("memory", len(cls.made))

    @classmethod
    def defaultDataStore(cls):  # noqa: N802
        return ("default",)

    @classmethod
    def dataStoreForIdentifier_(cls, ident):  # noqa: N802
        cls.made.append("saved")
        return ("saved", ident)


class FakeUUID(_Alloc):
    def initWithUUIDString_(self, s):  # noqa: N802
        return s


class FakeDelegate(_Alloc):
    pass


@pytest.fixture()
def mac(monkeypatch):
    foundation = ModuleType("Foundation")
    foundation.NSThread = SimpleNamespace(isMainThread=lambda: True)  # ui() runs inline
    foundation.NSMakeRect = _rect
    foundation.NSURL = SimpleNamespace(URLWithString_=lambda u: u)
    foundation.NSURLRequest = SimpleNamespace(requestWithURL_=lambda u: u)
    foundation.NSUUID = FakeUUID
    webkit = ModuleType("WebKit")
    webkit.WKWebView, webkit.WKWebViewConfiguration, webkit.WKWebsiteDataStore = FakeWKWebView, FakeConfig, FakeStore
    for name, mod in (("Foundation", foundation), ("WebKit", webkit), ("AppKit", ModuleType("AppKit"))):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(cp, "_delegate_class", lambda: FakeDelegate)
    monkeypatch.setattr(cp, "_stores", {})
    monkeypatch.setattr(cp, "_delegates", {})
    monkeypatch.setattr(bo, "_push_page", lambda _w, _e: None)
    monkeypatch.setattr(bo, "_mode", "memory")
    FakeStore.made = []
    content = FakeView(_rect(0, 0, 800, 600))
    host = FakeWKWebView(_rect(0, 0, 800, 600))  # the app's own page
    content.addSubview_positioned_relativeTo_(host, cp._ABOVE, None)
    window = SimpleNamespace(native=SimpleNamespace(contentView=lambda: content, makeFirstResponder_=lambda v: None))
    return SimpleNamespace(window=window, content=content, host=host)


def test_pane_is_a_child_view_framed_bottom_up_and_scaled(mac) -> None:
    pane = bo._Pane(pane_id="m1", window=mac.window, pending_url="https://example.com/")
    pane.css_bounds, pane.css_viewport = (10.0, 20.0, 300.0, 200.0), (800.0, 600.0)
    cp.create_control(pane)
    view = pane.control.view
    assert pane.ready and pane.failed == ""
    assert mac.content.subviews() == [mac.host, view]  # above the page
    assert cp.host_view(mac.content) is mac.host
    assert view.loaded == ["https://example.com/"] and view.delegate.pane is pane
    f = view.frame()
    assert (f.origin.x, f.origin.y, f.size.width, f.size.height, view.hidden) == (10, 380, 300, 200, False)

    pane.css_bounds, pane.css_viewport = (10.0, 10.0, 100.0, 50.0), (400.0, 300.0)  # page at 2x
    cp.apply_bounds(pane)
    f = view.frame()
    assert (f.origin.x, f.origin.y, f.size.width, f.size.height) == (20, 480, 200, 100)

    pane.visible = False
    cp.apply_bounds(pane)
    assert view.hidden is True
    pane.behind_until = time.monotonic() + 30  # an agent's pane: drawn under the page
    cp.apply_bounds(pane)
    assert view.hidden is False and mac.content.subviews() == [view, mac.host]
    pane.visible, pane.behind_until = True, 0.0
    cp.apply_bounds(pane)
    assert mac.content.subviews() == [mac.host, view]

    pane.control.Dispose()
    assert pane.control.view is None and mac.content.subviews() == [mac.host] and not cp._delegates


def test_each_vault_profile_has_its_own_data_store(mac, monkeypatch) -> None:
    monkeypatch.setattr(bo, "user_data_dir", lambda: "/Users/x/Library/ducky/webview2_browser")
    mem, again, saved = cp.data_store("memory"), cp.data_store("memory"), cp.data_store("saved")
    assert mem is again and mem[0] == "memory" and saved[0] == "saved"
    assert FakeStore.made == ["memory", "saved"]  # one per mode, made once
    assert saved == cp.data_store("saved")  # a stable identifier for the same profile folder

    pane = bo._Pane(pane_id="m2", window=mac.window)
    monkeypatch.setattr(bo, "_mode", "saved")
    cp.create_control(pane)
    assert pane.control.view.config.store is saved


def test_popups_open_a_tab_and_the_app_never_embeds_itself(mac, monkeypatch) -> None:
    pushed: list = []
    monkeypatch.setattr(bo, "_push_page", lambda _w, e: pushed.append(e))
    pane = bo._Pane(pane_id="m3", window=mac.window)
    assert cp.on_policy(pane, "https://example.com/", False) is True
    assert cp.on_policy(pane, "https://example.com/new", True) is False
    assert pushed[0]["type"] == "browser_pane_new_window" and pushed[0]["url"] == "https://example.com/new"
    assert cp.on_policy(pane, "file:///etc/passwd", False) is False
    cp.on_failed(pane, -1202, "bad cert")
    assert pane.cert_error and pane.failed == pane.cert_error and pane.cert_error_status == "-1202"


def test_install_binds_the_overlay_helpers(monkeypatch) -> None:
    fake = SimpleNamespace(**{n: object() for n in ("_ui", "_ui_sync", "_on_ui_thread", "_create_control",
                                                   "_apply_bounds", "snapshot", "evaluate", "devtools",
                                                   "clear_browsing_data")})
    cp.install(fake)
    assert fake._create_control is cp.create_control and fake._apply_bounds is cp.apply_bounds
    assert fake.devtools is cp.devtools and "_create_control" in fake._webview2
