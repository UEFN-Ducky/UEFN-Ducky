"""Builtin play-test nodes: check UEFN, start/stop the game, wait for a player, expect a log line.

Every node checks before it acts — Start game is a no-op while a session is
already playing, Stop game while none is — so a workflow can be re-run without
launching twice. Results merge into the run payload, so a Branch node can read
``running``, ``playing`` or ``has_player`` from the step before it.
"""

from __future__ import annotations

from typing import Any

_PLAYER_WAIT_CAP_S = 120.0
_LOG_WAIT_CAP_S = 120.0


def _uefn_online() -> dict[str, Any]:
    from backend.testing.live_graph import tester_uefn_status

    try:
        return tester_uefn_status()
    except Exception:
        return {"uefn_online": False, "listener_online": False, "epic_mcp_online": False}


def _probe() -> dict[str, Any]:
    from backend.tools.tester.session_play import session_probe

    try:
        return session_probe()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "playing": False, "has_player": False, "player_count": 0}


def check_uefn(cfg: dict[str, Any]) -> dict[str, Any]:
    """Cheap probes only. Never launches, never waits."""
    from frontend.window_view import _uefn_running

    out: dict[str, Any] = {
        "running": bool(_uefn_running()),
        "listener_online": False,
        "epic_mcp_online": False,
        "project_match": False,
        "project_name": "",
        "playing": False,
        "has_player": False,
        "player_count": 0,
    }
    status = _uefn_online()
    out["listener_online"] = bool(status.get("listener_online"))
    out["epic_mcp_online"] = bool(status.get("epic_mcp_online"))
    if out["listener_online"]:
        out.update(_project_state(str(cfg.get("project") or "")))
    if out["listener_online"] or out["epic_mcp_online"]:
        probe = _probe()
        out["playing"] = bool(probe.get("playing"))
        out["has_player"] = bool(probe.get("has_player"))
        out["player_count"] = int(probe.get("player_count") or 0)
    out["ready"] = bool(out["running"] and out["listener_online"] and out["project_match"])
    return {"ok": True, "result": out}


def _project_state(project: str) -> dict[str, Any]:
    from backend.bridge.client import listener_get_health
    from backend.bridge.status import _project_from_health
    from frontend.settings import PANEL_LISTENER_PORT, PanelSettings
    from frontend.window_view import uefnproject_path

    selected = ""
    try:
        selected = str(uefnproject_path(project or None).parent)
    except Exception:
        selected = (PanelSettings.load().uefn_project_root or "").strip()
    health = listener_get_health(int(PANEL_LISTENER_PORT), timeout=0.8)
    if not isinstance(health, dict) or health.get("status") != "ok":
        return {}
    _dir, name, match = _project_from_health(health, selected_project_root=selected)
    return {"project_name": str(name or ""), "project_match": bool(match and (name or not selected))}


def start_game(cfg: dict[str, Any]) -> dict[str, Any]:
    from backend.automations.uefn import until_stopped
    from backend.tools.tester.session_play import start_game as _start, wait_for_player

    status = _uefn_online()
    if not status.get("uefn_online"):
        return {
            "ok": False,
            "error": "UEFN is not running or its listener is offline — put Open UEFN project "
            "(or Wait for UEFN) before Start game.",
        }
    probe = _probe()
    skip = cfg.get("skip_if_playing")
    if probe.get("playing") and skip is not False:
        result: dict[str, Any] = {**_session_fields(probe), "already_playing": True, "started": False}
    else:
        started = _start()
        if not started.get("ok"):
            return {"ok": False, "error": f"Could not start the game: {started.get('error') or 'unknown error'}"}
        result = {"playing": True, "already_playing": False, "started": True, "source": started.get("source", "")}
    wait_s = float(cfg.get("wait_player") or 0)
    if wait_s > 0:
        waited = until_stopped(wait_for_player, min(wait_s, _PLAYER_WAIT_CAP_S))
        result.update(_session_fields(waited))
        if not waited.get("ok"):
            return {"ok": False, "error": str(waited.get("error") or "no player joined"), "result": result}
    return {"ok": True, "result": result}


def stop_game(_cfg: dict[str, Any]) -> dict[str, Any]:
    status = _uefn_online()
    if not status.get("uefn_online"):
        return {"ok": True, "result": {"playing": False, "already_stopped": True, "stopped": False}}
    probe = _probe()
    if not probe.get("playing"):
        return {"ok": True, "result": {"playing": False, "already_stopped": True, "stopped": False}}
    from backend.bridge import send_command

    try:
        out = send_command("stop_pie", {})
    except Exception as exc:
        return {"ok": False, "error": f"Could not stop the game: {exc}"}
    return {"ok": True, "result": {"playing": False, "already_stopped": False, "stopped": True, **(out if isinstance(out, dict) else {})}}


def wait_player(cfg: dict[str, Any]) -> dict[str, Any]:
    from backend.automations.uefn import until_stopped
    from backend.tools.tester.session_play import wait_for_player

    timeout = min(max(float(cfg.get("timeout") or 60), 1.0), _PLAYER_WAIT_CAP_S)
    waited = until_stopped(wait_for_player, timeout)
    result = _session_fields(waited)
    if not waited.get("ok"):
        return {"ok": False, "error": str(waited.get("error") or "no player joined"), "result": result}
    return {"ok": True, "result": result}


def expect_log(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    from backend.automations.uefn import until_stopped
    from backend.tools.tester.session_play import expect_log as _expect

    timeout = min(max(float(cfg.get("timeout") or 30), 1.0), _LOG_WAIT_CAP_S)
    since = payload.get("log_offset") if cfg.get("from_start") is not True else 0
    found = until_stopped(_expect, str(cfg.get("regex") or ""), timeout, int(since or 0))
    result = {
        "log_matches": list(found.get("log_matches") or []),
        "log_count": int(found.get("count") or 0),
        "log_offset": int(found.get("log_offset") or 0),
    }
    if not found.get("ok"):
        return {"ok": False, "error": f"{found.get('error') or 'log pattern not seen'}: {cfg.get('regex') or ''}", "result": result}
    return {"ok": True, "result": result}


def _session_fields(probe: dict[str, Any]) -> dict[str, Any]:
    return {
        "playing": bool(probe.get("playing")),
        "has_player": bool(probe.get("has_player")),
        "player_count": int(probe.get("player_count") or 0),
    }
