"""How a ``--backend`` process ends, and how it starts beside a Ducky already running.

* stdin's end or a ``bye`` line runs the app's own Exit (``ui_web/shutdown.py``).
* The launcher (parent) gone, the backend exits the same way.
* A second ``--backend`` passes its launch to the Ducky in ``panel.pid`` and never kills it.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from typing import IO, Any

MAX_LINE_BYTES = 1024 * 1024
PARENT_POLL_S = 1.0
# As the window start (webview_app._LIVING_PANEL_WAIT_S): a Ducky still starting answers late.
LIVING_PANEL_WAIT_S = 45.0
# request_app_exit ends the process from its own threads; this is only the wait for it.
EXIT_WAIT_S = 10.0

FIRST = "first"
HANDED_OFF = "handed_off"
BUSY = "busy"

_log = logging.getLogger(__name__)


def read_messages(stream: IO[bytes]) -> Iterator[dict[str, Any]]:
    """The launcher's messages until stdin ends. Blank, broken and oversize lines are skipped."""
    while True:
        try:
            line = stream.readline(MAX_LINE_BYTES + 1)
        except (OSError, ValueError):
            return
        if not line:
            return
        if len(line) > MAX_LINE_BYTES:
            # The rest of an oversize line is dropped up to its newline.
            while line and not line.endswith(b"\n"):
                try:
                    line = stream.readline(MAX_LINE_BYTES)
                except (OSError, ValueError):
                    return
            continue
        text = line.strip()
        if not text:
            continue
        try:
            message = json.loads(text.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            _log.warning("backend: not a JSON line from the launcher")
            continue
        if isinstance(message, dict):
            yield message


def serve(
    stream: IO[bytes],
    *,
    on_call: Callable[[dict[str, Any]], None],
    dialogs: DialogBroker | None = None,
    reply: Callable[[dict[str, Any]], Any] | None = None,
    on_hidden: Callable[[bool], None] | None = None,
) -> str:
    """Hand each ``call`` to ``on_call`` until a ``bye`` or stdin's end; return why it ended.

    A ``__window_hidden [bool]`` call is the launcher saying its main window went to the tray
    or came back: it goes to ``on_hidden`` (the update switch reads it), never to ``on_call``.

    A ``__dialog_result`` call is the launcher's answer to a ``dialog`` event: it goes to the
    waiting dialog (``dialogs``), never to ``on_call``. Waiting dialogs end (None) with serve."""
    try:
        for message in read_messages(stream):
            kind = message.get("t")
            if kind == "call" and message.get("name") == DIALOG_RESULT_CALL:
                args = message.get("args")
                value = args[0] if isinstance(args, list) and args else None
                found = dialogs.answer(message.get("id"), value) if dialogs is not None else False
                if reply is not None:
                    try:
                        reply({"t": "result", "id": message.get("id"), "ok": True, "value": found})
                    except Exception:
                        pass
            elif kind == "call" and message.get("name") == WINDOW_HIDDEN_CALL:
                args = message.get("args")
                if on_hidden is not None:
                    try:
                        on_hidden(bool(args[0]) if isinstance(args, list) and args else False)
                    except Exception:
                        _log.warning("backend: the window's hidden state was not taken", exc_info=True)
                if reply is not None:
                    try:
                        reply({"t": "result", "id": message.get("id"), "ok": True, "value": on_hidden is not None})
                    except Exception:
                        pass
            elif kind == "call" and message.get("name") == "__menu_result":
                from frontend.native_host.launcher_windows import answer_menu

                found = answer_menu(message.get("args"))
                if reply is not None:
                    try:
                        reply({"t": "result", "id": message.get("id"), "ok": True, "value": found})
                    except Exception:
                        pass
            elif kind == "call":
                try:
                    on_call(message)
                except Exception:
                    _log.warning("backend call failed to start", exc_info=True)
            elif kind == "bye":
                return f"bye:{message.get('reason') or ''}".rstrip(":")
        return "stdin_eof"
    finally:
        if dialogs is not None:
            dialogs.cancel_all()


# --- file dialogs: the launcher owns the window, so it shows them -------------------------

DIALOG_RESULT_CALL = "__dialog_result"
# The launcher's main window went to the tray (True) or came back (False).
WINDOW_HIDDEN_CALL = "__window_hidden"
DIALOG_TIMEOUT_S = 600.0
# pywebview's FileDialog values (OPEN, FOLDER, SAVE).
DIALOG_KINDS = {10: "open", 20: "folder", 30: "save"}


class _DialogWaiter:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None


class DialogBroker:
    """One ``dialog`` event out, then a wait for its ``__dialog_result`` (None after the timeout)."""

    def __init__(
        self,
        send_event: Callable[[str, dict[str, Any]], bool],
        *,
        timeout_s: float = DIALOG_TIMEOUT_S,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self._send_event = send_event
        self._timeout_s = timeout_s
        self._new_id = new_id or (lambda: f"dlg-{uuid.uuid4().hex}")
        self._lock = threading.Lock()
        self._waiting: dict[str, _DialogWaiter] = {}
        self._closed = False

    def ask(self, args: dict[str, Any]) -> Any:
        dialog_id = self._new_id()
        waiter = _DialogWaiter()
        with self._lock:
            if self._closed:
                return None
            self._waiting[dialog_id] = waiter
        try:
            if not self._send_event("dialog", {"id": dialog_id, "args": args}):
                return None
            if not waiter.done.wait(self._timeout_s):
                _log.warning("file dialog %s: no answer in %.0f s", dialog_id, self._timeout_s)
                return None
            return waiter.value
        finally:
            with self._lock:
                self._waiting.pop(dialog_id, None)

    def answer(self, dialog_id: object, value: Any) -> bool:
        """True when a dialog was waiting for this id."""
        with self._lock:
            waiter = self._waiting.get(dialog_id) if isinstance(dialog_id, str) else None
        if waiter is None:
            return False
        waiter.value = value
        waiter.done.set()
        return True

    def cancel_all(self) -> None:
        """The launcher is gone: every waiting dialog returns None now."""
        with self._lock:
            self._closed = True
            waiters = list(self._waiting.values())
        for waiter in waiters:
            waiter.done.set()


def _dialog_type(value: object) -> int:
    try:
        return int(getattr(value, "value", value))  # pywebview's FileDialog enum or a plain int
    except (TypeError, ValueError):
        return 10


def _pywebview_shape(kind: str, value: Any) -> Any:
    """The launcher's answer as pywebview returns it: a tuple of paths, or None for cancel."""
    if value is None or value == "" or value == []:
        return None
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        paths = tuple(str(v) for v in value if v)
        return paths or None
    return None


class _EventSink:
    """``window.events.<name> += handler`` does nothing: the launcher has its own window."""

    def __getattr__(self, _name: str) -> _EventSink:
        return self

    def __iadd__(self, _handler: object) -> _EventSink:
        return self

    def __isub__(self, _handler: object) -> _EventSink:
        return self


class BackendWindow:
    """The window object Python code finds in ``--backend`` mode. ``create_file_dialog``
    asks the launcher; every other window call is a no-op returning None."""

    def __init__(self, dialogs: DialogBroker) -> None:
        self._dialogs = dialogs
        self.events = _EventSink()

    def create_file_dialog(
        self,
        dialog_type: object = 10,
        directory: str = "",
        allow_multiple: bool = False,
        save_filename: str = "",
        file_types: tuple[str, ...] | list[str] = (),
    ) -> Any:
        kind_id = _dialog_type(dialog_type)
        kind = DIALOG_KINDS.get(kind_id, "open")
        args = {
            "dialog_type": kind_id,
            "kind": kind,
            "directory": str(directory or ""),
            "allow_multiple": bool(allow_multiple),
            "save_filename": str(save_filename or ""),
            "file_types": [str(t) for t in (file_types or ())],
        }
        return _pywebview_shape(kind, self._dialogs.ask(args))

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *_a, **_k: None


def _app_exit(*, api: Any, tk_root: Any, window_holder: dict[str, object]) -> None:
    from frontend.ui_web.shutdown import request_app_exit

    request_app_exit(api=api, tk_root=tk_root, window_holder=window_holder)


class Shutdown:
    """The one graceful exit, whichever asks first (stdin, bye, parent gone, the app's Exit)."""

    def __init__(
        self,
        api: Any,
        *,
        exit_fn: Callable[..., None] = _app_exit,
        wait_s: float = EXIT_WAIT_S,
    ) -> None:
        self._api = api
        self._exit_fn = exit_fn
        self._wait_s = wait_s
        self._lock = threading.Lock()
        self.reason: str | None = None

    def run(self, reason: str) -> bool:
        """Start the exit; True for the first caller. Waits ``wait_s`` for the process to end."""
        with self._lock:
            if self.reason is not None:
                return False
            self.reason = reason
        _log.info("backend exiting: %s", reason)
        try:
            self._exit_fn(api=self._api, tk_root=None, window_holder={})
        except Exception:
            _log.warning("backend exit path failed", exc_info=True)
            # The exit path marks the run closed itself (abort_and_quit); it failed first.
            try:
                from frontend import usage_stats

                usage_stats.app_closing()
            except Exception:
                pass
        if self._wait_s > 0:
            time.sleep(self._wait_s)
        return True


def parent_alive_check(pid: int) -> Callable[[], bool]:
    """A check that ``pid`` is still the process it was now (a reused pid reads as gone)."""
    if pid <= 0:
        return lambda: False
    try:
        import psutil

        try:
            proc = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return lambda: False

        def _alive() -> bool:
            try:
                if not proc.is_running():
                    return False
                # Windows has no zombies, and psutil's status() there walks every thread of
                # every process: 3% of the app's CPU for this one poll (Nov 11 2026).
                return sys.platform == "win32" or proc.status() != psutil.STATUS_ZOMBIE
            except psutil.Error:
                return False

        return _alive
    except ImportError:
        from frontend.open_files import panel_process_alive

        return lambda: panel_process_alive(pid)


def watch_parent(
    pid: int,
    on_dead: Callable[[], None],
    *,
    alive: Callable[[], bool] | None = None,
    interval_s: float = PARENT_POLL_S,
    stop: threading.Event | None = None,
) -> threading.Thread | None:
    """Call ``on_dead`` once when the launcher process ``pid`` is gone. None for no parent."""
    if pid <= 1:
        return None
    check = alive or parent_alive_check(pid)
    halt = stop or threading.Event()

    def _watch() -> None:
        while not halt.is_set():
            if not check():
                on_dead()
                return
            halt.wait(interval_s)

    thread = threading.Thread(target=_watch, daemon=True, name="backend-parent-watch")
    thread.start()
    return thread


def _default_read_pid() -> int | None:
    from frontend.open_files import read_living_panel_pid

    return read_living_panel_pid()


def _default_alive(pid: int) -> bool:
    from frontend.ui_web.webview_app import ducky_process_alive

    return ducky_process_alive(pid)


def _default_handoff(paths: list[str], links: list[str], first: bool) -> bool:
    from frontend.open_files import try_handoff_to_running

    if first:
        return bool(try_handoff_to_running(paths, links=links))
    return bool(try_handoff_to_running(paths, links=links, retries=1, timeout_s=2.0))


def second_instance(
    open_paths: list[str],
    deep_links: list[str],
    *,
    read_pid: Callable[[], int | None] = _default_read_pid,
    alive: Callable[[int], bool] = _default_alive,
    handoff: Callable[[list[str], list[str], bool], bool] = _default_handoff,
    wait_s: float = LIVING_PANEL_WAIT_S,
    clock: Callable[[], float] = time.monotonic,
    own_pid: int | None = None,
) -> tuple[str, int | None]:
    """The panel.pid rule for ``--backend``: ``(FIRST, None)`` when no Ducky runs,
    ``(HANDED_OFF, pid)`` when the running one took this launch, ``(BUSY, pid)`` when it is
    alive but never answered. It never kills: a busy Ducky stays, the launcher falls back."""
    import os

    living = read_pid()
    if living is None or living == (own_pid if own_pid is not None else os.getpid()):
        return FIRST, None
    if not alive(living):
        return FIRST, None
    handed = handoff(open_paths, deep_links, True)
    deadline = clock() + wait_s
    while not handed and clock() < deadline and alive(living):
        handed = handoff(open_paths, deep_links, False)
    if handed:
        return HANDED_OFF, living
    return (BUSY, living) if alive(living) else (FIRST, None)
