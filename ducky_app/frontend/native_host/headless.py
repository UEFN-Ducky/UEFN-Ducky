"""``UEFN-Ducky.exe --backend``: the panel's server and API with no window, for Ducky-Launcher.exe.

The launcher draws the window (WebView2) and talks to this process in JSON lines, one UTF-8
object per line, at most 1 MB: stdout carries ``hello`` / ``result`` / ``event``, stdin
carries ``call`` / ``bye``, and stdin's end means shut down. Same port, same paths and the
same ``panel.pid`` as the window start. No pywebview and no Tk are imported here.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
import threading
import time
from typing import IO, Any, Callable
from urllib.parse import urlparse

BACKEND_FLAG = "--backend"
AFTER_UPDATE_FLAG = "--after-update"
PROTOCOL = 1
MAX_LINE_BYTES = 1024 * 1024
# A result over the line cap is parked here and answered with this error plus a token; the
# page (usePanelApi.ts) fetches it once from /__panel_large_result/<token>. Without it a
# plugin panel's big reply (a 1.5 MB road network) only said "message over 1048576 bytes".
LARGE_RESULT_PREFIX = "large result:"
LARGE_RESULT_TTL_S = 120.0
_large_results: dict[str, tuple[float, bytes]] = {}
_large_lock = threading.Lock()
# As the window: the deferred startup work waits until the page had its first calls.
BACKGROUND_AFTER_HELLO_S = 1.5
# No hello was sent: the launcher reports its own code (23 no hello / 24 start error) and
# starts the window the old way.
EXIT_START_FAILED = 1
EXIT_INSTANCE_BUSY = 3

_log = logging.getLogger(__name__)


def wants_backend(argv: list[str] | None = None) -> bool:
    args = sys.argv if argv is None else argv
    return BACKEND_FLAG in args[1:]


def take_backend_flag(argv: list[str]) -> bool:
    """Remove ``--backend`` from ``argv`` in place; True when it was there."""
    found = False
    while BACKEND_FLAG in argv[1:]:
        del argv[argv.index(BACKEND_FLAG, 1)]
        found = True
    return found


def take_after_update(argv: list[str]) -> int | None:
    """Remove ``--after-update <pid>`` from ``argv`` in place; the pid, or None."""
    if AFTER_UPDATE_FLAG not in argv[1:]:
        return None
    i = argv.index(AFTER_UPDATE_FLAG, 1)
    pid_text = argv[i + 1] if i + 1 < len(argv) else ""
    del argv[i : i + 2]
    try:
        return int(pid_text)
    except ValueError:
        return None


def encode(message: dict[str, Any]) -> bytes:
    """One protocol line. Raises ValueError over ``MAX_LINE_BYTES``."""
    data = (json.dumps(message, ensure_ascii=False, separators=(",", ":"), default=str) + "\n").encode("utf-8")
    if len(data) > MAX_LINE_BYTES:
        raise ValueError(f"message over {MAX_LINE_BYTES} bytes")
    return data


def park_large_result(value: Any) -> str:
    """Keep one oversized result (as the ``/__panel_api`` reply body) for a single fetch."""
    body = json.dumps({"ok": True, "result": value}, ensure_ascii=False, default=str).encode("utf-8")
    token = secrets.token_urlsafe(18)
    now = time.monotonic()
    with _large_lock:
        for key in [k for k, (at, _) in _large_results.items() if now - at > LARGE_RESULT_TTL_S]:
            del _large_results[key]
        _large_results[token] = (now, body)
    return token


def take_large_result(token: str) -> bytes | None:
    """The parked reply body for ``token``, once; None when unknown or expired."""
    with _large_lock:
        hit = _large_results.pop(str(token or ""), None)
    if hit is None or time.monotonic() - hit[0] > LARGE_RESULT_TTL_S:
        return None
    return hit[1]


def send_large_result(handler: Any, token: str, local: bool) -> None:
    """Answer ``GET /__panel_large_result/<token>`` on the panel UI server: this PC only, once."""
    body = take_large_result(token) if local else None
    if body is None:
        handler._send_json(404, {"ok": False, "error": "result expired"})
        return
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class LineWriter:
    """Thread-safe writer of protocol lines to the launcher (stdout)."""

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream
        self._lock = threading.Lock()

    def send(self, message: dict[str, Any]) -> bool:
        try:
            data = encode(message)
        except (TypeError, ValueError) as exc:
            if message.get("t") != "result":
                _log.warning("backend message not sent: %s", exc)
                return False
            error = str(exc)
            if message.get("ok") and isinstance(exc, ValueError):
                error = f"{LARGE_RESULT_PREFIX}{park_large_result(message.get('value'))} ({exc})"
            data = encode({"t": "result", "id": message.get("id"), "ok": False, "error": error})
        with self._lock:
            try:
                self._stream.write(data)
                self._stream.flush()
            except (OSError, ValueError):
                return False
        return True

    def event(self, name: str, data: dict[str, Any] | None = None) -> bool:
        return self.send({"t": "event", "name": name, "data": dict(data or {})})


def app_version() -> str:
    try:
        from frontend import __version__

        return str(__version__)
    except Exception:
        return "0"


def server_port(http_url: str) -> int:
    return int(urlparse(http_url).port or 0)


BACKEND_UI_PORT_ENV = "UEFN_DUCKY_BACKEND_UI_PORT"


def backend_ui_port() -> int | None:
    """The internal UI port the launcher asked for (0 = any free one); None = the usual port.
    Taken out of the environment so the processes this one starts never see it."""
    raw = (os.environ.pop(BACKEND_UI_PORT_ENV, None) or "").strip()
    if not raw:
        return None
    try:
        port = int(raw)
    except ValueError:
        _log.warning("%s=%r is not a port; using the usual one", BACKEND_UI_PORT_ENV, raw)
        return None
    return port if 0 <= port <= 65535 else None


def hello_message(*, port: int, profile_dir: str | None, pid: int | None = None, **extra: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "t": "hello",
        "protocol": PROTOCOL,
        "app_version": app_version(),
        "port": int(port),
        "origin": f"http://127.0.0.1:{int(port)}",
        "profile_dir": profile_dir or "",
        "pid": int(pid if pid is not None else os.getpid()),
    }
    message.update(extra)
    return message


def dispatch_call(api: Any, message: dict[str, Any]) -> dict[str, Any]:
    """One ``call`` to its ``result``: the API's public methods, as the window's js_api has them."""
    call_id = message.get("id")
    name = message.get("name")
    args = message.get("args")
    if args is None:
        args = []
    if not isinstance(name, str) or not name or name.startswith("_"):
        return {"t": "result", "id": call_id, "ok": False, "error": f"not a call: {name!r}"}
    fn = getattr(api, name, None)
    if not callable(fn):
        return {"t": "result", "id": call_id, "ok": False, "error": f"no such call: {name}"}
    if not isinstance(args, list):
        return {"t": "result", "id": call_id, "ok": False, "error": "args must be a list"}
    try:
        value = fn(*args)
    except Exception as exc:
        return {"t": "result", "id": call_id, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"t": "result", "id": call_id, "ok": True, "value": value}


def _std_binary(which: str) -> IO[bytes] | None:
    """The real stdin/stdout as bytes. A windowed EXE may have no ``sys.stdout`` even though
    the launcher gave it pipes, so fall back to the Windows standard handle."""
    stream = getattr(sys, f"__{which}__", None) or getattr(sys, which, None)
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        return buffer
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import msvcrt

        handle = ctypes.windll.kernel32.GetStdHandle(-10 if which == "stdin" else -11)
        if not handle or handle == -1:
            return None
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY if which == "stdin" else os.O_WRONLY)
        return os.fdopen(fd, "rb" if which == "stdin" else "wb", buffering=0)
    except Exception:
        return None


def _register_shell_integrations() -> None:
    # Shortcuts, Open with and uefn-ducky:// keep pointing at UEFN-Ducky.exe (this EXE).
    if os.environ.get("DUCKY_SANDBOX"):
        return
    try:
        from frontend.autostart import apply_saved_setting
        from frontend.open_files import register_windows_open_with, register_windows_url_protocol

        register_windows_open_with()
        register_windows_url_protocol()
        apply_saved_setting()
    except Exception:
        _log.warning("shell integrations not registered", exc_info=True)


def _start_auto_update(api: Any) -> None:
    """The background updater and the folder access check, as the Python window starts them
    (webview_app._after_gui_start). Missing here, launcher starts (every start since 1.2.478)
    never downloaded an update."""
    try:
        from frontend import update_swap
        from frontend.app_versions import take_handover

        update_swap.start(api=api, tk_root_holder={}, window_holder=api._window_holder)
        update_swap.continue_chats(take_handover().get("chats"))  # the chats the switch stopped
    except Exception:
        _log.warning("auto-update did not start", exc_info=True)
    try:
        from frontend import folder_access

        folder_access.check_soon()  # Windows Controlled folder access blocking Documents
    except Exception:
        pass


UEFN_LINK_POLL_S = 8.0


def _watch_uefn_link(api: Any, *, every_s: float = UEFN_LINK_POLL_S, rounds: int | None = None) -> None:
    """UEFN link changes for the usage counts (uefn.connect, the heartbeat's uefn_up), as the
    window's listener poll reports them. ``rounds`` is for tests."""
    from frontend import usage_stats
    from frontend.ui_web import webview_app as panel

    last: str | None = None
    done = 0
    while rounds is None or done < rounds:
        try:
            mode = panel.uefn_link_mode(api)
            if mode != last:
                last = mode
                usage_stats.uefn_mode(mode)
        except Exception:
            pass
        done += 1
        if rounds is None or done < rounds:
            time.sleep(every_s)


def _start_usage_stats(api: Any) -> None:
    """app.open, the stats thread (heartbeat, timed sends, crash hooks) and UEFN link changes,
    as the Python window starts them (webview_app._after_gui_start and its listener poll).
    Missing here, launcher starts (every start since 1.2.478) sent no app.open, heartbeat or
    uefn.connect and sent their queued rows only at a clean quit: a new PC read as
    "installed, never opened" (13 of 50 opened, Nov 5-11 2026)."""
    try:
        from frontend import usage_stats

        raw_t0 = os.environ.get("UEFN_DUCKY_BOOT_T0")
        boot_ms = (time.perf_counter() - float(raw_t0)) * 1000.0 if raw_t0 else None
        threading.Thread(
            target=usage_stats.app_opened, args=(boot_ms,), name="usage-stats-open", daemon=True
        ).start()
        threading.Thread(target=_watch_uefn_link, args=(api,), name="usage-uefn-link", daemon=True).start()
    except Exception:
        _log.warning("usage stats did not start", exc_info=True)


def _bind_notifications(writer: LineWriter) -> None:
    def _toast(title: str, message: str, on_click: Callable[[], None] | None = None) -> bool:
        return writer.event("toast", {"title": str(title or ""), "message": str(message or "")})

    try:
        from frontend import notifications

        notifications.bind_desktop(_toast, None)
    except Exception:
        _log.warning("desktop notifications did not bind", exc_info=True)


def _bind_open_handlers(writer: LineWriter) -> None:
    """A second launch's files and links go to the page's queue (the window shows them)."""
    try:
        from frontend.open_files import (
            enqueue_deep_links,
            enqueue_open_paths,
            set_deep_link_handler,
            set_open_files_handler,
        )

        set_open_files_handler(lambda paths: enqueue_open_paths(paths) if paths else None)
        set_deep_link_handler(lambda links: enqueue_deep_links(links) if links else None)
    except Exception:
        _log.warning("open-files handlers not set", exc_info=True)


def run_backend(
    argv: list[str] | None = None,
    *,
    stdin: IO[bytes] | None = None,
    stdout: IO[bytes] | None = None,
) -> int:
    """Start the panel's server and API, say hello, then serve the launcher until it is done."""
    use_sys_argv = argv is None
    args = list(sys.argv if argv is None else argv)
    take_backend_flag(args)
    from frontend.autostart import START_HIDDEN_ENV, take_background_flag

    if take_background_flag(args):
        os.environ[START_HIDDEN_ENV] = "1"
    previous_pid = take_after_update(args)
    if use_sys_argv:
        sys.argv[:] = args
    if previous_pid is not None:
        try:
            from frontend.app_versions import wait_for_exit

            wait_for_exit(previous_pid)
        except Exception:
            pass
    # Before panel_httpd reads the port, as the window start.
    try:
        from frontend.settings import pick_panel_port

        pick_panel_port()
    except Exception:
        pass

    out = stdout if stdout is not None else _std_binary("stdout")
    inp = stdin if stdin is not None else _std_binary("stdin")
    if out is None or inp is None:
        _log.error("--backend needs the launcher's stdin and stdout pipes")
        return EXIT_START_FAILED
    writer = LineWriter(out)
    try:
        from frontend import boot_report  # stdlib only, already loaded by the launch

        boot_report.on_stage(lambda name, ms: writer.event("stage", {"name": name, "ms": ms}))
    except Exception:
        pass
    if stdout is None:
        sys.stdout = sys.stderr  # a stray print never lands in the protocol stream

    from backend.store import db as store_db

    store_db.allow_rebuild()
    from frontend.open_files import cli_deep_links, cli_open_paths, enqueue_deep_links, enqueue_open_paths
    from frontend.native_host import lifecycle
    from frontend.ui_web import webview_app as panel

    open_paths = cli_open_paths(args)
    deep_links = cli_deep_links(args)
    state, living_pid = lifecycle.second_instance(open_paths, deep_links)
    if state == lifecycle.HANDED_OFF:
        writer.send(hello_message(port=_living_port(), profile_dir=panel.webview_profile_dir(),
                                  pid=living_pid, handoff=True))
        writer.event("quit", {"reason": "handoff", "pid": living_pid})
        return 0
    if state == lifecycle.BUSY:
        _log.warning("Ducky %s is running but did not take this launch", living_pid)
        return EXIT_INSTANCE_BUSY

    from frontend.bundle_root import is_packaged_runtime

    if is_packaged_runtime():
        from frontend.frozen_process import claim_panel_process

        claim_panel_process()
        threading.Thread(target=_register_shell_integrations, daemon=True, name="register-open-with").start()
    enqueue_open_paths(open_paths)
    enqueue_deep_links(deep_links)

    api: Any = None
    try:
        from backend.workspace.diff_workers import enable as enable_diff_workers

        enable_diff_workers()
        start_background = panel.make_background_work()
        api = panel.boot_panel_api()
        panel.install_workspace_services()
        # Python code that opens a file dialog itself finds this window: the launcher shows it.
        dialogs = lifecycle.DialogBroker(writer.event)
        window = lifecycle.BackendWindow(dialogs)
        api._tray = None
        # The launcher keeps "hidden" true to its window (lifecycle.WINDOW_HIDDEN_CALL): the
        # update switch waits less for a window in the tray and starts the next version there.
        api._window_holder = {"window": window, "hidden": os.environ.get(START_HIDDEN_ENV) == "1"}
        ending = lifecycle.Shutdown(api)

        def _on_exit() -> None:
            writer.event("quit", {"reason": "app"})
            ending.run("app")

        api.bind_window(window, on_hide=lambda: None, on_exit=_on_exit)
        _bind_open_handlers(writer)
        ui_port = backend_ui_port()
        url, _web_debug, bundled_http = panel.start_panel_server(ui_port=ui_port)
        # PanelApi._push drops every live chat event while no window is known
        # (focus_windows.all_windows()); without this the page never saw turns end or
        # question cards, and every chat sat on "Thinking about the next step".
        from frontend.ui_web import focus_windows

        focus_windows.configure(api=api, base_url=url.split("?")[0], main_window=window)
        # Browser panes: a WinForms form of this process inside the launcher's window (the
        # launcher is our parent). Nothing loads until the first pane opens.
        if sys.platform == "win32":
            from frontend.native_host.pane_host import PaneHost
            from frontend.ui_web import browser_overlay

            browser_overlay.configure(main_window=PaneHost(os.getppid()))
        port, extra = server_port(bundled_http), {}
        if ui_port is not None:
            # The launcher owns the public port (url, panel.pid, hand-off, IDE bridges keep it)
            # and proxies to this internal one.
            from frontend.ui_web.panel_httpd import panel_ui_bound_port

            port, extra = panel_ui_bound_port(), {"public_port": _living_port()}
        _bind_notifications(writer)
    except Exception:
        _log.exception("--backend did not start")
        return EXIT_START_FAILED

    writer.send(hello_message(port=port, profile_dir=panel.webview_profile_dir(), url=url, **extra))
    try:
        from frontend.ui_web.web_dev import is_dev_panel

        writer.event("title", {"title": "UEFN Ducky (Dev)" if is_dev_panel() else "UEFN Ducky"})
    except Exception:
        pass
    try:
        from frontend.app_versions import mark_ready

        mark_ready(app_version())
    except Exception:
        pass
    _start_auto_update(api)
    _start_usage_stats(api)
    try:
        from frontend import boot_report

        # The launcher shows the window now: this launch has started. Without it a native
        # session stayed "starting" in boot.json, and a crash read as "closed while starting".
        boot_report.stage("hello")
        boot_report.mark("ok")
        boot_report.ask_pending()  # the last launch's crash pop-up, kept until now (launcher.py)
    except Exception:
        pass
    try:
        from frontend import boot_report
        from frontend.ui_web.agent_turns.state import list_running_agents

        boot_report.start_heartbeat(lambda: len(list_running_agents()))
    except Exception:
        _log.warning("crash heartbeat did not start", exc_info=True)
    timer = threading.Timer(BACKGROUND_AFTER_HELLO_S, start_background)
    timer.daemon = True
    timer.start()

    lifecycle.watch_parent(os.getppid(), lambda: ending.run("parent_exited"))

    def _call(message: dict[str, Any]) -> None:
        threading.Thread(
            target=lambda: writer.send(dispatch_call(api, message)),
            daemon=True,
            name="backend-call",
        ).start()

    reason = lifecycle.serve(
        inp, on_call=_call, dialogs=dialogs, reply=writer.send,
        on_hidden=lambda hidden: api._window_holder.__setitem__("hidden", bool(hidden)),
    )
    ending.run(reason)
    return 0


def _living_port() -> int:
    from frontend.ui_web.panel_httpd import PANEL_UI_HTTP_PORT

    return int(PANEL_UI_HTTP_PORT)


def main(argv: list[str] | None = None) -> int:
    """The whole ``--backend`` start for an entry that skipped ``frontend.launcher.main``."""
    try:
        from frontend.launcher import scrub_pyinstaller_boot_env

        scrub_pyinstaller_boot_env()
    except Exception:
        pass
    from frontend import ssl_boot

    ssl_boot.inject_in_background()  # boot_panel_api waits for it
    try:
        from frontend import net_access

        net_access.install()
    except Exception:
        pass
    return run_backend(argv)
