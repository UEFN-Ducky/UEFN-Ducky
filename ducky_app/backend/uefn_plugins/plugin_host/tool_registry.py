"""Plugin MCP tools: who owns each, which a run may use, intent rows, MCPs-tab rows, call slots."""

from __future__ import annotations

import contextlib
import re
import threading
from typing import Any, Iterator

from backend.uefn_plugins.plugin_version import format_plugin_version


def _unregister_plugin_mcp_tools(tool_names: set[str]) -> None:
    """Remove a plugin's tools from the shared FastMCP server (best-effort).

    Without this, a reload leaves the old ``api.tool`` wrapper registered, and a
    reinstalled plugin keeps serving its previous tool code. Must hold ``_LOCK``.
    """
    if not tool_names:
        return
    try:
        from backend.server import mcp

        manager = getattr(mcp, "_tool_manager", None)
        if manager is None:
            return
        remove = getattr(manager, "remove_tool", None)
        for name in tool_names:
            try:
                if callable(remove):
                    remove(name)
                else:
                    getattr(manager, "_tools", {}).pop(name, None)
            except Exception:
                getattr(manager, "_tools", {}).pop(name, None)
    except Exception:
        _log.debug("could not unregister plugin MCP tools", exc_info=True)


def set_active_uefn_agent_plugin_ids(plugin_ids: list[str] | None) -> None:
    """Scope which UEFN app-plugin agent.tools are available for this run.

    Called by the embedded agent with the chat's override. None = unset (external
    MCP / no scope → all enabled plugins keep their tools). A list (even empty)
    means opt-in only for those plugin ids.
    """
    if plugin_ids is None:
        _ACTIVE_UEFN_AGENT_IDS.set(None)
        return
    from backend.uefn_plugins.store import normalize_plugin_id

    ids: list[str] = []
    for item in plugin_ids:
        try:
            pid = normalize_plugin_id(str(item))
        except ValueError:
            continue
        if pid not in ids:
            ids.append(pid)
    _ACTIVE_UEFN_AGENT_IDS.set(tuple(ids))


def uefn_agent_tools_allowed(plugin_id: str) -> bool:
    """True when the plugin is enabled and opted in for this run (or unscoped). The shared-
    spaces provider's tools are part of the app, like core tools: never opted out per chat."""
    if not is_plugin_enabled(plugin_id):
        return False
    active = _ACTIVE_UEFN_AGENT_IDS.get()
    if active is None:
        return True
    if plugin_id in active:
        return True
    from backend.uefn_plugins.spaces import provider_plugin

    return plugin_id == provider_plugin()


def plugin_intent_rows() -> list[tuple[re.Pattern[str], frozenset[str]]]:
    ensure_plugins_loaded()
    active = _ACTIVE_UEFN_AGENT_IDS.get()
    with _LOCK:
        if active is None:
            return [(pat, tools) for _pid, pat, tools in _PLUGIN_INTENT_ROWS]
        allowed = set(active)
        return [(pat, tools) for pid, pat, tools in _PLUGIN_INTENT_ROWS if pid in allowed]


def plugin_tool_names() -> frozenset[str]:
    ensure_plugins_loaded()
    with _LOCK:
        return frozenset(_PLUGIN_TOOL_NAMES)


def store_plugin_tool_names() -> frozenset[str]:
    """Every tool a Store plugin registered, without waiting for plugins to load."""
    with _LOCK:
        return frozenset(_PLUGIN_TOOL_NAMES) | frozenset(_PLUGIN_TOOL_OWNER)


def plugin_for_tool(name: str) -> str:
    """Owning plugin id for an MCP tool, or ``ducky`` for core host tools."""
    n = (name or "").strip()
    if not n:
        return "ducky"
    with _LOCK:
        owner = _PLUGIN_TOOL_OWNER.get(n)
    if owner:
        return owner
    try:
        from backend.mcp_plugins.registry import parse_plugin_tool

        parsed = parse_plugin_tool(n)
        if parsed:
            return parsed[0]
    except Exception:
        pass
    return "ducky"


