"""42.30: Verse-field tools open the widget's editor once so fields are real — no Unreal required.

Without that the event field is a transient object: the Assets digest lists the widget with no
members and a click binding is saved with no target (verified live in UEFN 42.30, Oct 2 2026).
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent


class _Editors:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    def open_editor_for_assets(self, assets):
        if self.fail:
            raise RuntimeError("no editor")
        self.calls += [("open", a.path) for a in assets]
        return True

    def close_all_editors_for_asset(self, asset):
        self.calls.append(("close", asset.path))


class _Asset:
    def __init__(self, path: str) -> None:
        self.path = path


@pytest.fixture()
def author(monkeypatch):
    editors = _Editors()
    unreal = types.ModuleType("unreal")
    unreal.AssetEditorSubsystem = object
    unreal.get_editor_subsystem = lambda cls: editors
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    # listener.dispatch / listener.registry.umg_spec without the real (Unreal-importing) package.
    listener = types.ModuleType("listener")
    listener.__path__ = []
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda name: (lambda fn: fn)
    registry = types.ModuleType("listener.registry")
    registry.__path__ = []
    spec_mod = importlib.util.spec_from_file_location("listener.registry.umg_spec", _HERE / "registry" / "umg_spec.py")
    umg_spec = importlib.util.module_from_spec(spec_mod)
    spec_mod.loader.exec_module(umg_spec)
    for name, mod in {"listener": listener, "listener.dispatch": dispatch, "listener.registry": registry,
                      "listener.registry.umg_spec": umg_spec}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("umg_author_under_test", _HERE / "registry" / "umg_author.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    open_now: list[str] = []
    mod._ref = lambda obj: f"{obj.path}.{obj.path.rsplit('/', 1)[-1]}"
    mod._open_asset_packages = lambda: set(open_now)
    # No real Win32 from a test: the person's window is 7 and gets every give-back.
    editors.given_back = []
    mod._foreground_window = lambda: 7
    mod._give_foreground_back = editors.given_back.append
    return mod, editors, open_now


def test_opens_the_editor_once_then_remembers(author):
    mod, editors, _ = author
    wbp = _Asset("/Proj/UI/UW_Shop")
    assert mod.ensure_verse_fields_live(wbp) == "opened"
    assert editors.calls == [("open", "/Proj/UI/UW_Shop"), ("close", "/Proj/UI/UW_Shop")]
    assert mod.ensure_verse_fields_live(wbp) == "ready"
    assert len(editors.calls) == 2


def test_the_persons_window_gets_the_keyboard_back_after_the_flash(author):
    mod, editors, _ = author
    wbp = _Asset("/Proj/UI/UW_Shop")
    mod.ensure_verse_fields_live(wbp)
    assert editors.given_back == [7]
    mod.ensure_verse_fields_live(wbp)  # remembered: no second flash, nothing to give back
    assert editors.given_back == [7]
    editors.fail = True
    mod.forget_verse_fields_live("/Proj/UI/UW_Shop")
    assert mod.ensure_verse_fields_live(wbp).startswith("failed:")
    assert editors.given_back == [7, 7]  # a failed open may still have raised UEFN


def test_leaves_an_editor_the_user_has_open(author):
    mod, editors, open_now = author
    open_now.append("/Proj/UI/UW_Shop")
    assert mod.ensure_verse_fields_live(_Asset("/Proj/UI/UW_Shop")) == "already_open"
    assert editors.calls == [] and editors.given_back == []


def test_a_recreated_widget_is_opened_again(author):
    mod, editors, _ = author
    wbp = _Asset("/Proj/UI/UW_Shop")
    mod.ensure_verse_fields_live(wbp)
    mod.forget_verse_fields_live("/Proj/UI/UW_Shop")
    assert mod.ensure_verse_fields_live(wbp) == "opened"
    assert editors.calls.count(("open", "/Proj/UI/UW_Shop")) == 2


def test_a_failed_open_is_reported_and_retried(author):
    mod, editors, _ = author
    editors.fail = True
    wbp = _Asset("/Proj/UI/UW_Shop")
    assert mod.ensure_verse_fields_live(wbp).startswith("failed:")
    editors.fail = False
    assert mod.ensure_verse_fields_live(wbp) == "opened"


_EVENT = "/Proj/UI/UW_Shop:MVVMBlueprintViewEvent_0"


@pytest.fixture()
def binder(author, monkeypatch):
    """bind_widget_event with a fake MVVM: `events` is the view's event list, `created` what it made."""
    mod, _, _ = author
    order: list[str] = []
    events: list[dict] = []
    created: list[str] = []
    child_class = {"value": "/Script/UIFramework.UIFrameworkCustomButtonWidget"}
    wbp = _Asset("/Proj/UI/UW_Shop")
    monkeypatch.setattr(mod, "_load_wbp", lambda path: wbp)
    monkeypatch.setattr(mod, "ensure_verse_fields_live", lambda w: order.append("live") or "opened")
    monkeypatch.setattr(mod, "_widget_object", lambda w, name: (object(), {"widgetClassPath": {"refPath": child_class["value"]}}))
    monkeypatch.setattr(mod, "_ref_of", lambda node: node.get("refPath", "") if isinstance(node, dict) else "")
    monkeypatch.setattr(mod, "_ref", lambda obj: obj if isinstance(obj, str) else obj.path)
    monkeypatch.setattr(mod, "_field_names", lambda path: ["BuyClicked", "OnToggleButtonClicked"])
    monkeypatch.setattr(mod, "_compile_checked", lambda w: {"compiled": True})
    monkeypatch.setattr(mod, "_view_events", lambda w: list(events))

    class _MVVM:
        @staticmethod
        def create_view_event_binding(wbp, widget, path, dest_context, dest):
            order.append(f"bind:{path}")
            if path.startswith("Nope_"):
                return None  # what UEFN 42.30 returns for a path that names nothing
            created.append(path)
            events.append({"source": f"ToggleButton1.{path}" if "." in path else f"BuyButton.{path}",
                           "destination": dest, "sends": {"Param0": "0"} if dest.startswith("OnToggle") else {},
                           "ref": _EVENT})
            return _EVENT

    monkeypatch.setattr(mod, "_mvvm", lambda: _MVVM)
    return mod, order, events, created, child_class


