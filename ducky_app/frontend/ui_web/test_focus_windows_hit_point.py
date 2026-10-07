"""focus_windows — drop hit points; close_this_window closes the CALLER, not the active window."""

from __future__ import annotations

import frontend.ui_web.panel_api  # noqa: F401 — the mixins import it; load it first like the app does
from frontend.ui_web import focus_windows
from frontend.ui_web.focus_windows import drop_hit_points


def test_drop_point_is_tried_before_the_cursor():
    assert drop_hit_points(400, 200, (10, 10)) == [(400, 200), (10, 10)]


def test_zeroed_drop_point_falls_back_to_the_cursor():
    assert drop_hit_points(0, 0, (111, 222)) == [(111, 222)]


def test_zero_drop_and_no_cursor_has_nothing_to_hit_test():
    assert drop_hit_points(0, 0, None) == []


def test_nonzero_x_alone_is_a_real_drop_point():
    assert drop_hit_points(50, 0, (1, 1)) == [(50, 0), (1, 1)]


def test_cursor_matching_the_drop_point_is_not_duplicated():
    assert drop_hit_points(400, 200, (400, 200)) == [(400, 200)]


def test_close_this_window_closes_the_caller_even_when_another_window_is_active(monkeypatch):
    from frontend.ui_web.panel_api_window import PanelApiWindowMixin

    caller, active = object(), object()
    groups = [
        focus_windows._FocusGroup(window=caller, tabs={"file:a.verse": "a"}, wid="focus-caller"),
        focus_windows._FocusGroup(window=active, tabs={"chat:b": "b"}, wid="focus-active"),
    ]
    destroyed: list[object] = []
    monkeypatch.setattr(focus_windows, "_focus_groups", groups)
    monkeypatch.setattr(focus_windows, "_destroy_window", destroyed.append)
    monkeypatch.setattr(focus_windows, "_drop_registry_window", lambda _wid: None)
    monkeypatch.setattr(focus_windows, "_log_close", lambda *_a: None)

    api = PanelApiWindowMixin()
    api._window = object()  # main
    api._resolve_window = lambda: active  # type: ignore[method-assign]
    api.close_this_window("test", "focus-caller")

    assert destroyed == [caller]
    assert [g.wid for g in focus_windows._focus_groups] == ["focus-active"]


def test_model_change_does_not_ask_main_to_open_the_chat(monkeypatch):
    """Main opening + claiming the chat closed the focus window that held it."""
    from types import SimpleNamespace

    import frontend.ui_web.panel_api as pa
    from frontend.ui_web.panel_api_settings import PanelApiSettingsMixin

    conv = SimpleNamespace(id="c1", title="T", folder_id="", coding_agent="", model="", provider="")
    calls: list[dict] = []
    monkeypatch.setattr(pa, "load_conversation", lambda _cid: conv)
    monkeypatch.setattr(pa, "save_conversation", lambda _c: None)
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: calls.append(k))

    api = PanelApiSettingsMixin()
    api._push = lambda _e: None  # type: ignore[attr-defined]
    assert api.set_conversation_coding_agent("c1", "codex", "gpt-6-astra")["ok"]
    assert calls and calls[0].get("open_tab") is False


def test_late_close_and_return_cannot_destroy_a_reopened_chat_window(monkeypatch):
    old, reopened = object(), object()
    old_group = focus_windows._FocusGroup(window=old, tabs={"chat:c": "Same name"}, wid="focus-old")
    new_group = focus_windows._FocusGroup(window=reopened, tabs={"chat:c": "Same name"}, wid="focus-new")
    monkeypatch.setattr(focus_windows, "_focus_groups", [new_group])
    destroyed, returned = [], []
    monkeypatch.setattr(focus_windows, "_destroy_window", destroyed.append)
    monkeypatch.setattr(focus_windows, "_notify_main_window", lambda *args: returned.append(args))
    monkeypatch.setattr(focus_windows, "_drop_registry_window", lambda _wid: None)
    monkeypatch.setattr(focus_windows, "_log_close", lambda *_a: None)
    focus_windows.close_focus_window("chat:c", window_id=old_group.wid)
    assert not focus_windows.return_tab_to_main("chat:c", "Same name", window_id=old_group.wid)
    assert not destroyed and not returned
    assert focus_windows.list_focus_window_ids() == ["chat:c"]
    focus_windows.close_focus_window("chat:c", window_id=new_group.wid)
    assert destroyed == [reopened]


def test_destroy_stops_bridge_dispatch_before_disposing_native_window(monkeypatch):
    from types import SimpleNamespace
    from frontend.ui_web import ui_dispatch
    calls = []
    monkeypatch.setattr(ui_dispatch, "drop_window", lambda _w: calls.append("drop"))
    monkeypatch.setattr(ui_dispatch, "schedule_call", lambda op: op())
    window = SimpleNamespace(destroy=lambda: calls.append("destroy"))
    focus_windows._destroy_window(window)
    assert calls[:2] == ["drop", "destroy"]


def test_header_close_hands_the_tabs_back_and_other_closes_do_not(monkeypatch):
    """The focus window's own close button must not lose its tabs (an OS close never did)."""
    from frontend.ui_web.panel_api_window import PanelApiWindowMixin

    windows = [object(), object()]
    monkeypatch.setattr(focus_windows, "_focus_groups", [
        focus_windows._FocusGroup(window=windows[0], tabs={"workflows:main": "Workflows", "chat:c": "C"}, wid="focus-a"),
        focus_windows._FocusGroup(window=windows[1], tabs={"file:a.verse": "a"}, wid="focus-b"),
    ])
    returned: list[dict[str, str]] = []
    monkeypatch.setattr(focus_windows, "_return_tabs_to_main", returned.append)
    monkeypatch.setattr(focus_windows, "_destroy_window", lambda _w: None)
    monkeypatch.setattr(focus_windows, "_drop_registry_window", lambda _wid: None)
    monkeypatch.setattr(focus_windows, "_log_close", lambda *_a: None)

    api = PanelApiWindowMixin()
    api._window = object()  # main
    api.close_this_window("header close button", "focus-a", True)
    api.close_this_window("last tab closed", "focus-b")

    assert returned == [{"workflows:main": "Workflows", "chat:c": "C"}]
    assert focus_windows._focus_groups == []