def plugin_plan_tools() -> frozenset[str]:
    """Union of explicit ``agent.tools.plan_tools`` from loaded desktop plugins."""
    ensure_plugins_loaded()
    out: set[str] = set()
    with _LOCK:
        out.update(_PLUGIN_PLAN_TOOLS)
        for meta in _CONTRIBUTIONS.get("agent_tools", {}).values():
            if not isinstance(meta, dict):
                continue
            for name in meta.get("plan_tools") or []:
                if isinstance(name, str) and name.strip():
                    out.add(name.strip())
    return frozenset(out)


def plugin_destructive_desktop_tools() -> frozenset[str]:
    """Union of ``agent.tools.destructive_tools`` from loaded desktop plugins."""
    ensure_plugins_loaded()
    out: set[str] = set()
    with _LOCK:
        out.update(_PLUGIN_DESTRUCTIVE_TOOLS)
        for meta in _CONTRIBUTIONS.get("agent_tools", {}).values():
            if not isinstance(meta, dict):
                continue
            for name in meta.get("destructive_tools") or []:
                if isinstance(name, str) and name.strip():
                    out.add(name.strip())
    return frozenset(out)


def sensitive_tool_plugin(name: str) -> str:
    """Owning plugin id when ``name`` is one of its ``agent.tools.sensitive`` tools, else ''."""
    n = (name or "").strip()
    with _LOCK:
        pid = _PLUGIN_TOOL_OWNER.get(n, "")
        meta = _CONTRIBUTIONS.get("agent_tools", {}).get(pid) if pid else None
    if not isinstance(meta, dict):
        return ""
    sensitive = meta.get("sensitive")
    if sensitive is True or (isinstance(sensitive, list) and n in sensitive):
        return pid
    return ""


def sensitive_tool_block_reason(name: str, *, listing: bool = False) -> str:
    """Why the calling run's model may not use this sensitive plugin tool ('' = it may)."""
    pid = sensitive_tool_plugin(name)
    if not pid:
        return ""
    from backend.uefn_plugins.model_gate import block_reason

    return block_reason(pid, name, label=_plugin_label(pid), listing=listing)


def filter_uefn_plugin_tools(tools: list[Any]) -> list[Any]:
    """Drop UEFN app-plugin tools that are Store-disabled or not in the run allowlist.

    ``active is None`` follows Store enable (all enabled plugins). Chat allowlists
    are not rewritten on disable — membership stays; this filter hides tools until
    the plugin is enabled again.
    """
    ensure_plugins_loaded()
    try:
        from backend.tools.support.plugin_gate import HOST_DISK_TOOLS as _host_disk
    except Exception:
        _host_disk = frozenset()
    active = _ACTIVE_UEFN_AGENT_IDS.get()
    with _LOCK:
        owner = dict(_PLUGIN_TOOL_OWNER)
        for pid, names in _API_TOOL_REGISTRY.items():
            for name in names:
                owner.setdefault(name, pid)
        for pid, _pat, names in _PLUGIN_INTENT_ROWS:
            for name in names:
                owner.setdefault(name, pid)
        # A disabled plugin's unload() drops its _PLUGIN_TOOL_OWNER rows on a
        # background thread; the last owner still hides its tools.
        for name, pid in _TOOL_LAST_OWNER.items():
            owner.setdefault(name, pid)
    if not owner:
        return list(tools)
    enabled: dict[str, bool] = {}  # once per plugin, not once per tool (a settings read each)
    out: list[Any] = []
    for t in tools:
        name = getattr(t, "name", None)
        if name not in owner:
            out.append(t)
            continue
        pid = owner[name]
        if name in _host_disk:
            out.append(t)
            continue
        if pid not in enabled:
            enabled[pid] = is_plugin_enabled(pid)
        if not enabled[pid]:
            try:
                from backend.tools.support.plugin_gate import heal_enable_store_plugin

                if heal_enable_store_plugin(pid) and is_plugin_enabled(pid):
                    out.append(t)
            except Exception:
                pass
            continue
        if active is not None and pid not in active:
            continue
        if sensitive_tool_block_reason(name, listing=True):
            continue
        out.append(t)
    return out


