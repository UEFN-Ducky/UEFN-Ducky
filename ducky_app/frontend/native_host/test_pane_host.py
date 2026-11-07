"""Browser panes under Ducky-Launcher.exe (pane_host + browser_overlay)."""

from __future__ import annotations

import subprocess
import sys
import time
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
    host = SimpleNamespace(is_launcher_host=True, push_event=pushed.append)
    old = bo._main_window
    try:
        bo.configure(main_window=host)
        assert bo._window_for_wid("") is host and bo._window_for_wid("focus-7") is host
        bo._push_page(host, {"type": "browser_pane_state", "pane_id": "p"})
        assert pushed == [{"type": "browser_pane_state", "pane_id": "p"}]
    finally:
        bo.configure(main_window=old)


# Pumps messages like the launcher's UI thread: a cross-process child shares its input queue,
# so focus calls wait on this thread.
_FAKE_LAUNCHER = """
import tkinter as tk, sys, threading
r = tk.Tk(); r.geometry('800x600-4000-4000'); r.update()
done = threading.Event()
threading.Thread(target=lambda: (sys.stdin.readline(), done.set()), daemon=True).start()
def poll():
    r.destroy() if done.is_set() else r.after(100, poll)
print('up', flush=True); poll(); r.mainloop()
"""


def test_pane_opens_inside_another_process_window(tmp_path, monkeypatch) -> None:
    """A real WebView2 pane in a form parented into a window of another process (stands in
    for the launcher), placed off screen."""
    pytest.importorskip("webview.platforms.winforms")
    from frontend.native_host import pane_host as ph
    from frontend.ui_web import browser_overlay as bo

    proc = subprocess.Popen([sys.executable, "-c", _FAKE_LAUNCHER], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, text=True)
    old = bo._main_window
    try:
        assert proc.stdout.readline().strip() == "up"
        pushed: list[dict] = []
        host = ph.PaneHost(proc.pid, class_name="TkTopLevel", push=pushed.append)
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
        while time.monotonic() < deadline and not bo._panes["t1"].ready:
            time.sleep(0.2)
        pane = bo._panes["t1"]
        assert pane.ready, pane.failed
        assert any(e.get("pane_id") == "t1" for e in pushed)
        size = bo._ui_sync(host, lambda: (int(pane.control.Width), int(pane.control.Height), bool(pane.control.Visible)))
        assert size == (200, 150, True)  # 1:1, the viewport given is the client size
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
