"""How a ``--backend`` process ends, and how it starts beside a Ducky already running.

* stdin's end or a ``bye`` line runs the app's own Exit (``ui_web/shutdown.py``).
* The launcher (parent) gone, the backend exits the same way.
* A second ``--backend`` passes its launch to the Ducky in ``panel.pid`` and never kills it.
"""

from __future__ import annotations

import json
import logging
import threading
import time
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


def serve(stream: IO[bytes], *, on_call: Callable[[dict[str, Any]], None]) -> str:
    """Hand each ``call`` to ``on_call`` until a ``bye`` or stdin's end; return why it ended."""
    for message in read_messages(stream):
        kind = message.get("t")
        if kind == "call":
            try:
                on_call(message)
            except Exception:
                _log.warning("backend call failed to start", exc_info=True)
        elif kind == "bye":
            return f"bye:{message.get('reason') or ''}".rstrip(":")
    return "stdin_eof"


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
                return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
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
    from frontend.ui_web.webview_app import _ducky_process_alive

    return _ducky_process_alive(pid)


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