def _plugin_tool_names_for(pid: str, meta: dict[str, Any] | None = None) -> list[str]:
    names: set[str] = set()
    if isinstance(meta, dict):
        for name in meta.get("tools") or []:
            if isinstance(name, str) and name.strip():
                names.add(name.strip())
    with _LOCK:
        names.update(_API_TOOL_REGISTRY.get(pid) or ())
    return sorted(names)


def _rebuild_intent_row(pid: str, tool_names: set[str], intent_re: re.Pattern[str] | None = None) -> None:
    """Replace this plugin's intent row (must hold ``_LOCK``)."""
    _PLUGIN_INTENT_ROWS[:] = [row for row in _PLUGIN_INTENT_ROWS if row[0] != pid]
    if not tool_names:
        return
    if intent_re is None:
        raw = _API_INTENT_PATTERNS.get(pid)
        if raw:
            try:
                intent_re = re.compile(raw, re.I)
            except re.error:
                intent_re = None
    if intent_re is None:
        intent_re = re.compile(rf"\b{re.escape(pid)}\b", re.I)
    _PLUGIN_INTENT_ROWS.append((pid, intent_re, frozenset(tool_names)))


def _record_plugin_api_tool(
    pid: str,
    tool_name: str,
    *,
    intent: str | None = None,
    listener: bool = False,
) -> None:
    """Track a tool registered via ``api.tool()`` (survives contribution reloads)."""
    with _LOCK:
        _API_TOOL_REGISTRY.setdefault(pid, set()).add(tool_name)
        if isinstance(intent, str) and intent.strip():
            _API_INTENT_PATTERNS[pid] = intent.strip()
        meta = _CONTRIBUTIONS["agent_tools"].setdefault(pid, {"category": pid, "tools": []})
        tools = {str(t).strip() for t in (meta.get("tools") or []) if str(t).strip()}
        tools.add(tool_name)
        meta["tools"] = sorted(tools)
        _PLUGIN_TOOL_NAMES.add(tool_name)
        _PLUGIN_TOOL_OWNER[tool_name] = pid
        _TOOL_LAST_OWNER[tool_name] = pid
        if listener:
            _PLUGIN_HOST_ONLY_TOOLS.discard(tool_name)
        else:
            _PLUGIN_HOST_ONLY_TOOLS.add(tool_name)
        _rebuild_intent_row(pid, tools)


def is_plugin_host_only_tool(name: str) -> bool:
    """True when ``api.tool(listener=False)`` — no UEFN editor listener required."""
    n = (name or "").strip()
    if not n:
        return False
    ensure_plugins_loaded()
    with _LOCK:
        return n in _PLUGIN_HOST_ONLY_TOOLS


def plugin_tool_unavailable_reason(name: str) -> str:
    """Why a plugin tool this process served before is gone now ('' when it is served,
    or was never a plugin tool here)."""
    n = (name or "").strip()
    with _LOCK:
        if not n or n in _PLUGIN_TOOL_OWNER:
            return ""
        pid = _TOOL_LAST_OWNER.get(n, "")
        failure = _LOAD_FAILURES.get(pid, "")
    if not pid:
        return ""
    label = _plugin_label(pid)
    if failure:
        return (
            f"{n} belongs to the {label} plugin, which failed to load: {failure} "
            "Fix the plugin and reload it; until then none of its tools exist. "
            "This has nothing to do with the UEFN listener."
        )
    return f"{n} belongs to the {label} plugin, which is not loaded right now (reloading, turned off or removed)."


