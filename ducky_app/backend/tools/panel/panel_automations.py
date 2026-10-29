"""MCP tools so a ducky can author, share and run Workflows."""

from __future__ import annotations

import asyncio
import contextvars
import json
import threading
import uuid
from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json


# What one focus event may carry: the editor lights up and frames these nodes.
_FOCUS_MAX_NODES = 60
_NOTE_MAX = 280


def _reveal_graph(workflow_id: str, action: str, *, nodes: list[str] | None = None, select: bool = False, note: str = "") -> None:
    """Open the Workflows editor on this graph. With ``nodes`` the camera glides to them and
    they light up (selected too when ``select``); ``note`` shows as a caption. Panel-closed is a no-op."""
    wid = (workflow_id or "").strip()
    if not wid:
        return
    event: dict[str, Any] = {"type": "graph_focus", "id": wid, "action": action}
    if nodes:
        event["nodes"] = [str(n) for n in nodes][:_FOCUS_MAX_NODES]
    if select:
        event["select"] = True
    if note.strip():
        event["note"] = note.strip()[:_NOTE_MAX]
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event(event)
    except Exception:
        pass


def _node_kind(node: dict[str, Any]) -> str:
    if node.get("role") == "starter":
        return "trigger" if node.get("plugin_id") else "starter"
    return "node"


def filter_nodes(nodes: list[dict[str, Any]], plugin: str = "", fields: str = "") -> list[dict[str, Any]]:
    """``plugin`` keeps only that plugin's nodes and triggers. Rows are compact (id, kind, label,
    group) when a plugin is named or ``fields`` lists extra keys ("config_fields,inputs");
    ``fields="all"`` (or no filter at all) keeps the full catalog rows."""
    pid = (plugin or "").strip()
    if pid:
        nodes = [n for n in nodes if str(n.get("plugin_id") or "") == pid]
    want = [f.strip() for f in (fields or "").split(",") if f.strip()]
    if "all" in want or (not pid and not want):
        return nodes
    return [{"id": n.get("type"), "kind": _node_kind(n), "label": n.get("label"), "group": n.get("group"),
             **{k: n[k] for k in want if k in n}} for n in nodes]


@mcp.tool()
def list_workflow_nodes(plugin: str = "", fields: str = "", pretty: bool = False) -> str:
    """Catalog of builtin + enabled-plugin workflow nodes (starters, triggers, actions).

    plugin="googledrive" returns only that plugin's nodes and triggers as small rows
    {id, kind: node|trigger, label, group}. fields adds keys to those rows
    ("config_fields,inputs,outputs,description") or "all" for the full rows. With neither,
    the full catalog (large: icons and every schema)."""
    from backend.automations.catalog import list_nodes

    return tool_json({"ok": True, "nodes": filter_nodes(list_nodes(), plugin, fields)}, pretty=pretty)


@mcp.tool()
def list_workflow_templates(pretty: bool = False) -> str:
    """Builtin, plugin and custom workflow templates. shape "workflow" = one graph;
    shape "bundle" = a folder template: several workflows in nested folders
    (workflow_count, folder_count, root, bundle with each workflow's graph) whose Run
    workflow steps point at each other once created."""
    from backend.automations.templates import list_templates

    return tool_json({"ok": True, "templates": list_templates()}, pretty=pretty)


@mcp.tool()
def list_workflows(pretty: bool = False) -> str:
    """Saved workflows, one flat row each: id, name, folder, enabled, trigger, owner (an id
    from `owners`: Local is this PC only, a shared space syncs to everyone in it), last_run,
    nodes, the first 120 chars of the description, and for a reusable workflow its signature
    ("inputs -> outputs"). get_workflow has the full description and graph."""
    from backend.automations.store import list_workflows as _list
    from backend.automations.shared import owners

    return tool_json({"ok": True, "workflows": [_row(w) for w in _list()], "owners": owners()["owners"]}, pretty=pretty)


def _row(w: dict[str, Any]) -> dict[str, Any]:
    """A flat row (a TOON table) small enough that 30+ workflows fit one result: the full
    rows (owner object per row, signature types) spilled at 22k chars for 32 workflows."""
    desc = str(w.get("description") or "")
    sig = w.get("signature") or {}
    inputs = ",".join(str(i.get("name", "")) for i in sig.get("inputs") or [] if isinstance(i, dict))
    return {
        "id": w.get("id"),
        "name": w.get("name"),
        "folder": w.get("folder") or "",
        "enabled": bool(w.get("enabled")),
        "trigger": (w.get("trigger") or {}).get("label", ""),
        "owner": (w.get("owner") or {}).get("id", ""),
        "last_run": int(w.get("last_run") or 0),
        "nodes": w.get("node_count") or 0,
        "description": desc if len(desc) <= 120 else desc[:119] + "…",
        "signature": f"{inputs} -> {','.join(map(str, sig.get('outputs') or []))}" if sig else "",
    }


@mcp.tool()
def get_workflow(workflow_id: str, include_code: bool = False, pretty: bool = False) -> str:
    """Full workflow graph, owner, and this PC's last run log.

    Custom code nodes (type code.js) carry code_sha and code_lines instead of their code;
    read one with get_workflow_node_code, or pass include_code=true for every node's code.
    Saving the graph back without code keeps each node's code while its code_sha matches."""
    from backend.automations.store import get_workflow as _get

    wf = _get(workflow_id)
    if wf is None:
        return tool_json({"ok": False, "error": "workflow not found"}, pretty=pretty)
    return tool_json({"ok": True, "workflow": wf if include_code else without_code(wf)}, pretty=pretty)


def without_code(wf: dict[str, Any]) -> dict[str, Any]:
    """The workflow with each Custom code node's code swapped for its line count."""
    graph = wf.get("graph") or {}
    if not any(isinstance(n, dict) and n.get("type") == "code.js" for n in graph.get("nodes") or []):
        return wf
    nodes = []
    for node in graph.get("nodes") or []:
        cfg = node.get("config") if isinstance(node, dict) and isinstance(node.get("config"), dict) else None
        if node.get("type") == "code.js" and cfg is not None and isinstance(cfg.get("code"), str):
            cfg = {k: v for k, v in cfg.items() if k != "code"}
            cfg["code_lines"] = len(node["config"]["code"].splitlines())
            node = {**node, "config": cfg}
        nodes.append(node)
    return {**wf, "graph": {**graph, "nodes": nodes}}


def with_saved_code(graph: Any, before: Any) -> Any:
    """A graph as without_code gave it, with each Custom code node's stored code put back
    (while its code_sha still matches) and code_lines dropped, so wire and lock checks
    compare it with what is saved."""
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return graph
    stored = {str(n.get("id")): n.get("config") for n in (before or {}).get("nodes") or []
              if isinstance(n, dict) and n.get("type") == "code.js" and isinstance(n.get("config"), dict)}
    nodes = []
    for node in graph["nodes"]:
        cfg = node.get("config") if isinstance(node, dict) and isinstance(node.get("config"), dict) else None
        if cfg is not None and node.get("type") == "code.js":
            cfg = {k: v for k, v in cfg.items() if k != "code_lines"}
            was, sha = stored.get(str(node.get("id"))) or {}, cfg.get("code_sha")
            if not isinstance(cfg.get("code"), str) and sha and sha == was.get("code_sha") and isinstance(was.get("code"), str):
                cfg["code"] = was["code"]
            node = {**node, "config": cfg}
        nodes.append(node)
    return {**graph, "nodes": nodes}


