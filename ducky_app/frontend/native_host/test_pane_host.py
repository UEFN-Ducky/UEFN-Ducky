"""Browser panes under Ducky-Launcher.exe (pane_host + browser_overlay)."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


def test_child_style_drops_frame_and_popup() -> None:
    from frontend.native_host import pane_host as ph

    style = ph.child_style(ph._WS_POPUP | ph._WS_CAPTION | ph._WS_THICKFRAME | 0x10000000)
    assert style & ph._WS_CHILD and style & ph._WS_CLIPSIBLINGS
    assert not style & (ph._WS_POPUP | ph._WS_CAPTION | ph._WS_THICKFRAME)
    assert style & 0x10000000  # WS_VISIBLE kept


def test_shown_rects_only_visible_controls_of_the_form() -> None:
    from frontend.native_host.pane_host import shown_rects

    form = object()
    c = lambda vis, parent=form, w=300: SimpleNamespace(Visible=vis, Parent=parent, Left=10, Top=20, Width=w, Height=200)  # noqa: E731
    assert shown_rects([c(True), c(False), c(True, object()), c(True, w=1)], form) == [(10, 20, 300, 200)]


def test_launcher_host_takes_every_wid_and_pushes_events() -> None:
    from frontend.ui_web import browser_overlay as bo

    pushed: list[dict] = []
    other = object()
    host = SimpleNamespace(is_launcher_host=True, push_event=pushed.append)
    host.for_wid = lambda wid: other if wid else host
    old = bo._main_window
    try:
        bo.configure(main_window=host)
        assert bo._window_for_wid("") is host and bo._window_for_wid("focus-7") is other
        bo._push_page(host, {"type": "browser_pane_state", "pane_id": "p"})
        assert pushed == [{"type": "browser_pane_state", "pane_id": "p"}]
    finally:
        bo.configure(main_window=old)


# Pumps messages like the launcher's UI thread: a cross-process child shares its input queue,
# so focus calls wait on this thread.
_FAKE_LAUNCHER = """
import os, tkinter as tk, sys, threading
r = tk.Tk(); r.geometry('800x600-4000-4000'); r.update()
done = threading.Event()
threading.Thread(target=lambda: (sys.stdin.readline(), done.set()), daemon=True).start()
def poll():
    r.destroy() if done.is_set() else r.after(100, poll)
print(os.getpid(), flush=True); poll(); r.mainloop()
"""


def _start_fake_launcher() -> tuple[subprocess.Popen, int]:
    """The fake launcher and the pid that owns its window, once the window is up.

    Not the Popen's pid: in a venv on Windows ``python.exe`` is a redirector that runs the
    real interpreter as its child, so the window belongs to another pid. Both window tests
    below looked under the redirector's pid and failed on every run from a venv (each
    release's test step, Nov 2026)."""
    proc = subprocess.Popen([sys.executable, "-c", _FAKE_LAUNCHER], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    return proc, int(proc.stdout.readline().strip())


def test_each_launcher_window_gets_its_own_host() -> None:
    """Nov 8 2026: a tab popped out kept its page in the main window, on top of everything."""
    from frontend.native_host import pane_host as ph

    assert (ph.wid_mark(""), ph.wid_mark("main"), ph.wid_mark("nope")) == (0, 0, 0)
    assert ph.wid_mark("focus-00ff") == 255
    host = ph.PaneHost(1)
    assert host.for_wid("") is host and host.for_wid("main") is host
    assert host.for_wid("focus-00ff") is host.for_wid("focus-00ff") is not host


def test_a_popout_is_found_by_its_mark_and_never_taken_for_the_main_window() -> None:
    """The first window of the class in z-order was taken: a focus window in front got the panes."""
    from frontend.native_host import pane_host as ph

    proc, pid = _start_fake_launcher()
    try:
        hwnd = ph.find_launcher_window(pid, "TkTopLevel")
        assert hwnd
        u = ctypes.WinDLL("user32")
        u.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
        assert u.SetPropW(hwnd, ph.WID_PROP, 255)
        assert ph.find_launcher_window(pid, "TkTopLevel", 255) == hwnd
        assert ph.find_launcher_window(pid, "TkTopLevel") == 0
    finally:
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(10)


def test_pane_opens_inside_another_process_window(tmp_path, monkeypatch) -> None:
    """A real WebView2 pane in a form parented into a window of another process (stands in
    for the launcher), placed off screen."""
    pytest.importorskip("webview.platforms.winforms")
    from frontend.native_host import pane_host as ph
    from frontend.ui_web import browser_overlay as bo

    proc, pid = _start_fake_launcher()
    old = bo._main_window
    try:
        pushed: list[dict] = []
        host = ph.PaneHost(pid, class_name="TkTopLevel", push=pushed.append)
        monkeypatch.setattr(bo, "user_data_dir", lambda: str(tmp_path / "profile"))
        monkeypatch.setattr(bo, "_mode", "memory")
        bo.configure(main_window=host)

        form = host.native
        assert form is not None
        assert int(ph._user32().GetParent(host._hwnd) or 0) == host._parent
        cw, ch = host.client_size()
        assert cw > 100 and ch > 100

        assert bo.open_pane("t1", "about:blank")["ok"]
        bo.set_bounds("t1", 10, 10, 200, 150, cw, ch, True)
        deadline = time.monotonic() + 30
        # ready is set on the UI thread just before its state push, so wait for both.
        while time.monotonic() < deadline and not (
                bo._panes["t1"].ready and any(e.get("pane_id") == "t1" for e in pushed)):
            time.sleep(0.2)
        pane = bo._panes["t1"]
        assert pane.ready, pane.failed
        assert any(e.get("pane_id") == "t1" for e in pushed)
        size = bo._ui_sync(host, lambda: (int(pane.control.Width), int(pane.control.Height), bool(pane.control.Visible)))
        assert size == (200, 150, True)  # 1:1, the viewport given is the client size

        # On screen = the form above the launcher's page, clipped to the pane; hidden (an
        # overlay's snapshot stands in) = under it; shown again = back on top. pythonnet's
        # fresh wrapper per read made shown_rects empty, so the form never came up.
        def placed() -> tuple[bool, tuple[int, int, int, int]]:
            time.sleep(0.5)
            u, g = ph._user32(), ctypes.WinDLL("gdi32")
            u.GetWindow.argtypes, u.GetWindow.restype = [wintypes.HWND, wintypes.UINT], wintypes.HWND
            u.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN]
            g.CreateRectRgn.restype = wintypes.HRGN
            g.GetRgnBox.argtypes = [wintypes.HRGN, ctypes.POINTER(wintypes.RECT)]
            rgn, rc = g.CreateRectRgn(0, 0, 0, 0), wintypes.RECT()
            u.GetWindowRgn(host._hwnd, rgn)
            g.GetRgnBox(rgn, ctypes.byref(rc))
            g.DeleteObject(rgn)
            return not u.GetWindow(host._hwnd, 3), (rc.left, rc.top, rc.right, rc.bottom)  # GW_HWNDPREV

        assert placed() == (True, (10, 10, 210, 160))
        bo.set_bounds("t1", 10, 10, 200, 150, cw, ch, False, "covered_by card")
        assert placed()[0] is False
        bo.set_bounds("t1", 10, 10, 200, 150, cw, ch, True)
        assert placed() == (True, (10, 10, 210, 160))
    finally:
        try:
            bo.close_pane("t1")
        except Exception:
            pass
        bo._panes.pop("t1", None)
        bo.configure(main_window=old)
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(10)
