"""Walk an automation graph from a starter and execute builtin / plugin nodes."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from backend.automations import catalog, plugin
from backend.automations.store import (
    KIND_PIPELINE,
    append_run,
    get_automation,
    normalize_kind,
)

_log = logging.getLogger("automations")
_MAX_STEPS = 256
_FOREACH_CAP = 50
# ponytail: wait sleeps the runner thread; 120s ceiling. Per-node async if graphs nest waits.
_WAIT_CAP_S = 120.0
_AGENT_WAIT_CAP_S = 900.0
def _announce_run(wf: dict[str, Any], *, phase: str, detail: str = "", run_id: str = "") -> None:
    """Header activity tray — running/finished graphs. Never blocks the walk."""
    wid = str(wf.get("id") or "").strip()
    if not wid:
        return
    kind = normalize_kind(wf.get("kind"))
    title = str(wf.get("name") or wid)
    payload = {
        "type": "background_job",
        "id": f"graph:{wid}",
        "source": kind,
        "title": title,
        "detail": detail,
        "phase": phase,
    }
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event(payload)
        if run_id and phase in ("done", "error"):
            push_ui_event({**payload, "id": f"graph-run:{wid}:{run_id}"})
            push_ui_event({"type": "graphs_changed"})
    except Exception:
        pass


_ACTION_TYPES = frozenset(
    {
        "ducky.prompt",
        "ducky.spawn",
        "flow.wait",
        "flow.foreach",
        "flow.branch",
        "tool.call",
        "pipeline.agent",
        "pipeline.finish",
    }
)


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
    starts = _start_ids(nodes, trigger_id=trigger_id, starter_id=starter_id, payload=payload or {})
    if not starts:
        return {"ok": False, "error": "no starter node", "steps": [], "id": wf["id"]}
    ctx: dict[str, Any] = dict(payload or {})
    _prepare_run_ctx(ctx, wf)
    ident_token = _bind_hub_identity(ctx)
    started = time.time()
    ok = False
    error = ""
    steps: list[Any] = []
    try:
        _announce_run(wf, phase="working", detail="Running")
        steps, ok, error, _seen = _walk(nodes, edges, ctx, starts)
    finally:
        if ident_token is not None:
            from backend.workspace import identity

            identity.reset(ident_token)
        _announce_run(
            wf,
            phase="done" if ok else "error",
            detail=(error or "Finished") if ok else (error or "Failed"),
            run_id=str(int(started)),
        )
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
    return {
        "ok": ok,
        "error": error,
        "id": wf["id"],
        "steps": steps,
        "conv_id": ctx.get("conv_id"),
        "files": ctx.get("files") or [],
        "text": ctx.get("text") or ctx.get("assistant_text") or "",
    }


def run_pipeline(
    pipeline_id: str,
    *,
    prompt: str = "",
    files: list[Any] | None = None,
    caller_conv_id: str = "",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    wf = get_automation(pipeline_id)
    if wf is None or normalize_kind(wf.get("kind")) != KIND_PIPELINE:
        return {"ok": False, "error": "pipeline not found", "steps": []}
    body = dict(payload or {})
    caller = (caller_conv_id or str(body.get("caller_conv_id") or "")).strip()
    if not caller:
        try:
            from backend.workspace.identity import current

            bound = current()
        except Exception:
            bound = None
        if bound and bound.conv_id:
            caller = bound.conv_id
    if prompt:
        body["prompt"] = prompt
    if files is not None:
        body["files"] = files
    body["caller_conv_id"] = caller
    return run_automation(pipeline_id, trigger_id="start.chat", payload=body)


def emit_automation(trigger_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    tid = (trigger_id or "").strip()
    if not tid:
        return {"ok": False, "error": "trigger_id required", "runs": []}
    from backend.automations.store import _all

    runs: list[dict[str, Any]] = []
    for wf in _all():
        if not wf.get("enabled"):
            continue
        if normalize_kind(wf.get("kind")) == KIND_PIPELINE:
            continue
        nodes = (wf.get("graph") or {}).get("nodes") or []
        if any(_node_matches_trigger(n, tid, payload) for n in nodes if isinstance(n, dict)):
            runs.append(run_automation(str(wf["id"]), trigger_id=tid, payload=payload or {}))
    return {"ok": True, "trigger_id": tid, "runs": runs}


def _node_matches_trigger(node: dict[str, Any], trigger_id: str, payload: dict[str, Any] | None = None) -> bool:
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    if str(node.get("type") or "") != trigger_id and str(cfg.get("trigger_id") or "") != trigger_id:
        return False
    if not payload:
        return True
    for key, raw in cfg.items():
        if key == "trigger_id" or raw in ("", None):
            continue
        if key in payload and str(payload[key]) != str(raw):
            return False
    return True


def _start_ids(
    nodes: dict[str, dict[str, Any]],
    *,
    trigger_id: str,
    starter_id: str,
    payload: dict[str, Any] | None = None,
) -> list[str]:
    if starter_id and starter_id in nodes:
        return [starter_id]
    starters = catalog.starter_types()
    if trigger_id:
        hits = [nid for nid, n in nodes.items() if _node_matches_trigger(n, trigger_id, payload)]
        if hits:
            return hits
    manuals = [nid for nid, n in nodes.items() if n.get("type") == "start.manual"]
    if manuals:
        return manuals
    any_start = [nid for nid, n in nodes.items() if n.get("type") in starters]
    return any_start


def _next_ids(source: str, edges: list[dict[str, Any]], kind: str) -> list[str]:
    if kind in ("true", "false", "each", "done"):
        specific = [
            str(e.get("target"))
            for e in edges
            if str(e.get("source")) == source and str(e.get("kind") or "") == kind
        ]
        if specific:
            return specific
        if kind == "done":
            return []
    return [
        str(e.get("target"))
        for e in edges
        if str(e.get("source")) == source and str(e.get("kind") or "main") == "main"
    ]


def _foreach_items(ctx: dict[str, Any], field: str) -> list[Any]:
    raw = _payload_get(ctx, field or "cards")
    if raw is None:
        return []
    if isinstance(raw, list):
        return list(raw)[:_FOREACH_CAP]
    return [raw]


def _apply_foreach_item(ctx: dict[str, Any], item: Any) -> None:
    ctx["item"] = item
    if isinstance(item, dict):
        ctx["card"] = item
        cid = item.get("id") or item.get("card_id")
        if cid:
            ctx["card_id"] = cid
        art = item.get("art") if isinstance(item.get("art"), dict) else {}
        prompt = item.get("prompt") or art.get("prompt") or item.get("name") or item.get("description") or ""
        if prompt:
            ctx["prompt"] = str(prompt)
        return
    if item not in (None, ""):
        ctx["prompt"] = str(item)


def _walk(
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    ctx: dict[str, Any],
    start_ids: list[str],
    *,
    stop_at: str = "",
    seen: int = 0,
) -> tuple[list[dict[str, Any]], bool, str, int]:
    steps: list[dict[str, Any]] = []
    queue = list(start_ids)
    visited: set[str] = set()
    ok = True
    error = ""
    while queue and seen < _MAX_STEPS:
        nid = queue.pop(0)
        if not nid or nid == stop_at:
            continue
        node = nodes.get(nid)
        if node is None or nid in visited:
            continue
        visited.add(nid)
        seen += 1
        ntype = str(node.get("type") or "")
        if ntype == "flow.foreach":
            cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
            field = str(cfg.get("field") or "cards")
            items = _foreach_items(ctx, field)
            steps.append(
                {
                    "ok": True,
                    "id": nid,
                    "type": ntype,
                    "label": str(node.get("label") or ntype),
                    "result": {"count": len(items), "field": field},
                }
            )
            each_ids = _next_ids(nid, edges, "each")
            for item in items:
                _apply_foreach_item(ctx, item)
                nested, n_ok, n_err, seen = _walk(
                    nodes, edges, ctx, each_ids, stop_at=nid, seen=seen
                )
                steps.extend(nested)
                if not n_ok:
                    return steps, False, n_err, seen
            queue.extend(_next_ids(nid, edges, "done"))
            continue
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
    if seen >= _MAX_STEPS and queue:
        return steps, False, "step budget exceeded", seen
    return steps, ok, error, seen


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
        if ntype == "flow.foreach":
            items = _foreach_items(payload, str(cfg.get("field") or "cards"))
            return {
                "ok": True,
                "id": node.get("id"),
                "type": ntype,
                "label": label,
                "result": {"count": len(items), "field": str(cfg.get("field") or "cards")},
            }
        if ntype == "flow.branch":
            return {**_branch_node(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "tool.call":
            return {**_call_tool(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "pipeline.agent":
            return {**_pipeline_agent(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "pipeline.finish":
            return {**_pipeline_finish(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        handler = plugin.get_handler(ntype)
        if handler is None:
            # Starters / plugin triggers need no handler — just pass the payload on.
            if ntype.startswith("start.") or ntype not in _ACTION_TYPES:
                return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "result": dict(payload)}
            return {"ok": False, "id": node.get("id"), "type": ntype, "label": label, "error": f"no handler for {ntype}"}
        result = handler(_plugin_ctx(cfg, payload, node))
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


def _run_message_and_wait(
    conv_id: str,
    text: str,
    mode: str,
    model: str,
    *,
    timeout_sec: float,
    parent: str = "",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from frontend.ui_web.agent_modes import run_message_and_wait

    return dict(
        run_message_and_wait(
            conv_id,
            text,
            mode,
            model,
            timeout_sec=timeout_sec,
            parent=parent,
            attachments=attachments,
        )
        or {}
    )


def _prepare_run_ctx(ctx: dict[str, Any], wf: dict[str, Any]) -> None:
    kind = normalize_kind(wf.get("kind"))
    ctx.setdefault("_workflow_kind", kind)
    if kind != KIND_PIPELINE:
        return
    caller = str(ctx.get("caller_conv_id") or "").strip()
    if caller and not ctx.get("artifact_dir"):
        from backend.automations.artifacts import caller_run_dir

        run_id = str(ctx.get("pipeline_run_id") or uuid.uuid4())
        ctx["pipeline_run_id"] = run_id
        ctx["artifact_dir"] = str(caller_run_dir(caller, run_id))
    ctx.setdefault("files", [])
    if _pipeline_needs_group(wf):
        _ensure_pipeline_group(ctx, wf)


def _bind_hub_identity(ctx: dict[str, Any]):
    hub = str(ctx.get("group_id") or "").strip()
    if not hub:
        return None
    from backend.workspace.identity import RunContext, bind

    return bind(
        RunContext(
            run_id=str(ctx.get("pipeline_run_id") or ""),
            conv_id=hub,
            group_id=hub,
            leader_conv_id=str(ctx.get("caller_conv_id") or ""),
        )
    )


def _caller_group_home(caller: str) -> tuple[str, bool]:
    """Return (folder_id, already_grouped)."""
    if not caller:
        return "", False
    from frontend.ui_web.group_orchestrator import is_group_conversation
    from frontend.ui_web.project_chats import load_conversation

    conv = load_conversation(caller)
    if conv is None:
        return "", False
    folder = str(getattr(conv, "folder_id", "") or "")
    if is_group_conversation(conv):
        return folder, True
    parent_id = str(getattr(conv, "parent_conv_id", "") or "").strip()
    if parent_id:
        parent = load_conversation(parent_id)
        if parent is not None and is_group_conversation(parent):
            return str(getattr(parent, "folder_id", "") or folder), True
    return folder, False


def _pipeline_needs_group(wf: dict[str, Any]) -> bool:
    """Only swarm tiles need a hub. Image/device graphs must not kidnap the chat."""
    types = {str(n.get("type") or "") for n in (wf.get("graph") or {}).get("nodes") or [] if isinstance(n, dict)}
    return bool(types & {"pipeline.agent", "ducky.spawn"})


def _ensure_pipeline_group(ctx: dict[str, Any], wf: dict[str, Any]) -> None:
    if str(ctx.get("group_id") or "").strip():
        return
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    caller = str(ctx.get("caller_conv_id") or "").strip()
    parent_folder, _already_grouped = _caller_group_home(caller)
    try:
        from backend.tools.panel.ducky_panel import _panel_api

        created = _panel_api().group_create(
            name=str(wf.get("name") or "Pipeline"),
            folder_id=parent_folder,
            open_tab=False,
        )
    except Exception as exc:
        _log.warning("pipeline group_create failed: %s", exc)
        return
    if not created.get("ok"):
        _log.warning("pipeline group_create failed: %s", created.get("error"))
        return
    hub_id = str(created.get("id") or "").strip()
    ctx["group_id"] = hub_id
    ctx["group_folder_id"] = str(created.get("folder_id") or "")
    if not hub_id:
        return
    # ponytail: leader pointer only — do not move the caller's chat into the hub.
    if caller:
        hub = load_conversation(hub_id)
        if hub is not None:
            hub.leader_conv_id = caller
            save_conversation(hub)


def _plugin_ctx(cfg: dict[str, Any], payload: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    return {
        "config": cfg,
        "payload": payload,
        "node": node,
        "kind": str(payload.get("_workflow_kind") or "automation"),
        "files": payload.get("files") or [],
        "artifact_dir": str(payload.get("artifact_dir") or ""),
    }


def _resolve_profile(ducky: str) -> dict[str, Any] | None:
    from frontend.agent_profiles import get_agent_profile, list_agent_profiles_available

    key = (ducky or "").strip()
    if not key:
        return None
    profile = get_agent_profile(key)
    if profile:
        return profile
    low = key.lower()
    matches = [
        row
        for row in list_agent_profiles_available()
        if str(row.get("name") or "").strip().lower() == low
    ]
    return matches[0] if len(matches) == 1 else None


def _agent_spawn_kwargs(profile: dict[str, Any]) -> dict[str, Any]:
    try:
        from backend.tools.panel.ducky_panel import _profile_spawn_kwargs

        return dict(_profile_spawn_kwargs(profile))
    except Exception:
        return {
            "ducky_style": str(profile.get("ducky_style") or "classic"),
            "ducky_name": str(profile.get("name") or ""),
            "profile_id": str(profile.get("id") or ""),
            "ducky_personality": str(profile.get("ducky_personality") or ""),
        }


def _pipeline_agent(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    from backend.automations.artifacts import chat_dir, copy_files_into, list_files

    ducky = str(cfg.get("ducky") or cfg.get("profile_id") or payload.get("ducky") or "").strip()
    if not ducky:
        return {"ok": False, "error": "ducky profile required"}
    profile = _resolve_profile(ducky)
    if profile is None:
        return {"ok": False, "error": f"unknown ducky: {ducky}"}
    kwargs = _agent_spawn_kwargs(profile)
    seat = _seat_agent_cluster(cfg, payload, profile, kwargs)
    if not seat.get("ok"):
        return seat
    conv_id = str(seat.get("conv_id") or "")
    if not conv_id:
        return {"ok": False, "error": "group_invite did not return a member"}
    worker_dir = chat_dir(conv_id)
    incoming = payload.get("files") or []
    copy_files_into(incoming, worker_dir)
    prompt = str(cfg.get("prompt") or payload.get("prompt") or "")
    timeout = min(max(float(cfg.get("timeout_sec") or 180.0), 1.0), _AGENT_WAIT_CAP_S)
    caller = str(payload.get("caller_conv_id") or "").strip()
    wait = _run_message_and_wait(
        conv_id,
        prompt,
        str(cfg.get("mode") or "agent"),
        str(cfg.get("model") or kwargs.get("model") or ""),
        timeout_sec=timeout,
        parent=caller,
        attachments=_files_as_attachments(incoming),
    )
    if str(wait.get("status") or "") != "done":
        return {
            "ok": False,
            "error": str(wait.get("error") or wait.get("status") or "agent failed"),
            "result": {"conv_id": conv_id, **wait},
        }
    files = list_files(worker_dir)
    dest = str(payload.get("artifact_dir") or "")
    if dest:
        from pathlib import Path

        files = copy_files_into(files, Path(dest))
    text = str(wait.get("assistant_text") or "")
    return {
        "ok": True,
        "result": {
            "conv_id": conv_id,
            "assistant_text": text,
            "text": text,
            "files": files,
            "agent_group_id": seat.get("group_id") or "",
            "agent_group_folder_id": seat.get("group_folder_id") or "",
        },
    }


def _seat_agent_cluster(
    cfg: dict[str, Any],
    payload: dict[str, Any],
    profile: dict[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    title = str(cfg.get("title") or kwargs.get("ducky_name") or profile.get("name") or "Agent")
    parent_folder = str(payload.get("group_folder_id") or "")
    try:
        from backend.tools.panel.ducky_panel import _panel_api

        api = _panel_api()
        created = api.group_create(name=title, folder_id=parent_folder, open_tab=False)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not created.get("ok"):
        return {"ok": False, "error": str(created.get("error") or "group_create failed")}
    nest_id = str(created.get("id") or "").strip()
    pid = str(profile.get("id") or "").strip()
    try:
        invited = api.group_invite(nest_id, pid, model=str(cfg.get("model") or kwargs.get("model") or ""))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not invited.get("ok"):
        return {"ok": False, "error": str(invited.get("error") or "group_invite failed")}
    member = invited.get("member") if isinstance(invited.get("member"), dict) else {}
    return {
        "ok": True,
        "conv_id": str(member.get("member_conv_id") or ""),
        "group_id": nest_id,
        "group_folder_id": str(created.get("folder_id") or ""),
    }


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _files_as_attachments(files: Any) -> list[dict[str, Any]]:
    import base64
    from pathlib import Path

    out: list[dict[str, Any]] = []
    for raw in files or []:
        src = Path(raw["path"] if isinstance(raw, dict) else raw)
        if src.suffix.lower() not in _IMAGE_EXTS or not src.is_file():
            continue
        mime = "image/jpeg" if src.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        if src.suffix.lower() == ".gif":
            mime = "image/gif"
        elif src.suffix.lower() == ".webp":
            mime = "image/webp"
        out.append(
            {
                "kind": "image",
                "name": src.name,
                "mime": mime,
                "data_base64": base64.b64encode(src.read_bytes()).decode("ascii"),
            }
        )
    return out


def _pipeline_finish(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    caller = str(cfg.get("caller_conv_id") or payload.get("caller_conv_id") or "").strip()
    if not caller:
        return {"ok": True, "result": {"posted": False, "reason": "no caller"}}
    from frontend.ui_web.project_chats import append_message, load_conversation

    conv = load_conversation(caller)
    if conv is None:
        return {"ok": False, "error": f"caller chat not found: {caller}"}
    text = str(cfg.get("message") or payload.get("text") or payload.get("assistant_text") or "Pipeline finished.")
    files = payload.get("files") or []
    attachments = _files_as_attachments(files)
    msg: dict[str, Any] = {"role": "assistant", "content": text, "text": text, "ts": time.time()}
    if attachments:
        msg["attachments"] = attachments
    append_message(conv, msg)
    return {"ok": True, "result": {"posted": True, "caller_conv_id": caller, "text": text, "files": files}}


def _branch_node(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(cfg.get("mode") or "data").strip().lower() or "data"
    if mode == "agent":
        judge = _pipeline_agent(cfg, payload)
        if not judge.get("ok"):
            return {**judge, "branch": False}
        text = str((judge.get("result") or {}).get("text") or "")
        branch = _agent_decides(text, str(cfg.get("equals") or ""))
        return {
            "ok": True,
            "branch": branch,
            "result": {**(judge.get("result") or {}), "branch": branch, "judge_text": text},
        }
    branch = _eval_branch(cfg, payload)
    return {"ok": True, "branch": branch, "result": {"branch": branch}}


def _payload_get(payload: dict[str, Any], field: str) -> Any:
    if not field:
        return payload
    cur: Any = payload
    for part in field.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _eval_branch(cfg: dict[str, Any], payload: dict[str, Any]) -> bool:
    field = str(cfg.get("field") or "").strip()
    value = _payload_get(payload, field) if field else payload
    op = str(cfg.get("op") or "").strip().lower()
    if op == "exists":
        return value not in (None, "", [], {})
    if op == "contains" or cfg.get("contains"):
        return _value_contains(value, str(cfg.get("contains") or cfg.get("equals") or ""))
    if "equals" in cfg and str(cfg.get("equals") or "") != "":
        return str(value) == str(cfg.get("equals"))
    return bool(value)


def _value_contains(value: Any, needle: str) -> bool:
    if needle == "":
        return bool(value)
    if isinstance(value, list):
        return any(_value_contains(item, needle) for item in value)
    if isinstance(value, dict):
        blob = " ".join(str(v) for v in value.values())
        return needle in blob or needle in str(value)
    return needle in str(value or "")


_YES = frozenset({"yes", "true", "approve", "ok", "pass"})
_NO = frozenset({"no", "false", "reject", "fail"})


def _agent_decides(text: str, equals: str) -> bool:
    low = (text or "").strip().lower()
    if equals:
        return equals.lower() in low or low == equals.lower()
    for token in _NO:
        if token in low.split() or f" {token} " in f" {low} ":
            return False
    for token in _YES:
        if token in low.split() or f" {token} " in f" {low} ":
            return True
    return bool(low)


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
