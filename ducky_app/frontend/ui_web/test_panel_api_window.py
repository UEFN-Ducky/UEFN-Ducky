"""Window input, capture list, snip and the text menu under Ducky-Launcher.exe (--backend)."""

from __future__ import annotations

import io
import os
import threading

from frontend import window_view
from frontend.native_host import inventory, launcher_windows, lifecycle
from frontend.native_switch import NATIVE_CHILD_ENV
from frontend.ui_web import panel_api  # noqa: F401  (loads panel_api_window as the app does)
from frontend.ui_web import panel_api_window, webview_native_menu


class _Api(panel_api_window.PanelApiWindowMixin):
    def __init__(self, window=None) -> None:
        self._window = window
        self._tk_root = None  # as PanelApi.__init__; --backend never binds a Tk root


class _Broker:
    def __init__(self, on_event=None) -> None:
        self.events: list[tuple[str, dict]] = []
        self.on_event = on_event

    def _send_event(self, name, data):
        self.events.append((name, data))
        if self.on_event:
            self.on_event(name, data)
        return True


class _BackendWindow:
    def __init__(self, broker) -> None:
        self._dialogs = broker


def test_launcher_windows_are_ours_under_backend(monkeypatch):
    parent = os.getppid()
    monkeypatch.setattr(window_view, "_hwnd_pid", lambda hwnd: parent if hwnd == 77 else 1)
    monkeypatch.delenv(NATIVE_CHILD_ENV, raising=False)
    assert not window_view._is_our_hwnd(77)  # the Python window: only this process
    monkeypatch.setenv(NATIVE_CHILD_ENV, "1")
    assert window_view._is_our_hwnd(77)  # the launcher's window is the panel's
    assert not window_view._is_our_hwnd(5)


def test_window_input_never_types_into_the_launcher_window(monkeypatch):
    monkeypatch.setenv(NATIVE_CHILD_ENV, "1")
    monkeypatch.setattr(window_view, "_hwnd_pid", lambda _hwnd: os.getppid())
    sent: list = []
    monkeypatch.setattr(window_view, "_send_key", lambda *a, **k: sent.append(a))
    monkeypatch.setattr(window_view, "_raise_window", lambda *a: sent.append(a) or True)
    assert window_view.bring_to_front(77) is False
    _Api().window_input(77, {"type": "key", "key": "a"})
    assert sent == []
    assert isinstance(_Api().window_box("77"), dict)
    assert isinstance(_Api().window_text_focus(0), bool)


def test_capture_list_shield_blanks_launcher_titles(monkeypatch):
    monkeypatch.setenv(NATIVE_CHILD_ENV, "1")
    seen: list[frozenset[int]] = []

    def fake_titled():
        seen.append(window_view._our_pids())
        return []

    monkeypatch.setattr(window_view, "_our_titled_windows", fake_titled)
    api = _Api()
    if window_view.sys.platform == "win32":
        assert api.capture_list_shield(True) is True
        assert api.capture_list_shield(False) is True
        assert seen and os.getppid() in seen[0]


def test_native_capture_refuses_fast_so_the_page_falls_back():
    out = _Api().native_capture_start("monitor:1", 30)
    assert out == {"ok": False, "error": "no main WebView2"}
    assert _Api().native_capture_stop("monitor:1") is False


def test_snip_needs_no_window(monkeypatch):
    from frontend.ui_web import snip_overlay

    monkeypatch.setattr(panel_api_window._pa.sys, "platform", "win32")
    monkeypatch.setattr(snip_overlay, "snip_screen_interactive", lambda root: {"ok": False, "reason": "cancelled"})
    assert _Api().snip_screen("c1") == {"ok": False, "reason": "cancelled"}


def test_native_menu_pick_goes_to_the_launcher(monkeypatch):
    monkeypatch.setenv(NATIVE_CHILD_ENV, "1")
    monkeypatch.setattr(webview_native_menu, "pick", lambda _t, _c: False)

    def launcher(name, data):
        threading.Thread(target=launcher_windows.answer_menu, args=([data["id"], data["items"][0]],)).start()

    broker = _Broker(launcher)
    assert _Api(_BackendWindow(broker)).native_menu_pick("tok1", 3) == {"ok": True}
    assert broker.events == [("menu_pick", {"id": "tok1", "items": [3]})]


def test_native_menu_pick_python_window_unchanged(monkeypatch):
    monkeypatch.delenv(NATIVE_CHILD_ENV, raising=False)
    monkeypatch.setattr(webview_native_menu, "pick", lambda _t, _c: False)
    broker = _Broker()
    assert _Api(_BackendWindow(broker)).native_menu_pick("tok", 3) == {"ok": False}
    assert broker.events == []


def test_menu_times_out_and_unknown_answers_are_ignored():
    assert launcher_windows.ask_menu(lambda *_a: True, "m1", [1], timeout_s=0.05) is None
    assert launcher_windows.answer_menu(["nobody", 1]) is False
    assert launcher_windows.answer_menu("bad") is False


def test_serve_routes_menu_result_to_the_waiting_menu():
    results: list[dict] = []
    got: list = []
    ready = threading.Event()

    def send(_name, _data):
        ready.set()
        return True

    waiter = threading.Thread(target=lambda: got.append(launcher_windows.ask_menu(send, "m9", [4], timeout_s=5)))
    waiter.start()
    assert ready.wait(5)
    stream = io.BytesIO(b'{"t":"call","id":"c1","name":"__menu_result","args":["m9",4]}\n')
    calls: list = []
    lifecycle.serve(stream, on_call=calls.append, reply=results.append)
    waiter.join(5)
    assert got == [4] and calls == []
    assert results == [{"t": "result", "id": "c1", "ok": True, "value": True}]


def test_only_bind_calls_are_out_of_the_lean_cut():
    assert inventory.NOT_LEAN_CUT == frozenset({"bind_tk_root", "bind_window"})
