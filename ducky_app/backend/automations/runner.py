"""Walk an automation graph from a starter and execute builtin / plugin nodes."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from backend.automations import catalog, plugin
from backend.automations.store import append_run, get_automation

_log = logging.getLogger("automations")
_MAX_STEPS = 64
# ponytail: wait sleeps the runner thread; 120s ceiling. Per-node async if graphs nest waits.
_WAIT_CAP_S = 120.0
_ACTION_TYPES = frozenset({"ducky.prompt", "ducky.spawn", "flow.wait", "flow.branch", "tool.call"})


def run_automation(
    workflow_id: str,
    *,
    trigger_id: str = "",
    payload: dict[str, Any] | None = None,
    starter_id: str = "",
) -> dict[str, Any]:
    wf = get_automation(workflow_id)
    if wf is None:
        return {"ok": False, "error": "automation not found", "steps": []}
    graph = wf.get("graph") or {}
    nodes = {str(n.get("id")): n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    starts = _start_ids(nodes, trigger_id=trigger_id, starter_id=starter_id)
    if not starts:
        return {"ok": False, "error": "no starter node", "steps": [], "id": wf["id"]}
    ctx: dict[str, Any] = dict(payload or {})
    steps: list[dict[str, Any]] = []
    started = time.time()
    ok = True
    error = ""
    seen = 0
    queue = list(starts)
    visited: set[str] = set()
    while queue and seen < _MAX_STEPS:
        nid = queue.pop(0)
        node = nodes.get(nid)
        if node is None or nid in visited:
            continue
        visited.add(nid)
        seen += 1
        step = _exec_node(node, ctx)
        steps.append(step)
        if not step.get("ok", True):
            ok = False
            error = str(step.get("error") or "step failed")
            break
        if step.get("result") and isinstance(step["result"], dict):
            ctx.update({k: v for k, v in step["result"].items() if k not in ("ok",)})
        kind = "true" if step.get("branch") else "false" if "branch" in step else "main"
        queue.extend(_next_ids(nid, edges, kind))
    ended = time.time()
    run = {
        "started": started,
        "ended": ended,
        "ok": ok,
        "error": error,
        "trigger_id": trigger_id,
        "steps": steps,
    }
    append_run(wf["id"], run)
    return {"ok": ok, "error": error, "id": wf["id"], "steps": steps, "conv_id": ctx.get("conv_id")}


def emit_automation(trigger_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    tid = (trigger_id or "").strip()
    if not tid:
        return {"ok": False, "error": "trigger_id required", "runs": []}
    from backend.automations.store import _all

    runs: list[dict[str, Any]] = []
    for wf in _all():
        if not wf.get("enabled"):
            continue
        nodes = (wf.get("graph") or {}).get("nodes") or []
        if any(_node_matches_trigger(n, tid) for n in nodes if isinstance(n, dict)):
            runs.append(run_automation(str(wf["id"]), trigger_id=tid, payload=payload or {}))
    return {"ok": True, "trigger_id": tid, "runs": runs}


def _node_matches_trigger(node: dict[str, Any], trigger_id: str) -> bool:
    if str(node.get("type") or "") == trigger_id:
        return True
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    return str(cfg.get("trigger_id") or "") == trigger_id


def _start_ids(nodes: dict[str, dict[str, Any]], *, trigger_id: str, starter_id: str) -> list[str]:
    if starter_id and starter_id in nodes:
        return [starter_id]
    starters = catalog.starter_types()
    if trigger_id:
        hits = [nid for nid, n in nodes.items() if _node_matches_trigger(n, trigger_id)]
        if hits:
            return hits
    manuals = [nid for nid, n in nodes.items() if n.get("type") == "start.manual"]
    if manuals:
        return manuals
    any_start = [nid for nid, n in nodes.items() if n.get("type") in starters]
    return any_start


def _next_ids(source: str, edges: list[dict[str, Any]], kind: str) -> list[str]:
    if kind in ("true", "false"):
        specific = [
            str(e.get("target"))
            for e in edges
            if str(e.get("source")) == source and str(e.get("kind") or "") == kind
        ]
        if specific:
            return specific
    return [
        str(e.get("target"))
        for e in edges
        if str(e.get("source")) == source and str(e.get("kind") or "main") == "main"
    ]


def _exec_node(node: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    ntype = str(node.get("type") or "")
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    label = str(node.get("label") or ntype)
    try:
        if ntype == "ducky.prompt":
            return {**_prompt_ducky(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "ducky.spawn":
            return {**_spawn_ducky(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "flow.wait":
            secs = min(max(float(cfg.get("seconds") or 0), 0.0), _WAIT_CAP_S)
            if secs:
                time.sleep(secs)
            return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "result": {"waited": secs}}
        if ntype == "flow.branch":
            branch = _eval_branch(cfg, payload)
            return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "branch": branch, "result": {"branch": branch}}
        if ntype == "tool.call":
            return {**_call_tool(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        handler = plugin.get_handler(ntype)
        if handler is None:
            # Starters / plugin triggers need no handler — just pass the payload on.
            if ntype.startswith("start.") or ntype not in _ACTION_TYPES:
                return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "result": dict(payload)}
            return {"ok": False, "id": node.get("id"), "type": ntype, "label": label, "error": f"no handler for {ntype}"}
        result = handler({"config": cfg, "payload": payload, "node": node})
        if isinstance(result, dict) and result.get("ok") is False:
            return {"ok": False, "id": node.get("id"), "type": ntype, "label": label, "error": result.get("error") or "plugin node failed", "result": result}
        return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "result": result}
    except Exception as exc:
        _log.exception("automation node %s failed", ntype)
        return {"ok": False, "id": node.get("id"), "type": ntype, "label": label, "error": str(exc)}


def _prompt_ducky(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    conv_id = str(cfg.get("conv_id") or cfg.get("chat_id") or payload.get("conv_id") or "").strip()
    prompt = str(cfg.get("prompt") or payload.get("prompt") or "")
    if not conv_id:
        return {"ok": False, "error": "conv_id required"}
    run_id = _run_message(conv_id, prompt, str(cfg.get("mode") or "agent"), str(cfg.get("model") or ""))
    return {"ok": True, "result": {"conv_id": conv_id, "run_id": run_id}}


def _spawn_ducky(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    from frontend.settings import PanelSettings
    from frontend.ui_web.project_chats import create_conversation

    # Never set_project_root — spawn stays on the island the panel already has open.
    settings = PanelSettings.load()
    conv = create_conversation(
        settings,
        "",
        title=str(cfg.get("title") or payload.get("title") or "Automation"),
        ducky_style=str(cfg.get("ducky_style") or "classic"),
    )
    prompt = str(cfg.get("prompt") or payload.get("prompt") or "")
    run_id = ""
    if prompt:
        run_id = _run_message(conv.id, prompt, str(cfg.get("mode") or "agent"), str(cfg.get("model") or ""))
    return {"ok": True, "result": {"conv_id": conv.id, "run_id": run_id}}


def _run_message(conv_id: str, text: str, mode: str, model: str) -> str:
    from frontend.ui_web.agent_modes import run_message

    return str(run_message(conv_id, text, mode, model) or "")


def _eval_branch(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    field = str(cfg.get("field") or "").strip()
    if not field:
        return bool(payload)
    value = payload.get(field)
    if "equals" in cfg and str(cfg.get("equals") or "") != "":
        return str(value) == str(cfg.get("equals"))
    return bool(value)


def _call_tool(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    import inspect

    name = str(cfg.get("name") or payload.get("tool") or "").strip()
    if not name:
        return {"ok": False, "error": "tool name required"}
    raw = cfg.get("arguments")
    if raw is None and cfg.get("arguments_json"):
        try:
            raw = json.loads(str(cfg.get("arguments_json") or "{}"))
        except ValueError as exc:
            return {"ok": False, "error": f"bad arguments_json: {exc}"}
    if raw is None:
        raw = payload.get("arguments") or {}
    if not isinstance(raw, dict):
        return {"ok": False, "error": "arguments must be an object"}
    from backend.server import mcp

    tool = mcp._tool_manager.get_tool(name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool: {name}"}
    fn = getattr(tool, "fn", None)
    if fn is None:
        return {"ok": False, "error": f"tool has no fn: {name}"}
    result = fn(**raw)
    if inspect.isawaitable(result):
        return {"ok": False, "error": "async tool — register a sync plugin node instead"}
    return {"ok": True, "result": {"tool": name, "output": result}}
