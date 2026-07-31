"""Shared UEFN listener health / status checks (panel UI, MCP tools, agent)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.bridge import listener_get_health, post_command_to_listener, seconds_since_last_post_ok


@dataclass
class ListenerStatusState:
    """Mutable state for wedged detection and project-info cache."""

    ping_fail_streak: int = 0
    project_cache: dict[str, Any] | None = None


def _sec_to_uptime(sec: float) -> str:
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def _normalize_project_path(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        from frontend.deploy import resolve_uefn_project_root

        return str(resolve_uefn_project_root(Path(raw)))
    except (OSError, ValueError):
        try:
            return str(Path(raw).resolve())
        except OSError:
            return raw


def ping_listener(
    port: int,
    *,
    attempts: int = 3,
    timeout: float = 3.0,
) -> tuple[bool, dict[str, Any] | None]:
    """POST ping with short retries — UEFN main thread can miss a single request when busy."""
    last: dict[str, Any] | None = None
    for attempt in range(max(1, attempts)):
        try:
            last = post_command_to_listener(port, "ping", {}, timeout=timeout)
            return True, last
        except Exception:
            if attempt + 1 < attempts:
                time.sleep(0.25)
    return False, last


def listener_project_fields(
    port: int,
    *,
    selected_project_root: str = "",
    cache: dict[str, Any] | None = None,
    cache_ttl_sec: float = 20.0,
) -> tuple[str, str, bool, dict[str, Any] | None]:
    """Return (uefn_project_dir, uefn_project_name, project_match, updated_cache)."""
    from frontend.ui_web.project_chats import project_display_name

    now = time.time()
    if cache and now - float(cache.get("at", 0)) < cache_ttl_sec:
        return (
            str(cache.get("uefn_project_dir", "")),
            str(cache.get("uefn_project_name", "")),
            bool(cache.get("project_match", True)),
            cache,
        )

    uefn_project_dir = ""
    uefn_project_name = ""
    project_match = True
    try:
        info = post_command_to_listener(port, "get_project_info", {}, timeout=3.0)
        if isinstance(info, dict):
            # The live editor's identity is its content-root NAME (e.g. "Roguelike").
            # project_dir is always the shared FortniteGame folder, so it can't tell
            # user projects apart — match by name against the panel project's folder.
            uefn_project_name = str(info.get("project_name") or "").strip()
            raw_dir = str(info.get("project_dir") or "").strip()
            if raw_dir:
                uefn_project_dir = _normalize_project_path(raw_dir)
            selected = _normalize_project_path(selected_project_root)
            if uefn_project_name and selected:
                selected_name = project_display_name(selected)
                project_match = uefn_project_name.casefold() == selected_name.casefold()
    except Exception:
        pass

    updated = {
        "at": now,
        "uefn_project_dir": uefn_project_dir,
        "uefn_project_name": uefn_project_name,
        "project_match": project_match,
    }
    return uefn_project_dir, uefn_project_name, project_match, updated


def is_listener_ready(port: int, *, require_ping: bool = False) -> bool:
    """Cheap GET health check, or full GET + POST ping when ``require_ping``."""
    health = listener_get_health(port)
    if health is None or health.get("status") != "ok":
        return False
    if not require_ping:
        return True
    ok, _ = ping_listener(port, attempts=1, timeout=2.0)
    return ok


def fetch_listener_status(
    port: int,
    *,
    state: ListenerStatusState | None = None,
    version: str = "",
    selected_project_root: str = "",
) -> dict[str, Any]:
    """Full listener status: online, wedged, uptime, UEFN project match.

    Online is GET-health authoritative: a reachable listener stays online even when
    busy or when a command ping times out (MCP traffic can block POST briefly).
    Wedged = GET ok but POST ping fails twice while not busy — icons show wedged,
    not offline. When a long command finishes, the next status poll clears the streak.
    """
    st = state if state is not None else ListenerStatusState()

    health = listener_get_health(port)
    get_ok = health is not None and health.get("status") == "ok"
    busy = bool(health.get("busy")) if get_ok and isinstance(health, dict) else False
    commands_ok = False
    ping_result: dict[str, Any] | None = None
    if get_ok:
        if busy:
            # Editor is still processing — do not count as wedge; treat as online/busy.
            commands_ok = True
            ping_result = {"uptime_sec": float(health.get("uptime_sec", 0) or 0), "busy": True}
        elif seconds_since_last_post_ok() < 10.0:
            # An agent command just succeeded — the listener is provably processing
            # commands. Skip the status ping so it doesn't queue on the UEFN main
            # thread behind (and ahead of) agent tool calls.
            commands_ok = True
            ping_result = {"uptime_sec": float(health.get("uptime_sec", 0) or 0)}
        else:
            commands_ok, ping_result = ping_listener(port)

    if get_ok:
        online = True
        if commands_ok:
            st.ping_fail_streak = 0
            wedged = False
        else:
            st.ping_fail_streak += 1
            wedged = st.ping_fail_streak >= 2
    else:
        st.ping_fail_streak = 0
        online = False
        wedged = False

    uptime = 0.0
    if ping_result and isinstance(ping_result, dict):
        uptime = float(ping_result.get("uptime_sec", 0) or 0)
    elif get_ok and isinstance(health, dict):
        uptime = float(health.get("uptime_sec", 0) or 0)

    if wedged:
        status_text = "Listener wedged — restart UEFN (commands not processing)"
    elif online and busy:
        status_text = "Online · busy (editor command running)"
    elif online and uptime:
        status_text = f"Online · up {_sec_to_uptime(uptime)}"
    elif online:
        status_text = "Online"
    else:
        status_text = "Offline — open UEFN + deploy listener"

    uefn_project_dir = ""
    uefn_project_name = ""
    project_match = True
    if online and not busy:
        uefn_project_dir, uefn_project_name, project_match, st.project_cache = listener_project_fields(
            port,
            selected_project_root=selected_project_root,
            cache=st.project_cache,
        )
    elif online and st.project_cache:
        uefn_project_dir = str(st.project_cache.get("uefn_project_dir", ""))
        uefn_project_name = str(st.project_cache.get("uefn_project_name", ""))
        project_match = bool(st.project_cache.get("project_match", True))

    return {
        "online": online,
        "wedged": wedged,
        "busy": busy,
        "version": version,
        "uptime_sec": uptime,
        "status_text": status_text,
        "uefn_project_dir": uefn_project_dir,
        "uefn_project_name": uefn_project_name,
        "project_match": project_match,
        "port": port,
    }
