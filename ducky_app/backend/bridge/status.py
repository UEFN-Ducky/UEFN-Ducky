"""Shared UEFN listener health / status checks (panel UI, MCP tools, agent).

Status polls use GET /health only — never POST ping or get_project_info.
Those POST commands queue on the UEFN Slate tick and were the #1 freeze source
(~thousands of game-thread commands per session).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.bridge import listener_get_health, post_command_to_listener, seconds_since_last_post_ok

# GET ok + tick_age above this while not busy ⇒ Slate tick stopped (wedged).
_WEDGE_TICK_AGE_SEC = 10.0


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
    """POST ping with short retries — kept for explicit agent/tool use only.

    Status polling must NOT call this; use GET health + tick_age_sec instead.
    """
    last: dict[str, Any] | None = None
    for attempt in range(max(1, attempts)):
        try:
            last = post_command_to_listener(port, "ping", {}, timeout=timeout)
            return True, last
        except Exception:
            if attempt + 1 < attempts:
                time.sleep(0.25)
    return False, last


def _project_from_health(
    health: dict[str, Any],
    *,
    selected_project_root: str = "",
) -> tuple[str, str, bool]:
    """Derive (uefn_project_dir, uefn_project_name, project_match) from GET health."""
    from frontend.ui_web.project_chats import project_display_name

    uefn_project_name = str(health.get("project_name") or "").strip()
    raw_dir = str(health.get("project_dir") or "").strip()
    uefn_project_dir = _normalize_project_path(raw_dir) if raw_dir else ""
    project_match = True
    selected = _normalize_project_path(selected_project_root)
    if uefn_project_name and selected:
        selected_name = project_display_name(selected)
        project_match = uefn_project_name.casefold() == selected_name.casefold()
    return uefn_project_dir, uefn_project_name, project_match


def listener_project_fields(
    port: int,
    *,
    selected_project_root: str = "",
    cache: dict[str, Any] | None = None,
    cache_ttl_sec: float = 20.0,
) -> tuple[str, str, bool, dict[str, Any] | None]:
    """Return (uefn_project_dir, uefn_project_name, project_match, updated_cache).

    Reads project identity from GET health (zero game-thread cost). Never POSTs
    get_project_info for status — that used to hammer the Slate tick.
    """
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
    health = listener_get_health(port)
    if health and health.get("status") == "ok":
        uefn_project_dir, uefn_project_name, project_match = _project_from_health(
            health,
            selected_project_root=selected_project_root,
        )

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


def _tick_age_sec(health: dict[str, Any]) -> float | None:
    raw = health.get("tick_age_sec")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def fetch_listener_status(
    port: int,
    *,
    state: ListenerStatusState | None = None,
    version: str = "",
    selected_project_root: str = "",
) -> dict[str, Any]:
    """Full listener status: online, wedged, uptime, UEFN project match.

    Online is GET-health authoritative. Wedged = GET ok, not busy, and the Slate
    tick heartbeat is stale (``tick_age_sec`` > threshold). Never POSTs ping or
    get_project_info — those queue on the editor game thread.
    """
    st = state if state is not None else ListenerStatusState()

    health = listener_get_health(port)
    get_ok = health is not None and health.get("status") == "ok"
    busy = bool(health.get("busy")) if get_ok and isinstance(health, dict) else False
    tick_age = _tick_age_sec(health) if get_ok and isinstance(health, dict) else None
    current_command = (
        str(health.get("current_command") or "") if get_ok and isinstance(health, dict) else ""
    )

    if get_ok:
        online = True
        # Legacy listeners without tick_age: fall back to recent successful POST
        # as proof of life (never POST from here).
        if busy:
            st.ping_fail_streak = 0
            wedged = False
        elif tick_age is not None:
            if tick_age > _WEDGE_TICK_AGE_SEC:
                st.ping_fail_streak += 1
                wedged = st.ping_fail_streak >= 2
            else:
                st.ping_fail_streak = 0
                wedged = False
        elif seconds_since_last_post_ok() < 30.0:
            st.ping_fail_streak = 0
            wedged = False
        else:
            # No tick_age (old listener) and no recent agent traffic — stay online,
            # do not wedge from silence alone (would require a POST probe).
            st.ping_fail_streak = 0
            wedged = False
    else:
        st.ping_fail_streak = 0
        online = False
        wedged = False

    uptime = 0.0
    if get_ok and isinstance(health, dict):
        uptime = float(health.get("uptime_sec", 0) or 0)

    from backend.mcp_plugins.epic import probe_epic_mcp
    from frontend.uefn_project_beta import read_uefn_beta_access

    epic = probe_epic_mcp()
    epic_online = bool(epic.get("epic_mcp_online"))
    epic_reason = str(epic.get("epic_mcp_reason") or "")
    beta = read_uefn_beta_access(selected_project_root)
    if wedged:
        status_text = "Listener wedged — restart UEFN (commands not processing)"
    elif online and busy:
        cmd = f" · {current_command}" if current_command else ""
        status_text = f"Online · busy (editor command running{cmd})"
    elif online and uptime:
        status_text = f"Online · up {_sec_to_uptime(uptime)}"
    elif online:
        status_text = "Online"
    else:
        status_text = "Offline — open UEFN + deploy listener"
        if beta.get("listener_init_race"):
            status_text = "Offline — restart UEFN to reconnect Ducky listener"
    if epic_online:
        status_text = f"{status_text} · UEFN MCP online"
    elif epic_reason == "disabled":
        status_text = f"{status_text} · UEFN MCP disabled (Settings → MCPs)"
    else:
        status_text = f"{status_text} · UEFN MCP offline"

    uefn_project_dir = ""
    uefn_project_name = ""
    project_match = True
    if online and isinstance(health, dict):
        # Prefer live GET fields; cache for panel consumers that call separately.
        uefn_project_dir, uefn_project_name, project_match = _project_from_health(
            health,
            selected_project_root=selected_project_root,
        )
        if uefn_project_name or uefn_project_dir:
            st.project_cache = {
                "at": time.time(),
                "uefn_project_dir": uefn_project_dir,
                "uefn_project_name": uefn_project_name,
                "project_match": project_match,
            }
        elif st.project_cache:
            uefn_project_dir = str(st.project_cache.get("uefn_project_dir", ""))
            uefn_project_name = str(st.project_cache.get("uefn_project_name", ""))
            project_match = bool(st.project_cache.get("project_match", True))

    # Race is only "active" when Toolsets+Python are on AND the listener failed to start.
    coexistence = bool(beta.get("python_and_toolsets"))
    init_race_active = bool(coexistence and not online)

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
        "tick_age_sec": tick_age,
        "current_command": current_command,
        "epic_mcp_online": epic_online,
        "epic_mcp_reason": epic_reason,
        "epic_mcp_url": str(epic.get("epic_mcp_url") or ""),
        "epic_mcp_setup_steps": list(epic.get("epic_mcp_setup_steps") or []),
        "uefn_python_editor_scripting": bool(beta.get("python_editor_scripting")),
        "uefn_mcp_toolsets": bool(beta.get("uefn_mcp_toolsets")),
        "python_and_toolsets": coexistence,
        "listener_init_race": init_race_active,
        "beta_access_note": str(beta.get("agent_note") or "") if init_race_active else "",
    }
