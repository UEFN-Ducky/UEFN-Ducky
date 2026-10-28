"""Host-side panel meta tools — status, errors, chat management (no UEFN listener)."""

from __future__ import annotations

import asyncio

import json
import sys
import threading
import time
from itertools import islice
from typing import Any, Literal, Optional

from frontend.agent_profiles import get_agent_profile, list_agent_profiles_available
from frontend.archive_folder import is_archive_folder_id
from frontend.error_log import read_errors
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings
from frontend.ui_web.project_switch import get_panel_project_info, list_panel_projects
from frontend.ui_web.project_chats import (
    append_message,
    conversation_project_slug,
    create_conversation,
    delete_conversation,  # noqa: F401 - test_subagent_recycle patches it here as a guard
    list_conversations,
    load_conversation,
    load_folders,
    move_conversation,
    project_slug,
    project_slug_display_name,
    rename_conversation,
    save_conversation,
)

_SUBAGENT_HANDOFF_PROMPT = (
    "HANDOFF REQUIRED. Write a complete handoff for a fresh replacement of yourself "
    "(same specialist, new chat). Include: goal; what was done; files/assets/devices "
    "touched (paths and labels); key decisions; remaining work; exact next steps; "
    "blockers; and anything a new you must know to continue without redoing work. "
    "Be exhaustive and concrete. Do not start new work — only the handoff."
)
from backend.util.json_util import tool_json
from backend.bridge.status import ListenerStatusState, fetch_listener_status
from backend.server import mcp

_status_state = ListenerStatusState()
_MAX_MESSAGE_CHARS = 8000
_MAX_READ_MESSAGES = 80


def _project_root() -> str:
    return PanelSettings.load().uefn_project_root.strip()


def _island_arg(project: str) -> str | None:
    """A launch/restart ``project``: None for the selected island, a path as given, else
    the root of a known project's name, slug or content mount."""
    import os

    key = (project or "").strip()
    if not key:
        return None
    if os.path.exists(key):
        return key
    return _resolve_project_root_arg(key)


def _resolve_project_root_arg(project: str) -> str:
    """Resolve a project reference (path, display name, or slug) to its root.

    Empty → the panel's active project, so existing callers are unaffected.
    """
    key = (project or "").strip()
    if not key:
        return _project_root()
    from frontend.ui_web.project_switch import list_panel_projects, normalize_project_path

    low = key.lower()
    norm_key = normalize_project_path(key).lower()
    projects = list_panel_projects()
    for p in projects:
        path = str(p.get("path") or "")
        if norm_key and path.lower() == norm_key:
            return path
        if str(p.get("name") or "").strip().lower() == low or str(p.get("slug") or "").lower() == low:
            return path
    # The live UEFN name is the island's content mount (root plugin), which can be a
    # GUID no folder is called: match it against each island's own descriptors.
    from frontend.project_kind import is_island_identity

    for p in projects:
        if p.get("kind") == "uefn" and is_island_identity(str(p.get("path") or ""), key):
            return str(p.get("path") or "")
    names = ", ".join(str(p.get("name") or "") for p in projects) or "(none)"
    raise ValueError(
        f"Unknown project {project!r}. Known projects: {names} — see ducky_list_projects."
    )


def _truncate_text(text: str, limit: int = _MAX_MESSAGE_CHARS) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _serialize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages[-_MAX_READ_MESSAGES:]:
        role = str(m.get("role", ""))
        content = m.get("content", "")
        if isinstance(content, str) and content.strip():
            out.append({"role": role, "content": _truncate_text(content), "ts": m.get("ts")})
            continue
        for block in m.get("blocks") or []:
            if block.get("type") != "tool_call":
                continue
            out.append(
                {
                    "role": "tool",
                    "name": block.get("name", "?"),
                    "status": block.get("status", "?"),
                    "ts": m.get("ts"),
                }
            )
    return out


_DESC_TRUNC = 200
_DISPATCHER_BLOCKLIST = frozenset({"ducky_call_tool", "ducky_get_tools"})
# Old names kept callable through ducky_call_tool only; they are not registered, so
# tools/list stays one name per job. Value = the tool that serves the call.
HIDDEN_ALIAS_TOOLS: dict[str, str] = {
    "ducky_find_tools": "ducky_get_tools",
    "ducky_recycle_subagent": "ducky_recycle_member",
}


def _mode_discovery(name: str, catalog: dict[str, Any]) -> dict[str, Any]:
    from backend.agent.run_context import current_mode
    from backend.agent.toolsets.plan_safe import mode_tool_block_reason

    mode = current_mode()
    if mode not in ("ask", "plan"):
        return {}
    reason = mode_tool_block_reason(mode, name, {}, catalog, discovery=True)
    return {
        "allowed": not bool(reason),
        "mode": mode,
        "mode_reason": reason or f"Allowed in {mode.title()} mode; dispatcher targets are checked at execution.",
        "hint": reason or "Call with ducky_call_tool(name, arguments) for an allowed target.",
    }


def _truncate_desc(text: str, limit: int = _DESC_TRUNC) -> str:
    from backend.agent.toolsets.tool_index import truncate_desc

    return truncate_desc(text, limit)


def _discovery_cached_connections() -> dict[str, Any]:
    """Read already-loaded caches only: no imports, probes, locks or maintenance.

    These private caches have no public snapshot API. Treat unavailable or expired
    entries as unknown; never forward arbitrary provider strings from them.
    """
    status: dict[str, Any] = {}
    epic = sys.modules.get("backend.mcp_plugins.epic")
    cache = getattr(epic, "_probe_cache", {})
    observed = cache.get("result")
    if isinstance(observed, dict) and 0 <= time.time() - cache.get("at", 0) < 5:
        online = observed.get("epic_mcp_online")
        if type(online) is bool:
            status["epic_mcp_online"] = online
            reason = observed.get("epic_mcp_reason")
            known = ("", "disabled", "unreachable", "other_editor", "port_taken")
            status["epic_mcp_reason"] = reason if reason in known else "unknown"
    host = sys.modules.get("backend.uefn_plugins.host")
    cache = getattr(host, "_CONNECTION_CACHE", {})
    # Bound the snapshot and never wait for a provider lock. A concurrent cache
    # change can invalidate iteration; the caller reports unknown in that case.
    rows = []
    for at, row in tuple(islice(cache.values(), 50)):
        if 0 <= time.monotonic() - at < 30 and type(row.get("online")) is bool:
            rows.append({"online": row["online"]})
    if rows:
        status["plugin_connections"] = rows
    return status


def _discovery_miss_context() -> dict[str, Any]:
    """Report existing connection evidence without inferring tool availability."""
    context: dict[str, Any] = {
        "hint": (
            "No match in the current catalog is not proof that a tool does not exist. "
            "Check the provider's catalog/connection state, "
            "including registration and policy filtering. Report the observed "
            "missing/offline capability and any unknown cause accurately. "
            "Connection evidence below is cached, is not a complete provider "
            "inventory, and does not establish which provider owns this query. "
            "Epic online is a TCP probe, not MCP session or tool readiness."
            " Plugin rows are anonymous cached observations; provider names and "
            "details are omitted. No status probes or project maintenance run here."
        ),
    }
    try:
        context["connection_status"] = _discovery_cached_connections() or None
    except Exception:
        context["connection_status"] = None
    if context["connection_status"] is None:
        context["hint"] += " Connection status could not be read; availability is unknown."
    return context


# Last catalog revision per tool-filter scope. A chat's catalog is filtered by its own
# plugin and tool-group selection, so two chats with different filters alternating in
# one process are not a catalog change (each alternation used to start a full re-list);
# a change, or a change back, within one scope still is.
_seen_catalog_revisions: dict[tuple[Any, ...], str] = {}
_seen_lock = threading.Lock()


def _catalog_scope() -> tuple[Any, ...]:
    from backend.agent.builtin_toolsets import _ACTIVE_BUILTIN_GROUPS
    from backend.mcp_plugins.store import _ACTIVE_PLUGIN_IDS
    from backend.uefn_plugins.host import _ACTIVE_UEFN_AGENT_IDS

    return (_ACTIVE_BUILTIN_GROUPS.get(), _ACTIVE_PLUGIN_IDS.get(), _ACTIVE_UEFN_AGENT_IDS.get())


def _note_catalog_revision(revision: str) -> None:
    """A new tool catalog (plugin loaded, MCP server connected): re-index tool search soon."""
    if not revision:
        return
    try:
        scope = _catalog_scope()
    except Exception:
        scope = ()
    with _seen_lock:
        prev = _seen_catalog_revisions.get(scope)
        _seen_catalog_revisions[scope] = revision
        while len(_seen_catalog_revisions) > 64:
            _seen_catalog_revisions.pop(next(iter(_seen_catalog_revisions)))
    if prev and prev != revision:
        try:
            from backend.search import indexer

            indexer.request_refresh("tools")
        except Exception:
            pass


def _ranked_tool_names(
    tools: list[Any], by_name: dict[str, Any], text: str, limit: int, *, all_words: bool = False
) -> list[str]:
    """Meaning + keyword hits from the search index first, then the old all-words matches
    (every one with ``all_words``, so paging reaches each match the old pager did)."""
    from backend.agent.toolsets.tool_index import search_tool_catalog

    names: list[str] = []
    try:
        from backend.search import query as search_query

        names = [r["tool"] for r in search_query.search_tools(text, set(by_name), limit=limit) if r.get("tool") in by_name]
    except Exception:
        names = []
    seen = set(names)
    for row in search_tool_catalog(tools, text, limit=None if all_words else limit)["matches"]:
        if row["name"] not in seen:
            seen.add(row["name"])
            names.append(row["name"])
    return names


@mcp.tool()
async def ducky_get_tools(
    name: str = "",
    pattern: str = "",
    limit: int = 10,
    pretty: bool = False,
    offset: int = 0,
    diagnostics: bool = False,
    server_id: str = "",
) -> str:
    """Discover full MCP schemas by canonical name/alias or ranked search tokens.
    Search returns total, next_offset and a content revision; use offset to page.
    Known unavailable tools stay visible. Discovery does not prove call readiness.
    diagnostics=true returns sanitized cached MCP status without connecting; server_id
    optionally selects one server. It uses the caller's mode, never a policy override.
    """
    from backend.agent.tools import list_mcp_tools, resolve_invented_tool_name
    from backend.agent.toolsets.excluded import EXCLUDED_TOOLS
    from backend.agent.toolsets.tool_index import tool_catalog_row, catalog_revision

    if diagnostics:
        from frontend.ui_web.mcp_catalog import build_mcp_diagnostics
        from backend.agent.run_context import current_mode

        return tool_json(build_mcp_diagnostics(server_id, current_mode()), pretty=pretty)

    n = (name or "").strip()
    p = (pattern or "").strip()
    lim = max(1, min(int(limit or 10), 50))
    tools = [t for t in await list_mcp_tools() if t.name not in EXCLUDED_TOOLS]
    by_name = {t.name: t for t in tools}
    revision = catalog_revision(tools)
    if not n and not p:
        return tool_json({
            "count": len(tools), "total": len(tools), "revision": revision,
            "hint": "Pass name= or pattern= ? empty catalog dumps stall the IDE. Then invoke only if allowed in the current mode.",
        }, pretty=pretty)

    def describe(row: dict[str, Any]) -> dict[str, Any]:
        row.update({"hint": "Call with ducky_call_tool(name, arguments). Always pass arguments.",
                    **_mode_discovery(row["name"], by_name)})
        if row["connection_state"] == "unavailable":
            row["hint"] = "Known tool is currently unavailable; wait for provider recovery before calling. " + row.get("mode_reason", "")
        return row

    _note_catalog_revision(revision)
    if n:
        canonical = n if n in by_name else resolve_invented_tool_name(n, set(by_name))
        if canonical:
            return tool_json({**describe(tool_catalog_row(by_name[canonical])), "revision": revision}, pretty=pretty)
        close = await asyncio.to_thread(_ranked_tool_names, tools, by_name, n, 10)
        return tool_json({
            "ok": False, "error": f"tool not found in current catalog: {n}",
            "close_matches": close[:10],
            "revision": revision, **_discovery_miss_context(),
        }, pretty=pretty)

    ranked = await asyncio.to_thread(_ranked_tool_names, tools, by_name, p, 50, all_words=True)
    start = max(0, int(offset))
    names = ranked[start:start + lim]
    end = start + len(names)
    page = {"revision": revision, "total": len(ranked), "count": len(names), "offset": start,
            "next_offset": end if end < len(ranked) else None,
            "matches": [tool_catalog_row(by_name[name]) for name in names]}
    page["matches"] = [describe(row) for row in page["matches"]]
    guidance = {"hint": "Invoke a discovered tool only if allowed in the current mode and available. Use next_offset for more matches."}
    if not page["total"]:
        guidance = _discovery_miss_context()
    return tool_json({"pattern": p, **page, **guidance}, pretty=pretty)


@mcp.tool()
async def ducky_find(query: str, plugin: str = "", limit: int = 10, pretty: bool = False) -> str:
    """Find the person's own things by name (projects, videos, pictures, cards, pages, files)
    in every plugin, only in copies they can open (Local and their teams). Call it FIRST when
    the person names something they have ("the promo", "my logo") — before offering to make
    or pay for one. Each hit says which plugin and copy holds it and `open_with`: the plugin
    tool that opens it (call it via ducky_call_tool). `plugin` limits it to one plugin id.
    """
    from backend.search.find import find

    return tool_json(await asyncio.to_thread(find, query, plugin=plugin, limit=limit), pretty=pretty)


