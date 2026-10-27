"""Opening Settings → Feedback froze the app (Oct 26): the tab read and scrubbed every
log row to show two counts, and the click's fullscreen topmost confetti overlay only
went away if every animation frame succeeded."""

from __future__ import annotations

import gc
import json
import sys
import time

import pytest

from frontend import error_log


def test_counts_never_read_the_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(error_log, "_use_db", lambda: False)
    monkeypatch.setattr(error_log, "_path", lambda name: tmp_path / name)
    now = time.time()
    (tmp_path / "errors.jsonl").write_text(
        "".join(json.dumps({"ts": now, "source": "t", "message": f"e{i}"}) + "\n" for i in range(3)), encoding="utf-8"
    )
    (tmp_path / "activity.jsonl").write_text(
        json.dumps({"ts": now - 2 * error_log.MAX_AGE_S, "source": "t", "message": "old"}) + "\n"
        + json.dumps({"ts": now, "source": "t", "message": "new"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(error_log, "_scrub", lambda _t: pytest.fail("counting must not scrub rows"))
    assert error_log.counts() == {"errors": 3, "log": 1}


@pytest.fixture
def tk_root():
    if sys.platform != "win32":
        pytest.skip("desktop confetti is Windows only")
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    yield root
    root.destroy()
    # Tk objects must be freed on the thread that made them. Left for a later GC pass, one
    # ran on a background thread (perf_trace's report loop), Tcl panicked and killed the
    # test worker with 0x80000003, and the release's test step stalled (Oct 27).
    del root
    gc.collect()


def _pump(root, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(0.01)


def _overlays(root) -> list:
    return [w for w in root.winfo_children() if w.winfo_class() == "Toplevel"]


def test_a_failing_frame_still_closes_the_overlay(tk_root, monkeypatch):
    import tkinter as tk

    from frontend.ui_web import confetti_overlay as co

    def boom(*_a, **_k):
        raise tk.TclError("frame failed")

    monkeypatch.setattr(tk.Canvas, "create_rectangle", boom)
    co.burst_desktop_confetti(tk_root, 100, 100, count=5)
    _pump(tk_root, 0.3)
    assert _overlays(tk_root) == []


def test_the_overlay_is_click_through_and_ends_on_its_deadline(tk_root, monkeypatch):
    import ctypes

    from frontend.ui_web import confetti_overlay as co

    monkeypatch.setattr(co, "_MAX_LIFE_MS", 400)
    co.burst_desktop_confetti(tk_root, 100, 100, count=5)
    _pump(tk_root, 0.25)
    (win,) = _overlays(tk_root)
    frame = int(str(win.wm_frame()), 16)
    get_ex = getattr(ctypes.windll.user32, "GetWindowLongPtrW", None) or ctypes.windll.user32.GetWindowLongW
    get_ex.restype = ctypes.c_ssize_t
    assert int(get_ex(frame, -20)) & 0x20, "WS_EX_TRANSPARENT must be on the top-level window"
    _pump(tk_root, 0.4)
    assert _overlays(tk_root) == []