def test_bind_widget_event_makes_the_fields_live_before_binding(binder):
    mod, order, _, _, _ = binder
    out = mod.bind_widget_event("/Proj/UI/UW_Shop", "BuyButton", "OnClicked", "BuyClicked")
    assert order == ["live", "bind:OnButtonClicked"]
    assert out["verse_ready"] == "opened" and out["event_name"] == "OnButtonClicked"
    assert out["event"] == _EVENT and out["sends"] == {}
    assert "manual_step" not in out and "already_bound" not in out


def test_a_nested_inner_button_path_binds_and_reports_what_it_sends(binder):
    mod, order, _, created, child_class = binder
    child_class["value"] = "/Proj/VerseUIKit/Widgets/Buttons/UW_Button_Toggle.UW_Button_Toggle_C"
    out = mod.bind_widget_event("/Proj/UI/UW_Shop", "ToggleButton1", "Main_Button.OnButtonClicked", "OnToggleButtonClicked")
    assert created == ["Main_Button.OnButtonClicked"]
    assert out["sends"] == {"Param0": "0"}
    assert "View Bindings" in out["manual_step"] and "ToggleButton1.Main_Button.OnButtonClicked" in out["manual_step"]


def test_a_path_that_binds_nothing_raises_instead_of_reporting_success(binder):
    mod, _, events, _, child_class = binder
    child_class["value"] = "/Proj/VerseUIKit/Widgets/Buttons/UW_Button_Toggle.UW_Button_Toggle_C"
    with pytest.raises(ValueError, match="nothing was bound"):
        mod.bind_widget_event("/Proj/UI/UW_Shop", "ToggleButton1", "Nope_Button.OnButtonClicked", "OnToggleButtonClicked")
    assert events == []


def test_binding_the_same_click_again_returns_the_existing_event(binder):
    mod, _, events, created, child_class = binder
    child_class["value"] = "/Proj/VerseUIKit/Widgets/Buttons/UW_Button_Toggle.UW_Button_Toggle_C"
    args = ("/Proj/UI/UW_Shop", "ToggleButton1", "Main_Button.OnButtonClicked", "OnToggleButtonClicked")
    mod.bind_widget_event(*args)
    again = mod.bind_widget_event(*args)
    assert created == ["Main_Button.OnButtonClicked"] and len(events) == 1
    assert again["already_bound"] is True and again["event"] == _EVENT


class _TextWidget:
    path = "/Proj/UI/UW_Shop.UW_Shop:WidgetTree.Title_Text"

    def __init__(self) -> None:
        self.plain: list[str] = []

    def set_editor_property(self, name, value):
        self.plain.append(value)


def test_tree_text_is_written_as_localizable_text(author, monkeypatch):
    mod, _, _ = author
    sets: list[tuple[str, dict]] = []
    monkeypatch.setattr(mod, "_ref", lambda obj: obj.path)
    monkeypatch.setattr(mod, "_mod", lambda: types.SimpleNamespace(set_object_properties=lambda ref, values: sets.append((ref, values))))
    widget = _TextWidget()
    assert mod._apply_widget_props(widget, {"text": 'Say "Play"'}) == []
    (ref, values), = sets
    assert ref == _TextWidget.path and widget.plain == []
    assert values["Text"].startswith('NSLOCTEXT("", "') and values["Text"].endswith('", "Say \\"Play\\"")')
    sets.clear()
    mod._apply_widget_props(widget, {"text": 'INVTEXT("42")'})
    assert sets[0][1] == {"Text": 'INVTEXT("42")'}


def test_tree_text_falls_back_to_a_plain_string_and_says_so(author, monkeypatch):
    mod, _, _ = author

    def _refuse(ref, values):
        raise ValueError("Toolset not found")

    monkeypatch.setattr(mod, "_ref", lambda obj: obj.path)
    monkeypatch.setattr(mod, "_mod", lambda: types.SimpleNamespace(set_object_properties=_refuse))
    widget = _TextWidget()
    notes = mod._apply_widget_props(widget, {"text": "Play"})
    assert widget.plain == ["Play"]
    assert notes and "not localizable" in notes[0]
