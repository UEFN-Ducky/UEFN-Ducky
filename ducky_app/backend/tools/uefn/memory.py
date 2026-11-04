"""Project memory tools — named entries per project, shared by that project's duckies.

Every tool takes an optional ``project`` (path, name or slug): empty is the person's
selected project. Duckies work in any project, so they keep each project's memory too.
"""

from __future__ import annotations

from typing import Any

from backend.util.json_util import tool_json
from backend.memory.project import (
    append_entry,
    delete_entry,
    list_entries,
    read_entry,
    save_entry,
)
from backend.tools.support.plugin_gate import plugin_mcp_tool


def _read_root(project: str) -> str:
    """Resolve the project root: '' = the selected project, else path/name/slug."""
    if not (project or "").strip():
        return ""
    from backend.tools.panel.ducky_panel import _resolve_project_root_arg

    return _resolve_project_root_arg(project)


@plugin_mcp_tool("uefn")
def project_memory_list(project: str = "", pretty: bool = False) -> str:
    """Index of a project's memory — name + one-line description per entry (never bodies).

    Entries nest like skills: a topic that grew has sub-entries listed under it
    (`entry/sub`). Your prompt already carries the ACTIVE project's index; pull a
    specific entry with `project_memory_get(name)` ONLY when it is relevant. Pass
    `project` (path, name, or slug from ducky_list_projects) to see ANOTHER project's
    memory index.
    """
    root = _read_root(project)
    entries = list_entries(root)
    # No folder path: entries live in Ducky's database, and an agent handed the app-data
    # folder tried to read the entry's file there and was refused (Oct 22 2026, an Opus lead).
    return tool_json(
        {
            "count": len(entries),
            "entries": entries
            or "(empty — record facts this project's duckies should know with project_memory_save)",
        },
        pretty=pretty,
    )


@plugin_mcp_tool("uefn")
def project_memory_get(name: str, project: str = "", pretty: bool = False) -> str:
    """Pull ONE memory entry's full content by name (from the index). Never bulk-read entries.

    `name` is an entry (`coding-standards` — returns its body plus a sub-entry index)
    or a sub-entry (`coding-standards/error-handling`). Use after the index shows an
    entry relevant to the task — do not bulk-read entries you don't need. Pass
    `project` to read from ANOTHER project's memory.
    """
    root = _read_root(project)
    entry = read_entry(name, root)
    if entry is None:
        # An answer, not a failure: a missing entry raised, and models retried it in a loop.
        known = [e["name"] for e in list_entries(root)[:30]]
        return tool_json({
            "found": False, "name": name, "known": known,
            "hint": "No entry by that name. Use one of known, or go on without it; asking again won't find it.",
        }, pretty=pretty)
    return tool_json(entry, pretty=pretty)


@plugin_mcp_tool("uefn")
def project_memory_save(
    name: str,
    content: str,
    description: str = "",
    author: str = "",
    project: str = "",
    pretty: bool = False,
) -> str:
    """Create or replace ONE named entry (one fact/topic) in a project's memory. Never erase another's.

    Lands in the selected project; pass `project` (path, name, or slug from
    ducky_list_projects) when the fact belongs to the project you worked in.
    Use for facts this project's duckies need across sessions: device label conventions,
    wiring quirks, field names that worked, coding standards, decisions made — document
    hard-won solutions while solving them, like building a skill. `name` becomes the file
    slug (e.g. `device-naming`); when a topic grows, split it like a skill by saving
    sub-entries: `name="coding-standards/error-handling"` (one nesting level; the parent
    becomes the topic's index). `description` is the one-liner in every ducky's prompt
    index — make it say when to pull the entry. Pass `author` (your ducky name). Only
    replace an existing entry to update the same topic — never to erase another ducky's.
    """
    result = save_entry(name, content, description=description, author=author, project_root=_read_root(project))
    return tool_json({**result, "ok": True}, pretty=pretty)


@plugin_mcp_tool("uefn")
def project_memory_append(
    name: str, text: str, author: str = "", project: str = "", pretty: bool = False
) -> str:
    """Append a timestamped note to a memory entry (created if missing). Does not rewrite the entry.

    Use to extend an existing topic without rewriting it. Keep notes short and factual.
    `project` as in project_memory_save (empty = the selected project).
    """
    result = append_entry(name, text, author=author, project_root=_read_root(project))
    return tool_json({**result, "ok": True}, pretty=pretty)


@plugin_mcp_tool("uefn")
def project_memory_delete(name: str, project: str = "", pretty: bool = False) -> str:
    """Delete ONE wrong or obsolete memory entry. Never another ducky's just because it is unfamiliar.

    When unsure, ask the user or update the entry instead. `project` as in project_memory_save.
    """
    removed = delete_entry(name, _read_root(project))
    if not removed:
        raise ValueError(f"No memory entry named {name!r} in this project")
    return tool_json({"deleted": name, "ok": True}, pretty=pretty)


_OVERVIEW_MAX_CHATS = 20


@plugin_mcp_tool("uefn")
def ducky_memory_overview(include_chats: bool = True, pretty: bool = False) -> str:
    """Survey every project's memory index and the duckies' chats. Read-only: never pulls entry bodies.

    One list for every project; each chat = a ducky's context.

    Use this to find what is known about other projects before cross-project work. Pull
    an entry with `project_memory_get(name, project=...)`; read a chat with
    `ducky_read_chat(conv_id)` or summarize its context with `ducky_get_chat_context(conv_id)`.
    """
    from frontend.ui_web.project_switch import list_panel_projects

    projects_out: list[dict[str, Any]] = []
    for proj in list_panel_projects():
        path = str(proj.get("path") or "")
        entry: dict[str, Any] = {
            "name": proj.get("name"),
            "path": path,
            "active": bool(proj.get("active")),
        }
        try:
            entries = list_entries(path)
            entry["memory"] = {
                "count": len(entries),
                "index": [
                    {"name": e["name"], "description": e["description"], "updated": e["updated"]}
                    for e in entries[:30]
                ],
            }
        except Exception as exc:  # a broken project dir must not sink the survey
            entry["memory_error"] = str(exc)
        projects_out.append(entry)
    out: dict[str, Any] = {"projects": projects_out}
    if include_chats:
        try:
            from frontend.ui_web.chats.conversations import list_conversations

            convs = list_conversations(None)
            convs.sort(key=lambda c: c.updated or 0.0, reverse=True)
            out["chats"] = [{"id": c.id, "title": c.title, "updated": c.updated} for c in convs[:_OVERVIEW_MAX_CHATS]]
        except Exception as exc:
            out["chats_error"] = str(exc)
    return tool_json(out, pretty=pretty)
