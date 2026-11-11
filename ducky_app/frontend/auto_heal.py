"""Auto-heal: the app looks for the stuck states that once sat until a person noticed them, and
puts them right by itself.

Nov 11 2026, one day: a second copy moved panel_port.json, so every tool server started after
it talked to a port nothing listened on (plugin reloads, agent events and panel calls went
nowhere); a plugin an agent installed never reached the app's panels, which kept running the
old code until a restart. Each was fixed where it broke; this loop is the net for the next one
of their kind.

Each healer is cheap, safe to run every minute in the app process, never touches a running turn
or anything outside Ducky, and returns what it fixed ('' when nothing). What it fixed is logged
and kept for ``ducky_health`` (self_health.snapshot ``auto_heal``)."""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections import deque
from typing import Any, Callable

_log = logging.getLogger(__name__)

INTERVAL_S = 60.0
FIRST_AFTER_S = 90.0  # after startup's own work: its installs and plugin loads settle first
_healed: deque[dict[str, Any]] = deque(maxlen=50)
_started = False
_lock = threading.Lock()


def _answers(port: int) -> bool:
    """Something listens on this loopback port."""
    if not 0 < port < 65536:
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def heal_port_file() -> str:
    """panel_port.json names the port every new Ducky process uses to reach the app. When it says
    another port than the app's own, and only the app's own answers, it says the app's again (no
    file at all when the app is on the default)."""
    from frontend import settings

    mine = int(settings.PANEL_LISTENER_PORT)
    saved = settings._saved_panel_port()
    want = 0 if mine == settings.DEFAULT_PANEL_LISTENER_PORT else mine
    if saved == want or (not saved and not want):
        return ""
    if not _answers(mine - 1) or (saved and _answers(saved - 1)):
        return ""  # can't tell which one is the app: leave it
    path = settings.default_app_data_dir() / settings.PANEL_PORT_FILE
    if want:
        settings._save_panel_port(want)
    else:
        path.unlink(missing_ok=True)
    return (f"panel_port.json pointed new Ducky processes at {saved or settings.DEFAULT_PANEL_LISTENER_PORT}, "
            f"but the app listens on {mine}: they reach the app again")


def heal_stale_plugins() -> str:
    """A plugin another process installed (an agent's tool server, a Store update) that the app
    still runs the old copy of: the app loads its new files, as the tool server does."""
    from backend.uefn_plugins.plugin_host.lifecycle import reload_plugins_changed_on_disk

    stale = reload_plugins_changed_on_disk()
    return f"reloaded {', '.join(stale)} in the app: it ran the copy from before its last install" if stale else ""


HEALERS: tuple[tuple[str, Callable[[], str]], ...] = (
    ("port_file", heal_port_file),
    ("stale_plugins", heal_stale_plugins),
)


def run_once(now: float | None = None) -> list[dict[str, Any]]:
    """Every healer once; what they fixed (each also logged and kept for recent())."""
    out = []
    for name, heal in HEALERS:
        try:
            fixed = heal()
        except Exception:  # noqa: BLE001 — one healer's failure never stops the others
            _log.debug("auto-heal %s failed", name, exc_info=True)
            continue
        if fixed:
            row = {"at": time.time() if now is None else now, "healer": name, "fixed": fixed}
            with _lock:
                _healed.append(row)
            _log.warning("Auto-heal (%s): %s", name, fixed)
            out.append(row)
    return out


def recent(limit: int = 10) -> list[dict[str, Any]]:
    with _lock:
        return list(_healed)[-limit:]


def start() -> None:
    """The loop, once, in the app process only (tool servers and agent hosts heal nothing)."""
    global _started
    from backend.process_role import is_app

    with _lock:
        if _started or not is_app():
            return
        _started = True

    def loop() -> None:
        time.sleep(FIRST_AFTER_S)
        while True:
            run_once()
            time.sleep(INTERVAL_S)

    threading.Thread(target=loop, daemon=True, name="auto-heal").start()