@mcp.tool()
async def ducky_call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    description: str = "",
    pretty: bool = False,
) -> str:
    """Invoke any flat UEFN-Ducky MCP tool by name (Cursor CallMcpTool-style).
    Desktop plugins (blender_*) and nested MCP ({prefix}__*) use the same bridge —
    no server id. Arguments you already know (a skill's call line, an earlier call) go
    straight here; ducky_get_tools only for a schema you don't have. Always pass arguments.
    """
    from backend.agent.run_context import current_mode
    from backend.agent.tools import execute_tool, list_mcp_tools
    from backend.agent.toolsets.excluded import EXCLUDED_TOOLS
    from backend.agent.toolsets.plan_safe import mode_tool_block_reason

    mode = current_mode()
    if mode != "agent":
        try:
            catalog = {t.name: t for t in await list_mcp_tools()}
        except Exception:
            catalog = {}
        reason = mode_tool_block_reason(mode, "ducky_call_tool", {"name": name, "arguments": arguments}, catalog)
        if reason:
            return tool_json({"ok": False, "tool": str(name), "error": reason}, pretty=pretty)
    tool_name = (name or "").strip()
    # A model that wraps the call twice (ducky_call_tool → ducky_call_tool → the tool) means the
    # inner tool: run it instead of refusing the turn.
    # Models also spell the inner call {"tool": …, "args": …}.
    for _ in range(3):
        inner = arguments if isinstance(arguments, dict) else {}
        inner_name = next((str(inner[k]).strip() for k in ("name", "tool", "tool_name") if str(inner.get(k) or "").strip()), "")
        if tool_name != "ducky_call_tool" or not inner_name:
            break
        tool_name, arguments = inner_name, next((inner[k] for k in ("arguments", "args", "input") if k in inner), None)
    if not tool_name:
        raise ValueError("name must not be empty")
    if tool_name == "ducky_find_tools":
        a = arguments if isinstance(arguments, dict) else {}
        return await ducky_find_tools(
            query=str(a.get("query") or a.get("pattern") or ""),
            limit=int(a.get("limit") or 20), offset=int(a.get("offset") or 0), pretty=pretty,
        )
    tool_name = HIDDEN_ALIAS_TOOLS.get(tool_name, tool_name)
    if tool_name == "ducky_call_tool":
        raise ValueError('ducky_call_tool takes the tool itself: ducky_call_tool(name="set_workflow_folder", arguments={"workflow_id": "…", "folder": "…"})')
    if tool_name in _DISPATCHER_BLOCKLIST:
        raise ValueError(f"{tool_name} is a direct tool: call {tool_name}(...) itself, not through ducky_call_tool")
    if tool_name in EXCLUDED_TOOLS:
        raise ValueError(f"tool excluded: {tool_name}")
    args = arguments if isinstance(arguments, dict) else {}
    # description is for the model/UI only — not passed through.
    _ = (description or "").strip()
    result = await execute_tool(tool_name, args)
    if result.ok:
        from backend.agent.tool_spills import inline_or_spill

        data = result.data if isinstance(result.data, str) else tool_json(result.data)
        data = inline_or_spill(tool_name, data)
        if data.strip().startswith(("{", "[")):
            return data
        return tool_json({"ok": True, "tool": tool_name, "data": data}, pretty=pretty)
    return tool_json(
        {
            "ok": False,
            "tool": tool_name,
            "error": result.error,
            "hint": result.hint or "",
        },
        pretty=pretty,
    )


async def ducky_find_tools(query: str, limit: int = 20, pretty: bool = False, offset: int = 0) -> str:
    """Hidden alias (HIDDEN_ALIAS_TOOLS) for ducky_get_tools(pattern=query)."""
    return await ducky_get_tools(pattern=query, limit=limit, pretty=pretty, offset=offset)


@mcp.tool()
def ducky_get_status(pretty: bool = False) -> str:
    """Panel + listener + Epic UEFN MCP status. Works while UEFN is offline.

    ``epic_mcp_online`` is TCP to the nested unreal-mcp URL (default :8000/mcp).
    If false and the task needs editor Verse / entities / devices / PIC, tell the
    user ``epic_mcp_setup_steps`` — do not fall back to pruned Ducky editor tools.
    ``island_updates.behind``: another PC pushed to the open island (revision_pull, when the person agrees).
    ``island_locks``: files of the open island another PC is editing (locked): leave them alone.
    """
    from frontend import __version__

    settings = PanelSettings.load()
    result = fetch_listener_status(
        PANEL_LISTENER_PORT,
        state=_status_state,
        version=__version__,
        selected_project_root=settings.uefn_project_root,
    )
    try:
        from backend.uefn_plugins.host import attach_plugin_connections

        result = attach_plugin_connections(result)
    except Exception:
        result["plugin_connections"] = []
    try:
        from frontend.ui_web.verse_editor.urc.island_watch import for_agents, locks_for_agents

        updates = for_agents()
        if updates:
            result["island_updates"] = updates
        locks = locks_for_agents()
        if locks:
            result["island_locks"] = locks
    except Exception:
        pass
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_get_local_project(pretty: bool = False) -> str:
    """UEFN-Ducky panel project: path, name, and kind ('uefn' island or plain 'folder').

    From settings, not the live editor. A folder project is any folder (a code repo,
    say): its whole root is editable and UEFN/Verse tools do not apply to it.
    """
    return tool_json(get_panel_project_info(), pretty=pretty)


@mcp.tool()
def ducky_list_projects(pretty: bool = False) -> str:
    """Every project Ducky knows (path, name, active flag, kind): opened ones and every
    island found on disk. Duckies work in all of them, selected or not: pass full paths.

    kind is 'uefn' for an island (<Name>.uefnproject) or 'folder' for any other folder.
    active marks the person's selected project (their Content tab).
    """
    return tool_json({"projects": list_panel_projects()}, pretty=pretty)


@mcp.tool()
def ducky_set_project(path: str = "", name: str = "", pretty: bool = False) -> str:
    """Switch the person's selected project — same as picking one in the header dropdown.

    Only when the person asks to switch: it changes their Content tab. Working in another
    project needs no switch (full paths reach every project).
    Provide `path` (full folder) or `name` (display name from ducky_list_projects, e.g. MCPTest).
    Any folder works: a UEFN island, or a plain folder such as a code repo (a folder project
    gets no listener init and keeps every file it has).
    """
    from frontend.ui_web.project_switch import switch_panel_project

    # Same central switch as the header dropdown — resolves the ref, updates settings +
    # recent, pushes project_changed (so the panel resets stale diagnostics), and runs the
    # deduplicated one-time deploy inline so init_unreal.py exists before this returns.
    info = switch_panel_project(path=path, name=name, push_ui=True)
    return tool_json(info, pretty=pretty)


@mcp.tool()
def ducky_launch_uefn(project: str = "", pretty: bool = False) -> str:
    """Start Unreal Editor for Fortnite in a project (not the hub).

    Host-side Windows launch. Passes ``-ValkyrieProject=<path>`` — a bare
    ``.uefnproject`` path is ignored and the hub opens the last island instead.
    ``project`` is a folder, ``.uefnproject`` or a project name from ducky_list_projects;
    empty uses the selected island. The selected project does not change.
    Use when UEFN is closed. Does not restart a running editor.
    """
    from frontend.window_view import launch_uefn_project

    return tool_json(launch_uefn_project(_island_arg(project)), pretty=pretty)


@mcp.tool()
def ducky_close_uefn(pretty: bool = False) -> str:
    """Close Unreal Editor for Fortnite.

    Posts WM_CLOSE and presses Save if that modal appears. taskkill /F only if
    the process is still up after about 30 seconds.
    """
    from frontend.window_view import close_uefn

    return tool_json(close_uefn(), pretty=pretty)


@mcp.tool()
def ducky_restart_uefn(project: str = "", timeout: float = 180, pretty: bool = False) -> str:
    """Close UEFN, reopen it in the project, and wait until the listener matches.

    ``project`` is a folder, ``.uefnproject`` or a project name from ducky_list_projects;
    empty uses the selected island. Opening another island leaves the selected project
    as it is. ``timeout`` caps the wait (max 300s). Requires the island to have been
    connected before — the listener boots from the project's init_unreal.py.
    """
    from frontend.window_view import restart_uefn_project

    return tool_json(
        restart_uefn_project(_island_arg(project), wait=True, timeout=timeout),
        pretty=pretty,
    )


@mcp.tool()
def ducky_wait_uefn_ready(timeout: float = 180, project: str = "", pretty: bool = False) -> str:
    """Wait until the UEFN listener is online and the open island matches.

    ``project`` empty uses the panel's current island; otherwise a folder path, a
    ``.uefnproject`` path, or a recent project's name / mount. An unknown name fails at
    once instead of polling for the whole timeout. Polls GET health (no editor
    command). Returns project_name when ready.
    """
    import os

    from frontend.window_view import wait_uefn_ready

    root = (project or "").strip()
    if root and not os.path.exists(root):
        root = _resolve_project_root_arg(root)
    return tool_json(wait_uefn_ready(timeout=timeout, root=root or None), pretty=pretty)


@mcp.tool()
def ducky_sync_project_to_uefn(pretty: bool = False) -> str:
    """Switch the person's selected project to the island UEFN currently has open.

    Only when the person asks for it: it changes their Content tab. Duckies never need it
    to work, because file tools reach every project. UEFN names the live project by its
    content mount (the island's root plugin, sometimes a GUID), so the match is made
    against each known island's own descriptors, not its folder name.
    `already_synced` means the selected project IS the open island. On an error, do not
    retry, re-check status or switch projects: call `ducky_ask_user` once with the two
    options in the error text and end the turn.
    """
    from backend.bridge import post_command_to_listener
    from backend.bridge.status import _normalize_project_path, _same_or_nested
    from frontend.project_kind import is_island_identity, is_reserved_mount
    from frontend.ui_web.project_switch import (
        get_panel_project_info,
        switch_panel_project,
    )

    info = post_command_to_listener(PANEL_LISTENER_PORT, "get_project_info", {}, timeout=5.0)
    live_name = str(info.get("project_name") or "").strip() if isinstance(info, dict) else ""
    if is_reserved_mount(live_name):
        live_name = ""
    if not live_name:
        raise ValueError(
            "Could not read the live UEFN project — is an island map open in UEFN? "
            "Ask the user to open the island (ducky_ask_user), then end the turn."
        )
    island_dir = _normalize_project_path(str(info.get("island_dir") or "")) if isinstance(info, dict) else ""

    def _is_live(path: str) -> bool:
        if not path:
            return False
        if island_dir and _same_or_nested(island_dir, _normalize_project_path(path)):
            return True
        return is_island_identity(path, live_name)

    current = get_panel_project_info()
    if current.get("kind") == "uefn" and _is_live(str(current.get("path", ""))):
        return tool_json(
            {"status": "already_synced", "project": current, "uefn_project": live_name},
            pretty=pretty,
        )

    projects = list_panel_projects()
    for item in projects:
        if item.get("kind") == "uefn" and _is_live(str(item.get("path", ""))):
            new_info = switch_panel_project(path=str(item["path"]), push_ui=True)
            return tool_json(
                {"status": "switched", "project": new_info, "uefn_project": live_name},
                pretty=pretty,
            )

    checked = ", ".join(
        f"{item.get('name')} (mount {item.get('mount') or item.get('name')})"
        for item in projects
        if item.get("kind") == "uefn"
    ) or "none"
    panel_name = str(current.get("name") or "the panel project")
    raise ValueError(
        f"UEFN has island mount {live_name!r} open. None of the panel's recent islands "
        f"declares that mount in its .uefnproject (checked: {checked}). Needs the user: "
        f"call ducky_ask_user once with two options — open {panel_name} in UEFN, or add the "
        f"island folder UEFN has open via Add project… in the header dropdown — then end the "
        "turn. Do not call ducky_sync_project_to_uefn, ducky_set_project, ducky_list_projects "
        "or ducky_get_status again this turn; nothing changes until the user acts."
    )


@mcp.tool()
def ducky_get_errors(limit: int = 50, pretty: bool = False) -> str:
    """Recent UEFN-Ducky error log entries (bridge, agent, deploy). Host-side; no listener required."""
    limit = max(1, min(int(limit), 200))
    payload: dict[str, Any] = {"errors": read_errors(limit=limit)}
    try:
        from backend.tools.verse.verse_stats import summarize

        payload["verse_stats"] = summarize()
    except Exception:  # noqa: BLE001 — telemetry must never break the tool
        pass
    return tool_json(payload, pretty=pretty)


