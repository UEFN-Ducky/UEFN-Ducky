"""Ducky updates itself: download in the background, switch only when nobody is busy.

A background thread checks the Store's update feed a couple of minutes after start and
every 30 minutes, gets a newer version ready (``frontend.app_stage``, only the changed
files), then waits until switching can't interrupt anything: no workflow or team chat
running or due within 5 minutes, no terminal open, no unsaved editor change, no plugin
job, no phone or other PC connected, and either nobody has used Ducky for a while or every
window is hidden. A running chat doesn't hold it: the switch stops it and the new version
continues its turn. The switch closes this version without stopping the IDE bridges and
starts the new one where the window was; nothing is forced, so if that moment never
comes the next start runs the new version (``frontend.app_versions``).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

from frontend import __version__

_log = logging.getLogger(__name__)

# Soon after start, so a restart picks up the newest version (Oct 29: PCs sat days behind).
CHECK_AFTER_START_S = 15.0
# Right after a start nobody is mid-task yet: an update ready by then switches even while
# the window is in use (work, unsaved files and team chats still hold it).
STARTUP_SWITCH_S = 3 * 60.0
# Downloads that failed this many times in a row bring back the Setup popup (version_check).
FAILURES_FOR_SETUP = 2
CHECK_EVERY_S = 30 * 60.0
IDLE_TICK_S = 30.0
# Nobody touched Ducky for this long (or every window is hidden): it may switch.
AWAY_AFTER_S = 5 * 60.0
# A window that stopped reporting can't vouch that nothing is unsaved.
UI_REPORT_STALE_S = 3 * 60.0
SCHEDULE_MARGIN_S = 5 * 60.0
REMOTE_RECENT_S = 10 * 60.0
# The new version shows "Updated to …" in background activity this long after a switch.
UPDATED_NOTE_S = 10 * 60.0
SWITCHED_FROM_ENV = "UEFN_DUCKY_SWITCHED_FROM"
# A stopped chat finishes its current tool and saves its turn; past this it is cut off
# (its last checkpoint still holds the turn).
CHAT_STOP_WAIT_S = 10.0
# The new version continues the carried chats once its panel can show them.
PANEL_WAIT_S = 60.0

_lock = threading.Lock()
_thread: threading.Thread | None = None
_wake = threading.Event()
_check_now = threading.Event()
_started_at = time.time()
_state: dict[str, Any] = {"phase": "off", "version": "", "done": 0, "total": 0, "error": "", "failures": 0}
# Per window (the main one is "main"): {at, visible, idle_s, dirty, jobs}.
_ui: dict[str, dict[str, Any]] = {}
_holders: dict[str, Any] = {}


def _set(**fields: Any) -> None:
    with _lock:
        _state.update(fields)


def report_ui(state: dict[str, Any] | None) -> None:
    """A window's word on what only it knows: visible, seconds since the last input,
    unsaved editor files and plugin jobs still running."""
    data = state if isinstance(state, dict) else {}
    window = str(data.get("window") or "main")[:80]
    jobs = data.get("jobs") if isinstance(data.get("jobs"), list) else []
    with _lock:
        _ui[window] = {
            "at": time.time(),
            "visible": bool(data.get("visible", True)),
            "idle_s": max(0.0, float(data.get("idle_s") or 0.0)),
            "dirty": max(0, int(data.get("dirty") or 0)),
            "jobs": [str(j)[:80] for j in jobs if str(j).strip()][:5],
        }


def _fresh_windows(now: float) -> dict[str, dict[str, Any]]:
    with _lock:
        return {w: dict(r) for w, r in _ui.items() if now - float(r["at"]) <= UI_REPORT_STALE_S}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _running_chats() -> tuple[list[dict[str, str]], int]:
    """Running chats a switch carries to the new version (``conv_id``, ``mode``, ``model``),
    and how many team chats run: a team chat can't be continued, so it holds the switch."""
    from frontend.ui_web.agent_modes import list_running_agents, running_turn_settings
    from frontend.ui_web.project_chats import load_conversation_meta

    carried: list[dict[str, str]] = []
    teams = 0
    for conv_id in list_running_agents():
        if getattr(load_conversation_meta(conv_id), "is_group", False):
            teams += 1
        else:
            carried.append({"conv_id": conv_id, **running_turn_settings(conv_id)})
    return carried, teams


