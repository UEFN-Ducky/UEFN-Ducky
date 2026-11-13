"""Shared UEFN listener health / status checks (panel UI, MCP tools, agent).

Status polls use GET /health only — never POST ping or get_project_info.
Those POST commands queue on the UEFN Slate tick and were the #1 freeze source
(~thousands of game-thread commands per session).
"""

from __future__ import annotations

import os
import threading
import time
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.bridge import listener_get_health, post_command_to_listener, seconds_since_last_post_ok

# GET ok + tick_age above this while not busy ⇒ Slate tick stopped (wedged).
_WEDGE_TICK_AGE_SEC = 10.0
_PYTHON_SWEEP_INTERVAL_SEC = 60.0

# When each island was last swept, for the whole process. Agent turns and the coding-agent
# runner ask for status with no state of their own; kept per state, every one of those
# calls swept the island and rewrote a file in the Fortnite install.
_python_sweep_lock = threading.Lock()
_python_sweep_at: dict[str, float] = {}


def _claim_python_sweep(project_root: str, now: float) -> bool:
    """True for the one caller that should sweep ``project_root`` now (once a minute)."""
    key = os.path.normcase(os.path.normpath(project_root.strip()))
    with _python_sweep_lock:
        if now - _python_sweep_at.get(key, 0.0) < _PYTHON_SWEEP_INTERVAL_SEC:
            return False
        _python_sweep_at[key] = now
        return True


@dataclass(eq=False)  # identity-hashed: lives in a WeakSet below
class ListenerStatusState:
    """Mutable state for wedged detection and project-info cache."""

    ping_fail_streak: int = 0
    project_cache: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        with _states_lock:
            _states.add(self)


# Every live status state in the process, so a project switch can drop each one's cached
# island (the MCP tools, the panel API and the agent loop each keep their own). Weak:
# agent turns make a throwaway state per call and must not pile up here.
_states_lock = threading.Lock()
_states: weakref.WeakSet[ListenerStatusState] = weakref.WeakSet()


def forget_listener_project_caches() -> None:
    """Drop every cached live-project answer (called on a panel project switch)."""
    with _states_lock:
        for st in list(_states):
            st.project_cache = None
            st.ping_fail_streak = 0


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
        from frontend.project_kind import project_root_path

        return str(project_root_path(raw))
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


def _same_or_nested(a: str, b: str) -> bool:
    """True when one normalized path is the other or lies inside it (old islands keep
    the plugin under ``<root>/Plugins/<Name>``, so either side may be the deeper one)."""
    if not a or not b:
        return False
    pa = os.path.normcase(os.path.normpath(a))
    pb = os.path.normcase(os.path.normpath(b))
    if pa == pb:
        return True
    return pa.startswith(pb + os.sep) or pb.startswith(pa + os.sep)