@mcp.tool()
def save_workflow(
    graph: dict[str, Any] | None = None,
    name: str = "",
    description: str | None = None,
    enabled: bool | None = None,
    workflow_id: str = "",
    owner: str = "local",
    folder: str | None = None,
    allow_locked_changes: bool = False,
    pretty: bool = False,
) -> str:
    """Create or replace a workflow. Never switches the user's tab: if the Workflows editor
    is already open it glides to the nodes this save added or changed and lights them up.
    To bring the user there, call show_workflow.

    Save a workflow only when the person asked for one (or for a reusable flow). Never save
    one to test tools, nodes or a plugin: call a tool to test it, and ducky_plugin_test
    already runs every workflow node a plugin declares. A team member's new workflow is
    filed under "Made by agents/<team>" unless you name a folder.

    graph = {nodes:[{id,type,x,y,config,label,description,color?,icon?,locked?}],
    edges:[{source,target,kind,source_pin?,target_pin?}], groups:[{id,name,node_ids,parent_id?,color?,icon?,locked?}]}.
    White wires: kind main | true | false (after If / Branch) | each | done (after For each).
    For each stops the run at the first failed item unless config continue_on_error=true:
    then each failed item is listed in its failed output ([{index, error}]) and the next runs.
    Data wires: kind "data" with source_pin (an output) and target_pin (an input) from the
    node's pins in list_workflow_nodes (inputs/outputs with types); one wire per input; a
    wire whose types don't fit is refused with the reason. Nodes with exec:false (Inputs,
    Expression, Ask a model, Preview…) have no white wires: they run when their value is
    needed, and a graph with no start runs every one of them. Unwired inputs take
    config.inputs[pin].
    Cards are 240x82 at x,y; leave ~300 between columns. color: red|amber|green|blue|purple
    (none = by kind). icon: one emoji (none = the kind's icon). Groups are boxes on the canvas;
    a node is in at most one group and parent_id nests a group inside another.
    LOCKED (locked:true on a node or group, or inside a locked group) means the user froze it:
    keep it exactly as it is (spot, settings, wires, group). A save that changes one is refused
    with the names; ask the user, and only after they agree pass allow_locked_changes=true.
    Pass workflow_id to update the same graph. Updating, anything you leave out (graph,
    name, description, enabled) stays as it is; each save refreshes the open canvas.
    owner picks where a NEW workflow lives: "local" (default, this PC only) or a
    space id from list_workflows owners (shared with that space). An existing
    workflow keeps its owner; use copy_workflow to move it. folder files it in the
    list ("Play tests/Tycoon"; "" = top level); omit it to keep the current folder.

    Reusable workflows (functions): a flow.input node (config inputs=[{name,default}])
    is where a caller's values arrive; flow.output nodes (config outputs=[{name,value}])
    end a path and hand values back (blank value = the field of that name; "{{field}}"
    reads a step's value). Another workflow runs it with a workflow.call node:
    config {workflow_id, args:{input_name: "literal or {{field}}"}, share?}; the returned
    values become fields for the next steps (also under "returned"). share=true runs it
    on a copy of this run's fields and brings every field back. If it has flow.output
    nodes and none is reached, the caller's path stops there; if a Repeat until in it ran
    out of tries (its last try failed, or its check never said yes), the node fails instead.

    Custom code nodes (type code.js) are edited with edit_workflow_node_code, not here; a
    graph sent back without their code keeps it while code_sha matches.
    """
    from backend.automations.locks import changed_nodes, locked_changes
    from backend.automations.store import get_workflow as _get, save_workflow as _save

    wid = (workflow_id or "").strip()
    before = (_get(wid) if wid else None) or {}
    new_graph = with_saved_code(graph, before.get("graph")) if graph is not None else before.get("graph") or {"nodes": [], "edges": []}
    from backend.automations.catalog import node_specs
    from backend.automations.pins import check_wires
    from backend.automations.runner import _signature

    misfits = check_wires(new_graph, node_specs(), _signature)
    if misfits:
        return tool_json({"ok": False, "error": "Some data wires don't fit: " + "; ".join(misfits[:10]), "wires": misfits}, pretty=pretty)
    if before and not allow_locked_changes:
        held = locked_changes(before.get("graph"), new_graph)
        if held:
            return tool_json({
                "ok": False,
                "error": "The user locked " + ", ".join(held) + ". Leave locked nodes and groups exactly as they are, "
                "or ask the user; pass allow_locked_changes=true only after they agree.",
                "locked": held,
            }, pretty=pretty)
    doc: dict[str, Any] = {
        "name": name or before.get("name") or "Untitled",
        "description": description if description is not None else str(before.get("description") or ""),
        "enabled": bool(enabled) if enabled is not None else bool(before.get("enabled", True)),
        "graph": new_graph,
    }
    if wid:
        doc["id"] = wid
    if not before:
        folder, made_by = _agents_folder(folder)
        if made_by:
            doc["created_by"] = made_by
    if folder is not None:
        doc["folder"] = folder
    try:
        saved = _save(doc, owner=owner)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if _chat_allows_everything():
        from backend.automations.code_approval import approve_workflow

        approve_workflow(saved, "chat")
    _reveal_graph(str(saved.get("id") or ""), "saved", nodes=changed_nodes(before.get("graph"), saved.get("graph")))
    return tool_json({"ok": True, "workflow": without_code(saved)}, pretty=pretty)


@mcp.tool()
def show_workflow(
    workflow_id: str,
    node_ids: list[str] | None = None,
    group_id: str = "",
    note: str = "",
    select: bool = True,
    title: str = "",
    body: str = "",
    pretty: bool = False,
) -> str:
    """Show the user part of a workflow in the editor while you explain it.

    Opens the Workflows editor on it, glides the camera to node_ids (or every node of
    group_id), lights them up and, with select=true, selects them so their details panel
    opens. note is a short caption over the canvas ("This Branch checks the score").
    With title (and body, short markdown) it is a **Show me** instead: those nodes are
    highlighted with a popup above them that only its close button closes, and the chat
    keeps a Show me button that plays it again. Nothing is saved. For a full Next / Back
    walk through a workflow use tour_workflow; for any other part of the app ducky_ui_show.
    """
    from backend.automations.store import get_workflow as _get
    from backend.automations.locks import group_members

    wid = (workflow_id or "").strip()
    wf = _get(wid) if wid else None
    if wf is None:
        return tool_json({"ok": False, "error": "workflow not found"}, pretty=pretty)
    graph = wf.get("graph") or {}
    present = {str(n.get("id")) for n in graph.get("nodes") or []}
    wanted = [str(n) for n in node_ids or []]
    if group_id.strip():
        groups = graph.get("groups") or []
        if not any(g.get("id") == group_id.strip() for g in groups):
            return tool_json({"ok": False, "error": f"group not found: {group_id}"}, pretty=pretty)
        wanted += group_members(groups, group_id.strip())
    shown = [n for n in dict.fromkeys(wanted) if n in present]
    missing = [n for n in dict.fromkeys(wanted) if n not in present]
    if (title or body).strip() and (shown or group_id.strip()):
        from backend.panel.rpc import panel_rpc

        target: Any = f"workflows.group.{group_id.strip()}" if group_id.strip() and not node_ids else [f"workflows.node.{n}" for n in shown]
        played = panel_rpc("show", {
            "target": target[0] if isinstance(target, list) and len(target) == 1 else target,
            "workflow_id": wid,
            "title": (title or body).strip()[:120],
            "body": body.strip(),
        }, timeout=30.0)
        if isinstance(played, dict) and played.get("error"):
            return tool_json({"ok": False, "error": played["error"], "shown": shown, "missing": missing}, pretty=pretty)
        return tool_json({"ok": True, "shown": shown, "missing": missing, "show_me": True}, pretty=pretty)
    _reveal_graph(wid, "show", nodes=shown, select=bool(select) and bool(shown), note=note)
    return tool_json({"ok": True, "shown": shown, "missing": missing}, pretty=pretty)