def _stop_chats() -> list[dict[str, str]]:
    """Stop the running chats so their turns are saved; the new version continues them."""
    from frontend.ui_web.agent_modes import cancel_agent, is_agent_running

    chats, _ = _running_chats()
    for chat in chats:
        cancel_agent(chat["conv_id"], by_user=False)
    deadline = time.time() + CHAT_STOP_WAIT_S
    while time.time() < deadline and any(is_agent_running(c["conv_id"]) for c in chats):
        time.sleep(0.2)
    return chats


def continue_chats(chats: Any) -> None:
    """Continue the chats a switch stopped, in the background once the panel is up (the new
    version after a switch, or this one when the switch failed)."""
    rows = [c for c in chats if isinstance(c, dict) and c.get("conv_id")] if isinstance(chats, list) else []
    if not rows:
        return

    def run() -> None:
        from frontend.ui_web import agent_modes

        deadline = time.time() + PANEL_WAIT_S
        while agent_modes.get_panel_push() is None and time.time() < deadline:
            time.sleep(0.5)
        for chat in rows:
            try:
                agent_modes.run_message(
                    str(chat["conv_id"]), "", str(chat.get("mode") or "agent"), str(chat.get("model") or ""), resume=True
                )
            except Exception:
                _log.warning("chat %s did not continue after the update", chat.get("conv_id"), exc_info=True)

    threading.Thread(target=run, name="continue-chats", daemon=True).start()


def work_reasons() -> list[str]:
    """Work a switch would cut off (the Update now button refuses while any is left). A
    running chat isn't: the switch stops it and the new version continues it."""
    reasons: list[str] = []
    try:
        _, teams = _running_chats()
        if teams:
            reasons.append(f"{_plural(teams, 'team chat')} running")
    except Exception:
        reasons.append("chats (can't tell if one is running)")
    try:
        from backend.automations import live_runs

        if any(run.get("state") == "started" for run in live_runs.runs()):
            reasons.append("a workflow running")
    except Exception:
        reasons.append("workflows (can't tell if one is running)")
    try:
        from frontend.ui_web.terminal.manager import get_terminal_manager

        open_terminals = len(get_terminal_manager().list_sessions())
        if open_terminals:
            reasons.append(f"{_plural(open_terminals, 'terminal')} open")
    except Exception:
        reasons.append("terminals (can't tell if one is open)")
    windows = _fresh_windows(time.time())
    dirty = sum(int(r["dirty"]) for r in windows.values())
    if dirty:
        reasons.append(_plural(dirty, "unsaved file"))
    for row in windows.values():
        reasons.extend(job for job in row["jobs"] if job not in reasons)
    return reasons


def idle_reasons(now: float | None = None) -> list[str]:
    """Why Ducky isn't idle yet (empty: it may switch now)."""
    now = time.time() if now is None else now
    reasons = work_reasons()
    try:
        from backend.automations.scheduler import due_within

        due = due_within(SCHEDULE_MARGIN_S, now)
        if due:
            reasons.append(f"{due[0]} starting soon")
    except Exception:
        reasons.append("scheduled workflows (can't tell)")
    try:
        from frontend.remote_direct import last_report

        report = last_report()
        if report.get("state") in ("connecting", "connected", "open") and now - float(report.get("at") or 0) < REMOTE_RECENT_S:
            reasons.append("a phone or another PC connected")
    except Exception:
        pass
    windows = _fresh_windows(now)
    with _lock:
        hidden = bool(_holders.get("window_holder", {}).get("hidden"))
    if "main" not in windows and not hidden:
        reasons.append("Ducky's window (not answering)")
    elif now - _started_at < STARTUP_SWITCH_S:
        pass  # just started: switching now is the restart the person already sat through
    elif any(r["visible"] and float(r["idle_s"]) < AWAY_AFTER_S for w, r in windows.items() if w != "main" or not hidden):
        reasons.append("you, using Ducky")
    return reasons


def status() -> dict[str, Any]:
    with _lock:
        out = dict(_state)
    if out.get("phase") == "ready":
        out["waiting_for"] = idle_reasons()
    switched_from = (os.environ.get(SWITCHED_FROM_ENV) or "").strip()
    if switched_from and out.get("phase") in ("off", "idle", "checking") and time.time() - _started_at < UPDATED_NOTE_S:
        out.update(phase="updated", version=__version__, previous=switched_from)
    return out


def _auto_update_allowed() -> bool:
    from frontend.app_stage import is_loopback
    from frontend.bundle_root import is_packaged_runtime
    from frontend.install_info import get_install_info
    from frontend.version_check import update_base_url, update_channel

    if not is_packaged_runtime():
        return False
    channel = update_channel(installed=bool(get_install_info()["installed"]))
    # A local rehearsal (loopback feed) may run from any copy but a dev build.
    return channel == "installed" or (channel != "dev" and is_loopback(update_base_url()))