@mcp.tool()
def ducky_perf_report(clear: bool = False, pretty: bool = False) -> str:
    """Panel freeze/lag diagnostics — slowest evaluate_js, tool payloads, UI stalls, listener cmds.

    Always-on tracing writes to %LOCALAPPDATA%/UEFN-Ducky/perf/latest-report.json.
    Host-side; no UEFN listener required. Pass clear=true to reset the in-memory ring
    between experiments (session files on disk are kept). When the listener is online,
    also attaches per-command editor timings from GET health (names the exact freeze).
    """
    from frontend.perf_trace import clear as clear_ring
    from frontend.perf_trace import read_latest_report, write_report

    if clear:
        clear_ring()
    report = write_report()
    # Prefer a freshly written report; fall back to disk if ring was empty after clear.
    if not report.get("ring_size") and not clear:
        disk = read_latest_report()
        if disk:
            report = disk

    # Attach editor-side per-command timings when available (zero-cost GET).
    try:
        from backend.bridge import configured_listener_port, listener_get_health

        health = listener_get_health(configured_listener_port(), timeout=0.5)
        if health and health.get("status") == "ok":
            timings = list(health.get("command_timings") or [])
            if timings:
                by_name: dict[str, list[float]] = {}
                for row in timings:
                    name = str(row.get("name") or "?")
                    by_name.setdefault(name, []).append(float(row.get("ms") or 0))
                editor_cmds = []
                for name, ms_list in by_name.items():
                    editor_cmds.append(
                        {
                            "name": name,
                            "count": len(ms_list),
                            "max_ms": round(max(ms_list), 2),
                            "mean_ms": round(sum(ms_list) / len(ms_list), 2),
                        }
                    )
                editor_cmds.sort(key=lambda r: -float(r["max_ms"]))
                report = {
                    **report,
                    "editor_command_timings": timings[-20:],
                    "editor_commands_by_name": editor_cmds[:15],
                    "tick_age_sec": health.get("tick_age_sec"),
                    "current_command": health.get("current_command") or "",
                }
                if editor_cmds and float(editor_cmds[0]["max_ms"]) >= 500:
                    hints = list(report.get("hints") or [])
                    hints.insert(
                        0,
                        f"Editor command `{editor_cmds[0]['name']}` max "
                        f"{editor_cmds[0]['max_ms']}ms — main-thread freeze source.",
                    )
                    report["hints"] = hints
    except Exception:
        pass

    # Chat load / save times and UI traffic per Ducky process (app window, tool servers,
    # other) with the bridge methods sending the biggest replies (ui_js_big_top).
    try:
        from frontend.perf_trace import per_process_report

        report = {**report, "per_process": per_process_report()}
    except Exception as exc:  # a meter never breaks the report
        report = {**report, "per_process": {"ok": False, "error": str(exc)}}

    return tool_json(report, pretty=pretty)


