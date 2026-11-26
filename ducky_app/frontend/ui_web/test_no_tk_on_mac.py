"""macOS: Cocoa owns the main thread, so nothing in the panel ever imports tkinter."""

from __future__ import annotations

import builtins
import sys
import threading

import pytest

from frontend import boot_report, mac_alert
from frontend.ui_web import shutdown, sound_import, webview_app
from frontend.ui_web import panel_api  # noqa: F401  (loads panel_api_window without the cycle)
from frontend.ui_web.panel_api_window import PanelApiWindowMixin


@pytest.fixture
def mac(monkeypatch):
    tried: list[str] = []
    real = builtins.__import__

    def spy(name, *a, **kw):
        if name.split(".")[0] == "tkinter":
            tried.append(name)
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", spy)
    monkeypatch.setattr(sys, "platform", "darwin")
    alerts: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(mac_alert, "ask", lambda title, message, buttons=("OK",), **kw: alerts.append((title, list(buttons))) or 0)
    monkeypatch.setattr(shutdown, "hard_exit", lambda **kw: None)
    monkeypatch.setattr(boot_report, "_test_copy", lambda: False)
    monkeypatch.setattr(boot_report, "_write_state", lambda: None)
    monkeypatch.setattr(boot_report, "_save", lambda body, prefix: "")
    monkeypatch.setattr(boot_report, "_send", lambda body, version: False)
    return {"tried": tried, "alerts": alerts}


def test_no_tkinter_import_on_darwin(mac):
    assert webview_app.hidden_tk_root() is None
    assert shutdown.start_tk_pump(webview_app.hidden_tk_root) is None
    assert PanelApiWindowMixin._pick_project_path_standalone(object()) is None
    with pytest.raises(RuntimeError):
        sound_import._pick_with_tk()
    shutdown.fatal_error_and_exit(RuntimeError("boom"))
    assert boot_report._ask_to_send({"message": "x"}) is True
    boot_report.show_now(None, {"message": "the window got stuck"}, reload=lambda: None)
    ran = threading.Event()
    shutdown.schedule_tk_call(ran.set)  # no Tk pump on mac: runs on its own thread
    assert ran.wait(2)
    assert mac["tried"] == []
    assert [a[0] for a in mac["alerts"]][0] == "UEFN Ducky"
    assert mac["alerts"][-1][1] == ["Send report", "Don't send", "Reload"]
