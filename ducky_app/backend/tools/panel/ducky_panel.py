"""Host-side panel meta tools — status, errors, chat management (no UEFN listener)."""

from __future__ import annotations

import time
from typing import Any, Literal

from frontend.agent_profiles import get_agent_profile, list_agent_profiles_available
from frontend.archive_folder import is_archive_folder_id
from frontend.error_log import read_errors
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings
from frontend.ui_web.project_switch import get_panel_project_info, list_panel_projects
from frontend.ui_web.project_chats import (
    append_message,
    create_conversation,
    delete_conversation,
    list_conversations,
    load_conversation,
    load_folders,
    move_conversation,
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
    names = ", ".join(str(p.get("name") or "") for p in projects) or "(none)"
    raise ValueError(
        f"Unknown project {project!r}. Known projects: {names} — see ducky_list_projects."
    )


def _is_cross_project(root: str) -> bool:
    from frontend.ui_web.project_switch import normalize_project_path

    return normalize_project_path(root).lower() != normalize_project_path(_project_root()).lower()


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
_DISPATCHER_BLOCKLIST = frozenset(
    {"ducky_call_tool", "ducky_get_tools", "ducky_find_tools"}
)


def _truncate_desc(text: str, limit: int = _DESC_TRUNC) -> str:
    from backend.agent.toolsets.tool_index import truncate_desc

    return truncate_desc(text, limit)


@mcp.tool()
async def ducky_get_tools(
    name: str = "",
    pattern: str = "",
    limit: int = 20,
    pretty: bool = False,
) -> str:
    """Discover MCP tool schemas (Cursor GetMcpTools-style). Prefer name= or pattern=
    over empty catalog. Full schemas are NOT in the system prompt — call this, then
    ducky_call_tool(name, arguments). Floor tools (workspace_*, status, ask_user) are
    already in tools[] and need no discovery.
    """
    from backend.agent.tools import _slim_description, _slim_tool_schema, list_mcp_tools
    from backend.agent.toolsets.excluded import EXCLUDED_TOOLS

    tools = await list_mcp_tools()
    by_name = {
        str(getattr(t, "name", "") or ""): t
        for t in tools
        if str(getattr(t, "name", "") or "") not in EXCLUDED_TOOLS
    }
    n = (name or "").strip()
    p = (pattern or "").strip()
    lim = max(1, min(int(limit or 20), 50))

    if n:
        t = by_name.get(n)
        if t is None:
            import re as _re

            q = n.lower()
            close = sorted(
                (tn for tn in by_name if q in tn.lower() or tn.lower() in q),
                key=len,
            )[:10]
            if not close:
                toks = [tok for tok in _re.split(r"[^a-z0-9]+", q) if len(tok) >= 3]
                if toks:
                    close = sorted(
                        (
                            tn
                            for tn in by_name
                            if any(tok in tn.lower() for tok in toks)
                        ),
                        key=len,
                    )[:10]
            return tool_json(
                {
                    "ok": False,
                    "error": f"unknown tool: {n}",
                    "close_matches": close,
                    "hint": (
                        "This registry spans core + desktop plugins + nested MCP "
                        "({prefix}__*); nested names appear only while that MCP's "
                        "session is connected. Pick one close_matches name (or one "
                        "ducky_get_tools(pattern=…) search) — two misses means the "
                        "tool does not exist: use the closest match, never retry "
                        "name variants."
                    ),
                },
                pretty=pretty,
            )
        return tool_json(
            {
                "name": t.name,
                "description": _slim_description(t.description or "") or t.name,
                "inputSchema": _slim_tool_schema(t),
                "hint": "Call with ducky_call_tool(name, arguments). Always pass arguments.",
            },
            pretty=pretty,
        )

    if p:
        q = p.lower()
        scored: list[tuple[int, dict[str, Any]]] = []
        for tname, t in by_name.items():
            desc = str(getattr(t, "description", "") or "")
            hay = f"{tname} {desc}".lower()
            if q not in hay and not all(tok in hay for tok in q.split()):
                continue
            score = (2 if q in tname.lower() else 0) + (1 if q in desc.lower() else 0)
            scored.append(
                (
                    score,
                    {
                        "name": tname,
                        "description": _truncate_desc(desc),
                    },
                )
            )
        scored.sort(key=lambda item: (-item[0], item[1]["name"]))
        matches = [row for _, row in scored[:lim]]
        hint = "Fetch one schema with ducky_get_tools(name=…) then ducky_call_tool."
        if not matches:
            hint = (
                "No matches. Registry spans core + desktop plugins + nested MCP "
                "({prefix}__*, connected sessions only). Try ONE broader single-word "
                "pattern — two misses means the tool does not exist: use the closest "
                "known tool instead of retrying variants."
            )
        return tool_json(
            {
                "pattern": p,
                "matches": matches,
                "count": len(matches),
                "hint": hint,
            },
            pretty=pretty,
        )

    # Empty catalog — last resort (names + short blurbs only).
    catalog = [
        {"name": tname, "description": _truncate_desc(str(getattr(t, "description", "") or ""))}
        for tname, t in sorted(by_name.items(), key=lambda kv: kv[0])
    ]
    return tool_json(
        {
            "catalog": catalog[:lim] if lim < len(catalog) else catalog,
            "count": len(catalog),
            "truncated": lim < len(catalog),
            "hint": "Prefer pattern= or name=. Then ducky_call_tool(name, arguments).",
        },
        pretty=pretty,
    )


@mcp.tool()
async def ducky_call_tool(
    name: str,
    arguments: dict[str, Any] | None = None,
    description: str = "",
    pretty: bool = False,
) -> str:
    """Invoke any flat UEFN-Ducky MCP tool by name (Cursor CallMcpTool-style).
    Desktop plugins (blender_*) and nested MCP ({prefix}__*) use the same bridge —
    no server id. Discover schemas with ducky_get_tools first. Always pass arguments.
    """
    from backend.agent.run_context import is_plan_only
    from backend.agent.tools import execute_tool
    from backend.agent.toolsets.excluded import EXCLUDED_TOOLS
    from backend.agent.toolsets.plan_safe import is_plan_safe_tool

    tool_name = (name or "").strip()
    if not tool_name:
        raise ValueError("name must not be empty")
    if tool_name in _DISPATCHER_BLOCKLIST:
        raise ValueError(f"cannot dispatch meta-tool {tool_name}")
    if tool_name in EXCLUDED_TOOLS:
        raise ValueError(f"tool excluded: {tool_name}")
    if is_plan_only() and not is_plan_safe_tool(tool_name):
        return tool_json(
            {
                "ok": False,
                "tool": tool_name,
                "error": "Plan mode: tool is not plan-safe (mutator).",
                "hint": "Switch to Agent mode, or use a read/discover tool.",
            },
            pretty=pretty,
        )
    args = arguments if isinstance(arguments, dict) else {}
    # description is for the model/UI only — not passed through.
    _ = (description or "").strip()
    result = await execute_tool(tool_name, args)
    if result.ok:
        # Prefer raw tool payload (already JSON string from most tools).
        data = result.data
        if isinstance(data, str) and data.strip().startswith(("{", "[")):
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


@mcp.tool()
async def ducky_find_tools(query: str, limit: int = 20, pretty: bool = False) -> str:
    """Alias for ducky_get_tools(pattern=query). Prefer ducky_get_tools."""
    return await ducky_get_tools(pattern=query, limit=limit, pretty=pretty)


@mcp.tool()
def ducky_get_status(pretty: bool = False) -> str:
    """Panel + listener + Epic UEFN MCP status. Works while UEFN is offline.

    ``epic_mcp_online`` is TCP to the nested unreal-mcp URL (default :8000/mcp).
    If false and the task needs editor Verse / entities / devices / PIC, tell the
    user ``epic_mcp_setup_steps`` — do not fall back to pruned Ducky editor tools.
    """
    from frontend import __version__

    settings = PanelSettings.load()
    result = fetch_listener_status(
        PANEL_LISTENER_PORT,
        state=_status_state,
        version=__version__,
        selected_project_root=settings.uefn_project_root,
    )
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_get_local_project(pretty: bool = False) -> str:
    """UEFN-Ducky panel project path and display name (from settings, not the live editor)."""
    return tool_json(get_panel_project_info(), pretty=pretty)


@mcp.tool()
def ducky_list_projects(pretty: bool = False) -> str:
    """Recent UEFN projects in the panel header dropdown (path, name, active flag)."""
    return tool_json({"projects": list_panel_projects()}, pretty=pretty)


@mcp.tool()
def ducky_set_project(path: str = "", name: str = "", pretty: bool = False) -> str:
    """Switch the panel's active UEFN project — same as picking one in the header dropdown.

    Provide `path` (full folder) or `name` (display name from ducky_list_projects, e.g. MCPTest).
    Use when workspace tools target the wrong project or UEFN has a different map open.
    """
    from frontend.ui_web.project_switch import switch_panel_project

    # Same central switch as the header dropdown — resolves the ref, updates settings +
    # recent, pushes project_changed (so the panel resets stale diagnostics), and runs the
    # deduplicated one-time deploy inline so init_unreal.py exists before this returns.
    info = switch_panel_project(path=path, name=name, push_ui=True)
    return tool_json(info, pretty=pretty)


@mcp.tool()
def ducky_sync_project_to_uefn(pretty: bool = False) -> str:
    """Point the panel's active project at whatever project UEFN currently has open.

    Reads the live editor's project via the listener and switches the panel to the matching
    recent project, so file/workspace tools and the live UEFN map are the same project. Use
    this when `ducky_get_status` reports `project_match: false`. Requires UEFN online; errors
    if the open project isn't in the panel's recent list (add it via the header dropdown first).
    """
    from backend.bridge import post_command_to_listener
    from frontend.ui_web.project_switch import (
        get_panel_project_info,
        resolve_panel_project_ref,
        switch_panel_project,
    )

    info = post_command_to_listener(PANEL_LISTENER_PORT, "get_project_info", {}, timeout=5.0)
    live_name = str(info.get("project_name") or "").strip() if isinstance(info, dict) else ""
    if not live_name:
        raise ValueError("Could not read the live UEFN project — is a map open in UEFN?")

    current = get_panel_project_info()
    if str(current.get("name", "")).casefold() == live_name.casefold():
        return tool_json(
            {"status": "already_synced", "project": current, "uefn_project": live_name},
            pretty=pretty,
        )

    try:
        resolved = resolve_panel_project_ref(name=live_name)
    except ValueError:
        raise ValueError(
            f"UEFN has {live_name!r} open, but it isn't in the panel's recent projects. "
            "Add it via the header project dropdown (Add project…), then retry."
        )
    new_info = switch_panel_project(path=resolved, push_ui=True)
    return tool_json(
        {"status": "switched", "project": new_info, "uefn_project": live_name}, pretty=pretty
    )


@mcp.tool()
def ducky_get_errors(limit: int = 50, pretty: bool = False) -> str:
    """Recent UEFN-Ducky error log entries (bridge, agent, deploy). Host-side; no listener required."""
    limit = max(1, min(int(limit), 200))
    return tool_json({"errors": read_errors(limit=limit)}, pretty=pretty)


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

    return tool_json(report, pretty=pretty)


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


def build_recycle_spawn_message(handoff: str, continue_message: str = "") -> str:
    """Compose the first message for a recycled subagent twin."""
    body = (handoff or "").strip() or "(no handoff text — inspect archived predecessor if needed)"
    follow = (continue_message or "").strip()
    parts = [
        "You are a FRESH version of a prior subagent. Your predecessor was archived after "
        "writing the handoff below. Continue from it — do not redo completed work.\n\n"
        "## Handoff from predecessor\n"
        f"{body}",
    ]
    if follow:
        parts.append(f"\n\n## Continue with this task\n{follow}")
    return _truncate_text("\n".join(parts), _MAX_MESSAGE_CHARS)


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


@mcp.tool()
def ducky_read_chat(conv_id: str, project: str = "", pretty: bool = False) -> str:
    """Read message history from any panel chat by id (including user-created chats).

    Pass `project` (path, name, or slug from ducky_list_projects) to read a chat from
    ANOTHER project — a ducky's chat history is its working memory, so this is how you
    check what a ducky elsewhere knows or decided.
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
    from frontend.ui_web.panel_api import PanelApi

    return PanelApi()


@mcp.tool()
def ducky_create_folder(name: str, parent_id: str = "", pretty: bool = False) -> str:
    """Create a Group Chat folder (chat org is groups only — aliases ducky_group_create)."""
    return ducky_group_create(name=name, parent_folder_id=parent_id or "", pretty=pretty)


@mcp.tool()
def ducky_group_create(name: str, parent_folder_id: str = "", pretty: bool = False) -> str:
    """Create a Group Chat hub (+ linked folder). Any agent can create/own groups.

    Nest groups inside groups by passing ``parent_folder_id`` of an outer group's
    folder. After create, add yourself with ``ducky_group_add_member(..., as_leader=true)``
    if you want to lead the swarm.
    """
    api = _panel_api()
    res = api.group_create(name=name.strip() or "Group", folder_id=parent_folder_id or "")
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_group_invite(group_id: str, ducky: str, pretty: bool = False) -> str:
    """Invite a ducky profile into a group as a lasting swarm member (not a parent-linked subagent)."""
    profile = _resolve_ducky_profile(ducky)
    if profile is None:
        raise ValueError(
            f"No ducky named {ducky!r}. Call ducky_list_duckies to see available duckies."
        )
    pid = str(profile.get("id") or "").strip() or ducky.strip()
    api = _panel_api()
    res = api.group_invite(group_id.strip(), pid)
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_invite failed"))
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
    group_id: str, conv_id: str, as_leader: bool = False, pretty: bool = False
) -> str:
    """Move an existing chat into a group (pass as_leader=true to own/lead that swarm)."""
    api = _panel_api()
    res = api.group_add_member(group_id.strip(), conv_id.strip(), as_leader=bool(as_leader))
    if not res.get("ok"):
        raise ValueError(str(res.get("error") or "group_add_member failed"))
    return tool_json(res, pretty=pretty)


@mcp.tool()
def ducky_rename_chat(conv_id: str, title: str, pretty: bool = False) -> str:
    """Rename any chat by conversation id."""
    root = _project_root()
    rename_conversation(conv_id.strip(), title, project_root=root)
    conv = load_conversation(conv_id.strip(), project_root=root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    return tool_json({"id": conv.id, "title": conv.title, "folder_id": conv.folder_id}, pretty=pretty)


@mcp.tool()
def ducky_move_chat(conv_id: str, folder_id: str, pretty: bool = False) -> str:
    """Move a chat into a folder (use after ducky_create_folder)."""
    root = _project_root()
    move_conversation(conv_id.strip(), folder_id.strip(), project_root=root)
    conv = load_conversation(conv_id.strip(), project_root=root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    return tool_json({"id": conv.id, "title": conv.title, "folder_id": conv.folder_id}, pretty=pretty)


@mcp.tool()
def ducky_send_chat_message(
    conv_id: str,
    message: str,
    mode: str = "agent",
    wait_for_reply: bool = True,
    timeout_sec: float = 180.0,
    pretty: bool = False,
) -> str:
    """Send a user message to any chat and run its agent — for follow-ups on your chats or the user's.

    Blocks until the linked chat finishes (default). Parent agent waits for the reply before continuing.
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

    from frontend.ui_web.agent_modes import run_message, run_message_and_wait

    mode_norm = (mode or "agent").lower()
    if wait_for_reply:
        outcome = run_message_and_wait(
            conv_id,
            text,
            mode_norm,
            timeout_sec=max(5.0, min(float(timeout_sec), 600.0)),
        )
        return tool_json(outcome, pretty=pretty)

    run_message(conv_id, text, mode_norm, "")
    return tool_json({"status": "running", "conv_id": conv_id}, pretty=pretty)


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
    from frontend.ui_web.agent_modes import get_active_conv_id

    active = get_active_conv_id()
    if active:
        return active
    import os

    return (os.environ.get("DUCKY_CONV_ID") or "").strip()


@mcp.tool()
def ducky_agent_list(sender: str = "", pretty: bool = False) -> str:
    """List live agents (panel chats) you can message: id, name, backend, running state.

    Use before `ducky_agent_send` / reuse via `ducky_send_chat_message`. `sender` is your
    own chat id (external coding agents must pass it; embedded duckies may omit it).
    Prefer `ducky_group_members` for swarm seats. `my_group_members` lists chats whose
    `parent_conv_id` is a group hub you lead or belong to (legacy key `my_subagents`
    kept as an alias for older prompts).
    """
    from frontend.ui_web.agent_modes import is_agent_running, linked_parent_of
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
        rows.append(
            {
                "id": conv.id,
                "title": conv.title,
                "ducky": conv.ducky_name or "",
                "coding_agent": conv.coding_agent or "ducky",
                "active": is_agent_running(conv.id),
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
            "my_group_members": my_group_members,
            "my_subagents": my_group_members,  # alias — subagents retired
            "count": len(rows),
            "my_group_member_count": len(my_group_members),
            "my_subagent_count": len(my_group_members),
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_agent_send(
    to: str,
    message: str,
    sender: str = "",
    expect_reply: bool = True,
    response_id: str = "",
    pretty: bool = False,
) -> str:
    """Send a message to another agent (chat) and return IMMEDIATELY — fire-and-forget.

    With `expect_reply=true` you get a `response_id`; finish your turn after sending —
    the reply (or an inactivity notice) arrives later as a `[ducky:agent-message]` turn
    in YOUR chat. To ANSWER a message that asked for a reply, pass its `response_id`
    and `expect_reply=false`. Never busy-wait for replies. `sender` is your own chat id
    (external coding agents must pass it; embedded duckies may omit it).
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

    from backend.agent.a2a_broker import send

    outcome = send(
        sender_conv_id=me,
        receiver_conv_id=target,
        body=text,
        expect_reply=bool(expect_reply),
        response_id=(response_id or "").strip(),
    )
    return tool_json({"status": "sent", "sender": me, **outcome}, pretty=pretty)


@mcp.tool()
def ducky_agent_inbox(conv_id: str = "", pretty: bool = False) -> str:
    """Re-read YOUR recent inter-agent inbox in full (delivered + still queued).

    `conv_id` is your own chat id (external coding agents must pass it).
    """
    me = _resolve_sender(conv_id)
    if not me:
        raise ValueError("conv_id required — pass your own chat id.")
    from backend.agent.a2a_broker import read_inbox

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
    from backend.agent.a2a_broker import on_agent_cancelled_by_user

    target = conv_id.strip()
    if load_conversation(target, project_root=_project_root()) is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    stopped: list[str] = []
    targets = [target] + (linked_children_of(target) if cascade else [])
    for cid in targets:
        if is_agent_running(cid):
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
    Settings → LLMs. Raises ValueError when neither is set or the selection
    is unavailable — never silently substitutes another model.
    """
    from frontend.ui_web.panel_api import resolve_model_selection
    from frontend.favorite_models import ResolveErr

    settings = PanelSettings.load()
    result = resolve_model_selection(profile.get("favorite_models"), settings)
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
    pretty: bool = False,
) -> str:
    """Invite a specialist into a Group swarm and send the first message.

    Subagents are retired. You MUST pass ``group_id`` (hub from ``ducky_group_create``)
    or a group ``folder_id``. Prefer reuse: ``ducky_group_members`` → if the right
    specialist is already seated, ``ducky_send_chat_message`` instead.

    Swarm flow (any agent): ``ducky_group_create`` → ``ducky_group_add_member``
    (yourself as leader) → nested ``ducky_group_create`` → ``ducky_spawn_chat`` /
    ``ducky_group_invite`` into those groups.

    ``folder_name`` creates a new nested group under the caller's folder when
    ``group_id``/``folder_id`` are empty — then invites into it.
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

    hub_id = _resolve_group_hub_id(group_id=group_id, folder_id=folder_id)
    if not hub_id and folder_name.strip():
        # Create a nested group beside the caller's current folder, then invite into it.
        parent_folder = ""
        if caller:
            parent_conv = load_conversation(caller, project_root=_project_root())
            if parent_conv is not None:
                parent_folder = parent_conv.folder_id or ""
        created = _panel_api().group_create(name=folder_name.strip(), folder_id=parent_folder)
        if not created.get("ok"):
            raise ValueError(str(created.get("error") or "group_create failed"))
        hub_id = str(created.get("id") or "").strip()
    if not hub_id:
        raise ValueError(
            "group_id is required (swarm seats only). "
            "Call ducky_group_create first, put yourself in with ducky_group_add_member"
            "(as_leader=true), then ducky_spawn_chat(group_id=…, ducky=…, message=…)."
        )

    invite = ducky_group_invite(group_id=hub_id, ducky=ducky, pretty=False)
    import json as _json

    invite_data = _json.loads(invite) if isinstance(invite, str) else invite
    member = invite_data.get("member") or {}
    conv_id = str(member.get("member_conv_id") or "").strip()
    if not conv_id:
        raise ValueError("group_invite did not return a member_conv_id")

    from frontend.ui_web.agent_modes import run_message, run_message_and_wait

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
        )
        if outcome.get("status") == "timeout" and caller:
            from backend.agent.a2a_broker import on_agent_stopped, open_thread
            from frontend.ui_web.agent_modes import is_agent_running

            rid = open_thread(caller, conv_id, deliver_result=True)
            if not is_agent_running(conv_id):
                on_agent_stopped(conv_id, "done")
            outcome["response_id"] = rid
            outcome["error"] = (
                f"No reply within {float(timeout_sec):.0f}s — the member is STILL WORKING. "
                f"Its result will arrive as a [ducky:agent-message] "
                f"(response_id {rid}). Do NOT re-spawn; finish your turn."
            )
        return tool_json({**base, **outcome}, pretty=pretty)

    response_id = ""
    if caller:
        from backend.agent.a2a_broker import open_thread

        response_id = open_thread(caller, conv_id, deliver_result=True)
    run_message(conv_id, text, mode_norm, "", parent=caller or hub_id)
    return tool_json({**base, "status": "running", "response_id": response_id}, pretty=pretty)


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
    """Retire a bloated group member: handoff → hard-delete → invite fresh twin into same group.

    Target must be a group member (parent = group hub). For ordinary follow-ups use
    ``ducky_send_chat_message``. Alias: ``ducky_recycle_subagent``.
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

    from frontend.ui_web.agent_modes import notify_chats_changed, run_message, run_message_and_wait
    from frontend.ui_web.group_orchestrator import (
        group_members,
        member_color_for_index,
        normalize_member,
        sync_group_members_from_folder,
    )

    was_leader = (getattr(parent_conv, "leader_conv_id", None) or "").strip() == old_id
    mode_norm = (mode or "agent").lower()
    handoff_timeout = max(30.0, min(float(timeout_sec), 600.0))
    handoff_outcome = run_message_and_wait(
        old_id,
        _SUBAGENT_HANDOFF_PROMPT,
        mode_norm,
        timeout_sec=handoff_timeout,
        cancel_on_timeout=False,
        parent=parent_id,
    )
    handoff_text = str(handoff_outcome.get("assistant_text") or "").strip()
    if not handoff_text and handoff_outcome.get("status") == "timeout":
        handoff_text = (
            f"(handoff timed out after {handoff_timeout:.0f}s — predecessor may still be finishing)"
        )
    if not handoff_text:
        handoff_text = str(handoff_outcome.get("error") or "empty handoff")

    prior_folder = (old.folder_id or "").strip()
    persona = _spawn_kwargs_from_conv(old)
    if not str(persona.get("model") or "").strip():
        raise ValueError(
            "Recycle needs a model on the old chat — set one on the member or profile."
        )
    title = (old.title or str(persona.get("ducky_name") or "") or "Member").strip()[:120]
    old_title = old.title

    delete_conversation(old_id, project_root=root)

    new_conv = create_conversation(
        folder_id=prior_folder or (parent_conv.folder_id or ""),
        title=title,
        project_root=root,
        parent_conv_id=parent_id,
        **persona,
    )

    # Refresh hub roster: drop old id, add twin; restore leader if needed.
    group = load_conversation(parent_id, project_root=root) or parent_conv
    sync_group_members_from_folder(group, project_root=root)
    group = load_conversation(parent_id, project_root=root) or group
    members = [m for m in group_members(group) if m.get("member_conv_id") != old_id]
    if not any(m.get("member_conv_id") == new_conv.id for m in members):
        members.append(
            normalize_member(
                {
                    "member_conv_id": new_conv.id,
                    "profile_id": str(getattr(new_conv, "profile_id", None) or ""),
                    "name": title,
                    "ducky_name": str(getattr(new_conv, "ducky_name", None) or ""),
                    "ducky_style": str(getattr(new_conv, "ducky_style", None) or ""),
                    "model": str(getattr(new_conv, "model", None) or ""),
                    "coding_agent": str(getattr(new_conv, "coding_agent", None) or ""),
                    "tts_voice": str(getattr(new_conv, "tts_voice", None) or ""),
                    "tts_speed": float(getattr(new_conv, "tts_speed", None) or 0.0),
                    "color": member_color_for_index(len(members)),
                },
                index=len(members),
            )
        )
    group.group_members = members
    if was_leader or not (getattr(group, "leader_conv_id", None) or "").strip():
        group.leader_conv_id = new_conv.id
    elif (getattr(group, "leader_conv_id", None) or "").strip() == old_id:
        group.leader_conv_id = new_conv.id
    save_conversation(group, root)

    notify_chats_changed(new_conv.id, new_conv.title, new_conv.folder_id)
    spawn_text = build_recycle_spawn_message(handoff_text, continue_message)
    base = {
        "status": "recycled",
        "old_conv_id": old_id,
        "old_title": old_title,
        "conv_id": new_conv.id,
        "title": new_conv.title,
        "folder_id": new_conv.folder_id,
        "ducky": new_conv.ducky_name or "",
        "parent_conv_id": parent_id,
        "group_id": parent_id,
        "handoff_status": handoff_outcome.get("status"),
        "handoff_chars": len(handoff_text),
        "coding_agent": getattr(new_conv, "coding_agent", None) or "ducky",
        "model": new_conv.model or "",
    }
    if wait_for_reply:
        is_external = str(base.get("coding_agent") or "ducky") != "ducky"
        outcome = run_message_and_wait(
            new_conv.id,
            spawn_text,
            mode_norm,
            # External CLIs: wait until done (0 = no wall-clock limit).
            timeout_sec=0.0 if is_external else max(5.0, min(float(timeout_sec), 900.0)),
            cancel_on_timeout=False,
            parent=parent_id,
        )
        return tool_json({**base, **outcome}, pretty=pretty)

    response_id = ""
    caller = _resolve_sender(sender)
    if caller:
        from backend.agent.a2a_broker import open_thread

        response_id = open_thread(caller, new_conv.id, deliver_result=True)
    run_message(new_conv.id, spawn_text, mode_norm, "", parent=parent_id)
    return tool_json({**base, "status": "running", "response_id": response_id}, pretty=pretty)


@mcp.tool()
def ducky_recycle_subagent(
    conv_id: str,
    continue_message: str = "",
    mode: str = "agent",
    wait_for_reply: bool = True,
    timeout_sec: float = 180.0,
    sender: str = "",
    pretty: bool = False,
) -> str:
    """Alias for ``ducky_recycle_member`` (subagents retired — group members only)."""
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

    Pass `project` (path, name, or slug from ducky_list_projects) to inspect a ducky
    from ANOTHER project — that returns a summary of its stored context (the live
    token breakdown only exists for the active project).
    """
    conv_id = conv_id.strip()
    root = _resolve_project_root_arg(project)
    conv = load_conversation(conv_id, project_root=root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")

    if _is_cross_project(root):
        from frontend.ui_web.project_chats import project_display_name

        total_chars = sum(len(str(m.get("content") or "")) for m in conv.messages)
        return tool_json(
            {
                "conv_id": conv.id,
                "title": conv.title,
                "project": project_display_name(root),
                "scope": "cross_project_summary",
                "message_count": len(conv.messages),
                "approx_tokens": total_chars // 4,
                "last_messages": _serialize_messages(conv.messages)[-12:],
                "note": (
                    "Live token breakdown is only computed for the active project; "
                    "this summarizes the ducky's stored context. Use ducky_read_chat "
                    "with the same project for the full history."
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

    segments: all | conversation | system | mcp_tools | rules | skill
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


def _terminal_manager():
    from frontend.ui_web.terminal import get_terminal_manager

    mgr = get_terminal_manager()
    try:
        from frontend.ui_web.agent_modes import get_panel_push

        push = get_panel_push()
        if push:
            mgr.set_push(push)
    except Exception:
        pass
    return mgr


@mcp.tool()
def ducky_terminal_open(
    shell: str = "bash",
    cwd: str = "",
    title: str = "",
    conv_id: str = "",
    pretty: bool = False,
) -> str:
    """Open a visible integrated terminal tab (bash or powershell) in the Ducky panel."""
    mgr = _terminal_manager()
    result = mgr.spawn(shell=shell, cwd=cwd or None, title=title, push_open=True, conv_id=conv_id.strip())
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_terminal_run(
    session_id: str,
    command: str,
    conv_id: str = "",
    wait: bool = True,
    background: bool = False,
    approval_timeout_s: float = 120.0,
    command_timeout_s: float = 300.0,
    pretty: bool = False,
) -> str:
    """Run a shell command in a panel terminal after user approves it in the Allow/Deny popup."""
    mgr = _terminal_manager()
    result = mgr.run_agent_command(
        session_id.strip(),
        command,
        source="ducky_terminal_run",
        conv_id=conv_id.strip(),
        background=background,
        wait=wait,
        approval_timeout_s=max(5.0, min(float(approval_timeout_s), 600.0)),
        command_timeout_s=max(5.0, min(float(command_timeout_s), 3600.0)),
    )
    return tool_json(result, pretty=pretty)


@mcp.tool()
def ducky_terminal_read_output(session_id: str, max_chars: int = 8000, pretty: bool = False) -> str:
    """Read recent output from a terminal session (for wait=false / parallel workflows)."""
    mgr = _terminal_manager()
    max_chars = max(500, min(int(max_chars), 32000))
    return tool_json(mgr.read_output(session_id.strip(), max_chars=max_chars), pretty=pretty)


@mcp.tool()
def ducky_terminal_list(pretty: bool = False) -> str:
    """List active integrated terminal sessions in the Ducky panel."""
    mgr = _terminal_manager()
    return tool_json({"sessions": mgr.list_sessions()}, pretty=pretty)


@mcp.tool()
def ducky_terminal_close(session_id: str, pretty: bool = False) -> str:
    """Close a terminal session and its tab in the Ducky panel."""
    mgr = _terminal_manager()
    return tool_json(mgr.kill(session_id.strip(), push_close=True), pretty=pretty)


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
    """Explicit chat_id, else active embedded conv, else DUCKY_CONV_ID env.

    Coding-agent CLIs (Cursor, Claude Code, …) inherit DUCKY_CONV_ID from
    mcp_inject so plan tools work without passing chat_id explicitly — same
    fallback as ``_resolve_sender``.
    """
    cid = (chat_id or "").strip()
    if cid:
        return cid
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
    title: str,
    overview: str = "",
    body_markdown: str = "",
    nodes: list[dict[str, Any]] | None = None,
    todos: list[dict[str, Any]] | None = None,
    chat_id: str = "",
    pretty: bool = False,
) -> str:
    """Create a project Plan with an outline tree (main → subplans). In Plan mode, create then stop.

    Field roles (do NOT mix):
    - ``overview``: short 1–3 sentence summary ONLY (plain text). Never paste JSON or XML here.
    - ``body_markdown``: longer plan description (markdown). Optional but preferred for context.
    - ``nodes``: REQUIRED for multi-step work — a real JSON **array** argument
      ``[{id?, content, status?, children?}, …]``, NOT text inside overview.
      Followable shape: Diagnose → Fix → Verify; each leaf = one concrete action + Done-when.

    Never leave only a chat prose Fix plan — call this tool with ``nodes`` filled.
    Legacy ``todos`` (flat {id?, content, status?}) become root nodes.
    chat_id defaults to the active conversation. One plan per chat; hierarchy is inside nodes.
    Parents cannot be completed until nested subplans are done. Rearrange with ducky_plan_move_node.
    """
    from backend.agent.coding_agents.plans import create_plan, outline_numbers, push_plan_updated, todo_progress

    cid = _resolve_plan_chat_id(chat_id)
    if not cid:
        return tool_json({"ok": False, "error": "chat_id required (no active conversation)"}, pretty=pretty)
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
    from backend.agent.coding_agents.plans import attach_next_tick

    return tool_json(attach_next_tick(out, plan), pretty=pretty)


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
    return tool_json(attach_next_tick(out, plan), pretty=pretty)


@mcp.tool()
def ducky_get_plan(chat_id: str = "", pretty: bool = False) -> str:
    """Load the project Plan for this conversation (or chat_id). Returns outline numbering 1, 1.1, 1.1.1, …"""
    from backend.agent.coding_agents.plans import attach_next_tick, load_plan, outline_numbers, todo_progress

    cid = _resolve_plan_chat_id(chat_id)
    if not cid:
        return tool_json({"ok": False, "error": "chat_id required (no active conversation)"}, pretty=pretty)
    root = _project_root()
    plan = load_plan(cid, project_root=root)
    if not plan:
        return tool_json(
            {"ok": True, "plan": None, "progress": todo_progress(None), "outline": []},
            pretty=pretty,
        )
    return tool_json(
        attach_next_tick(
            {
                "ok": True,
                "plan": plan,
                "progress": todo_progress(plan),
                "outline": [{"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]} for lab, n in outline_numbers(plan.get("nodes"))],
            },
            plan,
        ),
        pretty=pretty,
    )


@mcp.tool()
def ducky_plan_add_node(
    content: str,
    parent_id: str = "",
    index: int = -1,
    chat_id: str = "",
    template_id: str = "",
    kind: str = "",
    body_markdown: str = "",
    pretty: bool = False,
) -> str:
    """Add a step or subplan node. kind=step|subplan. parent_id empty = root. template_id edits a template."""
    from backend.agent.coding_agents.plans import add_node, outline_numbers, push_plan_updated, todo_progress

    cid = _resolve_plan_chat_id(chat_id) if not (template_id or "").strip() else ""
    try:
        plan = add_node(
            cid,
            content=content,
            parent_id=parent_id,
            index=None if index < 0 else index,
            kind=kind,
            body_markdown=body_markdown,
            project_root=_project_root() if not (template_id or "").strip() else None,
            template_id=(template_id or "").strip() or None,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if plan.get("kind") != "template":
        push_plan_updated(plan)
    return tool_json(
        {
            "ok": True,
            "plan": plan,
            "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
            "outline": [{"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]} for lab, n in outline_numbers(plan.get("nodes"))],
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_plan_update_node(
    node_id: str,
    content: str = "",
    status: str = "",
    chat_id: str = "",
    template_id: str = "",
    kind: str = "",
    body_markdown: str = "",
    pretty: bool = False,
) -> str:
    """Update a node's content, status, kind, and/or body_markdown. Status-only updates allowed after start."""
    from backend.agent.coding_agents.plans import (
        attach_next_tick,
        outline_numbers,
        push_plan_updated,
        todo_progress,
        update_node,
    )

    cid = _resolve_plan_chat_id(chat_id) if not (template_id or "").strip() else ""
    try:
        plan = update_node(
            cid,
            node_id,
            content=content if content else None,
            status=status if status else None,
            kind=kind if kind else None,
            body_markdown=body_markdown if body_markdown else None,
            project_root=_project_root() if not (template_id or "").strip() else None,
            template_id=(template_id or "").strip() or None,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if plan.get("kind") != "template":
        push_plan_updated(plan)
    return tool_json(
        attach_next_tick(
            {
                "ok": True,
                "plan": plan,
                "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
                "outline": [{"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]} for lab, n in outline_numbers(plan.get("nodes"))],
            },
            plan if plan.get("kind") != "template" else None,
        ),
        pretty=pretty,
    )


@mcp.tool()
def ducky_plan_delete_node(
    node_id: str,
    chat_id: str = "",
    template_id: str = "",
    pretty: bool = False,
) -> str:
    """Delete a subplan node and its nested children."""
    from backend.agent.coding_agents.plans import delete_node, outline_numbers, push_plan_updated, todo_progress

    cid = _resolve_plan_chat_id(chat_id) if not (template_id or "").strip() else ""
    try:
        plan = delete_node(
            cid,
            node_id,
            project_root=_project_root() if not (template_id or "").strip() else None,
            template_id=(template_id or "").strip() or None,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if plan.get("kind") != "template":
        push_plan_updated(plan)
    return tool_json(
        {
            "ok": True,
            "plan": plan,
            "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
            "outline": [{"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]} for lab, n in outline_numbers(plan.get("nodes"))],
        },
        pretty=pretty,
    )


@mcp.tool()
def ducky_plan_move_node(
    node_id: str,
    parent_id: str = "",
    index: int = 0,
    chat_id: str = "",
    template_id: str = "",
    pretty: bool = False,
) -> str:
    """Move a subplan under parent_id (empty = root) at index. Renumbers the outline."""
    from backend.agent.coding_agents.plans import move_node, outline_numbers, push_plan_updated, todo_progress

    cid = _resolve_plan_chat_id(chat_id) if not (template_id or "").strip() else ""
    try:
        plan = move_node(
            cid,
            node_id,
            parent_id=parent_id,
            index=index,
            project_root=_project_root() if not (template_id or "").strip() else None,
            template_id=(template_id or "").strip() or None,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if plan.get("kind") != "template":
        push_plan_updated(plan)
    return tool_json(
        {
            "ok": True,
            "plan": plan,
            "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
            "outline": [{"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]} for lab, n in outline_numbers(plan.get("nodes"))],
        },
        pretty=pretty,
    )


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
