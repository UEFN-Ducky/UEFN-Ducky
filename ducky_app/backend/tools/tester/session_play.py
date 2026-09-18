"""Live UEFN play helpers — Epic SessionToolset first, listener second."""

from __future__ import annotations

import json
import time
from typing import Any

_SESSION = "ValkyrieToolset.SessionToolset"
_DEVICE = "ValkyrieToolset.DeviceToolset"
_WAIT_CAP_S = 120.0


def start_game() -> dict[str, Any]:
    """Turn the island on. Epic StartGame first; listener play_in_editor as degrade."""
    from backend.testing.live_graph import _call_epic_tool, tester_uefn_status

    status = tester_uefn_status()
    if status.get("epic_mcp_online"):
        for tool in ("StartGame", "StartSession"):
            try:
                raw = _call_epic_tool(_SESSION, tool, {})
                return {"ok": True, "playing": True, "source": "epic", "tool": tool, "raw": raw}
            except Exception:
                continue
    from backend.bridge import send_command

    try:
        out = send_command("play_in_editor", {})
        return {"ok": True, "playing": True, "source": "listener", "degraded": True, **(out if isinstance(out, dict) else {})}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "playing": False}


def session_probe() -> dict[str, Any]:
    """Playing + has_player. Epic GetSessionStatus when online, then listener."""
    from backend.testing.live_graph import _call_epic_tool, tester_uefn_status

    out: dict[str, Any] = {"ok": True, "playing": False, "has_player": False, "player_count": 0}
    status = tester_uefn_status()
    if status.get("epic_mcp_online"):
        try:
            raw = _call_epic_tool(_SESSION, "GetSessionStatus", {})
            parsed = _as_dict(raw)
            out.update(_from_epic_status(parsed))
            out["source"] = "epic"
        except Exception as exc:
            out["epic_error"] = str(exc)
    from backend.bridge import send_command

    try:
        local = send_command("session_status", {})
        if isinstance(local, dict):
            if not out.get("playing"):
                out["playing"] = bool(local.get("playing"))
            if local.get("has_player") is not None:
                out["has_player"] = bool(local.get("has_player"))
            if local.get("player_count") is not None:
                out["player_count"] = int(local.get("player_count") or 0)
            out.setdefault("source", "listener")
    except Exception as exc:
        if not out.get("source"):
            return {"ok": False, "error": str(exc), "playing": False, "has_player": False, "player_count": 0}
    out["has_player"] = bool(out.get("has_player") or int(out.get("player_count") or 0) > 0)
    return out


def wait_for_player(timeout_sec: float = 60.0, poll_sec: float = 1.0) -> dict[str, Any]:
    deadline = time.time() + min(max(float(timeout_sec or 0), 1.0), _WAIT_CAP_S)
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = session_probe()
        if last.get("ok") is False:
            time.sleep(max(float(poll_sec), 0.2))
            continue
        if last.get("has_player"):
            return {**last, "ok": True}
        time.sleep(max(float(poll_sec), 0.2))
    return {**last, "ok": False, "error": "timed out waiting for player", "has_player": False}


def teleport_player(
    *,
    teleporter: str = "",
    actor_label: str = "",
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
) -> dict[str, Any]:
    """Move the live player. Prefer a labeled teleporter / actor, else x,y,z."""
    from backend.bridge import send_command

    label = (teleporter or actor_label or "").strip()
    try:
        before = send_command("actor_state_snapshot", {"label_filter": label, "limit": 8, "scope": "all"})
    except Exception:
        before = {}
    moved = send_command(
        "move_player_pawn",
        {"label": label, "x": x, "y": y, "z": z},
    )
    if not isinstance(moved, dict) or moved.get("ok") is False:
        return {"ok": False, "error": (moved or {}).get("error") if isinstance(moved, dict) else "move_player_pawn failed"}
    try:
        after = send_command("actor_state_snapshot", {"label_filter": label, "limit": 8, "scope": "all"})
        diff = send_command("actor_state_diff", {"before": before, "after": after, "epsilon": 1.0})
    except Exception:
        diff = {}
    return {"ok": True, **moved, "diff": diff}


def expect_log(regex: str, timeout_sec: float = 30.0, since_offset: int = 0) -> dict[str, Any]:
    from backend.bridge import send_command

    pattern = (regex or r"\[DUCKY-TEST\]").strip() or r"\[DUCKY-TEST\]"
    deadline = time.time() + min(max(float(timeout_sec or 0), 1.0), _WAIT_CAP_S)
    offset = int(since_offset or 0)
    matches: list[str] = []
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = send_command(
            "get_editor_log",
            {"last_n": 200, "since_offset": offset, "regex": pattern},
        )
        if isinstance(last, dict):
            offset = int(last.get("offset") or offset)
            matches = [str(x) for x in (last.get("lines") or [])]
            if matches:
                return {"ok": True, "log_matches": matches, "count": len(matches), "log_offset": offset}
        time.sleep(0.5)
    return {
        "ok": False,
        "error": "log pattern not seen",
        "log_matches": matches,
        "count": 0,
        "log_offset": offset if isinstance(last, dict) else since_offset,
    }


def _as_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _from_epic_status(parsed: dict[str, Any]) -> dict[str, Any]:
    playing = bool(
        parsed.get("playing")
        or parsed.get("isPlaying")
        or parsed.get("in_session")
        or parsed.get("sessionActive")
    )
    count = parsed.get("player_count")
    if count is None:
        count = parsed.get("playerCount") or parsed.get("numPlayers")
    try:
        n = int(count or 0)
    except (TypeError, ValueError):
        n = 0
    has = bool(parsed.get("has_player") or parsed.get("hasPlayer") or n > 0)
    return {"playing": playing, "has_player": has, "player_count": n}
