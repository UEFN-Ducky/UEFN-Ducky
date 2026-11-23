"""UEFN popups under Wine (X11): listed by UEFN's pid, captured with numbered buttons, pressed by XTest.

The fake-window tests run everywhere; the last one needs Linux with Xvfb and python-xlib."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from backend.tools.core import app_popups, uefn_modal
from backend.tools.core import uefn_popups as popups
from backend.tools.uefn import proton
from frontend import native_capture

_FIX = Path(__file__).resolve().parent / "testdata" / "popups"
PID, MAIN, DIALOG = 4242, 0x400001, 0x400007


@pytest.fixture
def wine(monkeypatch):
    img = Image.open(_FIX / "data_loss_warning.png").convert("RGB")
    w, h = img.size
    windows = [
        {"xid": MAIN, "pid": PID, "title": "Unreal Editor for Fortnite", "cls": "x", "transient_for": 0, "modal": False,
         "rect": {"left": 0, "top": 0, "right": 1600, "bottom": 900, "width": 1600, "height": 900}},
        {"xid": DIALOG, "pid": PID, "title": "Data Loss Warning", "cls": "x", "transient_for": MAIN, "modal": True,
         "rect": {"left": 300, "top": 200, "right": 300 + w, "bottom": 200 + h, "width": w, "height": h}},
        {"xid": 0x500001, "pid": 77, "title": "Some other app", "cls": "x", "transient_for": 0, "modal": True,
         "rect": {"left": 0, "top": 0, "right": 400, "bottom": 300, "width": 400, "height": 300}},
    ]
    for mod in (uefn_modal, popups):
        monkeypatch.setattr(mod, "_LINUX", True)
    monkeypatch.setattr(proton, "uefn_pids", lambda *_a: [PID])
    monkeypatch.setattr(native_capture, "x11_windows", lambda: list(windows))
    monkeypatch.setattr(native_capture, "grab_image", lambda *_a: img)
    skips: list[int] = []
    monkeypatch.setattr(popups, "mark_verse_skip", lambda: skips.append(1))
    return {"windows": windows, "skips": skips}


def test_wine_popups_are_the_uefn_pids_dialogs(wine) -> None:
    listed = popups.list_popups()
    assert [(p["hwnd"], p["title"], p["blocking"], p["dialog"]) for p in listed] == [
        (DIALOG, "Data Loss Warning", True, True)
    ]
    info = popups.describe_popup(listed[0], save=False)
    assert [b["n"] for b in info["buttons"]] == [1, 2]
    assert info["known"]["press"] == 1


def test_press_clicks_the_numbered_button_through_xtest(wine, monkeypatch) -> None:
    clicks: list = []

    def click(xid, popup, b):
        clicks.append((xid, b["nx"]))
        wine["windows"].pop(1)  # the dialog closes

    monkeypatch.setattr(app_popups, "_x11_click", click)
    buttons = popups.find_buttons(Image.open(_FIX / "data_loss_warning.png"))
    out = popups.press_button(DIALOG, 2)
    assert out["ok"] and out["closed"] and out["pressed"] == 2
    assert clicks == [(DIALOG, buttons[1]["nx"])]
    assert wine["skips"] == [1]  # Skip Rebuild & Continue path: no saving until Verse builds clean


def test_press_reports_a_missing_xdotool(wine, monkeypatch) -> None:
    def no_xdotool(*_a):
        raise OSError("xdotool is not installed")

    monkeypatch.setattr(app_popups, "_x11_click", no_xdotool)
    out = popups.press_button(DIALOG, 1)
    assert out["ok"] is False and "xdotool" in out["error"]


def _fake_xdotool(monkeypatch, *, focus_follows: bool) -> list[tuple]:
    calls: list[tuple] = []
    state = {"active": "99"}

    def xdo(*args):
        calls.append(tuple(map(str, args)))
        if args[0] == "windowactivate" and focus_follows:
            state["active"] = str(args[1])
        return state["active"] + "\n" if args[0] == "getactivewindow" else ""

    monkeypatch.setattr(app_popups, "_xdotool", xdo)
    return calls


def test_save_prompt_gets_enter_only_once_it_has_the_keyboard(wine, monkeypatch) -> None:
    wine["windows"][1]["title"] = "Save Content"
    assert uefn_modal.find_uefn_save_dialog() == {"title": "Save Content", "hwnd": DIALOG}
    calls = _fake_xdotool(monkeypatch, focus_follows=True)
    out = uefn_modal.dismiss_uefn_save_modal()
    assert out["sent_enter"] and out["window_title"] == "Save Content"
    assert ("key", "Return") in calls and calls[-1] == ("windowactivate", "99")  # the person's window back

    calls = _fake_xdotool(monkeypatch, focus_follows=False)
    out = uefn_modal.dismiss_uefn_save_modal()
    assert out["sent_enter"] is False and out["method"] == "enter_foreground_denied"
    assert ("key", "Return") not in calls


def test_the_persons_own_save_prompt_is_skipped(wine, monkeypatch) -> None:
    wine["windows"][1]["title"] = "Save Content"
    calls = _fake_xdotool(monkeypatch, focus_follows=True)
    out = uefn_modal.dismiss_uefn_save_modal(skip=frozenset({DIALOG}))
    assert out["reason"] == "no save dialog" and calls == []


@pytest.mark.skipif(not sys.platform.startswith("linux") or not shutil.which("Xvfb"), reason="needs Linux + Xvfb")
def test_fake_wine_windows_under_xvfb(monkeypatch) -> None:
    pytest.importorskip("Xlib")
    from Xlib import X, Xatom, display

    # A display of our own: a fixed number collided with xvfb-run's server or another xdist
    # worker's, Xvfb exited, and the test talked to that other server. An Xvfb still running
    # once we are connected is ours. (-displayfd would pick one, but loops forever where
    # /tmp/.X11-unix is read-only, as under WSLg.)
    xvfb = d = None
    for n in range(150 + os.getpid() % 500, 1000, 7):
        disp = f":{n}"
        if os.path.exists(f"/tmp/.X{n}-lock"):
            continue
        xvfb = subprocess.Popen(["Xvfb", disp, "-nolisten", "tcp", "-screen", "0", "1280x800x24"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            if xvfb.poll() is not None:
                break
            try:
                d = display.Display(disp)
                break
            except Exception:
                time.sleep(0.1)
        time.sleep(0.2)  # an Xvfb that lost the display exits right away
        if d is not None and xvfb.poll() is None:
            break
        if d is not None:
            d.close()
        d = None
        xvfb.kill()
        xvfb.wait(timeout=5)
    assert d is not None, "Xvfb did not start"
    try:
        monkeypatch.setenv("DISPLAY", disp)
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        root = d.screen().root
        atom = d.intern_atom

        def make(title: str, x: int, y: int, w: int, h: int, owner=None):
            win = root.create_window(x, y, w, h, 0, X.CopyFromParent, event_mask=X.StructureNotifyMask)
            win.change_property(atom("_NET_WM_PID"), Xatom.CARDINAL, 32, [PID])
            win.change_property(atom("_NET_WM_NAME"), atom("UTF8_STRING"), 8, title.encode())
            if owner is not None:
                win.set_wm_transient_for(owner)
            win.map()
            return win

        main = make("Unreal Editor for Fortnite", 0, 0, 1000, 700)
        dialog = make("Save Content", 300, 200, 420, 180, owner=main)
        root.change_property(atom("_NET_CLIENT_LIST"), Xatom.WINDOW, 32, [main.id, dialog.id])
        d.sync()

        monkeypatch.setattr(proton, "uefn_pids", lambda *_a: [PID])
        listed = popups.list_popups()
        assert [(p["hwnd"], p["title"], p["dialog"]) for p in listed] == [(dialog.id, "Save Content", True)]
        assert listed[0]["rect"]["width"] == 420
        assert uefn_modal.find_uefn_save_dialog()["hwnd"] == dialog.id

        assert popups.close_uefn_windows_x11() == 1
        deadline = time.time() + 3
        got = None
        while time.time() < deadline and got is None:
            while d.pending_events():
                ev = d.next_event()
                if ev.type == X.ClientMessage and ev.window.id == main.id:
                    got = ev.data[1][0]
            time.sleep(0.05)
        assert got == atom("WM_DELETE_WINDOW")
        d.close()
    finally:
        xvfb.terminate()
        xvfb.wait(timeout=5)