def _project_from_health(
    health: dict[str, Any],
    *,
    selected_project_root: str = "",
) -> tuple[str, str, bool]:
    """Derive (uefn_project_dir, uefn_project_name, project_match) from GET health.

    The listener names the live island by its content mount, which is the island's
    ROOT PLUGIN name, not its folder: UEFN names that plugin by a module GUID for
    some islands (folder ``MyProjectBJJJ``, mount ``86ef2167-…``). So the match is
    decided against every name the selected island declares in its own descriptors
    (``.uefnproject`` plugins/title, ``.uplugin`` stems, folder), and first by path
    when the listener reports the island's folder (``island_dir``). A reserved mount
    (``Temp``, ``Game``, ``Engine``) means no island map is open, never a mismatch.
    """
    from frontend.project_kind import is_island_identity, is_reserved_mount

    uefn_project_name = str(health.get("project_name") or "").strip()
    if is_reserved_mount(uefn_project_name):
        uefn_project_name = ""
    raw_dir = str(health.get("project_dir") or "").strip()
    uefn_project_dir = _normalize_project_path(raw_dir) if raw_dir else ""
    island_dir = str(health.get("island_dir") or "").strip()
    project_match = True
    selected = _normalize_project_path(selected_project_root)
    if uefn_project_name and selected:
        from frontend.project_kind import is_folder_project

        # A folder project is not an island, so UEFN having one open is no mismatch.
        if not is_folder_project(selected):
            by_path = _same_or_nested(_normalize_project_path(island_dir), selected) if island_dir else False
            project_match = by_path or is_island_identity(selected, uefn_project_name)
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
    selected = _normalize_project_path(selected_project_root)
    # A cached answer is only good for the project it was computed against: after a
    # project switch the old match flag must not survive for the TTL.
    if (
        cache
        and now - float(cache.get("at", 0)) < cache_ttl_sec
        and str(cache.get("selected_root", "")) == selected
    ):
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
        "selected_root": selected,
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
    # Seconds the running command has sat inside a UEFN window it opened (a message box).
    try:
        in_window = float(health.get("in_window_sec") or 0.0) if get_ok and isinstance(health, dict) else 0.0
    except (TypeError, ValueError):
        in_window = 0.0

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

    from backend.mcp_plugins.epic import EDITOR_UNREAL, editor_label, expected_editor, probe_epic_mcp
    from frontend.uefn_project_beta import read_uefn_beta_access

    epic = probe_epic_mcp(project_root=selected_project_root or None)
    epic_online = bool(epic.get("epic_mcp_online"))
    epic_reason = str(epic.get("epic_mcp_reason") or "")
    epic_editor = str(epic.get("epic_mcp_editor") or "")
    # An Unreal Engine project never uses the UEFN listener; its editor is Unreal Editor.
    unreal_project = expected_editor(selected_project_root or "") == EDITOR_UNREAL
    epic_label = editor_label(epic_editor or (EDITOR_UNREAL if unreal_project else ""))
    beta = read_uefn_beta_access(selected_project_root)
    if wedged:
        status_text = "Listener wedged — call reload_listener once, then stay on workspace_*"
    elif online and busy:
        cmd = f" · {current_command}" if current_command else ""
        if in_window >= 3.0:
            status_text = (f"Online · held by a UEFN popup for {in_window:.0f} s{cmd}: "
                           "call uefn_popups, then uefn_popup_press")
        else:
            status_text = f"Online · busy (editor command running{cmd})"
    elif online and uptime:
        status_text = f"Online · up {_sec_to_uptime(uptime)}"
    elif online:
        status_text = "Online"
    elif unreal_project:
        status_text = "Unreal project"
    else:
        status_text = "Offline — open UEFN + deploy listener"
        if beta.get("listener_init_race"):
            status_text = "Offline — listener init race; stay on workspace_*; reload_listener once"
    if epic_online:
        status_text = f"{status_text} · {editor_label(epic_editor)} MCP online"
    elif epic_reason == "other_editor":
        status_text = f"{status_text} · MCP port held by {editor_label(epic_editor)}"
    elif epic_reason == "port_taken":
        status_text = f"{status_text} · MCP port taken by another program"
    elif epic_reason == "disabled":
        status_text = f"{status_text} · {epic_label} MCP disabled (Settings → MCPs)"
    else:
        status_text = f"{status_text} · {epic_label} MCP offline"

    uefn_project_dir = ""
    uefn_project_name = ""
    project_match = True
    selected_norm = _normalize_project_path(selected_project_root)
    if online and isinstance(health, dict):
        # Prefer live GET fields; cache for panel consumers that call separately.
        uefn_project_dir, uefn_project_name, project_match = _project_from_health(
            health,
            selected_project_root=selected_project_root,
        )
        if uefn_project_name or uefn_project_dir:
            st.project_cache = {
                "at": time.time(),
                "selected_root": selected_norm,
                "uefn_project_dir": uefn_project_dir,
                "uefn_project_name": uefn_project_name,
                "project_match": project_match,
            }
        elif st.project_cache and str(st.project_cache.get("selected_root", "")) == selected_norm:
            uefn_project_dir = str(st.project_cache.get("uefn_project_dir", ""))
            uefn_project_name = str(st.project_cache.get("uefn_project_name", ""))
            project_match = bool(st.project_cache.get("project_match", True))

    # Both sides of the comparison, so an agent (or a person reading ducky_get_status)
    # can see that mount "86ef2167-…" IS the selected island rather than guessing.
    selected_project_mount = ""
    if selected_norm:
        try:
            from frontend.project_kind import is_folder_project, island_mount_name

            if not is_folder_project(selected_norm):
                selected_project_mount = island_mount_name(selected_norm)
        except Exception:
            selected_project_mount = ""

    # Race is only "active" when Toolsets+Python are on AND the listener failed to start.
    coexistence = bool(beta.get("python_and_toolsets"))
    init_race_active = bool(coexistence and not online)

    if selected_project_root and _claim_python_sweep(selected_project_root, time.time()):
        try:
            from frontend.deploy import refresh_inits, resolve_uefn_project_root
            from frontend.project_kind import has_uefnproject

            root = resolve_uefn_project_root(Path(selected_project_root))
            if not has_uefnproject(root):
                raise ValueError("folder project: no island init refresh")
            refresh_inits(root)
        except Exception:
            pass

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
        "selected_project_mount": selected_project_mount,
        "port": port,
        "tick_age_sec": tick_age,
        "current_command": current_command,
        "in_window_sec": in_window,
        "epic_mcp_online": epic_online,
        "epic_mcp_reason": epic_reason,
        "epic_mcp_url": str(epic.get("epic_mcp_url") or ""),
        "epic_mcp_editor": epic_editor,
        "epic_mcp_setup_steps": list(epic.get("epic_mcp_setup_steps") or []),
        "epic_mcp_note": str(epic.get("epic_mcp_note") or ""),
        "uefn_python_editor_scripting": bool(beta.get("python_editor_scripting")),
        "uefn_mcp_toolsets": bool(beta.get("uefn_mcp_toolsets")),
        "python_and_toolsets": coexistence,
        "listener_init_race": init_race_active,
        "beta_access_note": str(beta.get("agent_note") or "") if init_race_active else "",
    }