@mcp.tool()
def tour_workflow(
    workflow_id: str,
    steps: list[dict[str, Any]] | None = None,
    auto: bool = False,
    pretty: bool = False,
) -> str:
    """Walk the user through one workflow with Next / Back, node by node, in the editor.

    Opens the workflow, then each step highlights nodes with a card explaining them.
    steps: [{"node_ids": ["test"], "title": "Play tester", "body": "A Tester ducky…"},
            {"group_id": "g1", "title": "…", "body": "…"},
            {"target": "workflows.toolbar.run", "title": "Test", "body": "Run it now."}]
    auto=true (no steps): build the tour from the graph itself, in run order, with each
    node's name, description and what goes in and out — the editor's Tour this workflow.
    Blocks until the user finishes or skips (at most 10 minutes). The chat keeps the
    tour with a Replay button. Returns {ok, completed, skipped, steps}.
    """
    from backend.automations.store import get_workflow as _get
    from backend.panel.rpc import panel_rpc

    wid = (workflow_id or "").strip()
    wf = _get(wid) if wid else None
    if wf is None:
        return tool_json({"ok": False, "error": "workflow not found"}, pretty=pretty)
    present = {str(n.get("id")) for n in (wf.get("graph") or {}).get("nodes") or []}
    groups = {str(g.get("id")) for g in (wf.get("graph") or {}).get("groups") or []}
    cleaned: list[dict[str, Any]] = []
    for step in steps or []:
        if not isinstance(step, dict):
            return tool_json({"ok": False, "error": "each step must be an object"}, pretty=pretty)
        title, body = str(step.get("title") or "").strip(), str(step.get("body") or "").strip()
        nodes = [str(n) for n in step.get("node_ids") or [] if str(n) in present]
        group = str(step.get("group_id") or "").strip()
        target = step.get("target")
        if nodes:
            row: dict[str, Any] = {"node_ids": nodes}
        elif group in groups:
            row = {"group_id": group}
        elif isinstance(target, (str, dict)) and target:
            row = {"target": target}
        else:
            return tool_json({"ok": False, "error": f"step {len(cleaned) + 1}: name node_ids or group_id from this workflow, or a target"}, pretty=pretty)
        cleaned.append({**row, "title": title or body[:48] or "Step", "body": body or title})
    if not cleaned and not auto:
        return tool_json({"ok": False, "error": "give steps, or auto=true to build them from the graph"}, pretty=pretty)
    out = panel_rpc("tour_workflow", {"workflow_id": wid, "steps": cleaned, "auto": bool(auto) and not cleaned}, timeout=600.0)
    return tool_json(out if isinstance(out, dict) else {"ok": False, "error": "no answer"}, pretty=pretty)


@mcp.tool()
def move_workflow_folder(owner: str, path: str, new_path: str = "", pretty: bool = False) -> str:
    """Rename or move a folder of the Workflows list, with everything in it.

    owner is "local" or a space id; path is the folder ("Play tests/Tycoon"). new_path is
    where it goes ("Play tests/Lobby" renames it); its parent ("Play tests") or "" for a
    top-level one removes the folder and keeps its workflows."""
    from backend.automations.store import move_folder

    try:
        moved = move_folder(owner, path, new_path)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json({"ok": True, "moved": moved}, pretty=pretty)


@mcp.tool()
def add_workflow_folder(owner: str, path: str, pretty: bool = False) -> str:
    """Make a folder in the Workflows list that may hold nothing yet. owner is "local"
    or a space id; path is "Play tests/Tycoon" (missing parents are made too). A shared
    space's folders, empty ones included, sync to everyone in it."""
    from backend.automations.store import add_folder

    try:
        return tool_json({"ok": True, "folders": add_folder(owner, path)}, pretty=pretty)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)


@mcp.tool()
def export_workflow_folder(owner: str, path: str, pretty: bool = False) -> str:
    """A folder of the Workflows list with everything in it as one bundle:
    {version, root, folders (relative, empty ones too), workflows: [{key (its id), name,
    description, enabled, folder (relative), graph}]}. path "" = the whole owner.
    import_workflow_bundle makes a copy of it anywhere."""
    from backend.automations.bundles import export_folder

    try:
        return tool_json({"ok": True, "bundle": export_folder(owner, path)}, pretty=pretty)
    except KeyError:
        return tool_json({"ok": False, "error": "folder not found"}, pretty=pretty)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)


@mcp.tool()
def import_workflow_bundle(bundle: dict[str, Any] | str, owner: str = "local", parent_path: str = "", name: str = "",
                           pretty: bool = False) -> str:
    """Make a folder bundle (export_workflow_folder's shape) under owner/parent_path:
    the whole nested tree, every workflow with a new id, and each Run workflow step
    that names a workflow of the bundle by its key (or "@key") pointed at the new one.
    name renames the root folder; a taken name becomes "Name (2)". All or nothing.
    Returns folder, workflows [{key, id, name}] and outside (calls to workflows that
    are not in the bundle)."""
    import json

    from backend.automations.bundles import import_bundle

    try:
        raw = json.loads(bundle) if isinstance(bundle, str) else bundle
        out = import_bundle(raw, owner, parent_path, name=name or None)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json({"ok": True, **out}, pretty=pretty)


@mcp.tool()
def copy_workflow_folder(owner_from: str, path: str, owner_to: str, parent_path: str = "", move: bool = False,
                         pretty: bool = False) -> str:
    """Copy a whole folder (nested folders, empty ones, every workflow) from one owner
    ("local" or a space id) into owner_to/parent_path. A copy gets new ids with its Run
    workflow steps pointed at the copies; move=True keeps the ids and removes the
    source once everything is in place (moving out of a shared space removes it for
    everyone in it). Inside one owner a move is move_workflow_folder."""
    from backend.automations.bundles import copy_folder

    try:
        out = copy_folder(owner_from, path, owner_to, parent_path, move=move)
    except KeyError:
        return tool_json({"ok": False, "error": "folder not found"}, pretty=pretty)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json({"ok": True, **out}, pretty=pretty)


@mcp.tool()
def clear_workflow_runs(workflow_id: str, pretty: bool = False) -> str:
    """Run log → Clear log: forget this PC's past runs of one workflow."""
    from backend.automations.store import clear_runs

    if not clear_runs(workflow_id):
        return tool_json({"ok": False, "error": "workflow not found"}, pretty=pretty)
    return tool_json({"ok": True}, pretty=pretty)


@mcp.tool()
def list_workflow_versions(workflow_id: str, pretty: bool = False) -> str:
    """Saved versions of a workflow (newest first): id, name, saved_at, node_count, note.
    The editor's History lists the same ones; restore_workflow_version brings one back."""
    from backend.automations.versions import list_versions

    return tool_json({"ok": True, "versions": list_versions(workflow_id)}, pretty=pretty)


@mcp.tool()
def restore_workflow_version(workflow_id: str, version_id: str, allow_locked_changes: bool = False, pretty: bool = False) -> str:
    """Bring back a saved version (name, description, on/off, graph) as a new save; the
    version it replaces stays in History, so this can be undone the same way. Locked
    nodes and groups are respected as in save_workflow."""
    from backend.automations.versions import get_version

    version = get_version(workflow_id, version_id)
    if not version:
        return tool_json({"ok": False, "error": "version not found"}, pretty=pretty)
    return save_workflow(
        graph=version.get("graph") or {"nodes": [], "edges": []},
        name=str(version.get("name") or ""),
        description=str(version.get("description") or ""),
        enabled=bool(version.get("enabled", True)),
        workflow_id=workflow_id,
        allow_locked_changes=allow_locked_changes,
        pretty=pretty,
    )


@mcp.tool()
def set_workflow_folder(workflow_id: str, folder: str = "", pretty: bool = False) -> str:
    """File a workflow in a folder of the Workflows list without touching its graph.
    folder is a path inside its owner ("Play tests/Tycoon"); "" = top level."""
    from backend.automations.store import set_folder

    try:
        out = set_folder(workflow_id, folder)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if out is None:
        return tool_json({"ok": False, "error": "workflow not found"}, pretty=pretty)
    return tool_json({"ok": True, "workflow": out}, pretty=pretty)


