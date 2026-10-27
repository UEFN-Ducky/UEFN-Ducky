"""Reload WebView2 after the *renderer* dies so the panel is not stuck black.

WebView2 paints ``WEBVIEW2_DEFAULT_BACKGROUND_COLOR`` (#0a0a0a) when the
renderer dies. Without ``ProcessFailed`` → Reload the window stays blank until
the user kills the EXE. Overlay browser panes that die while Visible sit on
top of the app and look the same.

Do **not** Reload on Gpu / Utility / Sandbox exits — those fire during tab
switches and compositor recycle. Reloading remounts React (Checking… forever,
every click stutters).

A busy page is not a dead one either: WebView2 raises RenderProcessUnresponsive
every few seconds while a long script runs or the PC is busy (UEFN compiling,
Fortnite running) and the page usually answers again on its own. Reloading on
the first one threw the whole UI away mid-work — "it freezes, then reloads
everything". Only a page that stays unresponsive for a full minute is reloaded.

A dead *browser* process is different: the WebView is closed for good, Reload and
Navigate do nothing, and the window stayed black until Ducky was killed (Oct 22 + 23
2026, after a WebView2 deadlock ran the browser out of memory). That window gets a
new WebView2 control instead; Python, agents and chats never stopped.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable

_log = logging.getLogger(__name__)

_DEBOUNCE_S = 3.0
_last_recover: dict[int, float] = {}
_attached: set[int] = set()

# CoreWebView2ProcessFailedKind: only these leave a dead document.
_RECOVER_KIND_NAMES = frozenset(
    {
        "browserprocessexited",
        "renderprocessexited",
    }
)
_RECOVER_KIND_INTS = frozenset({0, 1})
_UNRESPONSIVE_KIND = "renderprocessunresponsive"
_UNRESPONSIVE_KIND_INT = 2
# Reload a page only after it has stayed unresponsive this long.
_HUNG_RELOAD_AFTER_S = 60.0
# A pause this long between unresponsive events means the page answered in between.
_UNRESPONSIVE_GAP_S = 15.0
# Per WebView: (first, latest) unresponsive event of the current episode.
_unresponsive: dict[int, tuple[float, float]] = {}


def process_fail_kind(event: Any) -> str:
    """Normalize ProcessFailedKind to a short lower name or digit string."""
    raw = getattr(event, "ProcessFailedKind", "") if event is not None else ""
    try:
        return str(int(raw))
    except (TypeError, ValueError):
        pass
    name = getattr(raw, "name", None) or str(raw or "")
    return str(name).split(".")[-1].strip().lower()


def _kind_key(kind: str | int | None) -> str | int | None:
    if kind is None or kind is False:
        return None
    try:
        return int(kind)
    except (TypeError, ValueError):
        pass
    k = str(kind).split(".")[-1].strip().lower()
    return int(k) if k.isdigit() else k


def should_recover_process_fail(kind: str | int | None) -> bool:
    """True only when the document process is gone (not GPU/utility recycle,
    not a page that is merely busy)."""
    k = _kind_key(kind)
    if isinstance(k, int):
        return k in _RECOVER_KIND_INTS
    return k in _RECOVER_KIND_NAMES


def is_unresponsive_kind(kind: str | int | None) -> bool:
    k = _kind_key(kind)
    return k == _UNRESPONSIVE_KIND_INT or k == _UNRESPONSIVE_KIND


def is_browser_exit_kind(kind: str | int | None) -> bool:
    k = _kind_key(kind)
    return k == 0 or k == "browserprocessexited"


def hung_long_enough(key: int, now: float, *, label: str = "") -> bool:
    """One more RenderProcessUnresponsive for ``key``: True once the page has
    stayed unresponsive for _HUNG_RELOAD_AFTER_S without answering in between."""
    first, last = _unresponsive.get(key, (0.0, 0.0))
    if not first or now - last > _UNRESPONSIVE_GAP_S:
        first = now
        try:
            # Activity, not error: errors drop a repeat of the last line, and how
            # often the page goes busy is the thing worth seeing.
            from frontend.error_log import record_activity

            record_activity(
                "webview",
                f"WebView2 page busy ({label or 'unknown'}); waiting for it instead of reloading",
            )
        except Exception:
            pass
    _unresponsive[key] = (first, now)
    if now - first < _HUNG_RELOAD_AFTER_S:
        return False
    _unresponsive.pop(key, None)
    return True


_KIND_CODES = {"0": "browser_exit", "browserprocessexited": "browser_exit",
               "1": "render_exit", "renderprocessexited": "render_exit",
               "2": "hung", "renderprocessunresponsive": "hung"}


def error_source(reason: str) -> str:
    """The Errors-tab source (and anonymous error code) for a ``label:kind`` reason: which
    window died and how, e.g. ``webview_render_exit`` for the app window, ``webview_pane_hung``
    for a browser pane. All were "webview" (Oct 22 2026: 13 PCs in a week, no way to tell a
    dead app window from a website that crashed in the browser pane)."""
    label, _, kind = (reason or "").rpartition(":")
    if not label:  # a bare label, no kind
        label, kind = kind, ""
    where = "" if label in ("", "main") else "_pane" if label.startswith("pane") else "_window"
    how = _KIND_CODES.get(kind.strip().lower(), "")
    return "webview" + where + (f"_{how}" if how else "")


# CoreWebView2ProcessFailedEventArgs2.ExitCode values worth a name (Windows / Chromium).
_EXIT_NAMES = {0xE0000008: "oom", 0xC0000005: "access_violation", 0xC0000409: "fast_fail",
               0xC0000374: "heap_corruption", 0xC000001D: "illegal_instruction",
               0x80000003: "breakpoint", 0xC00000FD: "stack_overflow", 0xC0000017: "no_memory"}


def exit_reason(event: Any) -> str:
    """How the process ended, as a short slug (``oom``, ``access_violation``, ``x8000ffff``),
    or "" when the event has no exit code. webview_browser_exit on 3-5 PCs a day (Oct 2026)
    said that the browser died, never why."""
    try:
        code = int(getattr(event, "ExitCode")) & 0xFFFFFFFF
    except Exception:
        return ""
    return _EXIT_NAMES.get(code, f"x{code:08x}")


def _with_exit(source: str, exit: str) -> str:
    return f"{source}_{exit}" if exit else source


def _mark_crash_reload() -> None:
    """The page is about to load again after its process died: its next
    get_restore_tabs answers the chats it had open."""
    try:
        from frontend.ui_web.panel_api import mark_crash_reload

        mark_crash_reload()
    except Exception:
        _log.debug("mark_crash_reload failed", exc_info=True)


def recover_core_webview(core: Any, *, reason: str = "", exit: str = "") -> dict[str, Any]:
    """Reload (or re-Navigate) a live CoreWebView2. Debounced per instance."""
    if core is None:
        return {"ok": False, "error": "no core"}
    key = id(core)
    now = time.monotonic()
    if now - _last_recover.get(key, 0.0) < _DEBOUNCE_S:
        return {"ok": True, "action": "debounced"}
    _last_recover[key] = now
    try:
        from frontend.error_log import record_error

        how = f", exit {exit}" if exit else ""
        record_error(_with_exit(error_source(reason), exit), f"WebView2 process failed ({reason or 'unknown'}{how}); reloading")
    except Exception:
        pass
    if should_recover_process_fail(reason.rpartition(":")[2]):
        _mark_crash_reload()
    try:
        core.Reload()
        return {"ok": True, "action": "reload"}
    except Exception as exc:
        try:
            src = str(getattr(core, "Source", "") or "")
            if src:
                core.Navigate(src)
                return {"ok": True, "action": "navigate"}
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}


def attach_process_failed(
    core: Any,
    *,
    label: str = "main",
    on_fail: Callable[[str], None] | None = None,
    on_browser_exit: Callable[[str], None] | None = None,
) -> Any | None:
    """Subscribe ``ProcessFailed`` once. Keep the returned handler referenced.

    ``on_browser_exit`` replaces the Reload when the browser process itself died."""
    if core is None:
        return None
    key = id(core)
    if key in _attached:
        return None

    def _on_fail(_sender: Any, event: Any) -> None:
        kind = process_fail_kind(event)
        if is_unresponsive_kind(kind):
            if not hung_long_enough(key, time.monotonic(), label=label):
                return
        elif not should_recover_process_fail(kind):
            return
        _unresponsive.pop(key, None)
        reason = f"{label}:{kind}" if kind else label
        exit = exit_reason(event)
        if on_browser_exit is not None and is_browser_exit_kind(kind):
            try:
                from frontend.error_log import record_error

                how = f", exit {exit}" if exit else ""
                record_error(_with_exit(error_source(reason), exit),
                             f"WebView2 browser stopped ({reason}{how}); starting a new one")
            except Exception:
                pass
            _mark_crash_reload()  # the new control loads the page from scratch
            try:
                on_browser_exit(reason)
            except Exception:
                _log.exception("WebView2 rebuild request failed")
            return
        # Overlay panes: hide first so a dead control cannot cover the app.
        if on_fail is not None:
            try:
                on_fail(reason)
            except Exception:
                pass
        recover_core_webview(core, reason=reason, exit=exit)

    try:
        core.ProcessFailed += _on_fail
        _attached.add(key)
    except Exception:
        return None
    _on_fail.core_key = key  # type: ignore[attr-defined]
    return _on_fail


def forget_process_failed(handler: Any) -> None:
    """A replaced control's core: its id may be reused by the next core's wrapper."""
    key = getattr(handler, "core_key", None)
    if key is None:
        return
    _attached.discard(key)
    _last_recover.pop(key, None)
    _unresponsive.pop(key, None)


# ── A new WebView2 control after the browser process died ─────────────────────

# Give the dead browser's children a moment to exit before the profile is opened again.
_REBUILD_DELAY_S = 1.5
_REBUILD_RETRY_S = 3.0
_REBUILD_TRIES = 6
# A control whose CoreWebView2InitializationCompleted never comes counts as a failed try.
_REBUILD_INIT_TIMEOUT_S = 30.0


class _Rebuild:
    __slots__ = ("form", "attempt", "waiting")

    def __init__(self, form: Any) -> None:
        self.form = form
        self.attempt = 0
        self.waiting = False


_rebuild_lock = threading.Lock()
# One window at a time, the main window first: overlapping WebView2 boots on one
# profile freeze WinForms (see browser_overlay).
_rebuild_queue: list[_Rebuild] = []
_rebuild_busy = False


def _form_label(form: Any) -> str:
    window = getattr(form, "pywebview_window", None)
    try:
        from frontend.ui_web.focus_windows import is_main_window

        if is_main_window(window):
            return "main"
    except Exception:
        pass
    return f"window:{getattr(window, 'title', '') or '?'}"


def request_browser_rebuild(form: Any) -> None:
    """Queue a new WebView2 control for this pywebview form (any thread)."""
    global _rebuild_busy
    # Its browser is gone: no more Invokes into the dying control (0x80000003 on 1.2.427 was
    # pywebview injecting its JS API into one). The new control gets its own EdgeChrome.
    dead = getattr(form, "browser", None)
    if dead is not None:
        try:
            dead._ducky_dead = True
        except Exception:
            pass
        release_js_waiters(dead)
    with _rebuild_lock:
        if any(job.form is form for job in _rebuild_queue):
            return
        _rebuild_queue.append(_Rebuild(form))
        _rebuild_queue.sort(key=lambda job: _form_label(job.form) != "main")
        if _rebuild_busy:
            return
        _rebuild_busy = True
    _schedule_next_rebuild(_REBUILD_DELAY_S)


def _later(delay_s: float, fn: Callable[..., None], *args: Any) -> None:
    timer = threading.Timer(delay_s, fn, args=args)
    timer.daemon = True
    timer.start()


def _invoke_on_form(form: Any, fn: Callable[[], Any]) -> None:
    from System import Action

    form.Invoke(Action(fn))


def _schedule_next_rebuild(delay_s: float) -> None:
    global _rebuild_busy
    with _rebuild_lock:
        if not _rebuild_queue:
            _rebuild_busy = False
            return
    _later(delay_s, _run_next_rebuild)


def _run_next_rebuild() -> None:
    with _rebuild_lock:
        if not _rebuild_queue:
            return
        job = _rebuild_queue[0]
        job.attempt += 1
        job.waiting = True
        form, attempt = job.form, job.attempt
    try:
        if bool(getattr(form, "IsDisposed", False)):
            raise RuntimeError("window closed")
        _invoke_on_form(form, lambda: rebuild_browser(form, attempt))
    except Exception:
        _log.exception("WebView2 rebuild failed (%s, try %s)", _form_label(form), attempt)
        _rebuild_settled(form, attempt, False)
        return
    _later(_REBUILD_INIT_TIMEOUT_S, _rebuild_settled, form, attempt, False)


def rebuild_browser(form: Any, attempt: int = 0) -> Any:
    """WinForms UI thread: swap the form's dead WebView2 control for a new one.

    pywebview's EdgeChrome (with Ducky's patches) builds it, so the page, the JS
    bridge and the ProcessFailed hook come back exactly as at startup."""
    from webview.platforms import edgechromium, winforms

    old = getattr(form, "browser", None)
    release_js_waiters(old)
    forget_process_failed(getattr(old, "_ducky_process_failed_handler", None))
    old_control = getattr(form, "webview", None)
    if old_control is not None:
        try:
            form.Controls.Remove(old_control)
        except Exception:
            pass
        try:
            old_control.Dispose()
        except Exception:
            pass
    window = getattr(form, "pywebview_window", None)
    browser = edgechromium.EdgeChrome(form, window, winforms.cache_dir)
    form.browser = browser
    form.webview = browser.webview
    try:
        # Browser panes hosted on this form stay above the page.
        browser.webview.SendToBack()
    except Exception:
        pass

    def _ready(_sender: Any, args: Any) -> None:
        _rebuild_settled(form, attempt, bool(getattr(args, "IsSuccess", False)))

    browser._ducky_rebuild_ready = _ready
    browser.webview.CoreWebView2InitializationCompleted += _ready
    try:
        from frontend.ui_web.win_frameless import reset_webview_pin

        reset_webview_pin(window)
    except Exception:
        pass
    return browser


def _rebuild_settled(form: Any, attempt: int, ok: bool) -> None:
    """One try's outcome. A late or repeated outcome of the same try is ignored."""
    with _rebuild_lock:
        job = next((j for j in _rebuild_queue if j.form is form), None)
        if job is None or job.attempt != attempt or not job.waiting:
            return
        job.waiting = False
        closed = bool(getattr(form, "IsDisposed", False))
        final = ok or closed or attempt >= _REBUILD_TRIES
        if final:
            _rebuild_queue.remove(job)
    if not final:
        _schedule_next_rebuild(_REBUILD_RETRY_S)
        return
    label = _form_label(form)
    try:
        from frontend.error_log import record_activity, record_error

        if ok:
            record_activity("webview", f"WebView2 started again after its browser stopped ({label})")
        elif not closed:
            record_error(error_source(f"{label}:0"), f"WebView2 could not start again ({label}); restart Ducky")
    except Exception:
        pass
    _schedule_next_rebuild(0.2)


