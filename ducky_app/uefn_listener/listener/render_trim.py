"""Let the editor give render memory back after off-screen work.

A viewport capture (Epic's ``EditorAppToolset.CaptureViewport``) allocates large
transient render targets. Unreal's render-target pool frees an element only after
it has gone unused for a few dozen *rendered* frames, and the pool is ticked only
while a viewport draws. Editor viewports are not realtime by default, so after a
capture nothing draws and the buffers stay pinned: measured +700-800 MB of process
memory per capture on a dense island, never returned on its own (a forced garbage
collection frees nothing — these are not UObjects). Redrawing the viewport ~60
times returned all of it (+36 MB left over).

So after a capture, and after a batch of heavy editor commands, the Slate tick asks
the level viewports to redraw once per tick for a burst. The burst ends when UEFN's
private bytes are back within 100 MB of where they were before the work (and at
least ``TRIM_TICKS`` redraws were asked for), or after 30 s of wall time: a tick
count alone is not enough, because a throttled background editor draws few of them.

While UEFN is in the background the burst also turns on a draw-forcing override
(viewport realtime, or the editor's background CPU throttle off) and restores the
person's value when the burst ends; the saved setting is never written. While UEFN
is minimized nothing draws at all, so the burst waits until the window is restored.
"""

from __future__ import annotations

import os
import time

import unreal

#: Minimum redraws per burst. The pool keeps an element for ~30 unused frames;
#: 60 draws covers that with margin and costs about a second of viewport rendering.
TRIM_TICKS = 60
#: Wall-time cap of one burst (restarts when a minimized editor is restored).
MAX_BURST_SEC = 30.0
#: "Memory is back" means within this much of the baseline.
BACK_WITHIN_BYTES = 100 * 1024 * 1024

_MB = 1024 * 1024

#: The running burst, or None when idle.
_burst: dict | None = None


def _default_read_bytes() -> int:
    try:
        from listener.memory_ledger import private_bytes
    except Exception:
        return 0
    return private_bytes()


# Seams (tests replace these).
clock = time.monotonic
read_bytes = _default_read_bytes


def _log(msg: str, level: str = "info") -> None:
    try:
        from listener.logutil import log_msg

        log_msg(msg, level)
    except Exception:
        pass


def request_trim(ticks: int = TRIM_TICKS, baseline: int | None = None) -> None:
    """Ask for a redraw burst. Overlapping requests extend, never stack.

    ``baseline``: UEFN's private bytes before the work that grew it (the tick reads
    it before dispatch). Without one, the bytes at the request are the baseline.
    """
    global _burst
    now_bytes = 0
    try:
        now_bytes = int(read_bytes() or 0)
    except Exception:
        now_bytes = 0
    base = int(baseline) if baseline else now_bytes
    deadline = clock() + MAX_BURST_SEC
    if _burst is None:
        _burst = {
            "remaining": int(ticks),
            "baseline": base,
            "start_bytes": now_bytes,
            "started": clock(),
            "deadline": deadline,
            "parked": False,
            "override": None,
            "background": False,
        }
        return
    _burst["remaining"] = max(_burst["remaining"], int(ticks))
    if base and (not _burst["baseline"] or base < _burst["baseline"]):
        _burst["baseline"] = base
    _burst["start_bytes"] = max(_burst["start_bytes"], now_bytes)
    _burst["deadline"] = max(_burst["deadline"], deadline)


def pending() -> int:
    """Redraws still owed before the burst may end (0 when idle)."""
    if _burst is None:
        return 0
    return max(1, _burst["remaining"])  # waiting on memory still counts as one


def active() -> bool:
    return _burst is not None


def tick() -> None:
    """One redraw per editor tick while a burst is pending; silent when idle."""
    global _burst
    b = _burst
    if b is None:
        return
    try:
        state = window_state()
    except Exception:
        state = "foreground"
    if state == "minimized":
        if not b["parked"]:
            b["parked"] = True
            _restore_override(b)
            _log("Render trim waiting: UEFN minimized")
        return
    if b["parked"]:
        b["parked"] = False
        b["deadline"] = clock() + MAX_BURST_SEC
    try:
        if state == "background":
            b["background"] = True
            if b["override"] is None:
                b["override"] = _force_draw_override() or False
        _invalidate_viewports()
    except Exception as exc:  # no level viewport (hub), editor closing
        _restore_override(b)
        _burst = None
        _log(f"Render trim stopped: {exc}", "warn")
        return
    if b["remaining"] > 0:
        b["remaining"] -= 1
    try:
        now_bytes = int(read_bytes() or 0)
    except Exception:
        now_bytes = 0
    back = now_bytes <= 0 or now_bytes <= b["baseline"] + BACK_WITHIN_BYTES
    timed_out = clock() >= b["deadline"]
    if (b["remaining"] <= 0 and back) or timed_out:
        _finish(b, now_bytes, timed_out and not back)