@mcp.tool()
def copy_workflow(workflow_id: str, owner: str, move: bool = False, pretty: bool = False) -> str:
    """Copy a workflow to "local" or a space id (new id), or move it there (move=True).
    Moving a shared workflow to local removes it for everyone in the space."""
    from backend.automations.store import copy_workflow as _copy

    try:
        out = _copy(workflow_id, owner, move=move)
    except KeyError:
        return tool_json({"ok": False, "error": "workflow not found"}, pretty=pretty)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    _reveal_graph(str(out.get("id") or ""), "saved")
    return tool_json({"ok": True, "workflow": out}, pretty=pretty)


def _review_hint(out: dict[str, Any], workflow_id: str, node_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    """A run refused because Custom code waits for a person's Review: say so to the agent
    (needs_review, and a hint naming the nodes) next to the person-facing error."""
    from backend.automations.code_approval import REVIEW

    def text(value: Any) -> str:
        return str(value.get("message") or "") if isinstance(value, dict) else str(value or "")

    if not out.get("needs_review") and REVIEW not in text(out.get("error")):
        return out
    out["needs_review"] = True
    if out.get("hint"):
        return out
    from backend.automations.store import get_workflow as _get

    labels = {str(n.get("id")): str(n.get("label") or n.get("id"))
              for n in ((_get(workflow_id) or {}).get("graph") or {}).get("nodes") or [] if isinstance(n, dict)}
    held = [s for s in out.get("steps") or [] if isinstance(s, dict) and REVIEW in text(s.get("error"))]
    names = [labels.get(str(s.get("id"))) or str(s.get("label") or s.get("id") or "") for s in held]
    names = [n for n in dict.fromkeys(names or [labels.get(i, i) for i in node_ids]) if n] or ["the Custom code node"]
    out["hint"] = f"Ask the user to press Review on {', '.join(names)} (or run it once); a dry run works now."
    return out


AGENTS_FOLDER = "Made by agents"


def _agent_maker() -> tuple[str, str, str]:
    """(chat id, chat name, team name or "") for the agent calling these tools. Only agents
    call these tools; the person uses the editor."""
    try:
        from backend.tools.panel.panel_ui import _resolve_ask_user_conv_id
        from frontend.ui_web.project_chats import load_conversation

        cid = str(_resolve_ask_user_conv_id() or "").strip()
        conv = load_conversation(cid) if cid else None
    except Exception:
        return "", "", ""
    if conv is None:
        return cid, "", ""
    name = str(getattr(conv, "title", "") or getattr(conv, "ducky_name", "") or "").strip()
    hub = (getattr(conv, "parent_conv_id", None) or "").strip()
    team = ""
    if hub:
        try:
            from frontend.ui_web.group_orchestrator import _hub_chain

            top = load_conversation(_hub_chain(hub)[-1][0])
            team = str(getattr(top, "title", "") or "").strip() if top is not None and getattr(top, "is_group", False) else ""
        except Exception:
            team = ""
    return cid, name, team


def _agents_folder(folder: str | None) -> tuple[str | None, dict[str, str]]:
    """A new workflow a team member makes without naming a folder goes under
    "Made by agents/<team>", never loose at the top of the person's list (a team's agent
    once filled it with test workflows). A chat the person talks to directly keeps filing
    at the top, as asked. Either way the workflow remembers which chat made it."""
    cid, name, team = _agent_maker()
    made_by = {"conv_id": cid, "name": name} if cid else {}
    if (folder or "").strip() or not team:
        return folder, made_by
    return f"{AGENTS_FOLDER}/{team}", made_by


def _chat_allows_everything() -> bool:
    """The chat running this tool said "Allow everything": its yes is already given."""
    try:
        from backend.tools.panel.panel_ui import _resolve_ask_user_conv_id
        from backend.tools.panel.permission_prompt import allows_everything

        return allows_everything(_resolve_ask_user_conv_id())
    except Exception:
        return False


@mcp.tool()
def delete_workflow(workflow_id: str, confirmed: bool = False, pretty: bool = False) -> str:
    """Delete one workflow (for everyone in the space, if it is shared) and close it in the editor.

    It can't be undone, so first ask the user with ducky_ask_user (yes / no, naming the
    workflow and, for a shared one, that everyone in the space loses it). Only after they say yes call
    again with confirmed=true. Never delete to "clean up" on your own."""
    from backend.automations.store import delete_workflow as _delete
    from backend.automations.store import get_workflow as _get

    wid = (workflow_id or "").strip()
    if confirmed is not True and not _chat_allows_everything():
        wf = _get(wid)
        if wf is None:
            return tool_json({"ok": False, "error": "workflow not found"}, pretty=pretty)
        shared = (wf.get("owner") or {}).get("kind") == "shared"
        return tool_json({
            "ok": False,
            "needs_confirmation": True,
            "error": (f"Deleting “{wf.get('name') or wid}” can't be undone"
                      + (f" and everyone in {(wf.get('owner') or {}).get('label') or 'the space'} loses it" if shared else "")
                      + ". Ask the user with ducky_ask_user (yes / no) first; only after they say yes, call again with confirmed=true."),
        }, pretty=pretty)
    try:
        ok = _delete(wid)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if not ok:
        return tool_json({"ok": False, "error": "workflow not found"}, pretty=pretty)
    _reveal_graph(wid, "deleted")
    return tool_json({"ok": True, "id": wid}, pretty=pretty)


@mcp.tool()
def find_workflows(task: str, limit: int = 6, pretty: bool = False) -> str:
    """Saved workflows that fit a task the person asked for: call once per request.

    Searches the user's saved workflows and the ready-made templates (pictures, 3D,
    characters, play tests, UEFN, documents…) for ones that fit ``task`` (plain words,
    e.g. "prompt to 3D model in UEFN"), best first. A saved workflow that fits: run it
    with run_workflow (or show it). Nothing saved fits: just do the work, and never
    mention workflows. Templates are for when the person asks for a workflow: never
    create or save one on your own, and never call this again for plan steps."""
    from backend.automations.finder import find
    from backend.automations.store import list_workflows as _list
    from backend.automations.templates import list_templates

    found = find(task, _list(), list_templates(), limit=max(1, min(int(limit or 6), 20)))
    if found["workflows"]:
        found["hint"] = "Run the best saved workflow with run_workflow, or show it with show_workflow if the user wants to check it first."
    else:
        # Oct 18 2026: agents announced "no matching workflow" on every plan step and made
        # workflows nobody asked for from the templates.
        found["hint"] = ("No saved workflow fits. Do the work now and don't mention workflows. Use a template "
                         "only when the person asked for a workflow.")
    return tool_json({"ok": True, **found}, pretty=pretty)


@mcp.tool()
def create_workflow_from_template(
    template_id: str,
    name: str = "",
    owner: str = "local",
    folder: str = "",
    pretty: bool = False,
) -> str:
    """Make a new workflow from a template (ids from find_workflows or list_workflow_templates)
    (an open Workflows editor shows it; the user's tab never switches). Paid image / 3D steps come with Spend credits off: ask the
    user before turning spend on. Returns the new workflow (run it with run_workflow).

    A folder template (shape "bundle") makes its whole folder tree inside folder (the
    parent; "" = top level) of owner ("local" or a space id): every workflow with a new
    id, Run workflow steps between them pointed at the new ones. name renames the root
    folder. Returns bundle {folder, workflows [{key, id, name}], outside} and the main
    workflow it opened."""
    from backend.automations.store import get_workflow as _get
    from backend.automations.templates import use_template

    folder = _agents_folder(folder)[0] or ""
    try:
        out = use_template(template_id, owner, folder, name)
    except LookupError as exc:
        return tool_json({"ok": False, "error": exc.args[0] if exc.args else "template not found"}, pretty=pretty)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if out["shape"] == "bundle":
        bundle = {k: out[k] for k in ("folder", "owner", "workflows", "outside")}
        main = _get(out["main"]) if out["main"] else None
        if main:
            _reveal_graph(out["main"], "saved", nodes=[str(n.get("id")) for n in (main.get("graph") or {}).get("nodes") or []])
        return tool_json({"ok": True, "bundle": bundle, "workflow": main, "from_template": out["template"]}, pretty=pretty)
    saved = out["workflow"]
    _reveal_graph(str(saved.get("id") or ""), "saved", nodes=[str(n.get("id")) for n in (saved.get("graph") or {}).get("nodes") or []])
    return tool_json({"ok": True, "workflow": saved, "from_template": out["template"]}, pretty=pretty)


# The tool's name, and its name before tools had a group prefix (saved workflows keep it).
_REPO_FINDERS = frozenset({"ducky_plugin_find_repo", "find_plugin_repo"})


def release_plugin_of(workflow_id: str, payload: dict[str, Any] | None = None) -> str:
    """The plugin a release workflow publishes: its Call tool ducky_plugin_find_repo step's plugin
    argument, a "{{field}}" read from the payload or the workflow's input default. "" otherwise."""
    from backend.automations.store import get_workflow as _get

    wf = _get(workflow_id) or {}
    nodes = [n for n in (wf.get("graph") or {}).get("nodes") or [] if isinstance(n, dict)]
    finder = next((n for n in nodes if n.get("type") == "tool.call"
                   and (n.get("config") or {}).get("name") in _REPO_FINDERS), None)
    if finder is None:
        return ""
    cfg = finder.get("config") or {}
    args = cfg.get("arguments") if isinstance(cfg.get("arguments"), dict) else {}
    if not args and cfg.get("arguments_json"):
        try:
            args = json.loads(str(cfg["arguments_json"]))
        except ValueError:
            args = {}
    value = str((args or {}).get("plugin") or "").strip()
    field = value[2:-2].strip() if value.startswith("{{") and value.endswith("}}") else ""
    if not field:
        return value
    if (payload or {}).get(field):
        return str(payload[field]).strip()
    for node in nodes:
        if node.get("type") == "flow.input":
            for row in (node.get("config") or {}).get("inputs") or []:
                if isinstance(row, dict) and row.get("name") == field and row.get("default"):
                    return str(row["default"]).strip()
    return ""


_RELEASE_LOCKS: dict[int, asyncio.Lock] = {}


def _release_start_lock() -> asyncio.Lock:
    # ponytail: one lock per event loop (the tool server has one); per-plugin locks if releases queue up.
    return _RELEASE_LOCKS.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())


