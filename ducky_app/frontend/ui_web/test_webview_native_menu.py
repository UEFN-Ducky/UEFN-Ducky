"""Right-click over typed text: WebView2's own menu (spell checker suggestions...) shown as
Ducky's, and the pick handed back to WebView2."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from frontend.ui_web import webview_native_menu as nm


def _item(kind: str, label: str = "", name: str = "other", cid: int = 0, enabled: bool = True):
    return SimpleNamespace(Kind=f"CoreWebView2ContextMenuItemKind.{kind}", Label=label, Name=name,
                           CommandId=cid, IsEnabled=enabled)


SPELLING_MENU = [
    _item("Command", "villager", cid=11),
    _item("Command", "villagers", cid=12),
    _item("Separator"),
    _item("Command", "&Add to dictionary", name="addToDictionary", cid=13),
    _item("Separator"),
    _item("Command", "Cu&t", name="cut", cid=20),
    _item("Command", "&Paste", name="paste", cid=21, enabled=False),
    _item("Submenu", "Writing direction", name="writingDirection", cid=30),
    _item("Separator"),
    _item("Command", "Inspect", name="inspectElement", cid=40),
    _item("Separator"),
]


def test_menu_items_keep_the_spell_checker_and_editing_commands_only() -> None:
    assert nm.menu_items(SPELLING_MENU) == [
        {"id": 11, "label": "villager", "enabled": True},
        {"id": 12, "label": "villagers", "enabled": True},
        {"separator": True},
        {"id": 13, "label": "Add to dictionary", "enabled": True},
        {"separator": True},
        {"id": 20, "label": "Cut", "enabled": True},
        {"id": 21, "label": "Paste", "enabled": False},
    ]


class _Core:
    def __init__(self) -> None:
        self.handlers: list = []

    @property
    def ContextMenuRequested(self):  # noqa: N802 — the WebView2 event's name
        core = self

        class _Event:
            def __iadd__(self, fn):
                core.handlers.append(fn)
                return self

        return _Event()

    @ContextMenuRequested.setter
    def ContextMenuRequested(self, _value) -> None:  # noqa: N802
        pass


def _args(editable: bool):
    deferral = SimpleNamespace(done=False)
    deferral.Complete = lambda: setattr(deferral, "done", True)
    args = SimpleNamespace(ContextMenuTarget=SimpleNamespace(IsEditable=editable), MenuItems=SPELLING_MENU,
                           Handled=False, SelectedCommandId=-1)
    args.GetDeferral = lambda: deferral
    return args, deferral


@pytest.fixture
def pushed(monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr("frontend.ui_web.ui_dispatch.schedule_evaluate_js", lambda _w, script: sent.append(script))
    return sent


def test_typed_text_gets_ducky_menu_and_the_pick_runs_in_webview2(pushed) -> None:
    core = _Core()
    nm.attach(core, window=None, form=None, hide_others=True)
    args, deferral = _args(editable=True)
    core.handlers[0](None, args)
    assert args.Handled is True and not deferral.done
    payload = json.loads(pushed[0].split("__uefnPanelPush(", 1)[1].rstrip(")"))
    assert payload["type"] == "native_menu" and payload["items"][0]["label"] == "villager"

    assert nm.pick(payload["token"], 12) is True
    assert args.SelectedCommandId == 12 and deferral.done
    assert nm.pick(payload["token"], 11) is False, "one answer per menu"


def test_closing_the_menu_runs_nothing(pushed) -> None:
    core = _Core()
    nm.attach(core, window=None, form=None, hide_others=True)
    args, deferral = _args(editable=True)
    core.handlers[0](None, args)
    token = json.loads(pushed[0].split("__uefnPanelPush(", 1)[1].rstrip(")"))["token"]
    nm.pick(token, -1)
    assert args.SelectedCommandId == -1 and deferral.done


def test_keep_menu_events_turns_default_menus_back_on() -> None:
    settings = SimpleNamespace(AreDefaultContextMenusEnabled=False)
    nm.keep_menu_events(settings)
    assert settings.AreDefaultContextMenusEnabled is True
    nm.keep_menu_events(None)  # no settings: nothing to do


@pytest.mark.skipif(__import__("sys").platform != "win32", reason="the chrome patch is Windows only")
def test_the_menu_event_survives_pywebviews_own_ready_step(monkeypatch) -> None:
    """Oct 29 2026: shipped builds showed no spelling menu. pywebview's on_webview_ready runs after
    Ducky's and sets AreDefaultContextMenusEnabled = debug (False), which stops the event."""
    import types

    from frontend import boot_report
    from frontend.ui_web import win_frameless as wf

    class EdgeChrome:
        def on_webview_ready(self, sender, args):
            sender.CoreWebView2.Settings.AreDefaultContextMenusEnabled = False  # what pywebview does

        def on_navigation_completed(self, sender, args):
            pass

    platforms = types.ModuleType("webview.platforms")
    platforms.edgechromium = types.SimpleNamespace(EdgeChrome=EdgeChrome)
    platforms.winforms = types.SimpleNamespace(BrowserView=types.SimpleNamespace(BrowserForm=object))
    monkeypatch.setitem(__import__("sys").modules, "webview.platforms", platforms)
    monkeypatch.setattr(wf, "_make_chrome_browser_form", lambda: type("Form", (), {}))
    monkeypatch.setattr(wf, "_OriginalBrowserForm", type("Orig", (), {"update_title_bar_theme": lambda self: None}))
    monkeypatch.setattr(wf.install_pywebview_chrome_patches, "_installed", False, raising=False)
    monkeypatch.setattr(boot_report, "webview_ready", lambda *a, **k: None)
    wf.install_pywebview_chrome_patches()

    core = _Core()
    core.Settings = SimpleNamespace(IsNonClientRegionSupportEnabled=False, AreDefaultContextMenusEnabled=True)
    sender = SimpleNamespace(CoreWebView2=core)
    EdgeChrome.on_webview_ready(SimpleNamespace(pywebview_window=None, form=None), sender, SimpleNamespace(IsSuccess=True))
    assert core.handlers, "the right-click hook attached"
    assert core.Settings.AreDefaultContextMenusEnabled is True


def test_elsewhere_the_store_exe_shows_no_browser_menu_and_dev_keeps_it(pushed) -> None:
    core = _Core()
    nm.attach(core, window=None, form=None, hide_others=True)
    args, _ = _args(editable=False)
    core.handlers[0](None, args)
    assert args.Handled is True and pushed == []

    dev = _Core()
    nm.attach(dev, window=None, form=None, hide_others=False)
    args, _ = _args(editable=False)
    dev.handlers[0](None, args)
    assert args.Handled is False and pushed == []
