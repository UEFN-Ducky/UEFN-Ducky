"""MCP tools: skill packs and on-demand subskill load."""

from backend.tools.plugin_gate import plugin_mcp_tool
from backend.skill import (
    build_skill_prompt,
    default_skill_selection,
    seed_skill_packs,
    skill_read_subskill as _read_subskill,
)


@plugin_mcp_tool("verse")
def uefn_skill() -> str:
    """REQUIRED FIRST for wiring Verse @editable fields.

    Returns the combined enabled skill packs (UEFN operator + any toggled subskills).
    Workflow: ping → reload_listener if scalar_prop_wiring false → inspect_verse_device
    → wire_verse_device_ref (one field per call) → save_current_level.
    If STOP is true, run workspace_compile_verse yourself, then reload_listener —
    only ask the user to restart UEFN if STOP persists."""
    try:
        seed_skill_packs()
        return build_skill_prompt(default_skill_selection())
    except Exception as e:
        return (
            f"ERROR: {e}\n"
            "Fix: start UEFN-Ducky panel once (syncs skill packs to AppData) or run from repo checkout."
        )


@plugin_mcp_tool("verse")
def skill_read_subskill(pack_id: str, subskill_id: str = "") -> str:
    """List or load a skill-pack subskill. Omit subskill_id to list all subskills in a pack.

    Use when a subskill is disabled in the prompt index but needed for the current task."""
    try:
        seed_skill_packs()
        return _read_subskill(pack_id, subskill_id)
    except Exception as e:
        return f"ERROR: {e}"