def _run_ids(workflow_id: str) -> set[str]:
    from backend.automations import live_runs

    return {str(r.get("run")) for r in live_runs.runs(workflow_id)}


async def _record_release_run(plugin: str, workflow_id: str, known: set[str], running: asyncio.Task,
                              chat_id: str, step_id: str, wait_s: float = 10.0) -> None:
    """Wait for the run started just now to appear, then hold ``plugin`` for it (L2's release lock)."""
    from backend.tools.panel.panel_ai_plugins import start_release_run

    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while not running.done() and loop.time() < deadline:
        new = _run_ids(workflow_id) - known
        if new:
            found = resolve_plugin_repo(plugin)
            start_release_run(plugin, workflow_id, sorted(new)[0], chat_id=_caller_chat(chat_id),
                              step_id=step_id, clone=str(found.get("repo") or ""))
            return
        await asyncio.sleep(0.05)


def _caller_chat(conv_id: str) -> str:
    if conv_id:
        return conv_id
    try:
        from backend.workspace import identity as run_identity

        ctx = run_identity.current()
        return ((ctx.conv_id if ctx is not None else "") or "").strip()
    except Exception:
        return ""


def _tell_caller(chat_id: str, workflow_id: str, out: dict[str, Any]) -> None:
    """A background run ended: one notice to the chat that started it (wakes its ducky when idle)."""
    if not chat_id:
        return
    from backend.automations.store import get_workflow as _get

    name = str((_get(workflow_id) or {}).get("name") or workflow_id)
    state = "ok" if out.get("ok") else f"failed: {out.get('error') or 'unknown error'}"
    lines = [f"[Workflow] {name} finished: {state} (run `{out.get('run') or ''}`)."]
    text = str(out.get("text") or "").strip()
    if text:
        lines.append(text[:2000])
    if out.get("outputs"):
        lines.append("Outputs: " + json.dumps(out["outputs"], default=str)[:2000])
    try:
        from backend.agent.a2a_client import send_notice

        send_notice(sender_conv_id=f"workflow:{workflow_id}", receiver_conv_id=chat_id, body="\n".join(lines))
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Could not tell %s that workflow %s ended", chat_id, workflow_id)


@mcp.tool()
async def run_workflow(
    workflow_id: str,
    prompt: str = "",
    files: list[Any] | None = None,
    caller_conv_id: str = "",
    payload: dict[str, Any] | None = None,
    trigger_id: str = "",
    plan_step_id: str = "",
    wait: bool = False,
    pretty: bool = False,
) -> str:
    """Run a workflow now (an open Workflows editor shows it; the user's tab never switches).
    Does not switch the active island.

    Returns at once with {ok, run_id, status:"running"}: the run goes on by itself (only the
    workflow bar's Stop ends it) and the calling chat gets one "[Workflow] ... finished" message
    when it ends, so keep talking meanwhile. wait=true blocks and returns the whole result.

    A plugin release workflow (its Find the plugin repo step) holds that plugin while it
    runs: a second release run of the same plugin is refused with the running one's id.
    plan_step_id: the plan step this run does; the run id is noted on it.

    From chat: pass the user's request after a workflow reference as prompt, and
    their files. caller_conv_id defaults to the chat running this tool, which gets
    the files and the Return to user result. A reusable workflow takes its inputs
    in payload and gives its Return values back as outputs. Its Agent steps' duckies
    join your group (else one group named after the workflow, made when an Agent step
    first runs); a run never makes a nested or empty group.
    """
    from backend.automations.runner import public_payload
    from backend.automations.runner import run_workflow as _run

    loop = asyncio.get_running_loop()
    run_id = uuid.uuid4().hex[:12]
    caller = _caller_chat(caller_conv_id)

    def work(done: asyncio.Future) -> None:
        try:
            out = _run(
                workflow_id,
                trigger_id=trigger_id,
                # Only the app says a person pressed Run (approves code, allows spending).
                payload=public_payload(payload),
                prompt=prompt,
                files=files,
                caller_conv_id=caller_conv_id,
                run_id=run_id,
            )
        except Exception as exc:
            out = {"ok": False, "error": str(exc), "id": workflow_id, "run": run_id}
        if not wait:
            _tell_caller(caller, workflow_id, out)
        try:
            loop.call_soon_threadsafe(lambda: done.done() or done.set_result(out))
        except RuntimeError:
            pass  # the tool server's loop is gone; the run itself already finished

    def start() -> asyncio.Future:
        # Its own thread, not the tool call: agents inside the workflow call this MCP
        # server themselves (its loop stays free), and a cancelled or interrupted turn
        # can't stop the run. copy_context keeps the caller's identity variables.
        done = loop.create_future()
        threading.Thread(target=contextvars.copy_context().run, args=(work, done),
                         name=f"workflow-{workflow_id}-{run_id}", daemon=True).start()
        return done

    async def result(running: asyncio.Future) -> str:
        if not wait:
            return tool_json({"ok": True, "run_id": run_id, "status": "running", "workflow": workflow_id}, pretty=pretty)
        out = await asyncio.shield(running)
        if out.get("ok"):
            _reveal_graph(workflow_id, "saved")
        return tool_json(_review_hint(out, workflow_id), pretty=pretty)

    if not wait:
        _reveal_graph(workflow_id, "saved")
    plugin = release_plugin_of(workflow_id, payload)
    if not plugin:
        return await result(start())
    else:
        from backend.tools.panel.panel_ai_plugins import release_busy

        async with _release_start_lock():  # check, start and record as one: two calls can't both pass
            busy = release_busy(plugin)
            if busy:
                return tool_json({"ok": False, "error": f"{plugin}: {busy}. Wait for it or stop it first."}, pretty=pretty)
            known = _run_ids(workflow_id)
            running = start()
            await _record_release_run(plugin, workflow_id, known, running, caller_conv_id, plan_step_id)
        return await result(running)


