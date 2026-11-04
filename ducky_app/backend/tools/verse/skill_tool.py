"""MCP tools: skill packs, on-demand subskill load, and skill / tool search."""

import asyncio

from backend.server import mcp
from backend.skills.seeding import seed_skill_packs
from backend.skills.selection import default_skill_selection
from backend.skills.skill_index import build_skill_prompt
from backend.skills.skill_index import skill_read_subskill as _read_subskill
from backend.util.json_util import tool_json


@mcp.tool()
def uefn_skill() -> str:
    """REQUIRED FIRST for wiring Verse @editable fields.

    Returns the combined enabled skill packs (UEFN operator + any toggled subskills).
    Workflow: ping → reload_listener if scalar_prop_wiring false → inspect_verse_device
    → wire_verse_device_ref (one field per call) → save_current_level.
    If STOP is true, run workspace_compile_verse yourself, then reload_listener once.
    Never restart UEFN. If still stuck stay on workspace_*."""
    try:
        seed_skill_packs()
        return build_skill_prompt(default_skill_selection())
    except Exception as e:
        return (
            f"ERROR: {e}\n"
            "Fix: start UEFN-Ducky panel once (syncs skill packs to AppData) or run from repo checkout."
        )


@mcp.tool()
def skill_read_subskill(
    pack_id: str = "",
    subskill_id: str = "",
    skill: str = "",
    subskill: str = "",
    section: str = "",
    pack: str = "",
) -> str:
    """List or load a skill-pack subskill. Omit subskill_id to list all subskills in a pack.

    section= loads only that ## heading of the file (skill_search names it); an unknown
    section lists the file's headings. Use when a subskill is disabled in the prompt index
    but needed for the current task. skill / pack and subskill are the same as pack_id /
    subskill_id."""
    # Prompts show it positionally ("ducky", "ai_plugins"); agents guessed skill=/subskill=
    # (Oct 11) and pack=/subskill= (Oct 16, a gpt-6-luna run) and the call failed validation.
    pack = (pack_id or skill or pack or "").strip()
    if not pack:
        return 'ERROR: pack_id is required, e.g. skill_read_subskill(pack_id="ducky", subskill_id="ai_plugins")'
    try:
        seed_skill_packs()
        return _read_subskill(pack, subskill_id or subskill, section=section)
    except Exception as e:
        return f"ERROR: {e}"


def _lean(row: dict) -> dict:
    """What an agent needs from a hit: where it is, a snippet and the call that opens it.

    Scores, match details and the ids already inside ``read`` cost about a third of every
    hit's tokens (the app's own search boxes keep them).
    """
    out = {k: row[k] for k in ("tool", "where", "snippet", "read") if row.get(k)}
    return out


@mcp.tool()
async def skill_search(query: str, limit: int = 5, kind: str = "", pretty: bool = False) -> str:
    """Find the skill section or tool for a task, by meaning and keywords, in plain words.

    Searches every skill pack you can use (UEFN Ducky's own, yours, Store and plugin
    packs) down to single ## sections, and with kind="tools" every tool you can call.
    kind: "" or "skills" (default), "tools", or "all". Each hit has a `read` field: the
    exact skill_read_subskill(...) or ducky_get_tools(name=...) call to open it.
    Example: skill_search("custom gravity jump player controller")."""
    from backend.search import query as search_query

    text = (query or "").strip()
    if not text:
        return tool_json({"ok": False, "error": 'query is required, e.g. skill_search("night sky material")'})
    want = (kind or "skills").strip().lower()
    lim = max(1, min(int(limit or 5), 25))
    out: dict = {"query": text}
    try:
        if want in ("skills", "skill", "all"):
            # The run's skill selection lives in context vars: carry them into the worker thread.
            ctx = search_query.chat_context()
            out["skills"] = [_lean(r) for r in await asyncio.to_thread(search_query.search, text, limit=lim, context=ctx)]
        if want in ("tools", "tool", "all"):
            from backend.agent.tools import list_mcp_tools
            from backend.agent.toolsets.excluded import EXCLUDED_TOOLS

            names = {t.name for t in await list_mcp_tools() if t.name not in EXCLUDED_TOOLS}
            out["tools"] = [_lean(r) for r in await asyncio.to_thread(search_query.search_tools, text, names, limit=lim)]
    except Exception as exc:  # noqa: BLE001 — the index is a convenience, never a blocker
        out["error"] = f"search unavailable ({type(exc).__name__})"
    found = any(out.get(k) for k in ("skills", "tools"))
    out["hint"] = (
        "Open a hit with its `read` call; read one section before loading whole files."
        if found
        else "No match. Try other words, or list a pack's files with skill_read_subskill(\"<pack_id>\")."
    )
    return tool_json(out, pretty=pretty)
