"""Project memory tools — named entries per project, shared by that project's duckies.

Writes ALWAYS target the active project (a ducky only writes its own project's
memory). Reads take an optional ``project`` argument so any ducky can pull another
project's memory on demand.
"""

from __future__ import annotations

from typing import Any

from backend.util.json_util import tool_json
from backend.memory.project import (
    append_entry,
    delete_entry,
    list_entries,
    memory_dir,
    read_entry,
    save_entry,
)
from backend.tools.support.plugin_gate import plugin_mcp_tool


def _read_root(project: str) -> str:
    """Resolve the read-scope root: '' = active project, else path/name/slug."""
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
    return tool_json(
        {
            "dir": str(memory_dir(root)),
            "count": len(entries),
            "entries": entries
            or "(empty — record facts this project's duckies should know with project_memory_save)",
        },
        pretty=pretty,
    )


@plugin_mcp_tool("uefn")
def project_memory_get(name: str, project: str = "", pretty: bool = False) -> str:
    """Pull ONE memory entry's full content by name (from the index).

    `name` is an entry (`coding-standards` — returns its body plus a sub-entry index)
    or a sub-entry (`coding-standards/error-handling`). Use after the index shows an
    entry relevant to the task — do not bulk-read entries you don't need. Pass
    `project` to read from ANOTHER project's memory.
    """
    root = _read_root(project)
    entry = read_entry(name, root)
    if entry is None:
        known = ", ".join(e["name"] for e in list_entries(root)[:30]) or "(none)"
        raise ValueError(f"No memory entry named {name!r} in this project. Known entries: {known}")
    return tool_json(entry, pretty=pretty)


@plugin_mcp_tool("uefn")
def project_memory_save(
    name: str,
    content: str,
    description: str = "",
    author: str = "",
    pretty: bool = False,
) -> str:
    """Create or replace ONE named entry in YOUR project's memory (one fact/topic per entry).

    Writes always land in the ACTIVE project — you never write another project's memory.
    Use for facts this project's duckies need across sessions: device label conventions,
    wiring quirks, field names that worked, coding standards, decisions made — document
    hard-won solutions while solving them, like building a skill. `name` becomes the file
    slug (e.g. `device-naming`); when a topic grows, split it like a skill by saving
    sub-entries: `name="coding-standards/error-handling"` (one nesting level; the parent
    becomes the topic's index). `description` is the one-liner in every ducky's prompt
    index — make it say when to pull the entry. Pass `author` (your ducky name). Only
    replace an existing entry to update the same topic — never to erase another ducky's.
    """
    result = save_entry(name, content, description=description, author=author)
    return tool_json({**result, "ok": True}, pretty=pretty)


@plugin_mcp_tool("uefn")
def project_memory_append(name: str, text: str, author: str = "", pretty: bool = False) -> str:
    """Append a timestamped note to an entry in YOUR project's memory (created if missing).

    Use to extend an existing topic without rewriting it. Keep notes short and factual.
    """
    result = append_entry(name, text, author=author)
    return tool_json({**result, "ok": True}, pretty=pretty)


@plugin_mcp_tool("uefn")
def project_memory_delete(name: str, pretty: bool = False) -> str:
    """Delete ONE entry from YOUR project's memory — only for facts that are wrong or obsolete.

    Never delete another ducky's entry just because it is unfamiliar; when unsure, ask the
    user or update the entry instead.
    """
    removed = delete_entry(name)
    if not removed:
        raise ValueError(f"No memory entry named {name!r} in this project")
    return tool_json({"deleted": name, "ok": True}, pretty=pretty)


_OVERVIEW_MAX_CHATS = 20


@plugin_mcp_tool("uefn")
def ducky_memory_overview(include_chats: bool = True, pretty: bool = False) -> str:
    """Survey every known project's memory index and chats (each chat = a ducky's context).

    Use this to find what duckies in other projects know before starting cross-project
    work. Pull an entry with `project_memory_get(name, project=...)`; read a chat with
    `ducky_read_chat(conv_id, project=...)` or summarize its context with
    `ducky_get_chat_context(conv_id, project=...)`.
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
        if include_chats:
            try:
                from frontend.ui_web.project_chats import list_conversations

                convs = list_conversations(None, project_root=path)
                convs.sort(key=lambda c: c.updated or 0.0, reverse=True)
                entry["chats"] = [
                    {"id": c.id, "title": c.title, "updated": c.updated}
                    for c in convs[:_OVERVIEW_MAX_CHATS]
                ]
            except Exception as exc:
                entry["chats_error"] = str(exc)
        projects_out.append(entry)
    return tool_json({"projects": projects_out}, pretty=pretty)