@mcp.tool()
async def run_workflow_node(workflow_id: str, node_id: str, pretty: bool = False) -> str:
    """Run one node of a saved workflow now (the details panel's Run this node).

    Everything wired into it reuses what the last run made, so paid generators
    upstream don't run again; a value node that never ran runs first. Returns the
    node's outputs (node_outputs[node_id]) and its step in steps."""
    from backend.automations.runner import run_node

    out = await asyncio.to_thread(run_node, workflow_id, node_id)
    if out.get("ok"):
        _reveal_graph(workflow_id, "saved", nodes=[node_id], select=False)
    return tool_json(_review_hint(out, workflow_id, (str(node_id),)), pretty=pretty)


@mcp.tool()
def stop_workflow(workflow_id: str, pretty: bool = False, run_id: str = "") -> str:
    """Stop a specific run, or all runs of this workflow on this PC.
    Cancels active owned terminal process trees and prevents subsequent steps."""
    from backend.automations.runner import stop_workflow as _stop

    return tool_json({"ok": True, "stopped": _stop(workflow_id, run_id)}, pretty=pretty)


@mcp.tool()
def emit_workflow_trigger(
    trigger_id: str,
    payload: dict[str, Any] | None = None,
    pretty: bool = False,
) -> str:
    """Simulate a plugin trigger: run every enabled workflow on this PC that listens for trigger_id."""
    from backend.automations.runner import emit_trigger

    return tool_json(emit_trigger(trigger_id, payload or {}), pretty=pretty)