def is_uefn_agent_tool_plugin(plugin_id: str) -> bool:
    """True if an installed UEFN plugin contributes agent tools (manifest or api.tool)."""
    from backend.uefn_plugins.store import load_plugin_manifest, normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return False
    with _LOCK:
        if pid in _API_TOOL_REGISTRY and _API_TOOL_REGISTRY[pid]:
            return True
        if pid in _CONTRIBUTIONS["agent_tools"]:
            return True
    manifest = load_plugin_manifest(pid)
    if not manifest:
        return False
    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    tools_meta = contributes.get("agent.tools") or contributes.get("agent_tools")
    return bool(tools_meta)


def uefn_agent_tool_rows() -> list[dict[str, Any]]:
    """Tools & MCPs rows for enabled UEFN plugins that contribute agent.tools.

    Default-on for every ducky (deny-list opt-out on the profile). Schemas are
    still intent-gated per turn so they do not bloat the prompt until needed.
    """
    ensure_plugins_loaded()
    from backend.uefn_plugins.store import appdata_uefn_plugins_dir, load_plugin_manifest

    rows: list[dict[str, Any]] = []
    with _LOCK:
        agent_tools = dict(_CONTRIBUTIONS["agent_tools"])
    for pid, meta in sorted(agent_tools.items()):
        if not is_plugin_enabled(pid):
            continue
        manifest = load_plugin_manifest(pid) or {}
        tool_list = _plugin_tool_names_for(pid, meta if isinstance(meta, dict) else None)
        n = len(tool_list)
        desc = str(manifest.get("description") or "").strip()
        if not desc:
            cat = str((meta or {}).get("category") or pid)
            desc = f"App plugin tools ({cat})" + (f" — {n} tools" if n else "")
        rows.append(
            {
                "id": pid,
                "label": str(manifest.get("label") or pid),
                "description": desc,
                "version": format_plugin_version(manifest.get("version")),
                "kind": "uefn_plugin",
                "transport": "builtin",
                "tags": ["app-plugin"],
                "enabled": True,
                "default_enabled": True,
                "requirements_note": "",
                "setup_steps": [],
                "tool_prefix": "",
                "tool_names": tool_list,
                "intents": [],
                "path": str(appdata_uefn_plugins_dir() / pid),
            }
        )
    return rows


def uefn_plugin_tool_group_rows() -> list[dict[str, Any]]:
    """Settings → MCPs rows for desktop plugins that extend the app MCP server."""
    ensure_plugins_loaded()
    from backend.uefn_plugins.store import list_uefn_plugins

    rows: list[dict[str, Any]] = []
    with _LOCK:
        agent_tools = dict(_CONTRIBUTIONS["agent_tools"])
        api_reg = {k: set(v) for k, v in _API_TOOL_REGISTRY.items()}

    for plug in list_uefn_plugins():
        pid = str(plug.get("id") or "").strip()
        if not pid:
            continue
        contrib = plug.get("contributes") if isinstance(plug.get("contributes"), dict) else {}
        tools_meta = contrib.get("agent.tools") or contrib.get("agent_tools")
        meta = agent_tools.get(pid) if isinstance(agent_tools.get(pid), dict) else None
        if meta is None and isinstance(tools_meta, dict):
            meta = {
                "category": str(tools_meta.get("category") or pid),
                "tools": [
                    str(n).strip()
                    for n in (tools_meta.get("tools") or [])
                    if isinstance(n, str) and n.strip()
                ],
            }
        elif meta is None and tools_meta is True:
            meta = {"category": pid, "tools": []}
        tool_list = _plugin_tool_names_for(pid, meta)
        if not tool_list and not tools_meta and pid not in api_reg:
            continue
        if not tool_list and pid in api_reg:
            tool_list = sorted(api_reg[pid])
        n = len(tool_list)
        desc = str(plug.get("description") or "").strip()
        if not desc:
            cat = str((meta or {}).get("category") or pid)
            desc = f"From plugin ({cat})" + (f" — {n} tools" if n else "")
        enabled = bool(plug.get("enabled"))
        rows.append(
            {
                "id": pid,
                "label": str(plug.get("label") or pid),
                "description": desc,
                "version": format_plugin_version(plug.get("version")),
                "kind": "uefn_plugin",
                "transport": "builtin",
                "tags": ["app-plugin", "from-plugin"],
                "enabled": enabled,
                "default_enabled": bool(plug.get("default_enabled", True)),
                "requirements_note": "",
                "setup_steps": [],
                "tool_prefix": "",
                "tool_names": tool_list,
                "intents": [],
                "path": str(plug.get("path") or ""),
            }
        )
    rows.sort(key=lambda r: str(r.get("label") or r.get("id") or "").lower())
    return rows