# ── evaluate_js that never waits forever ─────────────────────────────────────


def release_js_waiters(browser: Any) -> None:
    """Free every evaluate_js still waiting on this browser (it answers None)."""
    for done in list(getattr(browser, "_ducky_js_waiters", None) or ()):
        try:
            done.release()
        except Exception:
            pass


def _net_types() -> tuple[Any, Any, Any, Any, Any]:
    from System import Action, Func, Object, String
    from System.Threading.Tasks import Task

    return Action, Func, Object, String, Task


def install_evaluate_js_release(edge_chrome_cls: Any) -> None:
    """pywebview's EdgeChrome.evaluate_js, except no caller waits forever: a script whose
    task faults (dead browser) answers None, and release_js_waiters frees the rest. Its
    callback read ``task.Result`` unguarded, so a faulted task never released the wait and
    the window's whole dispatch queue stopped behind it."""
    if getattr(edge_chrome_cls, "_ducky_js_release", False):
        return

    def evaluate_js(self: Any, script: str, parse_json: bool) -> Any:
        if self.__dict__.get("_ducky_dead"):
            return None  # its browser stopped; a rebuild is replacing this control
        Action, Func, Object, String, Task = _net_types()
        done = threading.Semaphore(0)
        out: list[Any] = [None]

        def _callback(task: Any) -> None:
            try:
                res = json.loads(task.Result)
                if parse_json and res is not None:
                    try:
                        res = json.loads(res)
                    except Exception:
                        pass
                out[0] = res
            except Exception:
                pass
            finally:
                done.release()

        waiters = self.__dict__.setdefault("_ducky_js_waiters", set())
        waiters.add(done)
        try:
            self.webview.Invoke(
                Func[Object](
                    lambda: self.webview.ExecuteScriptAsync(script).ContinueWith(
                        Action[Task[String]](_callback), self.syncContextTaskScheduler
                    )
                )
            )
            done.acquire()
        except Exception:
            _log.exception("Error occurred in script")
        finally:
            waiters.discard(done)
        return out[0]

    edge_chrome_cls.evaluate_js = evaluate_js
    edge_chrome_cls._ducky_js_release = True


def nudge_webview_visible(window: Any) -> None:
    """After hide-to-tray, make the WinForms WebView2 paint again.

    Occlusion can drop the compositor; a black DefaultBackgroundColor pane is
    left until Visible/Refresh. Does not Reload (that remounts React).
    """
    native = getattr(window, "native", None)
    ctrl = getattr(native, "webview", None) if native is not None else None
    if ctrl is None:
        return

    def _go() -> None:
        try:
            ctrl.Visible = True
        except Exception:
            pass
        try:
            ctrl.Refresh()
        except Exception:
            pass

    try:
        from frontend.ui_web.win_frameless import _run_on_form_ui

        _run_on_form_ui(native, _go)
    except Exception:
        try:
            _go()
        except Exception:
            pass