@mcp.tool()
def ducky_memory_report(start_trace: bool = False, stop_trace: bool = False, collect: bool = True,
                        top: int = 30, pretty: bool = False) -> str:
    """What the UEFN Ducky app process holds in memory. Host-side; no UEFN listener.

    Sections: ``process`` (RSS, private bytes, threads), ``gc`` (a full collection, then live
    objects by type), ``containers`` (the biggest module globals and thread locals by
    approximate deep size, the top ones broken down one level) and, once
    ``start_trace=true``, ``tracemalloc`` (top allocation sites; ``grew``: what grew since
    the previous call). ``stop_trace=true`` ends tracing. Takes a few to ~30 seconds.
    """
    from frontend.ui_web import agent_modes

    args = {"start_trace": bool(start_trace), "stop_trace": bool(stop_trace), "collect": bool(collect),
            "top": int(top)}
    if not agent_modes._in_bridge_process():
        from frontend.memory_report import report

        return tool_json(report(**args), pretty=pretty)
    import urllib.request

    from frontend.settings import PANEL_LISTENER_PORT

    # The app's memory is the window process's; this tool server is another process.
    request = urllib.request.Request(
        f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_api/memory_report",
        data=json.dumps({"args": args}).encode("utf-8"), headers={"Content-Type": "application/json"},
    )
    try:
        out = json.loads(urllib.request.urlopen(request, timeout=120).read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return tool_json({"ok": False, "error": f"Ducky's window did not answer: {exc}"}, pretty=pretty)
    return tool_json(out.get("result") if isinstance(out.get("result"), dict) else out, pretty=pretty)


def _tool_ids_from_conv(conv: Any) -> list[str] | None:
    ids: list[str] = []
    for x in getattr(conv, "builtin_toolsets", None) or []:
        s = str(x or "").strip()
        if s:
            ids.append(s)
    for x in getattr(conv, "mcp_plugins", None) or []:
        s = str(x or "").strip()
        if s:
            ids.append(s)
    for x in getattr(conv, "uefn_plugins", None) or []:
        s = str(x or "").strip()
        if s:
            ids.append(s)
    return ids or None


def _disabled_tool_ids_from_conv(conv: Any) -> list[str]:
    """Reconstruct deny-list so a recycled twin keeps the same tool surface."""
    from frontend.ui_web.project_chats import all_available_tool_ids

    effective = set(_tool_ids_from_conv(conv) or [])
    if not effective:
        return []
    return [tid for tid in all_available_tool_ids() if tid not in effective]


def _spawn_kwargs_from_conv(conv: Any) -> dict[str, Any]:
    """Copy persona/skills/model from an existing subagent for a recycled twin."""
    subs = getattr(conv, "enabled_subskills", None)
    return {
        "ducky_style": str(getattr(conv, "ducky_style", None) or "classic"),
        "ducky_name": str(getattr(conv, "ducky_name", None) or ""),
        "profile_id": str(getattr(conv, "profile_id", None) or "").strip(),
        "ducky_personality": str(getattr(conv, "ducky_personality", None) or ""),
        "tts_voice": str(getattr(conv, "tts_voice", None) or "").strip(),
        "tts_speed": float(getattr(conv, "tts_speed", 0) or 0.0),
        "disabled_packs": list(getattr(conv, "disabled_packs", None) or []),
        "enabled_subskills": dict(subs) if isinstance(subs, dict) else None,
        "disabled_tool_ids": _disabled_tool_ids_from_conv(conv),
        "model": str(getattr(conv, "model", None) or "").strip() or None,
        "provider": str(getattr(conv, "provider", None) or "").strip() or None,
        "coding_agent": str(getattr(conv, "coding_agent", None) or "ducky"),
        "skill_snapshot": str(getattr(conv, "skill_snapshot", None) or ""),
    }


def build_recycle_spawn_message(handoff: str, continue_message: str = "", max_chars: int = _MAX_MESSAGE_CHARS) -> str:
    """Compose the continuation for a fresh session in the same member chat."""
    body = (handoff or "").strip() or "(no handoff text; inspect this chat history if needed)"
    follow = (continue_message or "").strip()
    parts = [
        "You are continuing in a FRESH session of this same member chat. All history is preserved. "
        "Continue from the handoff below; do not redo completed work.\n\n"
        "## Handoff from previous session\n"
        f"{body}",
    ]
    if follow:
        parts.append(f"\n\n## Continue with this task\n{follow}")
    return _truncate_text("\n".join(parts), max_chars)


def next_subagent_title(title: str) -> str:
    """Bump `Name` → `Name (v2)` → `Name (v3)` for recycled twins."""
    import re

    base = (title or "").strip() or "Subagent"
    m = re.search(r"\(v(\d+)\)\s*$", base, flags=re.I)
    if m:
        return f"{base[: m.start()].rstrip()} (v{int(m.group(1)) + 1})"
    return f"{base} (v2)"


@mcp.tool()
def ducky_list_chats(folder_id: str | None = None, project: str = "", pretty: bool = False) -> str:
    """List all chat folders and conversations for the panel project.

    Omit folder_id to return every chat (yours, the user's, and group members).
    Pass `project` (path, name, or slug from ducky_list_projects) to list ANOTHER
    project's chats — every chat is another ducky whose context you can read.
    Rows include `parent_conv_id` (group hub id for swarm members).
    """
    root = _resolve_project_root_arg(project)
    folders = [
        {"id": f.id, "name": f.name, "parent_id": f.parent_id, "sort_order": f.sort_order}
        for f in load_folders(root)
    ]
    convs = list_conversations(folder_id, project_root=root)
    conversations = [
        {
            "id": c.id,
            "title": c.title,
            "folder_id": c.folder_id,
            "sort_order": c.sort_order,
            "updated": c.updated,
            "message_count": len(c.messages),
            "ducky": getattr(c, "ducky_name", "") or "",
            "parent_conv_id": (getattr(c, "parent_conv_id", None) or "").strip(),
            "archived": is_archive_folder_id(c.folder_id),
        }
        for c in convs
    ]
    return tool_json({"folders": folders, "conversations": conversations}, pretty=pretty)


def archive_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Archived chats as a tree: each group with what was archived with it under ``inside``."""
    ids = {r["id"] for r in rows}
    kids: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        parent = r.get("parent_conv_id") or ""
        if parent and parent != r["id"] and parent in ids:
            kids.setdefault(parent, []).append(r)
    seen: set[str] = set()

    def node(r: dict[str, Any]) -> dict[str, Any]:
        seen.add(r["id"])
        inside = [node(k) for k in kids.get(r["id"], []) if k["id"] not in seen]
        count = sum(1 + n["inside_count"] for n in inside)
        return {**r, "inside_count": count, "inside": inside}

    child_ids = {k["id"] for v in kids.values() for k in v}
    out = [node(r) for r in rows if r["id"] not in child_ids]
    # A parent loop (a group archived inside its own ducky) has no root: list it anyway,
    # or the badge counts chats the Archive never shows.
    out += [node(r) for r in rows if r["id"] not in seen]
    return out


@mcp.tool()
def ducky_list_archive(project: str = "", pretty: bool = False) -> str:
    """What is in the Duckies Archive: archived chats and groups (each group with the
    duckies and nested groups archived with it, under ``inside``) and archived ducky
    profiles. Read-only. Read a chat with ducky_read_chat(conv_id).

    Pass `project` (path, name, or slug from ducky_list_projects) for another project.
    """
    from frontend.agent_profiles import list_archived_agent_profiles

    root = _resolve_project_root_arg(project)
    rows = [
        {
            "id": c.id,
            "title": c.title,
            "is_group": bool(getattr(c, "is_group", False)),
            "updated": c.updated,
            "parent_conv_id": (getattr(c, "parent_conv_id", None) or "").strip(),
        }
        for c in list_conversations(project_root=root)
        if is_archive_folder_id(c.folder_id)
    ]
    profiles = [
        {"id": p.get("id"), "name": p.get("name"), "kind": p.get("kind")}
        for p in (list_archived_agent_profiles() or [])
        if isinstance(p, dict)
    ]
    return tool_json(
        {"chat_count": len(rows), "chats": archive_tree(rows), "profiles": profiles},
        pretty=pretty,
    )


@mcp.tool()
def ducky_read_chat(conv_id: str, project: str = "", pretty: bool = False) -> str:
    """Read message history from any panel chat by id (including user-created chats).

    Chat IDs work across all projects, without switching the active project.
    Optional `project` (path, name, or slug from ducky_list_projects) is a lookup
    hint; it does not restrict access or change the chat's owning project.
    """
    root = _resolve_project_root_arg(project)
    conv = load_conversation(conv_id.strip(), project_root=root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    return tool_json(
        {
            "id": conv.id,
            "title": conv.title,
            "folder_id": conv.folder_id,
            "messages": _serialize_messages(conv.messages),
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_create_chat(folder_id: str = "", title: str = "", pretty: bool = False) -> str:
    """Create a new panel chat in the given folder (empty folder_id = sidebar root)."""
    root = _project_root()
    conv = create_conversation(folder_id=folder_id or "", project_root=root)
    if title.strip():
        conv.title = title.strip()[:120]
        save_conversation(conv, root)
    return tool_json({"id": conv.id, "title": conv.title, "folder_id": conv.folder_id}, pretty=pretty)


def _panel_api():
    from frontend.ui_web.panel_api import shared_panel_api

    return shared_panel_api()


@mcp.tool()
def ducky_create_folder(name: str, parent_id: str = "", pretty: bool = False) -> str:
    """Create a Group Chat folder (chat org is groups only — aliases ducky_group_create)."""
    return ducky_group_create(name=name, parent_folder_id=parent_id or "", pretty=pretty)


@mcp.tool()
def ducky_group_create(name: str, parent_folder_id: str = "", pretty: bool = False) -> str:
    """Find or create a Group Chat hub (+ linked folder) by name.

    A live group with this name inside ``parent_folder_id`` is reused, so calling
    this twice does not stack duplicate groups. Pass ``parent_folder_id`` to nest
    inside another group's folder. After create, add yourself with
    ``ducky_group_add_member(..., as_leader=true)`` if you want to lead the swarm.
    """
    api = _panel_api()
    res = api.group_find_or_create(
        name=name.strip() or "Group", folder_id=parent_folder_id or "", open_tab=True
    )
    return tool_json(res, pretty=pretty)


def _require_lane_authority(group_id: str) -> str:
    """Only the user, the group's leader or the leader of a group it is nested in may assign
    lanes. Returns the caller id ('' = user)."""
    from backend.workspace import identity as run_identity

    ctx = run_identity.resolve_context()
    if ctx is None:
        return ""
    authorities = _panel_api().group_lane_authorities(group_id.strip())
    leader = authorities[0] if authorities else ""
    if ctx.conv_id and ctx.conv_id in authorities:
        return ctx.conv_id
    raise ValueError(
        "Only the group leader (or the user) can set write lanes. "
        + (f"Ask the leader (chat {leader}) in the group chat." if leader else "This group has no leader yet.")
    )


@mcp.tool()
def ducky_group_invite(
    group_id: str, ducky: str, write_allowed: Optional[list[str]] = None, name: str = "",
    model: str = "", context_limit: str = "", pretty: bool = False,
) -> str:
    """Invite a ducky profile into a group as a lasting swarm member (not a parent-linked subagent).

    ``name`` names the seat by its role ("Search lead", "Bench lead"); without it a second
    seat of the same profile is called "Verse Coder 2". Name every team seat by its role.

    ``write_allowed`` (leader/user only) seats the member with a write lane: gitignore-style
    globs such as ["Content/Verse/Shop/**"]; [] = read-only. Overlapping another member's
    lane is refused. Omit for an unrestricted member.

    ``model`` seats it on that model (e.g. "codex:gpt-5-mini") instead of the profile's own;
    a pay-per-token API key the person has not opened to agents is still refused.

    ``context_limit`` ("200k") is the member's context limit: past it, it starts a fresh
    session between turns (a handoff, same chat). Omit for 60% of its model's window.
    """
    profile = _resolve_ducky_profile(ducky)
    if profile is None:
        raise ValueError(
            f"No ducky named {ducky!r}. Call ducky_list_duckies to see available duckies."
        )
    pid = str(profile.get("id") or "").strip() or ducky.strip()
    if write_allowed is not None:
        _require_lane_authority(group_id)
    api = _panel_api()
    res = api.group_invite(
        group_id.strip(), pid, model=model.strip(), write_allowed=write_allowed, name=name.strip(), for_agent=True
    )
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_invite failed"))
    _store_context_limit(str((res.get("member") or {}).get("member_conv_id") or ""), context_limit)
    return tool_json(res, pretty=pretty)


def _store_context_limit(conv_id: str, value: str) -> None:
    """Keep a ``context_limit`` tool argument on the chat (nothing when it is empty)."""
    if conv_id and str(value or "").strip():
        from frontend.ui_web.context_limit import set_chat_limit

        set_chat_limit(conv_id, value, project_root=_project_root())


@mcp.tool()
def ducky_group_set_lane(
    group_id: str,
    member_conv_id: str,
    write_allowed: Optional[list[str]] = None,
    force: bool = False,
    pretty: bool = False,
) -> str:
    """Set a member's write lane (leader/user only): the project paths it may create/edit/rename.

    ``write_allowed``: gitignore-style globs (``Content/Verse/Shop/**``, ``Content/Verse/x.verse``;
    a bare folder means folder/**). [] = read-only, omitted = unrestricted. A lane that overlaps a
    teammate's lane is refused; ``force=true`` accepts warnings only. Takes effect on the
    member's next tool call. Members cannot change their own lane — they ask you.
    """
    caller = _require_lane_authority(group_id)
    api = _panel_api()
    res = api.group_set_member_lane(
        group_id.strip(), member_conv_id.strip(), write_allowed, force=bool(force), set_by=caller or "user"
    )
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_set_member_lane failed"))
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_group_get_lanes(group_id: str, pretty: bool = False) -> str:
    """Every member's write lane for a group hub (lane_set/1): plan disjoint lanes before spawning."""
    api = _panel_api()
    res = api.group_get_lanes(group_id.strip())
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_get_lanes failed"))
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_group_members(group_id: str, pretty: bool = False) -> str:
    """List members of a group hub (includes leader_conv_id)."""
    api = _panel_api()
    res = api.group_members(group_id.strip())
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_members failed"))
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_group_set_leader(group_id: str, member_conv_id: str, pretty: bool = False) -> str:
    """Set the designated leader (cross-group spokesperson) for a group hub."""
    api = _panel_api()
    res = api.group_set_leader(group_id.strip(), member_conv_id.strip())
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_set_leader failed"))
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_group_add_member(
    group_id: str, conv_id: str, as_leader: bool = False, move_folder: bool = True, pretty: bool = False
) -> str:
    """Move an existing chat into a group (pass as_leader=true to own/lead that swarm).

    With ``move_folder`` (default) a lead is also filed in the group's folder, so a
    workstream lead sits in the group it leads; a chat that leads the group it is filed in
    (you, the root lead) keeps its place. Returns ``folder_id`` = where the chat is filed.
    """
    api = _panel_api()
    res = api.group_add_member(
        group_id.strip(), conv_id.strip(), as_leader=bool(as_leader), move_folder=bool(move_folder)
    )
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_add_member failed"))
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_set_muted(conv_id: str, muted: bool = True, pretty: bool = False) -> str:
    """Mute (or unmute with muted=false) a chat or a whole group in the sidebar.

    Muting only quiets it for the person; the agent keeps working.
    """
    res = _panel_api().set_muted(conv_id.strip(), bool(muted))
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "set_muted failed"))
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_group_stop(group_id: str, pretty: bool = False) -> str:
    """Stop every running member of a group and of every group nested in it.

    Nothing is deleted: members keep their chats and plans and can be started again.
    Returns how many turns were stopped and how many groups were walked.
    """
    res = _panel_api().group_stop(group_id.strip())
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_stop failed"))
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_rename_self(title: str, pretty: bool = False) -> str:
    """First tool call in an unnamed chat: choose your task-based Ducky name."""
    from backend.tools.panel.panel_ui import _resolve_ask_user_conv_id
    from backend.agent.chat_title import sanitize_role_title
    from frontend.ui_web.agent_modes import notify_chats_changed

    conv_id = _resolve_ask_user_conv_id()
    cleaned = sanitize_role_title(title)
    if not conv_id or not cleaned:
        raise ValueError("A bound conversation and a nonempty task name are required.")
    root = _project_root()
    rename_conversation(conv_id, cleaned, project_root=root)
    notify_chats_changed(conv_id=conv_id, title=cleaned, open_tab=False)
    return tool_json({"id": conv_id, "title": cleaned}, pretty=pretty)


@mcp.tool()
def ducky_rename_chat(conv_id: str, title: str, seat_name: str = "", pretty: bool = False) -> str:
    """Rename any chat by conversation id.

    ``seat_name`` also renames a group member's seat: the ducky name its group shows
    ("Verse Coder 2" -> "Search lead").
    """
    root = _project_root()
    rename_conversation(conv_id.strip(), title, project_root=root)
    conv = load_conversation(conv_id.strip(), project_root=root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    seat = seat_name.strip()
    if seat:
        conv.ducky_name = seat
        save_conversation(conv, root)
        hub_id = (getattr(conv, "parent_conv_id", None) or "").strip()
        hub = load_conversation(hub_id, project_root=root) if hub_id else None
        if hub is not None and getattr(hub, "is_group", False):
            rows = [dict(r) for r in (getattr(hub, "group_members", None) or []) if isinstance(r, dict)]
            for row in rows:
                if row.get("member_conv_id") == conv.id:
                    row["name"] = row["ducky_name"] = seat
            hub.group_members = rows
            save_conversation(hub, root)
    return tool_json({"id": conv.id, "title": conv.title, "folder_id": conv.folder_id,
                      "ducky_name": getattr(conv, "ducky_name", "") or ""}, pretty=pretty)


@mcp.tool()
def ducky_move_chat(conv_id: str, folder_id: str, pretty: bool = False) -> str:
    """Move a chat into a folder (use after ducky_create_folder). To archive, use ducky_archive_chats."""
    if is_archive_folder_id(folder_id):
        raise ValueError("Archiving asks the person first: use ducky_archive_chats.")
    root = _project_root()
    move_conversation(conv_id.strip(), folder_id.strip(), project_root=root)
    conv = load_conversation(conv_id.strip(), project_root=root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    return tool_json({"id": conv.id, "title": conv.title, "folder_id": conv.folder_id}, pretty=pretty)


def _chats_leaving_with(conv: Any, root: str) -> set[str]:
    """Every chat that goes with ``conv``: its sub-agents, and for a group everything filed in its folder."""
    from frontend.ui_web.project_chats import conversation_descendant_ids, folder_subtree_ids

    out = {conv.id, *conversation_descendant_ids(conv.id, root)}
    if getattr(conv, "is_group", False):
        own = next((f for f in load_folders(root) if (getattr(f, "group_hub_id", None) or "") == conv.id), None)
        if own is not None:
            folders = set(folder_subtree_ids(own.id, root))
            out |= {c.id for c in list_conversations(project_root=root) if c.folder_id in folders}
    return out


@mcp.tool()
def ducky_archive_chats(
    conv_ids: list[str] | None = None,
    delete: bool = False,
    folder_ids: list[str] | None = None,
    pretty: bool = False,
) -> str:
    """Archive chats, groups and folders, or with delete=true delete them for good. The person always says yes first.

    The tool itself shows the person a Yes/No card naming every chat, even when the chat
    allows everything; nothing changes on No. A group goes with its duckies and nested
    groups, and their running agents are stopped. ``folder_ids`` (from ducky_list_chats)
    take plain folders with everything inside them; folders left empty go too. Archived chats can be restored from the
    sidebar's Archive; deleted ones can't. Never archives the chat calling it. Use only when
    the person asked to clean up chats, never on your own.
    """
    from backend.tools.panel.panel_plugin_data import _confirm
    from backend.tools.panel.panel_ui import _resolve_ask_user_conv_id
    from frontend.archive_folder import ARCHIVE_FOLDER_ID

    root = _project_root()
    me = str(_resolve_ask_user_conv_id() or "").strip()
    targets = []
    for cid in dict.fromkeys(str(c).strip() for c in conv_ids or [] if str(c).strip()):
        conv = load_conversation(cid, project_root=root)
        if conv is None:
            return tool_json({"ok": False, "error": f"Chat not found: {cid!r}"}, pretty=pretty)
        if me and me in _chats_leaving_with(conv, root):
            return tool_json({"ok": False, "error": f"“{conv.title or cid}” holds this chat; archive it from the sidebar "
                              "or move this chat out first."}, pretty=pretty)
        targets.append(conv)
    from frontend.ui_web.project_chats import folder_subtree_ids

    folders = {f.id: f for f in load_folders(root)} if folder_ids else {}
    folder_targets = []
    for fid in dict.fromkeys(str(f).strip() for f in folder_ids or [] if str(f).strip()):
        folder = folders.get(fid)
        if folder is None or is_archive_folder_id(fid):
            return tool_json({"ok": False, "error": f"Folder not found: {fid!r}"}, pretty=pretty)
        inside = set(folder_subtree_ids(fid, root))
        held = {c.id for c in list_conversations(project_root=root) if c.folder_id in inside}
        if me and me in held:
            return tool_json({"ok": False, "error": f"“{folder.name}” holds this chat; move this chat out first."},
                             pretty=pretty)
        folder_targets.append((folder, held))
    if not targets and not folder_targets:
        return tool_json({"ok": False, "error": "conv_ids and folder_ids are empty"}, pretty=pretty)
    names = ", ".join(
        [f"“{c.title or c.id[:8]}”" + (" (group, with its duckies)" if getattr(c, "is_group", False) else "")
         for c in targets]
        + [f"folder “{f.name}” (with everything in it)" for f, _held in folder_targets]
    )
    verb = "Delete" if delete else "Archive"
    note = " This can't be undone." if delete else " You can restore them from Archive."
    if not _confirm(f"{verb} {names}?{note}", title=f"{verb} chats"):
        return tool_json({"ok": False, "error": f"The person said no: nothing was {verb.lower()}d."}, pretty=pretty)
    api = _panel_api()
    for c in targets:
        if delete:
            api.delete_conversation(c.id)
        else:
            api.move_conversation(c.id, ARCHIVE_FOLDER_ID)
    for folder, held in folder_targets:
        if folder.id not in {f.id for f in load_folders(root)}:
            continue  # already went with a folder or group above
        api.delete_folder(folder.id, True)
        if delete:
            for cid in held:
                if load_conversation(cid, project_root=root) is not None:
                    api.delete_conversation(cid)
    out = {"ok": True, "deleted" if delete else "archived": [c.id for c in targets]}
    if folder_targets:
        out["folders"] = [f.id for f, _held in folder_targets]
    return tool_json(out, pretty=pretty)


@mcp.tool()
def ducky_send_chat_message(
    conv_id: str,
    message: str,
    mode: str = "agent",
    wait_for_reply: bool = True,
    timeout_sec: float = 180.0,
    context_limit: str = "",
    pretty: bool = False,
) -> str:
    """Send a user message to any chat and run its agent — for follow-ups on your chats or the user's.

    Blocks until the linked chat finishes (default). Parent agent waits for the reply before continuing.
    ``context_limit`` ("200k") sets that chat's context limit: past it, it starts a fresh session
    between turns.
    """
    text = _truncate_text(message.strip(), _MAX_MESSAGE_CHARS)
    if not text:
        raise ValueError("message must not be empty")
    conv_id = conv_id.strip()
    root = _project_root()
    target_conv = load_conversation(conv_id, project_root=root)
    if target_conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    if is_archive_folder_id(target_conv.folder_id):
        raise ValueError("Archived chats cannot run agents. Restore this chat or use an active ducky.")

    from frontend.ui_web.agent_modes import HELD_FOR_MODEL, run_message, run_message_and_wait
    from backend.workspace.identity import resolve_context

    context = resolve_context()
    me = str(getattr(context, "conv_id", "") or "").strip() if context is not None else ""
    if me and me != conv_id and _works_alone(me, conv_id, root):
        return tool_json({"status": "not_sent", "sender": me, "reason": WORK_ALONE}, pretty=pretty)
    if me and me != conv_id and _lead_uses_plan(me, conv_id, root):
        return tool_json({"status": "not_sent", "sender": me, "reason": LEAD_USES_PLAN}, pretty=pretty)
    if me and me != conv_id and _lead_without_plan(me, conv_id, root):
        return tool_json({"status": "not_sent", "sender": me, "reason": LEAD_NEEDS_PLAN}, pretty=pretty)
    text = _from_another_ducky(conv_id, text, root)
    _store_context_limit(conv_id, context_limit)
    mode_norm = (mode or "agent").lower()
    if wait_for_reply:
        outcome = run_message_and_wait(
            conv_id,
            text,
            mode_norm,
            timeout_sec=max(5.0, min(float(timeout_sec), 600.0)),
            # The wait ends; the agent does not. A coordinator checking on a builder
            # must never kill a long task just because it outlasted the wait.
            cancel_on_timeout=False,
            # Another agent's message waits for a busy chat; only the person's replaces its turn.
            queue_if_busy=bool(me),
        )
        if outcome.get("run_id"):
            _acknowledge_chat_message(conv_id)
        return tool_json(outcome, pretty=pretty)

    # An agent must not cancel the chat it messages: a busy one gets this when its turn ends.
    started = run_message(conv_id, text, mode_norm, "", queue_if_busy=True)
    if started:
        _acknowledge_chat_message(conv_id)
    status = {"queued": "queued", HELD_FOR_MODEL: "waiting_for_model"}.get(started, "running")
    return tool_json({"status": status, "conv_id": conv_id}, pretty=pretty)


def _from_another_ducky(receiver: str, text: str, root: str | None, sender: str = "") -> str:
    """A ducky's message into another chat says who sent it: the receiver, its chat and
    the group's overview otherwise read it as the person's own words. Not fenced: a ducky
    directing a chat it started (or leads) must still be followed."""
    from backend.workspace.identity import resolve_context

    if not sender:
        context = resolve_context()
        sender = str(getattr(context, "conv_id", "") or "").strip() if context is not None else ""
    if not sender or sender == receiver or text.startswith("[ducky:"):
        return text
    conv = load_conversation(sender, project_root=root)
    if conv is None:
        return text
    # Written into the receiver's chat below, and shown in the group chat they share now.
    from backend.agent.a2a_broker import _show_in_group_chat

    _show_in_group_chat(sender, receiver, text)
    from backend.agent.a2a_format import sender_label

    try:
        from backend.agent.a2a_broker import leads_receiver

        lead = leads_receiver(sender, receiver)
    except Exception:
        lead = False
    role = (
        "[ducky:agent-message] This is an assignment from your group leader, who directs "
        "this team for the user. Do this work (within your role and the user's rules)."
        if lead
        else "[ducky:agent-message] Sent into your chat by another ducky working for the user."
    )
    header = "[ducky:agent-message] from " + sender_label(
        sender, str(getattr(conv, "title", "") or ""), str(getattr(conv, "coding_agent", "") or "")
    )
    return f"{header}\n{role}\n[ducky:agent-message] Answer here in this chat; it goes back to them.\n\n{text}"


def _acknowledge_chat_message(receiver: str) -> None:
    from backend.workspace.identity import resolve_context
    from backend.agent.a2a_client import acknowledge_reports

    context = resolve_context()
    if context is not None and context.conv_id:
        try:
            acknowledge_reports(context.conv_id, receiver)
        except RuntimeError:
            pass  # The message was accepted; a lost acknowledgement leaves its report pending.


WORK_ALONE = (
    "Not sent: on a team plan you work your sub plan alone, like a sub-agent, and send no "
    "messages. Tick your points with ducky_plan_update_node; ticking the last one tells your "
    "lead. If you need something only another agent or the person can give, end your turn "
    "with one line: BLOCKED: <what you need>."
)


LEAD_USES_PLAN = (
    "Not sent: your members get their work from the plan, not from messages. Add or change their "
    "points (ducky_plan_add_node / ducky_plan_update_node); Ducky hands the change to the member "
    "when its current turn ends. To stop one, use ducky_agent_stop. Then end your turn and wait."
)


def _works_alone(me: str, target: str, root: str | None) -> bool:
    """A team member on its lead's plan sends nothing new: no reports, receipts, status rounds or
    peer chat (one team sent 501 messages and wrote 126 files). Anyone may answer a question
    put to it."""
    try:
        from backend.agent.a2a_broker import leads_receiver
        from backend.agent.coding_agents.plans import team_plan_for

        return not leads_receiver(me, target) and team_plan_for(me, root) is not None
    except Exception:
        return False


def _lead_uses_plan(me: str, target: str, root: str | None) -> bool:
    """A lead whose plan gives this member work changes the plan instead of messaging it: a lead
    kept interrupting its working member with "finish everything" and relayed each new bug."""
    try:
        from backend.agent.a2a_broker import leads_receiver
        from backend.agent.coding_agents.plans import load_plan, team_plan_for

        if not leads_receiver(me, target):
            return False
        team = team_plan_for(target, root)
        return team is not None and str(team.get("chat_id") or "") == me and load_plan(me, root) is not None
    except Exception:
        return False


LEAD_NEEDS_PLAN = (
    "Not sent: your team works from your plan and you have none yet. Write it first with "
    "ducky_create_plan, one sub plan per member assigned to its chat id (seat members with "
    "ducky_group_invite): assigning a sub plan starts its member."
)


def _lead_without_plan(me: str, target: str, root: str | None) -> bool:
    """A lead with no live plan gives its members work only through a plan. Oct 15 2026: a lead
    handed out work by message before writing any plan; a member then wrote a plan of its own and
    the keeper woke that member as a lead."""
    try:
        from backend.agent.a2a_broker import leads_receiver
        from backend.agent.coding_agents.plans import _live_plan, load_plan

        return leads_receiver(me, target) and not _live_plan(load_plan(me, root))
    except Exception:
        return False


def _spawn_needs_plan(caller: str, hub_id: str, root: str | None) -> bool:
    """Seating work into a group the caller leads, while its team has members and it has no live plan."""
    if not caller:
        return False
    try:
        from backend.agent.a2a_broker import leads_receiver
        from backend.agent.coding_agents.plans import _live_plan, load_plan
        from frontend.ui_web.team_keeper import _staffed_leaders

        hub = load_conversation(hub_id, project_root=root)
        lead = str(getattr(hub, "leader_conv_id", "") or "").strip() if hub is not None else ""
        if lead != caller and not leads_receiver(caller, hub_id):
            return False
        return not _live_plan(load_plan(caller, root)) and caller in _staffed_leaders(root)
    except Exception:
        return False


def _resolve_sender(sender: str) -> str:
    """Explicit sender chat id, else the currently running (embedded) chat.

    Falls back to DUCKY_CONV_ID from the environment: a coding-agent CLI (Cursor,
    Claude Code, …) is launched with its own chat id in that var, and the MCP
    bridge it spawns inherits it. That lets sub-agents nest under the spawning
    chat even when the model doesn't pass `sender` explicitly.
    """
    s = (sender or "").strip()
    if s:
        return s
    from backend.workspace import identity as run_identity

    ctx = run_identity.current()
    if ctx is not None and ctx.conv_id:
        return ctx.conv_id
    from frontend.ui_web.agent_modes import get_active_conv_id

    active = get_active_conv_id()
    if active:
        return active
    import os

    return (os.environ.get("DUCKY_CONV_ID") or "").strip()


@mcp.tool()
def ducky_agent_list(sender: str = "", pretty: bool = False) -> str:
    """List live agents (panel chats) you can message: id, name, backend, running state,
    and ``active_work``: what every other running chat is changing right now and where
    (any project: ``project``, ``files``). Check it before starting a change, so you
    never redo or overwrite another chat's work; ask that chat with ``ducky_agent_send``.

    Use before `ducky_agent_send` / reuse via `ducky_send_chat_message`. `sender` is your
    own chat id (external coding agents must pass it; embedded duckies may omit it).
    Prefer `ducky_group_members` for swarm seats. `my_group_members` lists chats whose
    `parent_conv_id` is a group hub you lead or belong to (legacy key `my_subagents`
    kept as an alias for older prompts).
    """
    from frontend.ui_web.agent_modes import is_agent_running, linked_parent_of
    from frontend.ui_web.agent_state import agent_state
    from frontend.ui_web.group_orchestrator import is_group_conversation

    me = _resolve_sender(sender)
    root = _project_root()
    rows: list[dict[str, Any]] = []
    by_id: dict[str, Any] = {}
    for conv in list_conversations(project_root=root):
        if is_archive_folder_id(conv.folder_id):
            continue
        by_id[conv.id] = conv
        parent_id = (getattr(conv, "parent_conv_id", None) or "").strip() or (
            linked_parent_of(conv.id) or ""
        )
        active = is_agent_running(conv.id)
        state: dict[str, Any] = {}
        if active:  # needs_answer / needs_approval / running_command / working / silent
            try:
                state = {k: v for k, v in agent_state(conv.id).items() if k in ("state", "since_s", "detail")}
            except Exception:  # a meter never breaks the list
                state = {}
        rows.append(
            {
                "id": conv.id,
                "title": conv.title,
                "ducky": conv.ducky_name or "",
                "coding_agent": conv.coding_agent or "ducky",
                "active": active,
                **state,
                "parent_conv_id": parent_id,
                "parent": parent_id,  # alias — older prompts used `parent`
                "is_self": conv.id == me,
                "message_count": len(conv.messages),
                "is_group_member": bool(
                    parent_id and is_group_conversation(by_id.get(parent_id) or load_conversation(parent_id, project_root=root))
                ),
            }
        )
    my_group_members = [
        r
        for r in rows
        if me
        and r.get("is_group_member")
        and (
            r.get("parent_conv_id") == me
            or (getattr(by_id.get(str(r.get("parent_conv_id") or "")), "leader_conv_id", None) or "") == me
        )
    ]
    return tool_json(
        {
            "caller": me,
            "agents": rows,
            "active_work": _active_work(me),
            "my_group_members": my_group_members,
            "my_subagents": my_group_members,  # alias — subagents retired
            "count": len(rows),
            "my_group_member_count": len(my_group_members),
            "my_subagent_count": len(my_group_members),
        },
        pretty=pretty,
    )


def _active_work(me: str) -> list[dict[str, Any]]:
    """Other chats' open runs in every project: ``{conv_id, ducky, project, files, seconds_ago}``."""
    import time

    try:
        from backend.workspace.journal import FileChangeJournal
        from backend.workspace.runtime import get_writer

        journal = get_writer().journal
        if not isinstance(journal, FileChangeJournal):
            return []
        rows = journal.active_work(exclude_conv_id=me)
    except Exception:  # awareness never breaks the list
        return []
    return [
        {"conv_id": r["conv_id"], "ducky": r["ducky_name"], "project": r["project_id"], "files": r["files"],
         "seconds_ago": max(0, int(time.time() - r["last_ts"]))}
        for r in rows
    ]


def _owed_reply(me: str, target: str) -> str:
    """The request ``target`` is still waiting for ``me`` to answer ('' when none). Builders
    answered without the id, each answer opened a new request back, the original stayed
    open and its "finished without replying" notice woke the lead: a loop of acks."""
    from backend.agent.a2a_client import open_threads_for_receiver

    try:
        threads = open_threads_for_receiver(me) or []
    except Exception:
        return ""
    for thread in threads:
        row = thread if isinstance(thread, dict) else vars(thread)
        if str(row.get("sender_conv_id") or "") == target:
            return str(row.get("response_id") or "")
    return ""


@mcp.tool()
def ducky_agent_send(
    to: str,
    message: str,
    sender: str = "",
    expect_reply: bool | None = None,
    response_id: str = "",
    pretty: bool = False,
) -> str:
    """Send a message to another agent (chat) and return IMMEDIATELY — fire-and-forget.

    With `expect_reply=true` you get a `response_id`; finish your turn after sending —
    the reply (or an inactivity notice) arrives later as a `[ducky:agent-message]` turn
    in YOUR chat. A new message expects a reply unless you pass `expect_reply=false`, except a
    lead's message to its own member: an assignment, answered by the member's report. To
    ANSWER a message that asked for a reply, pass its `response_id` (that makes it an
    answer; leave `expect_reply` out). A message to an agent still waiting on your answer
    IS that answer, even without the id. Never busy-wait for replies. `sender` is your own chat id
    (external coding agents must pass it; embedded duckies may omit it).

    A note that needs no reply (`expect_reply=false`, not from the receiver's lead) is not a
    turn of its own: it waits for a quiet minute and a half and reaches the receiver together
    with other notes, or with the next message that needs action. Send one note per finished
    piece of work, to the agent that needs it, not a copy to every teammate.
    """
    text = _truncate_text(message.strip(), _MAX_MESSAGE_CHARS)
    if not text:
        raise ValueError("message must not be empty")
    me = _resolve_sender(sender)
    if not me:
        raise ValueError(
            "sender chat id required — pass sender=\"<your chat id>\" (it is in your system prompt)."
        )
    target = (to or "").strip()
    root = _project_root()
    target_conv = load_conversation(target, project_root=root)
    if target_conv is None:
        raise ValueError(f"Receiver chat not found: {to!r}. Call ducky_agent_list first.")
    if is_archive_folder_id(target_conv.folder_id):
        raise ValueError("Receiver is archived and cannot be used as an agent. Choose an active ducky.")
    if target == me:
        raise ValueError("cannot send an agent message to yourself")

    from backend.agent.a2a_client import send

    reply_to = (response_id or "").strip()
    if not reply_to and expect_reply is None:
        reply_to = _owed_reply(me, target)
    if not reply_to and _works_alone(me, target, root):
        return tool_json({"status": "not_sent", "sender": me, "reason": WORK_ALONE}, pretty=pretty)
    if not reply_to and _lead_uses_plan(me, target, root):
        return tool_json({"status": "not_sent", "sender": me, "reason": LEAD_USES_PLAN}, pretty=pretty)
    # Every agent answered with response_id alone, hit "not both" and had to retry.
    expects = (not reply_to) if expect_reply is None else bool(expect_reply)
    if expect_reply is None and not reply_to:
        from backend.agent.a2a_broker import leads_receiver

        # A lead's assignment needs no answer: the member reports when the work is done. A
        # lead whose 67 of 91 messages asked for replies kept its whole team answering it.
        expects = not leads_receiver(me, target)
    outcome = send(
        sender_conv_id=me,
        receiver_conv_id=target,
        body=text,
        expect_reply=expects,
        response_id=reply_to,
    )
    status = "not_sent" if outcome.get("not_sent") else "sent"
    return tool_json({"status": status, "sender": me, **outcome}, pretty=pretty)


@mcp.tool()
def ducky_agent_inbox(conv_id: str = "", pretty: bool = False) -> str:
    """Re-read YOUR recent inter-agent inbox in full (delivered + still queued).

    `conv_id` is your own chat id (external coding agents must pass it).
    """
    me = _resolve_sender(conv_id)
    if not me:
        raise ValueError("conv_id required — pass your own chat id.")
    from backend.agent.a2a_client import read_inbox

    return tool_json({"conv_id": me, "messages": read_inbox(me)}, pretty=pretty)


@mcp.tool()
def ducky_agent_transcript(conv_id: str, pretty: bool = False) -> str:
    """Read another agent's conversation as flattened <user>/<assistant> blocks."""
    conv = load_conversation(conv_id.strip(), project_root=_project_root())
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    from backend.agent.a2a_format import flatten_transcript

    return tool_json(
        {
            "conv_id": conv.id,
            "title": conv.title,
            "coding_agent": conv.coding_agent or "ducky",
            "transcript": flatten_transcript(conv.messages),
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_agent_stop(conv_id: str, cascade: bool = False, pretty: bool = False) -> str:
    """Stop a running agent (chat); `cascade=true` also stops agents it spawned.

    Open reply threads it owed are closed and senders get a receiver-cancelled notice.
    """
    from frontend.ui_web.agent_modes import cancel_agent, is_agent_running, linked_children_of
    from backend.agent.a2a_client import on_agent_cancelled_by_user

    target = conv_id.strip()
    if load_conversation(target, project_root=_project_root()) is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    stopped: list[str] = []
    targets = [target] + (linked_children_of(target) if cascade else [])
    for cid in targets:
        if not is_agent_running(cid):
            # Nothing to stop. Marking an idle chat stopped blocked every automatic wake,
            # plan assignments included, until someone typed in it (Oct 25: a member a lead
            # "stopped" while idle never started its next sub plan).
            continue
        cancel_agent(cid)
        stopped.append(cid)
        on_agent_cancelled_by_user(cid)
    return tool_json({"stopped": stopped, "cascade": bool(cascade)}, pretty=pretty)


@mcp.tool()
def ducky_list_duckies(pretty: bool = False) -> str:
    """List available duckies (agent profiles) and when to use each one.

    Use this before delegating a job to pick the right specialist, then spawn it with
    `ducky_spawn_chat(ducky="<id or name>", message="...")`. Each entry has `when_to_use`
    (what that ducky is best at). Skills/tools are all available unless denied.
    """
    out: list[dict[str, Any]] = []
    for p in list_agent_profiles_available():
        out.append(
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "style": p.get("ducky_style"),
                "when_to_use": p.get("when_to_use") or "",
                "personality": _truncate_text(str(p.get("ducky_personality") or ""), 240),
                "disabled_packs": p.get("disabled_packs") or [],
                "disabled_tools": p.get("disabled_tool_ids") or [],
                "kind": p.get("kind"),
            }
        )
    return tool_json({"duckies": out, "count": len(out)}, pretty=pretty)


def _resolve_ducky_profile(ducky: str) -> dict[str, Any] | None:
    """Resolve a ducky by profile id, else by unique case-insensitive name.

    Duplicate display names are ambiguous — callers must pass the profile id
    from ``ducky_list_duckies`` (never guess which of two same-named agents).
    """
    key = (ducky or "").strip()
    if not key:
        return None
    profile = get_agent_profile(key)
    if profile:
        return profile
    low = key.lower()
    matches = [
        c
        for c in list_agent_profiles_available()
        if str(c.get("name") or "").strip().lower() == low
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        ids = ", ".join(repr(str(m.get("id") or "")) for m in matches)
        raise ValueError(
            f"Multiple duckies named {ducky!r} (ids: {ids}). "
            "Pass the profile id from ducky_list_duckies — names are not unique."
        )
    return None


def _profile_spawn_kwargs(profile: dict[str, Any]) -> dict[str, Any]:
    """Persona kwargs for create_conversation from a resolved ducky profile.

    Uses the profile's own model, else the global Default Model from
    Settings → LLMs. A missing model still spawns (empty model). A bad
    explicit pick raises — never silently substitutes another model.
    """
    from frontend.ui_web.panel_api import resolve_model_selection
    from frontend.favorite_models import ResolveErr, allow_create_without_model, refuse_paid_api_for_agent

    settings = PanelSettings.load()
    # Agents spawn here: never onto an API key the person has not opened to agents.
    result = refuse_paid_api_for_agent(allow_create_without_model(
        resolve_model_selection(profile.get("favorite_models"), settings)
    ), settings)
    if isinstance(result, ResolveErr):
        raise ValueError(result.message)
    disabled_packs = profile.get("disabled_packs")
    disabled_tools = profile.get("disabled_tool_ids")
    enabled_subs = profile.get("enabled_subskills")
    return {
        "ducky_style": str(profile.get("ducky_style") or "classic"),
        "ducky_name": str(profile.get("name") or ""),
        "profile_id": str(profile.get("id") or "").strip(),
        "ducky_personality": str(profile.get("ducky_personality") or ""),
        "tts_voice": str(profile.get("tts_voice") or "").strip(),
        "tts_speed": float(profile.get("tts_speed") or 0.0),
        "disabled_packs": disabled_packs if isinstance(disabled_packs, list) else [],
        "enabled_subskills": enabled_subs if isinstance(enabled_subs, dict) else None,
        "disabled_tool_ids": disabled_tools if isinstance(disabled_tools, list) else [],
        "model": result.model,
        "provider": result.provider or None,
        "coding_agent": result.coding_agent,
    }


def _caller_own_group(caller: str) -> str:
    """The group hub this chat already belongs to, or ''."""
    if not (caller or "").strip():
        return ""
    from frontend.ui_web.group_orchestrator import is_group_conversation

    root = _project_root()
    conv = load_conversation(caller, project_root=root)
    if conv is None:
        return ""
    if is_group_conversation(conv):
        return caller
    parent_id = (getattr(conv, "parent_conv_id", None) or "").strip()
    if not parent_id:
        return ""
    parent = load_conversation(parent_id, project_root=root)
    if parent is not None and is_group_conversation(parent):
        return parent_id
    return ""


def _resolve_group_hub_id(group_id: str = "", folder_id: str = "") -> str:
    """Resolve a group hub id from an explicit hub id or a group folder id."""
    root = _project_root()
    gid = (group_id or "").strip()
    if gid:
        hub = load_conversation(gid, project_root=root)
        if hub is not None and getattr(hub, "is_group", False):
            return gid
        raise ValueError(
            f"{gid!r} is not a group hub. Pass the group hub id from ducky_group_create."
        )
    fid = (folder_id or "").strip()
    if fid and fid != "default":
        for folder in load_folders(root):
            if folder.id == fid:
                hub_id = (getattr(folder, "group_hub_id", None) or "").strip()
                if hub_id:
                    return hub_id
                break
        raise ValueError(
            f"Folder {fid!r} is not a group folder. Create one with ducky_group_create first."
        )
    return ""


@mcp.tool()
def ducky_spawn_chat(
    message: str,
    ducky: str = "",
    group_id: str = "",
    folder_name: str = "",
    folder_id: str = "",
    chat_title: str = "",
    mode: str = "agent",
    wait_for_reply: bool = True,
    timeout_sec: float = 180.0,
    coding_agent: str = "ducky",
    sender: str = "",
    write_allowed: Optional[list[str]] = None,
    name: str = "",
    model: str = "",
    context_limit: str = "",
    pretty: bool = False,
) -> str:
    """Invite a specialist into a Group swarm and send the first message.

    ``model`` seats a new member on that model instead of the profile's own (an idle seat
    that is reused keeps its model). ``context_limit`` ("200k") is its context limit: past
    it, it starts a fresh session between turns (omit for 60% of its model's window).

    ``name`` names the seat by its role ("Search lead"): an idle seat of that name gets the
    message, otherwise a new seat is made with that name.

    ``write_allowed`` (leader/user only) gives the member a write lane — gitignore-style
    globs like ["Content/Verse/Shop/**"] — so parallel members never edit the same files.
    Partition lanes before spawning parallel work; overlaps are refused.

    Pass ``group_id`` (or a group ``folder_id``) only to target a specific group.
    With neither, the caller joins its own group: the group it belongs to, else a
    group named after this chat. ``folder_name`` finds or creates that named group
    inside the caller's folder. A ducky already seated and idle gets the message in
    its existing chat; a busy one gets a numbered copy ("Verse Coder 2") in the
    same group. Prefer ``ducky_send_chat_message`` when you already have the chat id.
    A lead whose team has members writes its plan first: work spawned into its team before
    that is refused (seat members with ``ducky_group_invite``, then assign sub plans).
    """
    text = _truncate_text(message.strip(), _MAX_MESSAGE_CHARS)
    if not text:
        raise ValueError("message must not be empty")
    del coding_agent, chat_title  # invite uses the profile's model; title = profile name
    caller = _resolve_sender(sender)

    if not ducky.strip():
        roster = [
            {"name": p.get("name"), "when_to_use": p.get("when_to_use") or ""}
            for p in list_agent_profiles_available()
            if str(p.get("name") or "").strip()
        ]
        if roster:
            return tool_json(
                {
                    "status": "needs_ducky",
                    "message": (
                        "No ducky selected. Pick the one whose when_to_use best fits this job, "
                        "then call ducky_spawn_chat again with ducky=<name> and group_id=<hub>."
                    ),
                    "duckies": roster,
                },
                pretty=pretty,
            )
        raise ValueError("ducky is required")

    root = _project_root()
    caller_conv = load_conversation(caller, project_root=root) if caller else None
    hub_id = _resolve_group_hub_id(group_id=group_id, folder_id=folder_id)
    if not hub_id and folder_name.strip():
        parent_folder = (getattr(caller_conv, "folder_id", None) or "") if caller_conv else ""
        created = _panel_api().group_find_or_create(
            name=folder_name.strip(), folder_id=parent_folder, open_tab=False
        )
        if not created.get("ok"):
            raise ValueError(str(created.get("error") or "group_create failed"))
        hub_id = str(created.get("id") or "").strip()
    if not hub_id:
        hub_id = _caller_own_group(caller)
    if not hub_id and caller_conv is not None:
        # One group named after this chat. Leader is a pointer; the chat is not moved.
        title = (getattr(caller_conv, "title", None) or "").strip() or "Group"
        created = _panel_api().group_find_or_create(
            name=title, folder_id=(caller_conv.folder_id or ""), open_tab=False
        )
        if not created.get("ok"):
            raise ValueError(str(created.get("error") or "group_create failed"))
        hub_id = str(created.get("id") or "").strip()
        hub = load_conversation(hub_id, project_root=root)
        if hub is not None and not (getattr(hub, "leader_conv_id", None) or "").strip():
            hub.leader_conv_id = caller
            save_conversation(hub, root)
    if not hub_id:
        raise ValueError("No group to seat this ducky in.")
    if _spawn_needs_plan(caller, hub_id, root):
        return tool_json({"status": "not_sent", "sender": caller, "reason": LEAD_NEEDS_PLAN}, pretty=pretty)

    profile = _resolve_ducky_profile(ducky)
    if profile is None:
        raise ValueError(
            f"No ducky named {ducky!r}. Call ducky_list_duckies to see available duckies."
        )
    if write_allowed is not None:
        _require_lane_authority(hub_id)
    seated = _panel_api().group_seat_profile(
        hub_id, str(profile.get("id") or "").strip(), write_allowed=write_allowed, for_agent=True,
        exclude_conv_id=caller, model=model.strip(), **({"name": name.strip()} if name.strip() else {}),
    )
    if not seated.get("ok"):
        raise ValueError(str(seated.get("error") or "group_invite failed"))
    member = seated.get("member") or {}
    conv_id = str(member.get("member_conv_id") or "").strip()
    if not conv_id:
        raise ValueError("group_invite did not return a member_conv_id")
    _store_context_limit(conv_id, context_limit)

    from frontend.ui_web.agent_modes import run_message, run_message_and_wait

    text = _from_another_ducky(conv_id, text, _project_root(), sender=caller)
    mode_norm = (mode or "agent").lower()
    base = {
        "conv_id": conv_id,
        "title": str(member.get("name") or ducky),
        "folder_id": "",
        "ducky": str(member.get("ducky_name") or member.get("name") or ducky),
        "group_id": hub_id,
        "coding_agent": str(member.get("coding_agent") or "ducky"),
        "model": str(member.get("model") or ""),
        "provider": "",
        "write_allowed": member.get("write_allowed"),
    }
    fresh = load_conversation(conv_id, project_root=_project_root())
    if fresh is not None:
        base["folder_id"] = fresh.folder_id or ""
        base["model"] = fresh.model or base["model"]
        base["provider"] = fresh.provider or ""
        base["coding_agent"] = getattr(fresh, "coding_agent", None) or base["coding_agent"]

    if wait_for_reply:
        # External coding agents (Claude Code / Codex / Cursor) run until they
        # finish or the user cancels — never wall-clock-kill at 15 minutes.
        is_external = str(base.get("coding_agent") or "ducky") != "ducky"
        wait_timeout = 0.0 if is_external else max(5.0, min(float(timeout_sec), 900.0))
        outcome = run_message_and_wait(
            conv_id,
            text,
            mode_norm,
            timeout_sec=wait_timeout,
            cancel_on_timeout=False if is_external else (not caller),
            parent=caller or hub_id,
            queue_if_busy=True,
        )
        if outcome.get("status") in ("timeout", "queued") and caller:
            from backend.agent.a2a_client import on_agent_stopped, open_thread
            from frontend.ui_web.agent_modes import is_agent_running

            rid = open_thread(caller, conv_id, deliver_result=True)
            if not is_agent_running(conv_id):
                on_agent_stopped(conv_id, "done")
            outcome["response_id"] = rid
            lead_in = (
                "The member is busy with an earlier task; this one runs right after it."
                if outcome.get("status") == "queued"
                else f"No reply within {float(timeout_sec):.0f}s — the member is STILL WORKING."
            )
            outcome["error"] = (
                f"{lead_in} Its result will arrive as a [ducky:agent-message] "
                f"(response_id {rid}). Do NOT re-spawn; finish your turn."
            )
        return tool_json({**base, **outcome}, pretty=pretty)

    response_id = ""
    if caller:
        from backend.agent.a2a_client import open_thread

        response_id = open_thread(caller, conv_id, deliver_result=True)
    # Queue behind a busy member's turn, never cancel it.
    started = run_message(conv_id, text, mode_norm, "", parent=caller or hub_id, queue_if_busy=True)
    status = "queued" if started == "queued" else "running"
    return tool_json({**base, "status": status, "response_id": response_id}, pretty=pretty)


@mcp.tool()
def ducky_recycle_member(
    conv_id: str,
    continue_message: str = "",
    mode: str = "agent",
    wait_for_reply: bool = True,
    timeout_sec: float = 180.0,
    sender: str = "",
    pretty: bool = False,
) -> str:
    """Refresh a group member: handoff, then reset only its session in the same chat.

    Never delete or replace the chat. Preserve every message, group membership,
    approvals and plan assignment; the next task starts a fresh agent session.
    Target must be a group member (parent = group hub). For ordinary follow-ups use
    ``ducky_send_chat_message``.
    """
    old_id = (conv_id or "").strip()
    if not old_id:
        raise ValueError("conv_id must not be empty")
    root = _project_root()
    old = load_conversation(old_id, project_root=root)
    if old is None:
        raise ValueError(f"Conversation not found: {old_id!r}")
    if getattr(old, "is_group", False):
        raise ValueError("Cannot recycle a group hub — target a group member chat.")

    parent_id = (getattr(old, "parent_conv_id", None) or "").strip()
    parent_conv = load_conversation(parent_id, project_root=root) if parent_id else None
    if parent_conv is None or not getattr(parent_conv, "is_group", False):
        raise ValueError(
            "Recycle targets group members only (parent must be a group hub). "
            "Seat agents with ducky_group_invite / ducky_spawn_chat(group_id=…)."
        )

    from frontend.ui_web.agent_modes import HELD_FOR_MODEL, run_message, run_message_and_wait
    from frontend.ui_web.context_control import reset_context
    from backend.tools.panel.permission_prompt import approvals_of

    kept_starter = str(approvals_of(old_id).get("started_by") or "")
    mode_norm = (mode or "agent").lower()
    handoff_timeout = max(30.0, min(float(timeout_sec), 600.0))
    handoff_outcome = run_message_and_wait(
        old_id,
        _SUBAGENT_HANDOFF_PROMPT,
        mode_norm,
        timeout_sec=handoff_timeout,
        cancel_on_timeout=False,
        parent=parent_id,
        started_by=kept_starter,
    )
    handoff_text = str(handoff_outcome.get("assistant_text") or "").strip()
    if not handoff_text and handoff_outcome.get("status") == "timeout":
        handoff_text = (
            f"(handoff timed out after {handoff_timeout:.0f}s — predecessor may still be finishing)"
        )
    if not handoff_text:
        handoff_text = str(handoff_outcome.get("error") or "empty handoff")

    # The handoff turn's reply lands before its run is marked idle (it runs in another
    # process), and the session reset refuses a running chat: every recycle failed with
    # "agent is working" after writing a handoff (Oct 25). Wait for idle first.
    from frontend.ui_web.agent_modes import is_agent_running

    idle_by = time.monotonic() + handoff_timeout
    while is_agent_running(old_id) and time.monotonic() < idle_by:
        time.sleep(0.5)
    # Session reset refuses a still-running handoff; history is never discarded.
    reset_context(old_id, ["session"], project_root=root)
    old_title = old.title
    member = load_conversation(old_id, project_root=root) or old
    spawn_text = build_recycle_spawn_message(handoff_text, continue_message)
    base = {
        "status": "recycled",
        "old_conv_id": old_id,
        "old_title": old_title,
        "conv_id": member.id,
        "title": member.title,
        "folder_id": member.folder_id,
        "ducky": member.ducky_name or "",
        "parent_conv_id": parent_id,
        "group_id": parent_id,
        "handoff_status": handoff_outcome.get("status"),
        "handoff_chars": len(handoff_text),
        "coding_agent": getattr(member, "coding_agent", None) or "ducky",
        "model": member.model or "",
    }
    if wait_for_reply:
        is_external = str(base.get("coding_agent") or "ducky") != "ducky"
        outcome = run_message_and_wait(
            member.id,
            spawn_text,
            mode_norm,
            # External CLIs: wait until done (0 = no wall-clock limit).
            timeout_sec=0.0 if is_external else max(5.0, min(float(timeout_sec), 900.0)),
            cancel_on_timeout=False,
            parent=parent_id,
            started_by=kept_starter,
        )
        return tool_json({**base, **outcome}, pretty=pretty)

    response_id = ""
    caller = _resolve_sender(sender)
    if caller:
        from backend.agent.a2a_client import open_thread

        response_id = open_thread(caller, member.id, deliver_result=True)
    started = run_message(member.id, spawn_text, mode_norm, "", parent=parent_id, started_by=kept_starter)
    status = "waiting_for_model" if started == HELD_FOR_MODEL else "running"
    return tool_json({**base, "status": status, "response_id": response_id}, pretty=pretty)


@mcp.tool()
def ducky_fresh_session(handoff: str, pretty: bool = False) -> str:
    """Start yourself fresh after this turn: your next turn runs in a new session of this same
    chat, opening with ``handoff`` and your task. History, plan, lane and approvals stay.

    Write the handoff yourself: goal, what is done, files touched, decisions, next steps,
    blockers. Use it when your context is long and the rest of the work doesn't need it.
    Ducky applies it when it next starts you on your plan, never mid-turn.
    """
    text = (handoff or "").strip()
    if not text:
        raise ValueError("handoff must not be empty")
    me = _resolve_sender("")
    if not me:
        raise ValueError("No calling chat: only an agent can ask for its own fresh session.")
    from frontend.ui_web.context_limit import request_fresh

    request_fresh(me, text)
    return tool_json({"status": "after_this_turn", "conv_id": me, "handoff_chars": len(text)}, pretty=pretty)


def ducky_recycle_subagent(
    conv_id: str,
    continue_message: str = "",
    mode: str = "agent",
    wait_for_reply: bool = True,
    timeout_sec: float = 180.0,
    sender: str = "",
    pretty: bool = False,
) -> str:
    """Hidden alias (HIDDEN_ALIAS_TOOLS) for ``ducky_recycle_member``."""
    return ducky_recycle_member(
        conv_id=conv_id,
        continue_message=continue_message,
        mode=mode,
        wait_for_reply=wait_for_reply,
        timeout_sec=timeout_sec,
        sender=sender,
        pretty=pretty,
    )


@mcp.tool()
def ducky_append_chat_message(
    conv_id: str,
    role: Literal["user", "assistant"],
    content: str,
    pretty: bool = False,
) -> str:
    """Append a static note to a chat without running the agent (use ducky_send_chat_message for follow-ups)."""
    text = _truncate_text(content.strip(), _MAX_MESSAGE_CHARS)
    if not text:
        raise ValueError("content must not be empty")
    root = _project_root()
    conv = load_conversation(conv_id.strip(), project_root=root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    append_message(conv, {"role": role, "content": text, "ts": time.time()}, project_root=root)
    return tool_json(
        {"id": conv.id, "title": conv.title, "message_count": len(conv.messages)},
        pretty=pretty,
    )


@mcp.tool()
def ducky_get_chat_context(
    conv_id: str,
    model: str = "",
    mode: str = "agent",
    project: str = "",
    pretty: bool = False,
) -> str:
    """Read token breakdown and omitted segments for any panel chat.

    Every ducky is in the one list, so each gets a live token breakdown; a chat an
    older Ducky still keeps under a project gets a stored-context summary labeled with
    that project. This never switches the selected project.
    Optional `project` (path, name, or slug from ducky_list_projects) is only a
    lookup hint, as in ducky_read_chat; it cannot override where the chat is kept.
    """
    conv_id = conv_id.strip()
    root = _resolve_project_root_arg(project)
    conv = load_conversation(conv_id, project_root=root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")

    home_slug = conversation_project_slug(conv.id, project_root=root)
    if home_slug is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")

    if home_slug not in (project_slug(""), project_slug(_project_root())):
        total_chars = sum(len(str(m.get("content") or "")) for m in conv.messages)
        return tool_json(
            {
                "conv_id": conv.id,
                "title": conv.title,
                "project": project_slug_display_name(home_slug),
                "scope": "cross_project_summary",
                "message_count": len(conv.messages),
                "approx_tokens": total_chars // 4,
                "last_messages": _serialize_messages(conv.messages)[-12:],
                "note": (
                    "Live token breakdown is only computed for the active project; "
                    "this summarizes the ducky's stored context. Use ducky_read_chat "
                    "with this conv_id for the full history."
                ),
            },
            pretty=pretty,
        )

    from backend.agent.context_memory import chat_context_memory_status, should_compress
    from frontend.settings import PanelSettings
    from frontend.ui_web.context_tokens import compute_context_usage

    settings = PanelSettings.load()
    usage_model = model.strip() or conv.model or ""
    usage = compute_context_usage(conv_id, usage_model, mode=mode)
    mem = chat_context_memory_status(conv, settings=settings)
    return tool_json(
        {
            "conv_id": conv.id,
            "title": conv.title,
            **usage,
            "context_summary_tokens": mem.get("context_summary_tokens", 0),
            "context_summary_through": mem.get("context_summary_through", 0),
            "keep_last": mem.get("keep_last"),
            "message_count": mem.get("message_count"),
            "compress_recommended": bool(mem.get("compress_recommended"))
            or should_compress(conv, settings=settings, force=False),
            "has_context_summary": bool(str(mem.get("context_summary") or "").strip()),
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_reset_chat_context(
    conv_id: str,
    segments: list[str],
    model: str = "",
    mode: str = "agent",
    pretty: bool = False,
) -> str:
    """Reset selected context segments on any panel chat (sub-agent, user chat, etc.).

    segments: all | conversation | system | mcp_tools | rules | skill | session

    ``conversation`` deletes the chat's messages. ``session`` only starts the chat's
    coding agent (Codex, Claude Code, Cursor) on a fresh thread for its next task and
    keeps every message; it refuses while that agent is working.
    """
    from frontend.ui_web.context_control import reset_context

    result = reset_context(conv_id.strip(), segments, project_root=_project_root(), model=model, mode=mode)
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_restore_chat_context(
    conv_id: str,
    segments: list[str],
    model: str = "",
    mode: str = "agent",
    pretty: bool = False,
) -> str:
    """Restore previously omitted static context segments on any panel chat."""
    from frontend.ui_web.context_control import restore_context

    result = restore_context(conv_id.strip(), segments, project_root=_project_root(), model=model, mode=mode)
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_read_tool_spill(name: str, offset: int = 0, max_chars: int = 8000, pretty: bool = False) -> str:
    """Page a spilled nested-tool result under AppData tool_spills (basename only).
    One call returns one whole 8,000-char page; the next page starts at offset + 8000."""
    from backend.agent.tool_spills import read_tool_spill

    try:
        return tool_json(read_tool_spill(name, offset=offset, max_chars=max_chars), pretty=pretty)
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)


@mcp.tool()
def ducky_list_tasks(pretty: bool = False) -> str:
    """List UEFN-Ducky Tasks (plan/handoff/verify containers) for the active project."""
    from backend.agent.coding_agents.epic import list_tasks

    return tool_json({"tasks": list_tasks(_project_root())}, pretty=pretty)


@mcp.tool()
def ducky_create_task(title: str, goal: str = "", pretty: bool = False) -> str:
    """Create a Task with a goal/spec for Plan → Handoff → Verify workflows."""
    from backend.agent.coding_agents.epic import create_task

    return tool_json(create_task(title, goal=goal, project_root=_project_root()), pretty=pretty)


@mcp.tool()
def ducky_add_task_phase(task_id: str, title: str, plan: str = "", pretty: bool = False) -> str:
    """Add a phase (with optional detailed plan) to a Task."""
    from backend.agent.coding_agents.epic import add_phase

    return tool_json(add_phase(task_id, title, plan=plan, project_root=_project_root()), pretty=pretty)


@mcp.tool()
def ducky_task_handoff(task_id: str, phase_id: str = "", pretty: bool = False) -> str:
    """Build a handoff prompt for a Task phase (pass to ducky_spawn_chat / coding agents)."""
    from backend.agent.coding_agents.epic import build_handoff_prompt

    return tool_json(
        {"ok": True, "prompt": build_handoff_prompt(task_id, phase_id=phase_id, project_root=_project_root())},
        pretty=pretty,
    )


@mcp.tool()
def ducky_verify_task(
    task_id: str,
    implementation_summary: str,
    phase_id: str = "",
    pretty: bool = False,
) -> str:
    """Verify an implementation summary against the Task/phase plan; writes a review artifact."""
    from backend.agent.coding_agents.epic import verify_against_plan

    return tool_json(
        verify_against_plan(
            task_id,
            phase_id=phase_id,
            implementation_summary=implementation_summary,
            project_root=_project_root(),
        ),
        pretty=pretty,
    )


def _resolve_plan_chat_id(chat_id: str = "") -> str:
    """Explicit chat_id, else the calling chat, else active embedded conv, else DUCKY_CONV_ID env.

    The shared tool server serves every outside agent from one process, so its
    environment names no chat: the per-call identity does — same order as
    ``_resolve_sender``. Without it Codex's plan ticks failed "chat_id required".
    """
    cid = (chat_id or "").strip()
    if cid:
        return cid
    from backend.workspace import identity as run_identity

    ctx = run_identity.current()
    if ctx is not None and ctx.conv_id:
        return str(ctx.conv_id).strip()
    try:
        from frontend.ui_web.agent_modes import get_active_conv_id

        active = get_active_conv_id()
        if active:
            return str(active).strip()
    except Exception:
        pass
    import os

    return (os.environ.get("DUCKY_CONV_ID") or "").strip()


@mcp.tool()
def ducky_create_plan(
    title: str = "",
    overview: str = "",
    body_markdown: str = "",
    nodes: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    chat_id: str = "",
    pretty: bool = False,
) -> str:
    """Create a project Plan with an outline tree (main → subplans). In Plan mode, create then stop.

    Field roles (do NOT mix):
    - ``title``: the plan's short name (left out: the first words of ``overview``).
    - ``overview``: short 1–3 sentence summary ONLY (plain text). Never paste JSON or XML here.
    - ``body_markdown``: longer plan description (markdown). Optional but preferred for context.
    - ``nodes``: REQUIRED for multi-step work — a real JSON **array** argument
      ``[{id?, content, status?, children?}, …]``, NOT text inside overview.
      Followable shape: Diagnose → Fix → Verify; each leaf = one concrete action + Done-when.

    Write steps a person can follow:
    - One outcome per step, about 15–30 minutes of one agent's work. "Check all features end
      to end" is fifteen steps (one per feature or check), not one; then progress shows and a
      failing check names itself.
    - ``content``: a short title in plain words (under 80 characters).
    - ``body_markdown`` on every step: what to do, Done when (name the check tool), and one
      concrete example of the result. Sections get a one-line description too.
    Example step:
      {"id": "mov-export", "content": "Export the test project as a 15 s MOV",
       "body_markdown": "Do: render the test project with the bundled ffmpeg.\\n"
                        "Done when: imk_render_get says done and the file plays 15 s at 1920x1080.\\n"
                        "Example: Island_trailer.mov, 15.0 s, 30 fps, 41 MB."}
    The result lists ``plan_review`` for steps that are too big or have no description: fix them
    before starting.

    Never leave only a chat prose Fix plan — call this tool with ``nodes`` filled.
    Legacy ``todos`` (flat {id?, content, status?}) become root nodes.
    chat_id defaults to the active conversation. One plan per chat; hierarchy is inside nodes.
    Parents cannot be completed until nested subplans are done. Rearrange with ducky_plan_move_node.

    Team plans: one main plan with one sub plan per agent or group (a section assigned to
    its chat or group id, its points inside), never several loose steps to one agent. Members see that section in their own plan panel.
    Finish every assignment: completed, or cancelled with the reason; set notes
    (body_markdown) and status in one call, preserving original notes. A section
    completes itself when its last step does, so put your result in that step's
    notes; never tick or annotate the finished section. Notes,
    status and owner are bookkeeping allowed while playing; finished steps stay
    frozen. Content, moving and deleting steps require a pause; adding a step does
    not. Assigning a sub plan starts its member: Ducky sends it the task, starts it again
    when a turn ends with points left, and tells the lead when the sub plan is finished
    (with its notes) or the member ends with "BLOCKED: ...". Nobody dispatches by message.
    A team works in ONE plan, its lead's: members never create their own. Members tick
    and split their points without chat_id (the edit lands in the team's plan); the last
    point checks the sub plan off and tells the lead, so members send no reports. ``after``
    = ids of steps that must finish first: such a step can't start, and its owner is
    not woken until they finish. A member blocked by a teammate's work: the lead adds
    the fix as a step for that teammate and sets the blocked step's ``after`` to it.
    """
    from backend.agent.coding_agents.plans import create_plan, outline_numbers, push_plan_updated, todo_progress

    cid = _resolve_plan_chat_id(chat_id)
    if not cid:
        return tool_json({"ok": False, "error": "chat_id required (no active conversation)"}, pretty=pretty)
    # Oct 17 2026: a lead sent overview without title twice and got a validation error each time.
    title = (title or "").strip() or (" ".join((overview or "").split())[:80].rstrip(" .,;:") or "Plan")
    try:
        plan = create_plan(
            cid,
            title=title,
            overview=overview,
            body_markdown=body_markdown,
            nodes=nodes,
            todos=todos,
            project_root=_project_root(),
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    push_plan_updated(plan)
    prog = todo_progress(plan)
    out: dict[str, Any] = {
        "ok": True,
        "plan": plan,
        "progress": prog,
        "outline": [
            {"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]}
            for lab, n in outline_numbers(plan.get("nodes"))
        ],
    }
    if prog.get("total", 0) == 0:
        out["warning"] = (
            "plan has 0 steps — pass nodes=[{id, content, children}] as a JSON array "
            "argument (not inside overview). UI Steps stay empty until nodes are set."
        )
    try:
        from backend.tools.panel.ducky_swarm import suggest_swarm

        suggestion = suggest_swarm(plan)
    except Exception:  # a suggestion never breaks plan creation
        suggestion = None
    if suggestion:
        out["swarm_suggestion"] = suggestion
    from backend.agent.coding_agents.plans import attach_next_tick, with_plan_review

    return tool_json(with_plan_review(attach_next_tick(out, plan), plan), pretty=pretty)


@mcp.tool()
def ducky_update_plan(
    nodes: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    title: str = "",
    overview: str = "",
    body_markdown: str = "",
    merge: bool = True,
    status: str = "",
    chat_id: str = "",
    pretty: bool = False,
) -> str:
    """Update the active chat Plan outline/body.

    Field roles: ``overview`` = short summary only; ``body_markdown`` = description;
    ``nodes`` = JSON array tree (never stringify nodes into overview).
    Prefer nodes= for full tree replace; todos+merge patches by id.
    Cannot complete a node while nested subplans are unfinished. Prefer ducky_plan_*_node for surgical edits.
    """
    from backend.agent.coding_agents.plans import (
        attach_next_tick,
        outline_numbers,
        push_plan_updated,
        todo_progress,
        update_plan,
    )

    cid = _resolve_plan_chat_id(chat_id)
    if not cid:
        return tool_json({"ok": False, "error": "chat_id required (no active conversation)"}, pretty=pretty)
    try:
        plan = update_plan(
            cid,
            title=title if title else None,
            overview=overview if overview else None,
            body_markdown=body_markdown if body_markdown else None,
            nodes=nodes,
            todos=todos,
            merge=merge,
            status=status if status else None,
            project_root=_project_root(),
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    push_plan_updated(plan)
    prog = todo_progress(plan)
    out: dict[str, Any] = {
        "ok": True,
        "plan": plan,
        "progress": prog,
        "outline": [
            {"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]}
            for lab, n in outline_numbers(plan.get("nodes"))
        ],
    }
    if prog.get("total", 0) == 0 and (nodes is not None or overview):
        out["warning"] = (
            "plan still has 0 steps — pass nodes as a JSON array argument, "
            "not pasted into overview text."
        )
    out = attach_next_tick(out, plan)
    if nodes is not None:
        from backend.agent.coding_agents.plans import with_plan_review

        out = with_plan_review(out, plan)
    return tool_json(out, pretty=pretty)


@mcp.tool()
def ducky_list_plans(pretty: bool = False) -> str:
    """List project plans for the active UEFN project only (not templates)."""
    from backend.agent.coding_agents.plans import list_plans

    return tool_json({"ok": True, "plans": list_plans()}, pretty=pretty)


@mcp.tool()
def ducky_list_plan_templates(pretty: bool = False) -> str:
    """List reusable plan templates (global). No progress counts — blueprints only."""
    from backend.agent.coding_agents.plans import list_templates

    return tool_json({"ok": True, "templates": list_templates()}, pretty=pretty)


@mcp.tool()
def ducky_get_plan_template(template_id: str, pretty: bool = False) -> str:
    """Load a plan template by id (outline tree, no progress tracking)."""
    from backend.agent.coding_agents.plans import load_template, outline_numbers

    doc = load_template(template_id)
    if not doc:
        return tool_json({"ok": False, "error": "template not found"}, pretty=pretty)
    return tool_json(
        {
            "ok": True,
            "template": doc,
            "outline": [{"n": lab, "id": n["id"], "content": n["content"]} for lab, n in outline_numbers(doc.get("nodes"))],
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_create_plan_template(
    title: str,
    overview: str = "",
    body_markdown: str = "",
    nodes: list[dict[str, Any]] | None = None,
    pretty: bool = False,
) -> str:
    """Create a reusable plan template (global). Does not create a project plan."""
    from backend.agent.coding_agents.plans import create_template, outline_numbers

    try:
        doc = create_template(
            title=title,
            overview=overview,
            body_markdown=body_markdown,
            nodes=nodes,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json(
        {
            "ok": True,
            "template": doc,
            "outline": [{"n": lab, "id": n["id"], "content": n["content"]} for lab, n in outline_numbers(doc.get("nodes"))],
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_update_plan_template(
    template_id: str,
    title: str = "",
    overview: str = "",
    body_markdown: str = "",
    nodes: list[dict[str, Any]] | None = None,
    pretty: bool = False,
) -> str:
    """Update a plan template. Does not change project plans instantiated from it."""
    from backend.agent.coding_agents.plans import outline_numbers, update_template

    try:
        doc = update_template(
            template_id,
            title=title if title else None,
            overview=overview if overview else None,
            body_markdown=body_markdown if body_markdown else None,
            nodes=nodes,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json(
        {
            "ok": True,
            "template": doc,
            "outline": [{"n": lab, "id": n["id"], "content": n["content"]} for lab, n in outline_numbers(doc.get("nodes"))],
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_instantiate_plan_template(
    template_id: str,
    chat_id: str = "",
    pretty: bool = False,
) -> str:
    """Snapshot a template onto the active chat as a project plan. Template is unchanged."""
    from backend.agent.coding_agents.plans import instantiate_template, outline_numbers, push_plan_updated, todo_progress

    cid = _resolve_plan_chat_id(chat_id)
    if not cid:
        return tool_json({"ok": False, "error": "chat_id required (no active conversation)"}, pretty=pretty)
    try:
        plan = instantiate_template(template_id, chat_id=cid, project_root=_project_root())
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    push_plan_updated(plan)
    return tool_json(
        {
            "ok": True,
            "plan": plan,
            "progress": todo_progress(plan),
            "outline": [{"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]} for lab, n in outline_numbers(plan.get("nodes"))],
        },
        pretty=pretty,
    )


# Plan step tools and terminal tools live in their own modules; importing them here
# registers them with every ducky_panel import and keeps the old import paths working.
from backend.tools.panel.ducky_panel_plans import (  # noqa: E402,F401
    ducky_get_plan,
    ducky_plan_add_node,
    ducky_plan_delete_node,
    ducky_plan_move_node,
    ducky_plan_update_node,
)
from backend.tools.panel.ducky_panel_terminal import (  # noqa: E402,F401
    ducky_terminal_close,
    ducky_terminal_list,
    ducky_terminal_open,
    ducky_terminal_read_output,
    ducky_terminal_run,
)
