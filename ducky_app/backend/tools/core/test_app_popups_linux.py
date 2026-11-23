"""App popups on Linux: Wayland says why it can't, X11 windows sorted into main window and
popups (fakes anywhere; real EWMH windows under Xvfb, pressed with xdotool, on Linux CI)."""

from __future__ import annotations

import os
import shutil
import threading
import time

import pytest

from backend.tools.core import app_popups as ap
from frontend import native_capture as nc
from frontend.test_native_capture_linux import fake_ewmh_window, xvfb  # noqa: F401  (fixture)


@pytest.fixture
def linux(monkeypatch):
    monkeypatch.setattr(ap, "_LINUX", True)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")


def test_wayland_says_why(linux, monkeypatch) -> None:
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert ap.list_popups("blender") == []
    out = ap.press(123, 1)
    assert out == {"ok": False, "error": ap.WAYLAND_REASON}
    assert ap.stall_report("blender")["ok"] is False


def test_linux_program_names(linux) -> None:
    assert ap.app_names("blender") == ("blender.exe",)
    assert ap.app_names("krita") == ("krita",)
    assert ap.exe_names("nothing") == ()  # exe_names stays the Windows table on every OS


def _rect(l, t, w, h):  # noqa: E741
    return {"left": l, "top": t, "right": l + w, "bottom": t + h, "width": w, "height": h}


def test_x11_main_window_and_modal_dialog(linux, monkeypatch) -> None:
    wins = [
        {"xid": 10, "pid": 5, "title": "Blender", "cls": "Blender", "transient_for": 0, "modal": False,
         "rect": _rect(0, 0, 1600, 900)},
        {"xid": 11, "pid": 5, "title": "Save changes?", "cls": "Blender", "transient_for": 10, "modal": True,
         "rect": _rect(600, 300, 400, 200)},
        {"xid": 12, "pid": 9, "title": "Other app", "cls": "X", "transient_for": 0, "modal": False,
         "rect": _rect(0, 0, 300, 300)},
    ]
    monkeypatch.setattr(nc, "x11_windows", lambda: wins)
    monkeypatch.setattr(ap, "_pids", lambda names: {5: "blender"})
    popups = ap.list_popups("blender")
    assert [(p["hwnd"], p["title"], p["blocking"], p["main_title"]) for p in popups] == [
        (11, "Save changes?", True, "Blender")]


def test_linux_pids_finds_this_process(linux) -> None:
    if not os.path.isdir("/proc"):
        pytest.skip("needs /proc")
    with open("/proc/self/comm", encoding="utf-8") as f:
        me = f.read().strip().lower()
    assert ap._linux_pids((me,)).get(os.getpid()) == me


def test_x11_dialog_listed_captured_and_pressed(xvfb, linux, monkeypatch) -> None:  # noqa: F811
    if not shutil.which("xdotool"):
        pytest.skip("needs xdotool")
    from Xlib import X, display

    main = fake_ewmh_window(xvfb, 0, 0, 1200, 800, title="Fake App")
    dialog = fake_ewmh_window(xvfb, 400, 300, 300, 160, title="Continue?", transient_for=main, modal=True)
    clicked = threading.Event()

    def close_on_click() -> None:  # the app: a click on the dialog closes it
        d2 = display.Display(os.environ["DISPLAY"])
        w = d2.create_resource_object("window", dialog.id)
        w.change_attributes(event_mask=X.ButtonPressMask)
        d2.sync()
        deadline = time.time() + 5
        while time.time() < deadline and not clicked.is_set():
            while d2.pending_events():
                if d2.next_event().type == X.ButtonPress:
                    w.destroy()
                    d2.sync()
                    clicked.set()
            time.sleep(0.02)
        d2.close()

    threading.Thread(target=close_on_click, daemon=True).start()
    time.sleep(0.3)
    me = os.getpid()
    monkeypatch.setattr(ap, "_pids", lambda names: {me: "fakeapp"})
    popups = ap.list_popups("fakeapp")
    assert [(p["title"], p["blocking"]) for p in popups] == [("Continue?", True)]
    assert ap._capture(popups[0]).size == (300, 160)
    monkeypatch.setattr(ap, "_buttons", lambda popup, img=None: ([{"nx": 0.5, "ny": 0.8, "primary": True}], img))
    out = ap.press(popups[0]["hwnd"], 1)
    assert out["ok"] and out["closed"] and clicked.is_set()
