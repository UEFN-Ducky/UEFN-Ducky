"""Live Tester device graph: UEFN MCP first, listener second, workspace last.

Never infer UEFN/listener health from a heavy snapshot. Connections uses GET
health + Epic TCP; Tester must use the same cheap probes so a slow graph walk
cannot paint "offline" while the header shows connected.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from typing import Any

_LISTENER_SNAPSHOT_TIMEOUT_SEC = 12.0
_EPIC_CALL_TIMEOUT_SEC = 8.0
_PROGRAMMATIC = "editor_toolset.toolsets.programmatic.ProgrammaticToolset"

# One ProgrammaticToolset call — DeviceToolset has no list-placed, ActorTools
# has no census. Sandbox may refuse `import unreal`; then we degrade to listener.
_EPIC_CENSUS_SCRIPT = """
def run():
    import unreal
    actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    nodes = []
    for actor in list(actor_sub.get_all_level_actors() or []):
        try:
            cls = actor.get_class().get_name() if actor.get_class() else ""
        except Exception:
            continue
        cl = (cls or "").lower()
        if "device" not in cl and "versedevice" not in cl:
            continue
        path = str(actor.get_path_name())
        try:
            label = str(actor.get_actor_label())
        except Exception:
            label = path.rsplit(".", 1)[-1]
        kind = "verse_script" if "versedevice" in cl else "creative_device"
        nodes.append({
            "id": path,
            "label": label,
            "class": cls,
            "kind": kind,
            "path": path,
            "placed": True,
        })
        if len(nodes) >= 100:
            break
    return {"nodes": nodes, "edges": [], "count": len(nodes), "source": "epic"}
"""


def tester_uefn_status() -> dict[str, Any]:
    """Cheap probes only — same signals as the header Connections menu."""
    from backend.bridge import configured_listener_port, listener_get_health
    from backend.mcp_plugins.epic import probe_epic_mcp

    epic = probe_epic_mcp()
    epic_online = bool(epic.get("epic_mcp_online"))
    health = listener_get_health(configured_listener_port(), timeout=0.5)
    listener_online = bool(health and health.get("status") == "ok")
    return {
        "epic_mcp_online": epic_online,
        "listener_online": listener_online,
        "uefn_online": epic_online or listener_online,
        "epic_mcp_url": str(epic.get("epic_mcp_url") or ""),
        "epic_mcp_reason": str(epic.get("epic_mcp_reason") or ""),
    }


def _call_epic_tool(toolset_name: str, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
    """Sync nested `unreal__call_tool`. Patchable in tests."""
    from backend.mcp_plugins.client_pool import get_plugin_pool

    args = {
        "toolset_name": toolset_name,
        "tool_name": tool_name,
        "arguments": arguments or {},
    }

    async def _run() -> str:
        return await get_plugin_pool().call_tool("unreal__call_tool", args)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(_run())).result(timeout=_EPIC_CALL_TIMEOUT_SEC)
    return asyncio.run(_run())


def _coerce_snapshot(raw: Any, *, source: str) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                return None
            try:
                raw = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                return None
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("returnValue"), dict):
        raw = raw["returnValue"]
    nested = raw.get("result")
    if isinstance(nested, dict) and "nodes" in nested:
        raw = nested
    nodes = raw.get("nodes")
    if not isinstance(nodes, list):
        return None
    out = dict(raw)
    out.setdefault("edges", [])
    out["count"] = len(nodes)
    out["source"] = str(out.get("source") or source)
    return out


def try_epic_snapshot() -> dict[str, Any] | None:
    """UEFN MCP census. One ProgrammaticToolset script — never execute_python."""
    text = _call_epic_tool(
        _PROGRAMMATIC,
        "execute_tool_script",
        {"script": _EPIC_CENSUS_SCRIPT},
    )
    return _coerce_snapshot(text, source="epic")


def try_listener_snapshot(kwargs: dict[str, Any] | None = None) -> dict[str, Any] | None:
    from backend.bridge import send_command

    params = {
        "limit": 100,
        "include_editables": True,
        "include_events": True,
        **(kwargs or {}),
    }
    # Full wait/retry (503 + idle). snapshot_live() must not call this when health is down.
    raw = send_command("device_graph_snapshot", params, timeout=_LISTENER_SNAPSHOT_TIMEOUT_SEC)
    return _coerce_snapshot(raw, source="listener") if isinstance(raw, dict) else None


def snapshot_live(kwargs: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return (snapshot, live_source, error). Epic first, listener second. Never Python."""
    status = tester_uefn_status()
    err: str | None = None
    if status["epic_mcp_online"]:
        try:
            snap = try_epic_snapshot()
            if snap is not None and (snap.get("nodes") or []):
                return snap, "epic", None
            err = "UEFN MCP census returned no devices"
        except Exception as exc:
            err = f"UEFN MCP snapshot failed: {exc}"
    if status["listener_online"]:
        try:
            snap = try_listener_snapshot(kwargs)
            if snap is not None:
                return snap, "listener", err
            err = (err + "; " if err else "") + "listener snapshot empty"
        except Exception as exc:
            err = (err + "; " if err else "") + f"listener snapshot failed: {exc}"
    elif not status["epic_mcp_online"]:
        err = err or "UEFN offline"
    return None, None, err


def list_tester_devices(
    *,
    project_root: str = "",
    snapshot_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Device outliner payload for the Tester dock and `tester_list_devices` MCP tool."""
    from backend.testing.device_sim import device_graph_audit, scan_verse_devices_from_files

    status = tester_uefn_status()
    live, live_source, err = snapshot_live(snapshot_kwargs)
    workspace = scan_verse_devices_from_files(project_root) if project_root else {
        "nodes": [],
        "edges": [],
        "count": 0,
        "source": "workspace",
    }
    audit = device_graph_audit(live) if live else None
    return {
        "ok": True,
        "uefn_online": bool(status["uefn_online"]),
        "epic_mcp_online": bool(status["epic_mcp_online"]),
        "listener_online": bool(status["listener_online"]),
        "live_source": live_source,
        "live": live,
        "workspace": workspace,
        "audit": audit,
        "error": err,
    }