def check_and_stage() -> dict[str, Any] | None:
    """Fetch the feed and get its newer version ready. Returns the update, or ``None``."""
    from frontend import app_stage
    from frontend.version_check import feed_is_paused, fetch_remote_payload, update_base_url

    _set(phase="checking", error="")
    payload, error = fetch_remote_payload()
    if payload is None or feed_is_paused(payload):
        _set(phase="idle", error=error or "")
        return None
    update = app_stage.feed_update(payload)
    if update is None:
        _set(phase="idle")
        app_stage.prune()
        return None
    version = str(update["version"])
    _set(phase="downloading", version=version, done=0, total=0)
    try:
        app_stage.stage(update, base_url=update_base_url(), progress=lambda d, t: _set(done=d, total=t))
    except Exception as exc:  # a failed download is retried at the next check
        _log.warning("update %s did not download: %s", version, exc)
        with _lock:
            _state.update(phase="failed", error=str(exc)[:300], failures=int(_state.get("failures") or 0) + 1)
        return None
    _set(phase="ready", error="", failures=0)
    app_stage.prune()
    return update


def switch(version: str) -> dict[str, Any]:
    """Close this version and start ``version`` where the window was. Does not return on success."""
    from frontend import app_versions

    folder = app_versions.version_dir(version)
    exe = folder / "UEFN-Ducky.exe"
    if not app_versions.is_ready(folder) or app_versions.is_bad(version):
        return {"ok": False, "error": f"{version} isn't ready."}
    api = _holders.get("api")
    window_holder = _holders.get("window_holder") or {}
    if not app_versions.record_start(version):
        _set(phase="failed", error=f"{version} didn't start before")
        return {"ok": False, "error": f"{version} didn't start before."}
    try:
        chats = _stop_chats()
    except Exception:
        _log.warning("running chats could not be stopped for the switch", exc_info=True)
        chats = []
    app_versions.write_handover(
        {
            "hidden": bool(window_holder.get("hidden")),
            "maximized": bool(getattr(api, "_maximized", False)),
            "from": __version__,
            "chats": chats,
        }
    )
    env = app_versions.hand_off_env()
    env[SWITCHED_FROM_ENV] = __version__
    if not app_versions.start_detached(exe, ["--after-update", str(os.getpid())], env=env):
        continue_chats(chats)
        return {"ok": False, "error": f"{version} could not be started."}
    _set(phase="switching")
    _log.info("switching to %s", version)
    try:
        from frontend import usage_stats

        usage_stats.app_closing()
    except Exception:
        pass
    from frontend.ui_web.shutdown import hard_exit

    hard_exit(api=api, keep_others=True)
    return {"ok": True}


def apply_now() -> dict[str, Any]:
    """The person asked to update now: switch unless it would cut off running work (running
    chats carry over)."""
    with _lock:
        phase, version = _state.get("phase"), str(_state.get("version") or "")
    if phase != "ready" or not version:
        return {"ok": False, "error": "No update is ready yet."}
    busy = work_reasons()
    if busy:
        return {"ok": False, "error": "Waits for: " + ", ".join(busy), "waiting_for": busy}
    return switch(version)


def _loop() -> None:
    _wake.wait(CHECK_AFTER_START_S)
    next_check = 0.0
    while True:
        _wake.clear()
        now = time.time()
        if now >= next_check or _check_now.is_set():
            _check_now.clear()
            next_check = now + CHECK_EVERY_S
            try:
                check_and_stage()
            except Exception:
                _log.warning("update check failed", exc_info=True)
                _set(phase="idle")
        with _lock:
            phase, version = _state.get("phase"), str(_state.get("version") or "")
        if phase == "ready" and version and not idle_reasons():
            switch(version)
        _wake.wait(IDLE_TICK_S)


def check_soon() -> None:
    """Settings' Check for updates: run the next check now."""
    _check_now.set()
    _wake.set()


def start(*, api: Any, tk_root_holder: dict[str, Any], window_holder: dict[str, Any]) -> None:
    """Start the background updater once per panel process (installed copies only)."""
    global _thread
    with _lock:
        _holders.update(api=api, tk_root_holder=tk_root_holder, window_holder=window_holder)
        if _thread is not None:
            return
    if not _auto_update_allowed():
        return
    _set(phase="idle")
    thread = threading.Thread(target=_loop, name="auto-update", daemon=True)
    with _lock:
        _thread = thread
    thread.start()