@mcp.tool()
def save_workflow_template(
    name: str = "",
    description: str = "",
    graph: dict[str, Any] | None = None,
    template_id: str = "",
    icon: str = "",
    category: str = "",
    owner: str = "",
    folder: str = "",
    pretty: bool = False,
) -> str:
    """Save a reusable custom workflow template (shown in the New workflow picker).

    Pass template_id (custom:…) to replace one. graph uses the same shape as save_workflow.
    category is the picker shelf (Images, 3D, Characters, Text & AI, Documents, Play
    tests, UEFN, or your own name); blank = Yours.

    folder (with owner, "local" or a space id) keeps that whole folder instead: every
    nested folder and workflow in it, as a folder template (blank name = the folder's
    name). Its Run workflow steps between those workflows keep pointing at each other
    in every copy made from it. Editing a folder template without folder keeps its tree.
    """
    from backend.automations.templates import save_custom, save_folder_template

    try:
        if (folder or "").strip():
            row = save_folder_template(owner or "local", folder, name, description=description, icon=icon or "📁",
                                       template_id=template_id, category=category)
        else:
            row = save_custom(
                name,
                description=description,
                icon=icon or "⚡",
                graph=graph,
                template_id=template_id,
                category=category,
            )
    except KeyError:
        return tool_json({"ok": False, "error": "folder not found"}, pretty=pretty)
    except (PermissionError, ValueError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json({"ok": True, "template": row}, pretty=pretty)


@mcp.tool()
def delete_workflow_template(template_id: str, pretty: bool = False) -> str:
    """Delete a custom:… template. Builtin and plugin templates cannot be deleted."""
    from backend.automations.templates import delete_custom

    if not delete_custom(template_id):
        return tool_json({"ok": False, "error": "template not found"}, pretty=pretty)
    return tool_json({"ok": True, "id": (template_id or "").strip()}, pretty=pretty)


# --------------------------------------------------------------------------- custom code nodes


def _node_of(wf: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    nid = str(node_id or "").strip()
    return next((n for n in (wf.get("graph") or {}).get("nodes") or [] if isinstance(n, dict) and str(n.get("id")) == nid), None)


def _pins_of(node: dict[str, Any], specs: dict[str, Any]) -> dict[str, Any] | None:
    """A node's pins as the runner sees them; None for a node whose plugin is off."""
    from backend.automations.pins import node_pins
    from backend.automations.runner import _signature

    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    if node.get("type") == "code.js":
        pins = cfg.get("pins") if isinstance(cfg.get("pins"), dict) else {}
        return {"exec": pins.get("exec", True) is not False, "inputs": list(pins.get("inputs") or []), "outputs": list(pins.get("outputs") or [])}
    spec = specs.get(str(node.get("type") or ""))
    return node_pins(node, spec, _signature) if spec is not None else None


def _last_inputs(wf: dict[str, Any], node_id: str) -> dict[str, Any]:
    """What the node ran with last on this PC (its newest step record that kept them)."""
    for run in reversed(list(wf.get("runs") or [])):
        for step in reversed(list(run.get("steps") or []) if isinstance(run, dict) else []):
            if isinstance(step, dict) and str(step.get("id")) == node_id and isinstance(step.get("inputs"), dict):
                return dict(step["inputs"])
    return {}


def node_code(workflow_id: str, node_id: str, *, draft: dict[str, Any] | None = None, missing: str = "") -> dict[str, Any]:
    """The JavaScript one node runs (a built-in's as generated code), with its pins,
    problems, what it was made from and whether it may run on its own here. ``draft``:
    the editor's unsaved node; a built-in's code and pins come from its settings.
    ``missing``: the error for a node the saved workflow doesn't have."""
    from backend.automations import code_check, codegen
    from backend.automations.catalog import node_specs
    from backend.automations.code_approval import is_approved
    from backend.automations.store import get_workflow as _get

    wf = _get(workflow_id)
    if wf is None:
        return {"ok": False, "error": "workflow not found"}
    node = _node_of(wf, node_id)
    if draft is not None and str(draft.get("type") or "") not in ("", "code.js"):
        node = {**draft, "id": str(node_id), "config": dict(draft["config"]) if isinstance(draft.get("config"), dict) else {}}
    if node is None:
        return {"ok": False, "error": missing or f"node not found: {node_id}"}
    wid, nid = str(wf["id"]), str(node["id"])
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    specs = node_specs()
    if node.get("type") == "code.js":
        code = cfg.get("code") if isinstance(cfg.get("code"), str) else ""
        checked = code_check.check(code)
        based = cfg.get("based_on") if isinstance(cfg.get("based_on"), dict) and cfg["based_on"].get("type") else None
        was = {"id": nid, "type": based["type"], "label": node.get("label") or "", "config": dict(based.get("config") or {})} if based else None
        based_code = codegen.generate(was, specs)["code"] if was else None
        out: dict[str, Any] = {
            "kind": "custom", "code": code, "code_sha": checked["code_sha"], "based_on": based, "based_on_code": based_code,
            "pins": _pins_of(node, specs), "settings_spec": list(cfg.get("settings_spec") or []),
            "uses": cfg.get("uses") if isinstance(cfg.get("uses"), dict) else checked["uses"],
            "problems": checked["problems"], "convertible": True, "reason": "",
            "approved": is_approved(wid, nid, checked["code_sha"]),
        }
    else:
        made = codegen.generate(node, specs)
        checked = code_check.check(made["code"]) if made["kind"] != "flow" else None
        out = {
            "kind": "flow" if made["kind"] == "flow" else "builtin", "code": made["code"],
            "code_sha": code_check.code_sha(made["code"]), "based_on": None, "based_on_code": None,
            "pins": _pins_of(node, specs) or {"exec": True, "inputs": [], "outputs": []}, "settings_spec": [],
            "uses": checked["uses"] if checked else {"tools": [], "builtins": []},
            "problems": checked["problems"] if checked else [],
            "convertible": bool(made["convertible"]), "reason": made["reason"], "approved": True,
        }
    return {"ok": True, **out, "last_inputs": _last_inputs(wf, nid)}


def _apply_edits(code: str, edits: list[Any]) -> str:
    for n, edit in enumerate(edits, 1):
        old = edit.get("old") if isinstance(edit, dict) else None
        new = edit.get("new") if isinstance(edit, dict) else None
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise ValueError(f"Edit {n}: give old (text in the code now) and new (what replaces it).")
        found = code.count(old)
        if found != 1:
            where = "isn't in the code" if not found else f"is in the code {found} times"
            raise ValueError(f"Edit {n}: its old text {where}; it must match exactly once (take in more of the lines around it).")
        code = code.replace(old, new, 1)
    return code


def _drop_wires(graph: dict[str, Any], node_id: str, pins: dict[str, Any], specs: dict[str, Any]) -> list[dict[str, Any]]:
    """Take out the wires that no longer fit the node's pins; returns what was dropped."""
    from backend.automations.pins import DATA_KIND, accepts

    nodes = {str(n.get("id")): n for n in graph.get("nodes") or [] if isinstance(n, dict)}
    ins = {p["id"]: p for p in pins.get("inputs") or []}
    outs = {p["id"]: p for p in pins.get("outputs") or []}
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for edge in graph.get("edges") or []:
        reason = ""
        source, target = str(edge.get("source")), str(edge.get("target"))
        if edge.get("kind") == DATA_KIND and node_id in (source, target):
            feeds_me = target == node_id
            pin_id = str(edge.get("target_pin") if feeds_me else edge.get("source_pin"))
            mine = (ins if feeds_me else outs).get(pin_id)
            other = nodes.get(source if feeds_me else target)
            other_pins = _pins_of(other, specs) if other is not None else None
            if mine is None:
                reason = f"no {'input' if feeds_me else 'output'} {pin_id!r} any more"
            elif other_pins is not None:
                their_id = edge.get("source_pin") if feeds_me else edge.get("target_pin")
                theirs = next((p for p in other_pins["outputs" if feeds_me else "inputs"] if p["id"] == their_id), None)
                if theirs is not None:
                    into, out = (mine, theirs) if feeds_me else (theirs, mine)
                    if not accepts(into["type"], out["type"]):
                        reason = f"{out['type']} can't feed {into['type']} any more"
        elif edge.get("kind") != DATA_KIND and node_id in (source, target) and not pins.get("exec", True):
            reason = "a value node has no white pins"
        if reason:
            dropped.append({**{k: edge[k] for k in ("source", "target", "kind", "source_pin", "target_pin") if k in edge}, "reason": reason})
        else:
            kept.append(edge)
    graph["edges"] = kept
    return dropped


def edit_node_code(
    workflow_id: str,
    node_id: str,
    *,
    code: str | None = None,
    edits: list[Any] | None = None,
    revert: bool = False,
    expected_sha: str | None = None,
    allow_locked_changes: bool = False,
    person: bool = False,
) -> dict[str, Any]:
    """Write a node's code (a built-in becomes Custom code in this workflow only), or
    revert it to the built-in it was made from, through the normal save. ``person``: a
    person saved it in the editor, which approves this node's code; an agent's save
    approves it only when its chat allows everything."""
    from copy import deepcopy

    from backend.automations import code_check, codegen
    from backend.automations.catalog import node_specs
    from backend.automations.code_approval import approve, is_local, node_sha
    from backend.automations.locks import locked_changes
    from backend.automations.pins import check_wires, node_pins
    from backend.automations.runner import _signature
    from backend.automations.store import get_workflow as _get, save_workflow as _save

    wf = _get(workflow_id)
    if wf is None:
        return {"ok": False, "error": "workflow not found"}
    graph = deepcopy(wf.get("graph") or {"nodes": [], "edges": []})
    node = _node_of({"graph": graph}, node_id)
    if node is None:
        return {"ok": False, "error": f"node not found: {node_id}"}
    nid, ntype = str(node["id"]), str(node.get("type") or "")
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    specs = node_specs()
    if revert:
        based = cfg.get("based_on") if isinstance(cfg.get("based_on"), dict) else None
        if ntype != "code.js":
            return {"ok": False, "error": "This node runs its built-in step already."}
        if not based or not based.get("type"):
            return {"ok": False, "error": "This Custom code node wasn't made from a built-in node, so there is nothing to revert to."}
        node["type"], node["config"] = str(based["type"]), deepcopy(dict(based.get("config") or {}))
        spec = specs.get(node["type"])
        pins = node_pins(node, spec, _signature) if spec is not None else None
    else:
        if code is not None and edits:
            return {"ok": False, "error": "Pass code (the whole module) or edits, not both."}
        if ntype == "code.js":
            current = cfg.get("code") if isinstance(cfg.get("code"), str) else ""
            based = None
        else:
            made = codegen.generate(node, specs)
            if not made["convertible"]:
                return {"ok": False, "error": made["reason"] or "This node can't become custom code."}
            current = made["code"]
            based = {"type": ntype, "config": deepcopy(cfg), "code_sha": code_check.code_sha(current)}
        if expected_sha and str(expected_sha).strip() != code_check.code_sha(current):
            return {"ok": False, "conflict": True, "code_sha": code_check.code_sha(current),
                    "error": "The code changed since you read it. Read it again with get_workflow_node_code, then edit that."}
        if code is not None:
            new_code = code
        elif edits:
            try:
                new_code = _apply_edits(current, list(edits))
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
        else:
            return {"ok": False, "error": "Pass code (the whole module), edits ([{old, new}]) or revert=true."}
        if not isinstance(new_code, str):
            return {"ok": False, "error": "code is the whole JavaScript module, as text."}
        checked = code_check.check(new_code)
        if based is not None:
            spec = specs.get(ntype)
            kept_pins = node_pins(node, spec, _signature) if spec is not None else {"exec": True, "inputs": [], "outputs": []}
            node["config"] = {"code": new_code, "based_on": based, "inputs": dict(cfg["inputs"]) if isinstance(cfg.get("inputs"), dict) else {},
                              "settings": {}, "spend": cfg.get("spend") is True, "pins": kept_pins}
        else:
            kept_pins = _pins_of(node, specs)
            node["config"] = {k: v for k, v in cfg.items() if k != "code_sha"}
            node["config"]["code"] = new_code
        node["type"] = "code.js"
        pins = checked["pins"] if checked["ok"] else kept_pins
        node["config"]["pins"] = pins  # what the wire checks below see (the save sets it the same way)
    dropped = _drop_wires(graph, nid, pins, specs) if pins is not None else []
    misfits = check_wires(graph, specs, _signature)
    if misfits:
        return {"ok": False, "error": "Some data wires don't fit: " + "; ".join(misfits[:10]), "wires": misfits}
    if not allow_locked_changes:
        held = locked_changes(wf.get("graph"), graph)
        if held:
            return {"ok": False, "locked": held,
                    "error": "The user locked " + ", ".join(held) + ". Leave locked nodes exactly as they are, "
                    "or ask the user; pass allow_locked_changes=true only after they agree."}
    try:
        saved = _save({"id": str(wf["id"]), "graph": graph})
    except (PermissionError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    after = _node_of(saved, nid) or {}
    after_cfg = after.get("config") if isinstance(after.get("config"), dict) else {}
    custom = after.get("type") == "code.js"
    if custom and is_local(saved) and (person or _chat_allows_everything()):
        approve(str(saved.get("id") or ""), nid, node_sha(after), "person" if person else "chat")
    return {
        "ok": True,
        "code_sha": after_cfg.get("code_sha") if custom else None,
        "pins": _pins_of(after, specs) if after else pins,
        "wires_dropped": dropped,
        "problems": list(after_cfg.get("problems") or []) if custom else [],
        "workflow": saved,
    }


@mcp.tool()
def get_workflow_node_code(workflow_id: str, node_id: str, pretty: bool = False) -> str:
    """The JavaScript one workflow node runs, to read before you change it.

    kind "builtin": a built-in node, shown as the code it would be (its Python step keeps
    running until you edit it). kind "custom": a Custom code node (type code.js); based_on
    and based_on_code are the built-in it was made from. kind "flow": a route node (If,
    Branch, loops, starts, ends, Run workflow), which code can't replace (convertible
    false, reason says why). Also: code_sha (pass it as expected_sha when you edit),
    pins, settings_spec, uses (tools and built-ins it may call), problems, approved
    (false = it won't run on its own until a person runs or reviews it) and last_inputs
    (what it ran with last, for test_workflow_node)."""
    return tool_json(node_code(workflow_id, node_id), pretty=pretty)


@mcp.tool()
def edit_workflow_node_code(
    workflow_id: str,
    node_id: str,
    code: str | None = None,
    edits: list[dict[str, str]] | None = None,
    revert: bool = False,
    expected_sha: str | None = None,
    allow_locked_changes: bool = False,
    pretty: bool = False,
) -> str:
    """Change the JavaScript of one workflow node and save the workflow.

    Read it first with get_workflow_node_code. Prefer edits=[{"old": "exact text now",
    "new": "replacement"}] (each old must match exactly once) over code (the whole module),
    and pass expected_sha (the code_sha you read) so a newer change is never overwritten.
    The first edit of a built-in turns it into a Custom code node in this workflow only;
    revert=true turns it back into the built-in it was made from. Pins come from the
    code's node declaration; data wires whose pins are gone are dropped and listed in
    wires_dropped. Locked nodes are refused like save_workflow. Code you save runs on its
    own only after a person runs or reviews it (or this chat allows everything): test it
    with test_workflow_node first (dry run). The ducky API: workflow_code_api."""
    out = edit_node_code(workflow_id, node_id, code=code, edits=edits, revert=bool(revert), expected_sha=expected_sha,
                         allow_locked_changes=bool(allow_locked_changes), person=False)
    saved = out.pop("workflow", None)
    if out.get("ok") and saved:
        _reveal_graph(str(saved.get("id") or ""), "saved", nodes=[str(node_id)])
    return tool_json(out, pretty=pretty)


@mcp.tool()
async def test_workflow_node(
    workflow_id: str,
    node_id: str,
    code: str | None = None,
    inputs: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    dry_run: bool = True,
    pretty: bool = False,
) -> str:
    """Run one Custom code node now without saving: its saved code, or draft code.

    inputs default to what it ran with last (get_workflow_node_code last_inputs); settings
    override its settings. dry_run (default) records each ducky.tool / ducky.builtin call
    instead of making it, so nothing outside changes. A real run (dry_run=false) of code you
    changed is refused with needs_review=true until the user reviews or runs it (or this
    chat allows everything): tell the user, the hint names the node.
    Returns {ok, outputs, log, tool_calls, error: {message, line, col}, ms}."""
    from backend.automations.runner import run_code_draft

    out = await asyncio.to_thread(run_code_draft, workflow_id, node_id, code=code, inputs=inputs, settings=settings,
                                  dry_run=bool(dry_run), person=False)
    return tool_json(_review_hint(out, workflow_id, (str(node_id),)), pretty=pretty)


def code_api_reference(tools: list[str] | None = None) -> dict[str, Any]:
    """What workflow_code_api answers (the panel's Code tab asks for the same)."""
    from backend.automations import code_api, code_check, codegen

    return {
        "ok": True,
        "dts": code_api.dts(),
        "manifest": code_api.MANIFEST,
        "blank": code_api.BLANK_CODE,
        "declaration_schema": code_check.DECLARATION_SCHEMA,
        "examples": codegen.EXAMPLES,
        "tools_dts": codegen.tools_dts(tools),
    }


@mcp.tool()
def workflow_code_api(tools: list[str] | None = None, pretty: bool = False) -> str:
    """What Custom code can use: the ducky API as TypeScript (dts), each call (manifest),
    the blank node, the node declaration's schema and examples. tools=[names] adds
    tools_dts: the arguments each of those MCP tools takes, for ducky.tool(name, args)."""
    return tool_json(code_api_reference(tools), pretty=pretty)


def _git_out(repo: str, *args: str) -> str:
    import shutil
    import subprocess
    import sys

    if not shutil.which("git"):
        return ""
    try:
        out = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=15,
                             stdin=subprocess.DEVNULL, creationflags=0x08000000 if sys.platform == "win32" else 0)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def _plugin_dir_matches(folder: Any, plugin: str) -> bool:
    """``folder`` is this plugin: it has a plugin.json whose id (or the folder's own name) is ``plugin``."""
    manifest = folder / "plugin.json"
    if not manifest.is_file():
        return False
    if folder.name == plugin:
        return True
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("id") or "") == plugin
    except (OSError, ValueError, AttributeError):
        return False


def resolve_plugin_repo(plugin: str, *, roots: list[str] | None = None, drafts: Any = None) -> dict[str, Any]:
    """Where plugin ``plugin`` (its id or folder name) lives as a git repo: a Ducky project that is
    the plugin, or holds it as ``<id>/`` or ``uefn-plugins/<id>/``, then its ai_plugins draft.
    A folder with git and an origin wins over one without."""
    from pathlib import Path

    pid = (plugin or "").strip().strip("/\\")
    if not pid:
        return {"ok": False, "error": "plugin is required (its id, e.g. island-media-kit)"}
    if roots is None:
        from backend.workspace.lanes import project_roots

        roots = project_roots()
    if drafts is None:
        try:
            from backend.uefn_plugins.store import appdata_ai_plugins_dir

            drafts = appdata_ai_plugins_dir()
        except Exception:
            drafts = None
    candidates: list[tuple[Path, str]] = []
    for root in roots:
        base = Path(root)
        for folder in (base, base / pid, base / "uefn-plugins" / pid):
            candidates.append((folder, "project"))
        for folder in sorted(base.glob("uefn-plugins/*")) if (base / "uefn-plugins").is_dir() else ():
            candidates.append((folder, "project"))
    if drafts is not None:
        candidates.append((Path(drafts) / pid, "ai_plugins"))
    seen: set[str] = set()
    found: list[dict[str, Any]] = []
    for folder, source in candidates:
        key = str(folder.resolve()).lower() if folder.exists() else ""
        if not key or key in seen or not _plugin_dir_matches(folder, pid):
            continue
        seen.add(key)
        top = _git_out(str(folder), "rev-parse", "--show-toplevel")
        git = bool(top) and Path(top).resolve() == folder.resolve()
        origin = _git_out(str(folder), "remote", "get-url", "origin") if git else ""
        branch = _git_out(str(folder), "rev-parse", "--abbrev-ref", "HEAD") if git else ""
        found.append({"repo": folder.as_posix(), "source": source, "git": git, "origin": origin, "branch": branch})
    if not found:
        where = ", ".join(roots) or "no projects"
        return {"ok": False, "error": f"No repo for plugin '{pid}': no folder with its plugin.json in your Ducky projects "
                                      f"({where}) or in ai_plugins/{pid}. Clone it into a project or add its folder as a project."}
    found.sort(key=lambda r: (not (r["git"] and r["origin"]), not r["git"]))
    best = found[0]
    if not best["git"]:
        return {"ok": False, "error": f"Plugin '{pid}' is at {best['repo']} but that folder isn't a git repo. "
                                      "git init it and add its origin, then run again.", "repo": best["repo"]}
    label = f"(git, origin {best['branch']})" if best["origin"] else "(git, no origin)"
    return {"ok": True, **best, "summary": f"repo: {best['repo']} {label}", "others": [r["repo"] for r in found[1:]]}


@mcp.tool()
def ducky_plugin_find_repo(plugin: str, pretty: bool = False) -> str:
    """Find a desktop plugin's git repo by its id or folder name: any Ducky project whose
    plugin.json has that id (the project itself, <id>/ or uefn-plugins/<id>/ inside it) or its
    ai_plugins draft. A repo with an origin wins. Returns {ok, repo, source, git, origin, branch,
    summary} or {ok: false, error} when no such folder exists."""
    return tool_json(resolve_plugin_repo(plugin), pretty=pretty)
