"""MCP tools so a ducky can author and test Automations graphs."""

from __future__ import annotations

from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json


@mcp.tool()
def list_automation_nodes(pretty: bool = False) -> str:
    """Catalog of builtin + enabled-plugin automation nodes and triggers."""
    from backend.automations.catalog import list_nodes

    return tool_json({"ok": True, "nodes": list_nodes(system="automation")}, pretty=pretty)


@mcp.tool()
def list_automation_templates(pretty: bool = False) -> str:
    """Plugin + user Automations templates (ready-made graphs)."""
    from backend.automations.templates import list_templates

    return tool_json({"ok": True, "templates": list_templates(system="automation")}, pretty=pretty)


@mcp.tool()
def list_automations(pretty: bool = False) -> str:
    """List saved Automations workflows (id, name, enabled, updated)."""
    from backend.automations.store import list_automations as _list

    return tool_json({"ok": True, "automations": _list()}, pretty=pretty)


@mcp.tool()
def get_automation(workflow_id: str, pretty: bool = False) -> str:
    """Full automation graph + last run log."""
    from backend.automations.store import get_automation as _get

    wf = _get(workflow_id)
    if wf is None:
        return tool_json({"ok": False, "error": "automation not found"}, pretty=pretty)
    return tool_json({"ok": True, "automation": wf}, pretty=pretty)


@mcp.tool()
def save_automation(
    graph: dict[str, Any] | None = None,
    name: str = "",
    enabled: bool = True,
    workflow_id: str = "",
    pretty: bool = False,
) -> str:
    """Create or replace an automation. graph = {nodes:[{id,type,x,y,config,label,description}], edges:[{source,target,kind}]}."""
    from backend.automations.store import save_automation as _save

    doc: dict[str, Any] = {
        "name": name or "Untitled",
        "enabled": bool(enabled),
        "graph": graph or {"nodes": [], "edges": []},
    }
    if (workflow_id or "").strip():
        doc["id"] = workflow_id.strip()
    return tool_json({"ok": True, "automation": _save(doc)}, pretty=pretty)


@mcp.tool()
def run_automation(
    workflow_id: str,
    trigger_id: str = "",
    payload: dict[str, Any] | None = None,
    pretty: bool = False,
) -> str:
    """Manual test run. Returns the step log. Does not switch the active island."""
    from backend.automations.runner import run_automation as _run

    return tool_json(
        _run(workflow_id, trigger_id=trigger_id, payload=payload or {}),
        pretty=pretty,
    )


@mcp.tool()
def emit_automation(
    trigger_id: str,
    payload: dict[str, Any] | None = None,
    pretty: bool = False,
) -> str:
    """Simulate a plugin trigger: run every enabled workflow that listens for trigger_id."""
    from backend.automations.runner import emit_automation as _emit

    return tool_json(_emit(trigger_id, payload or {}), pretty=pretty)


@mcp.tool()
def list_pipeline_nodes(pretty: bool = False) -> str:
    """Catalog of builtin + enabled-plugin pipeline nodes."""
    from backend.automations.catalog import list_nodes

    return tool_json({"ok": True, "nodes": list_nodes(system="pipeline")}, pretty=pretty)


@mcp.tool()
def list_pipeline_templates(pretty: bool = False) -> str:
    """Plugin + user Pipeline templates (ready-made graphs)."""
    from backend.automations.templates import list_templates

    return tool_json({"ok": True, "templates": list_templates(system="pipeline")}, pretty=pretty)


@mcp.tool()
def list_pipelines(pretty: bool = False) -> str:
    """List saved Pipelines (id, name, description, enabled)."""
    from backend.automations.store import KIND_PIPELINE, list_automations as _list

    return tool_json({"ok": True, "pipelines": _list(kind=KIND_PIPELINE)}, pretty=pretty)


@mcp.tool()
def get_pipeline(pipeline_id: str, pretty: bool = False) -> str:
    """Full pipeline graph + last run log."""
    from backend.automations.store import KIND_PIPELINE, get_automation, normalize_kind

    wf = get_automation(pipeline_id)
    if wf is None or normalize_kind(wf.get("kind")) != KIND_PIPELINE:
        return tool_json({"ok": False, "error": "pipeline not found"}, pretty=pretty)
    return tool_json({"ok": True, "pipeline": wf}, pretty=pretty)


@mcp.tool()
def save_pipeline(
    graph: dict[str, Any] | None = None,
    name: str = "",
    description: str = "",
    enabled: bool = True,
    pipeline_id: str = "",
    pretty: bool = False,
) -> str:
    """Create or replace a pipeline. Same graph shape as automations."""
    from backend.automations.store import KIND_PIPELINE, save_automation as _save

    doc: dict[str, Any] = {
        "name": name or "Untitled",
        "description": description,
        "enabled": bool(enabled),
        "kind": KIND_PIPELINE,
        "graph": graph or {"nodes": [], "edges": []},
    }
    if (pipeline_id or "").strip():
        doc["id"] = pipeline_id.strip()
    return tool_json({"ok": True, "pipeline": _save(doc)}, pretty=pretty)


@mcp.tool()
def run_pipeline(
    pipeline_id: str,
    prompt: str = "",
    files: list[Any] | None = None,
    caller_conv_id: str = "",
    payload: dict[str, Any] | None = None,
    pretty: bool = False,
) -> str:
    """Run a pipeline from chat. Stamps caller_conv_id from this run when omitted."""
    from backend.automations.runner import run_pipeline as _run

    return tool_json(
        _run(
            pipeline_id,
            prompt=prompt,
            files=files,
            caller_conv_id=caller_conv_id,
            payload=payload or {},
        ),
        pretty=pretty,
    )
