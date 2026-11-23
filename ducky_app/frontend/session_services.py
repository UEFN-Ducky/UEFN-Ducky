"""The services every app start runs once the panel is up, window or ``--backend`` alike.

The Python window (``webview_app._after_gui_start``) and the launcher's backend
(``native_host.headless.run_backend``) both call :func:`start_session_services`, so a
service added here starts on both paths. Window-only work (Tk pump, tray) stays in
``webview_app``. Each step logs its own failure and never stops the next one.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any, Callable

_log = logging.getLogger(__name__)


def _mark_ready(ctx: dict[str, Any]) -> None:
    from frontend import __version__
    from frontend import app_versions

    app_versions.mark_ready(str(__version__))
    if sys.platform.startswith("linux"):
        _confirm_appimage_swap_at_page_started(str(__version__))


def _confirm_appimage_swap_at_page_started(version: str) -> None:
    """A swapped-in AppImage counts as good only once its page ran (boot stage page_started),
    not when the backend booted: a build whose window never opens is rolled back."""
    from frontend import app_versions, boot_report

    def on_stage(name: str, _ms: int) -> None:
        if name == "page_started":
            app_versions.confirm_appimage_swap(version)

    boot_report.on_stage(on_stage)
    if boot_report._page.get("started"):  # the page beat this step (backend-only start)
        app_versions.confirm_appimage_swap(version)


def _update_swap(ctx: dict[str, Any]) -> None:
    from frontend import update_swap

    update_swap.start(api=ctx["api"], tk_root_holder=ctx["tk_root_holder"], window_holder=ctx["window_holder"])
    update_swap.continue_chats((ctx["handover"] or {}).get("chats"))  # the chats the switch stopped


def _folder_access(ctx: dict[str, Any]) -> None:
    from frontend import folder_access

    folder_access.check_soon()  # Windows Controlled folder access blocking Documents


def _usage_stats(ctx: dict[str, Any]) -> None:
    from frontend import usage_stats

    raw_t0 = os.environ.get("UEFN_DUCKY_BOOT_T0")
    boot_ms = (time.perf_counter() - float(raw_t0)) * 1000.0 if raw_t0 else None
    threading.Thread(target=usage_stats.app_opened, args=(boot_ms,), name="usage-stats-open", daemon=True).start()


def _uefn_watch(ctx: dict[str, Any]) -> None:
    # The header follows UEFN by itself and a listener that did not start is restarted.
    from frontend.ui_web.lag import uefn_watch

    uefn_watch.start(ctx["api"])


def _uninstaller_link(ctx: dict[str, Any]) -> None:
    from frontend import uninstaller_link

    threading.Thread(target=uninstaller_link.ensure_quietly, name="uninstaller-link", daemon=True).start()


def _crash_dumps(ctx: dict[str, Any]) -> None:
    # start_watch sets the filter again whenever .NET (WinForms, WebView2) replaced it.
    from frontend import native_crash

    if native_crash.install():
        native_crash.start_watch()


def _heartbeat(ctx: dict[str, Any]) -> None:
    from frontend import boot_report, busy_pc, low_memory
    from frontend.ui_web.agent_turns.state import list_running_agents

    boot_report.on_heartbeat(low_memory.on_heartbeat)
    boot_report.on_heartbeat(busy_pc.on_heartbeat)
    boot_report.start_heartbeat(lambda: len(list_running_agents()))  # later calls do nothing


SERVICES: tuple[tuple[str, Callable[[dict[str, Any]], None]], ...] = (
    ("mark ready", _mark_ready),
    ("auto-update", _update_swap),
    ("folder access check", _folder_access),
    ("usage stats", _usage_stats),
    ("UEFN watch", _uefn_watch),
    ("uninstaller link", _uninstaller_link),
    ("crash dump filter", _crash_dumps),
    ("crash heartbeat", _heartbeat),
)


def start_session_services(
    api: Any,
    window_holder: dict[str, Any],
    tk_root_holder: dict[str, Any],
    handover: dict[str, Any] | None,
) -> None:
    """Start every shared service in order; a failing one is logged and the rest still start."""
    ctx = {"api": api, "window_holder": window_holder, "tk_root_holder": tk_root_holder, "handover": handover}
    for name, step in SERVICES:
        try:
            step(ctx)
        except Exception:
            _log.warning("%s did not start", name, exc_info=True)