def _finish(b: dict, now_bytes: int, timed_out: bool) -> None:
    global _burst
    _restore_override(b)
    _burst = None
    if not now_bytes or not b["baseline"]:
        return
    grew = (b["start_bytes"] - b["baseline"]) / _MB
    left = (now_bytes - b["baseline"]) / _MB
    secs = clock() - b["started"]
    notes = []
    if b["background"]:
        notes.append("background")
    if b.get("override_name"):
        notes.append(b["override_name"])
    if timed_out:
        notes.append("30 s cap")
    tail = f" ({', '.join(notes)})" if notes else ""
    _log(f"Render trim: {grew:+.0f} MB → {left:+.0f} MB in {secs:.1f} s{tail}")


# ---------------------------------------------------------------- overrides


def _override_candidates():
    """(name, getter, setter) for each draw-forcing switch this UEFN exposes."""
    out = []
    for owner_name in ("LevelEditorSubsystem", "UnrealEditorSubsystem"):
        cls = getattr(unreal, owner_name, None)
        if cls is None:
            continue
        try:
            sub = unreal.get_editor_subsystem(cls)
        except Exception:
            sub = None
        if sub is None:
            continue
        get = getattr(sub, "editor_get_viewport_realtime", None)
        put = getattr(sub, "editor_set_viewport_realtime", None)
        if callable(get) and callable(put):
            out.append(("realtime override", get, put))
    lib = getattr(unreal, "EditorLevelLibrary", None)
    get = getattr(lib, "editor_get_viewport_realtime", None)
    put = getattr(lib, "editor_set_viewport_realtime", None)
    if callable(get) and callable(put):
        out.append(("realtime override", get, put))
    perf_cls = getattr(unreal, "EditorPerformanceSettings", None)
    get_default = getattr(unreal, "get_default_object", None)
    if perf_cls is not None and callable(get_default):
        try:
            perf = get_default(perf_cls)
        except Exception:
            perf = None
        if perf is not None:
            prop = "throttle_cpu_when_not_foreground"
            # Forcing a draw means the throttle *off*: invert so True = "draw".
            out.append(
                (
                    "background throttle off",
                    lambda: not bool(perf.get_editor_property(prop)),
                    lambda on: perf.set_editor_property(prop, not bool(on)),
                )
            )
    return out


def _force_draw_override():
    """Turn on the first available draw-forcing switch; return how to undo it."""
    for name, get, put in _override_candidates():
        try:
            was = bool(get())
            if was:
                return None  # already drawing: nothing to change, nothing to restore
            put(True)
        except Exception:
            continue
        if _burst is not None:
            _burst["override_name"] = name
        return lambda put=put, was=was: put(was)
    return None


def _restore_override(b: dict) -> None:
    undo = b.get("override")
    b["override"] = None
    if callable(undo):
        try:
            undo()
        except Exception as exc:
            _log(f"Render trim: could not restore the viewport setting: {exc}", "warn")


# ------------------------------------------------------------- window state


def window_state() -> str:
    """'foreground', 'background' or 'minimized' for this editor process."""
    if os.name != "nt":
        return "foreground"
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    pid = os.getpid()
    owner = wintypes.DWORD()
    fg = user32.GetForegroundWindow()
    if fg:
        user32.GetWindowThreadProcessId(fg, ctypes.byref(owner))
        if owner.value == pid:
            return "foreground"
    mine: list = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _each(hwnd, _lp):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # GW_OWNER
            mine.append(hwnd)
        return True

    user32.EnumWindows(proc(_each), 0)
    if mine and all(user32.IsIconic(h) for h in mine):
        return "minimized"
    return "background"


def _invalidate_viewports() -> None:
    sub = None
    try:
        sub = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    except Exception:
        sub = None
    if sub is not None and hasattr(sub, "editor_invalidate_viewports"):
        sub.editor_invalidate_viewports()
        return
    unreal.EditorLevelLibrary.editor_invalidate_viewports()
