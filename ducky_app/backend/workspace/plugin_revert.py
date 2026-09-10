"""Dispatch a changeset inverse back to the program that made the edit.

UEFN inverses go through the listener. Plugin inverses (Blender, Unity, …) go
through the plugin's registered FastMCP tool — never ``send_command``.
"""

from __future__ import annotations

import inspect
import json
from typing import Any, Iterable, Mapping

PROGRAM_UEFN = "uefn"


def program_of_slot(slot: str) -> str:
    text = (slot or "").strip()
    if "://" not in text:
        return "file"
    return text.split("://", 1)[0] or PROGRAM_UEFN


def program_of_entry(entry: Mapping[str, Any]) -> str:
    spec = entry.get("editor") if isinstance(entry.get("editor"), Mapping) else {}
    prog = str((spec or {}).get("program") or "")
    return prog or program_of_slot(str(entry.get("path") or ""))


def programs_of_entries(entries: Iterable[Mapping[str, Any]]) -> list[str]:
    seen: list[str] = []
    for entry in entries:
        prog = program_of_entry(entry)
        if prog in ("", "file") or prog in seen:
            continue
        seen.append(prog)
    return seen


def program_offline_reason(program: str) -> str:
    """Why this program cannot take an inverse right now, or '' if it can."""
    prog = (program or "").strip()
    if prog in ("", "file"):
        return ""
    if prog == PROGRAM_UEFN:
        from backend.bridge import configured_listener_port, listener_get_health

        if listener_get_health(configured_listener_port(), timeout=0.35) is None:
            return "UEFN listener is offline — open UEFN before undoing editor changes"
        return ""
    try:
        from backend.uefn_plugins.host import plugin_connection_for_program

        row = plugin_connection_for_program(prog)
    except Exception:
        row = None
    if row is not None:
        if row.get("online"):
            return ""
        return str(row.get("detail") or f"open {prog} — it is disconnected; editor changes were not touched")
    # ponytail: {program}_status if the plugin shipped one; no tool = don't guess.
    try:
        from backend.server import mcp

        tool = mcp._tool_manager.get_tool(f"{prog}_status")
    except Exception:
        tool = None
    if tool is None:
        return ""
    try:
        result = call_plugin_tool(f"{prog}_status", {})
    except Exception as exc:  # noqa: BLE001 — offline is a refuse, not a crash
        return f"open {prog} to undo editor changes ({exc})"
    if _status_says_offline(result):
        return f"open {prog} — it is disconnected; editor changes were not touched"
    return ""


def ensure_programs_ready(programs: Iterable[str]) -> None:
    for prog in programs:
        reason = program_offline_reason(prog)
        if reason:
            raise ConnectionError(reason)


def _status_says_offline(result: Any) -> bool:
    data: Any = result
    if isinstance(result, str):
        text = result.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            low = text.lower()
            return low.startswith("error") or "disconnect" in low or "offline" in low
    if isinstance(data, Mapping):
        if data.get("connected") is False or data.get("online") is False:
            return True
        return str(data.get("status") or "").lower() in ("offline", "disconnected", "error")
    return False


def post_inverse_step(command: str, params: Mapping[str, Any] | None, *, program: str = PROGRAM_UEFN) -> Any:
    """Run one inverse step against the program that owns the slot."""
    prog = (program or PROGRAM_UEFN).strip() or PROGRAM_UEFN
    payload = dict(params or {})
    if prog == PROGRAM_UEFN:
        from backend.bridge import send_command

        return send_command(command, payload, timeout=30.0)
    return call_plugin_tool(command, payload)


def call_plugin_tool(name: str, params: Mapping[str, Any] | None) -> Any:
    """Sync-call a FastMCP tool registered by a desktop plugin."""
    from backend.server import mcp

    tool = mcp._tool_manager.get_tool(name)
    if tool is None:
        raise ConnectionError(f"open/enable the plugin that provides {name!r}")
    fn = getattr(tool, "fn", None) or getattr(tool, "handler", None)
    if fn is None:
        raise ConnectionError(f"{name!r} has no callable")
    result = fn(**_kwargs_for(fn, dict(params or {})))
    if isinstance(result, str) and result.lower().lstrip().startswith("error"):
        raise RuntimeError(result)
    return result


def _kwargs_for(fn: Any, params: dict[str, Any]) -> dict[str, Any]:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return params
    kwargs: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if name in params:
            kwargs[name] = params[name]
    return kwargs