def count_uefn_plugin_tools(plugin_id: str) -> int:
    """Tool count for the MCPs tab Test button."""
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError:
        return 0
    ensure_plugins_loaded()
    with _LOCK:
        meta = _CONTRIBUTIONS["agent_tools"].get(pid)
    return len(_plugin_tool_names_for(pid, meta if isinstance(meta, dict) else None))


def manifest_tool_concurrency(manifest: dict[str, Any]) -> int:
    raw = manifest.get("tool_concurrency")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return DEFAULT_TOOL_CONCURRENCY
    return min(raw, 64)


def _tool_slots(pid: str) -> tuple[threading.BoundedSemaphore, int]:
    with _LOCK:
        limit = _TOOL_CONCURRENCY.get(pid, DEFAULT_TOOL_CONCURRENCY)
        slots = _TOOL_SLOTS.get(pid)
        if slots is None:
            slots = _TOOL_SLOTS[pid] = threading.BoundedSemaphore(limit)
        return slots, limit


@contextlib.contextmanager
def plugin_tool_slot(pid: str) -> Iterator[None]:
    """Hold one of ``pid``'s tool slots for a call. A call from inside the same plugin's call
    (same thread) reuses its slot, so a limit of 1 cannot deadlock the plugin on itself."""
    held: set[str] = getattr(_HELD_SLOTS, "pids", None) or set()
    if pid in held:
        yield
        return
    slots, limit = _tool_slots(pid)
    if not slots.acquire(timeout=TOOL_SLOT_WAIT_S):
        raise ValueError(
            f"Plugin '{pid}' is busy ({limit} of its calls are running); try again in a moment."
        )
    _HELD_SLOTS.pids = held | {pid}
    try:
        yield
    finally:
        _HELD_SLOTS.pids = held
        slots.release()


# Names from the rest of the host, bound after every definition above so the host
# modules can import each other in any order.
from backend.uefn_plugins.plugin_host.boot import ensure_plugins_loaded  # noqa: E402
from backend.uefn_plugins.plugin_host.errors import _plugin_label  # noqa: E402
from backend.uefn_plugins.plugin_host.lifecycle import is_plugin_enabled  # noqa: E402
from backend.uefn_plugins.plugin_host.state import (  # noqa: E402
    _ACTIVE_UEFN_AGENT_IDS,
    _API_INTENT_PATTERNS,
    _API_TOOL_REGISTRY,
    _CONTRIBUTIONS,
    _HELD_SLOTS,
    _LOAD_FAILURES,
    _LOCK,
    _PLUGIN_DESTRUCTIVE_TOOLS,
    _PLUGIN_HOST_ONLY_TOOLS,
    _PLUGIN_INTENT_ROWS,
    _PLUGIN_PLAN_TOOLS,
    _PLUGIN_TOOL_NAMES,
    _PLUGIN_TOOL_OWNER,
    _TOOL_CONCURRENCY,
    _TOOL_LAST_OWNER,
    _TOOL_SLOTS,
    DEFAULT_TOOL_CONCURRENCY,
    TOOL_SLOT_WAIT_S,
    _log,
)
