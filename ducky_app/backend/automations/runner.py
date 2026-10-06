"""Walk a workflow graph from a starter and execute builtin / plugin nodes."""

from __future__ import annotations

import contextvars
import json
import logging
import math
import re
import threading
import time
import uuid
from contextvars import ContextVar
from typing import Any, Callable

from pathlib import Path

from backend.automations import catalog, glbops, imageops, listops, media, notify, pdfops, plugin, servers
from backend.automations.expr import ExprError, as_number, as_text, evaluate, truthy
from backend.automations.files import file_ref, is_file_ref, kind_of, run_folder, with_url
from backend.automations.pins import DATA_KIND, node_pins
from backend.automations.store import LOCAL, all_workflows, append_run, get_workflow, runs_here, signature_of

_log = logging.getLogger("automations")
_MAX_STEPS = 256
_FOREACH_CAP = 50
_REPEAT_CAP = 10
# ponytail: wait sleeps the runner thread; 120s ceiling. Per-node async if graphs nest waits.
_WAIT_CAP_S = 120.0
_AGENT_WAIT_CAP_S = 900.0
# Run workflow nodes: how deep one workflow may run another, and what a called
# workflow inherits from its caller besides the inputs it is given.
_CALL_DEPTH_CAP = 8
_CALL_STACK: ContextVar[tuple[str, ...]] = ContextVar("workflow_call_stack", default=())
_RUN_PLUMBING = ("caller_conv_id", "artifact_dir", "pipeline_run_id", "group_id", "group_folder_id", "files")
# Payload keys the panel sets when a person pressed play (or approved spending). Read
# once, when the outermost run starts; a run's fields never carry them further.
_PERSON_KEYS = ("_person_started", "_spend_approved")
_RETURN_KEY = "_returned"
# Shared runs hand every field back except where this run reports and its own returns.
_NOT_SHARED_BACK = frozenset({_RETURN_KEY, "caller_conv_id", "artifact_dir", "pipeline_run_id", "group_id", "group_folder_id", *_PERSON_KEYS})
# Who started the run, fixed when the outermost run starts and inherited by the
# workflows it runs: a person pressed play (or Test), a person approved spending, and
# the chat whose ducky called it. Nothing a step makes can change them.
_PERSON: ContextVar[bool] = ContextVar("workflow_person_started", default=False)
_SPEND: ContextVar[bool] = ContextVar("workflow_spend_approved", default=False)
_RUN_CHAT: ContextVar[str] = ContextVar("workflow_run_chat", default="")
# A node that Custom code runs through ducky.builtin: what it is given was made at run
# time, so nothing in it counts as typed into the saved workflow.
_FROM_CODE: ContextVar[bool] = ContextVar("workflow_node_from_code", default=False)
_PLACEHOLDER = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")
_END_TYPES = frozenset({"pipeline.finish", "flow.end", "flow.output"})
# Stop: every run of a workflow (and the workflows it calls) shares one event; the
# walk checks it between steps and a step in progress is left to finish on its own.
_CANCEL: ContextVar[threading.Event | None] = ContextVar("workflow_cancel", default=None)
_ACTIVE: dict[str, set[threading.Event]] = {}
_ACTIVE_LOCK = threading.Lock()
# Live view: the editor lights up the step running now and the wire it came along.
_LIVE: ContextVar[tuple[str, str] | None] = ContextVar("workflow_live", default=None)
_OUTPUT_PARENTS: ContextVar[tuple[tuple[str, str, str], ...]] = ContextVar("workflow_output_parents", default=())
# A command typed into a saved Local workflow runs without the Allow/Deny pop-up:
# saving it there was the approval. One filled in while the run goes still asks.
_TYPED_COMMAND: ContextVar[str] = ContextVar("workflow_typed_command", default="")
STOPPED = "Stopped"


class _DataError(Exception):
    """A value a step needed could not be made; the step fails with this message."""


def _signature(workflow_id: str) -> dict[str, Any] | None:
    wf = get_workflow(workflow_id) if workflow_id else None
    return signature_of((wf.get("graph") or {}).get("nodes") or []) if wf else None


class _Dataflow:
    """Values on data wires for one run of one graph.

    Step nodes (white pins) run in wire order; before one runs its inputs are pulled
    from the wires into it. A data node (no white pins) runs the first time one of its
    values is pulled, then its outputs are reused for the rest of the run."""

    def __init__(self, nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]], specs: dict[str, dict[str, Any]]):
        self.nodes = nodes
        self.specs = specs
        self.feeds: dict[str, dict[str, tuple[str, str]]] = {}
        self.consumed: set[str] = set()
        for e in edges:
            if str(e.get("kind") or "") == DATA_KIND:
                target, source = str(e.get("target")), str(e.get("source"))
                self.feeds.setdefault(target, {})[str(e.get("target_pin"))] = (source, str(e.get("source_pin")))
                self.consumed.add(source)
        self.outputs: dict[str, dict[str, Any]] = {}
        self.busy: set[str] = set()
        self.steps: list[dict[str, Any]] = []
        self.order: list[dict[str, Any]] = []  # every step of the run, in the order it ran
        self.warnings: list[str] = []
        # Run one node: a step it needs that never ran (a Card brief before a Preview) runs now.
        self.run_missing_steps = False
        self._pins: dict[str, dict[str, Any]] = {}
        self._signatures: dict[str, dict[str, Any] | None] = {}

    def pins(self, nid: str) -> dict[str, Any]:
        if nid not in self._pins:
            node = self.nodes.get(nid) or {}
            self._pins[nid] = node_pins(node, self.specs.get(str(node.get("type") or "")), self.signature)
        return self._pins[nid]

    def signature(self, workflow_id: str) -> dict[str, Any] | None:
        if workflow_id not in self._signatures:
            self._signatures[workflow_id] = _signature(workflow_id)
        return self._signatures[workflow_id]

    def is_step(self, nid: str) -> bool:
        return bool(self.pins(nid)["exec"])

    @property
    def data_nodes(self) -> list[str]:
        return [nid for nid in self.nodes if not self.is_step(nid)]

    def _name(self, nid: str) -> str:
        node = self.nodes.get(nid) or {}
        return str(node.get("label") or node.get("type") or nid)

    def resolve(self, node: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        """The node's input values: from its wires, else what is set in its details."""
        nid = str(node.get("id"))
        set_here = (node.get("config") or {}).get("inputs") if isinstance((node.get("config") or {}).get("inputs"), dict) else {}
        values: dict[str, Any] = {}
        for pin in self.pins(nid)["inputs"]:
            wire = self.feeds.get(nid, {}).get(pin["id"])
            if wire:
                values[pin["id"]] = self.pull(wire[0], wire[1], ctx, for_node=nid)
            elif pin["id"] in set_here:
                values[pin["id"]] = _template(set_here[pin["id"]], ctx)
            elif "default" in pin:
                values[pin["id"]] = pin["default"]
        return values

    def pull(self, nid: str, pin: str, ctx: dict[str, Any], *, for_node: str = "") -> Any:
        if nid in self.outputs:
            return self.outputs[nid].get(pin)
        if nid not in self.nodes:
            return None
        if self.is_step(nid) and not self.run_missing_steps:
            self.warnings.append(f"{self._name(for_node)} used {self._name(nid)} before it ran, so it got nothing.")
            return None
        if nid in self.busy:
            raise _DataError(f"{self._name(nid)} feeds itself through its wires.")
        self.busy.add(nid)
        try:
            step = self.run_data(nid, ctx)
        finally:
            self.busy.discard(nid)
        if not step.get("ok", True):
            raise _DataError(f"{self._name(nid)}: {step.get('error') or 'failed'}")
        return self.outputs.get(nid, {}).get(pin)

    def run_data(self, nid: str, ctx: dict[str, Any]) -> dict[str, Any]:
        node = self.nodes[nid]
        _live_step(nid, "running", label=self._name(nid))
        try:
            inputs = self.resolve(node, ctx)
        except _DataError as exc:
            step = {"ok": False, "id": nid, "type": node.get("type"), "label": self._name(nid), "error": str(exc)}
        else:
            step = _exec_stoppable(node, ctx, inputs)
        self.steps.append(step)
        self.order.append(step)
        self.record(nid, step, ctx)
        _live_step(nid, "ok" if step.get("ok", True) else "stopped" if step.get("stopped") else "error", error=str(step.get("error") or ""))
        return step

    def record(self, nid: str, step: dict[str, Any], ctx: dict[str, Any]) -> None:
        outputs = step.get("outputs")
        if not isinstance(outputs, dict):
            result = step.get("result") if isinstance(step.get("result"), dict) else {}
            outputs = {pin["id"]: result.get(pin["id"]) for pin in self.pins(nid)["outputs"] if pin["id"] in result}
        if step.get("ok", True):
            self.outputs[nid] = outputs
            ctx.setdefault("nodes", {})[nid] = outputs  # {{nodes.<id>.<pin>}} in templates

    def run_sinks(self, ctx: dict[str, Any]) -> tuple[bool, str]:
        """Data nodes whose values nobody pulled yet (Preview, a generator on its own)."""
        for nid in self.data_nodes:
            if nid in self.outputs or nid in self.consumed or _cancelled():
                continue
            step = self.run_data(nid, ctx)
            if not step.get("ok", True):
                return False, f"{self._name(nid)}: {step.get('error') or 'failed'}"
        return (False, STOPPED) if _cancelled() else (True, "")

    def summary(self) -> dict[str, dict[str, Any]]:
        """Each node's outputs for the panel: long text cut, files as file refs."""
        return {nid: {pin: _preview_value(value) for pin, value in outs.items()} for nid, outs in self.outputs.items()}


def _preview_value(value: Any, depth: int = 0) -> Any:
    if is_file_ref(value):
        return with_url(value)
    if isinstance(value, str):
        return value if len(value) <= 4000 else value[:4000] + "…"
    if isinstance(value, list):
        return [_preview_value(item, depth + 1) for item in value[:50]]
    if isinstance(value, dict):
        if depth > 3:
            return "…"
        return {str(key): _preview_value(item, depth + 1) for key, item in list(value.items())[:50]}
    return value


def _last_outputs(wf: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """What each node made in its latest run on this PC (newest run first wins)."""
    out: dict[str, dict[str, Any]] = {}
    for run in reversed(list(wf.get("runs") or [])):
        for nid, values in (run.get("node_outputs") or {}).items() if isinstance(run, dict) else ():
            if isinstance(values, dict):
                out.setdefault(str(nid), values)
    return out


def keep_preview(workflow_id: str, node_id: str) -> dict[str, Any]:
    """"Use this" on a picture — on the card of the node that made it, or on a Preview: run
    the steps that take it (Save to card…), with exactly the one on screen (the last
    run's), not a new one."""
    wf = get_workflow(workflow_id)
    if wf is None:
        return {"ok": False, "error": "workflow not found", "steps": []}
    graph = wf.get("graph") or {}
    types = {str(n.get("id")): str(n.get("type") or "") for n in (graph.get("nodes") or []) if isinstance(n, dict)}
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict) and str(e.get("kind") or "") == DATA_KIND]
    if types.get(str(node_id)) == "util.preview":
        sources = {str(e.get("source")) for e in edges if str(e.get("target")) == str(node_id)}
    else:
        sources = {str(node_id)}
    users = sorted({str(e.get("target")) for e in edges if str(e.get("source")) in sources
                    and str(e.get("target")) != str(node_id) and types.get(str(e.get("target"))) != "util.preview"})
    if not sources or not users:
        return {"ok": False, "error": "Nothing takes this picture yet: wire it into a step (like Save to card).", "steps": [], "id": wf["id"]}
    steps: list[dict[str, Any]] = []
    out: dict[str, Any] = {"ok": True, "error": "", "id": wf["id"]}
    for target in users:
        step = run_node(workflow_id, target, kept_from=sorted(sources))
        steps.extend(step.get("steps") or [])
        out["node_outputs"] = step.get("node_outputs") or out.get("node_outputs") or {}
        if not step.get("ok"):
            return {**out, "ok": False, "error": step.get("error") or "failed", "steps": steps}
    return {**out, "steps": steps}


def _used_up(wf: dict[str, Any], nid: str) -> bool:
    """What this node made last was kept ("Use this") after it was made."""
    for run in reversed(list(wf.get("runs") or [])):
        if not isinstance(run, dict):
            continue
        if nid in (run.get("kept") or []):
            return True
        if any(isinstance(step, dict) and step.get("id") == nid and step.get("ok", True) for step in run.get("steps") or []):
            return False
    return False


def _feeders(flow: _Dataflow, nid: str) -> set[str]:
    """Every node wired into this one, all the way back."""
    seen: set[str] = set()
    todo = [nid]
    while todo:
        for src, _pin in flow.feeds.get(todo.pop(), {}).values():
            if src not in seen:
                seen.add(src)
                todo.append(src)
    return seen


def run_node(
    workflow_id: str,
    node_id: str,
    *,
    approve_spend: bool = False,
    kept_from: list[str] | None = None,
    person: bool = False,
) -> dict[str, Any]:
    """Run one node now. Everything wired into it reuses what it made last run (so a
    paid generator upstream doesn't run again); a node never run before runs, steps too.
    Once what it made was kept ("Use this"), it starts over: everything wired into it
    runs again (the next card that needs a picture, its prompt…). A Preview makes what
    it shows again (try again).
    approve_spend: a person pressed play, so paid steps this run needs may spend.
    kept_from: "Use this" — the nodes whose output this run takes and so uses up.
    person: a person (the panel) started it, not an agent: custom code they run counts as reviewed."""
    wf = get_workflow(workflow_id)
    if wf is None:
        return {"ok": False, "error": "workflow not found", "steps": []}
    graph = wf.get("graph") or {}
    nodes = {str(n.get("id")): n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
    node = nodes.get(str(node_id or ""))
    if node is None:
        return {"ok": False, "error": "That node isn't in the saved workflow; save first.", "steps": [], "id": wf["id"]}
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    flow = _Dataflow(nodes, edges, catalog.node_specs())
    flow.run_missing_steps = True
    nid = str(node["id"])
    remake = {src for src, _pin in flow.feeds.get(nid, {}).values()} if node.get("type") == "util.preview" else set()
    for made in {nid} | remake:
        if _used_up(wf, made):
            remake |= _feeders(flow, made)
    for other, values in _last_outputs(wf).items():
        if other != nid and other in nodes and other not in remake:
            flow.outputs[other] = values
    ctx: dict[str, Any] = {"caller_conv_id": _caller("")}
    _prepare_run_ctx(ctx, wf)
    ctx["nodes"] = dict(flow.outputs)
    wid = str(wf["id"])
    run_id = uuid.uuid4().hex[:12]
    flags = _start_run(person, approve_spend)
    cancel = threading.Event()
    cancel_token, live_token = _CANCEL.set(cancel), _LIVE.set((wid, run_id))
    stack_token = _CALL_STACK.set((*_CALL_STACK.get(), wid))
    with _ACTIVE_LOCK:
        _ACTIVE.setdefault(wid, set()).add(cancel)
    started = time.time()
    _push({"type": "workflow_run", "id": wid, "run": run_id, "state": "started"})
    try:
        if flow.is_step(nid):
            _live_step(nid, "running", label=flow._name(nid))
            try:
                step = _exec_stoppable(node, ctx, flow.resolve(node, ctx))
            except _DataError as exc:
                step = {"ok": False, "id": nid, "type": node.get("type"), "label": flow._name(nid), "error": str(exc)}
            flow.order.append(step)
            flow.record(nid, step, ctx)
            _live_step(nid, "ok" if step.get("ok", True) else "error", error=str(step.get("error") or ""))
        else:
            step = flow.run_data(nid, ctx)
        ok = bool(step.get("ok", True))
        error = "" if ok else f"{flow._name(nid)}: {step.get('error') or 'failed'}"
    finally:
        with _ACTIVE_LOCK:
            running = _ACTIVE.get(wid, set())
            running.discard(cancel)
            if not running:
                _ACTIVE.pop(wid, None)
        _CALL_STACK.reset(stack_token)
        _LIVE.reset(live_token)
        _CANCEL.reset(cancel_token)
        _end_run(flags)
    _push({"type": "workflow_run", "id": wid, "run": run_id, "state": "stopped" if cancel.is_set() else "done" if ok else "error", **({"error": error} if error else {})})
    node_outputs = flow.summary()
    steps = list(flow.order)
    record: dict[str, Any] = {"started": started, "ended": time.time(), "ok": ok, "error": error, "trigger_id": f"node:{nid}", "steps": steps, "node_outputs": node_outputs}
    if kept_from and ok:
        record["kept"] = list(kept_from)
    append_run(wid, record)
    out = {"ok": ok, "error": error, "id": wid, "steps": steps, "node_outputs": node_outputs}
    return {**out, "needs_review": True} if not ok and _needs_review(steps) else out


def run_code_draft(
    workflow_id: str,
    node_id: str,
    code: str | None = None,
    inputs: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    dry_run: bool = False,
    person: bool = False,
) -> dict[str, Any]:
    """Run one Custom code node now with draft code (None: its saved code), without
    saving it or logging a run. inputs None: what it ran with last time. dry_run: its
    ducky.tool / ducky.builtin calls are listed, not made. person: a person pressed Test.

    Returns {"ok", "outputs", "log", "tool_calls", "error": {"message","line","col"} | None, "ms"}."""
    from backend.automations import code_node

    return code_node.draft(workflow_id, node_id, code, inputs, settings, dry_run, person)


def stop_workflow(workflow_id: str) -> bool:
    """Stop every run of this workflow on this PC now. True when one was running."""
    with _ACTIVE_LOCK:
        events = list(_ACTIVE.get(str(workflow_id or "").strip(), ()))
    for event in events:
        event.set()
    return bool(events)


def is_running(workflow_id: str) -> bool:
    with _ACTIVE_LOCK:
        return bool(_ACTIVE.get(str(workflow_id or "").strip()))


def _cancelled() -> bool:
    event = _CANCEL.get()
    return bool(event and event.is_set())


def _push(event: dict[str, Any]) -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event(event)
    except Exception:
        pass


def _live_step(node_id: str, state: str, *, came_from: str = "", label: str = "", error: str = "") -> None:
    live = _LIVE.get()
    if not live:
        return
    wid, run_id = live
    event: dict[str, Any] = {"type": "workflow_step", "id": wid, "run": run_id, "node": node_id, "state": state}
    if came_from:
        event["from"] = came_from
    if label:
        event["label"] = label
    if error:
        event["error"] = error[:300]
    _push(event)


def _announce_run(wf: dict[str, Any], *, phase: str, detail: str = "", run_id: str = "") -> None:
    """Header activity tray — running/finished graphs. Never blocks the walk."""
    wid = str(wf.get("id") or "").strip()
    if not wid:
        return
    title = str(wf.get("name") or wid)
    payload = {
        "type": "background_job",
        "id": f"graph:{wid}",
        "source": "workflow",
        "title": title,
        "detail": detail,
        "phase": phase,
    }
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event(payload)
        if phase in ("done", "error"):
            push_ui_event({"type": "graphs_changed"})
    except Exception:
        pass


def _notify_phone(wf: dict[str, Any], ok: bool) -> None:
    """Push the phone (tap → Remote View on this workflow). Never blocks or raises."""
    try:
        from frontend.duckyos_account import notify_desktop_agent_done

        notify_desktop_agent_done(
            title=str(wf.get("name") or "Workflow")[:80],
            body="Workflow finished. Tap to open it." if ok else "Workflow failed. Tap to see why.",
            kind="workflow",
            target_id=str(wf.get("id") or ""),
        )
    except Exception:
        pass


_ACTION_TYPES = frozenset(
    {
        "ducky.prompt",
        "ducky.spawn",
        "flow.wait",
        "flow.foreach",
        "flow.repeat",
        "flow.branch",
        "tool.call",
        "pipeline.agent",
        "pipeline.finish",
        "flow.end",
        "flow.output",
        "workflow.call",
        "uefn.open_project",
        "uefn.launch",
        "uefn.close",
        "uefn.restart",
        "uefn.wait_ready",
        "uefn.wait_window",
        "uefn.check",
        "uefn.game.start",
        "uefn.game.stop",
        "uefn.player.wait",
        "uefn.log.expect",
        "fortnite.servers",
        "notify.message",
        "ui.spotlight",
        "code.js",
    }
)

_PLAY_TYPES = frozenset({"uefn.check", "uefn.game.start", "uefn.game.stop", "uefn.player.wait", "uefn.log.expect"})


def run_workflow(
    workflow_id: str,
    *,
    trigger_id: str = "",
    payload: dict[str, Any] | None = None,
    starter_id: str = "",
    prompt: str = "",
    files: list[Any] | None = None,
    caller_conv_id: str = "",
) -> dict[str, Any]:
    """Run one workflow. From a chat (a reference, or a ducky calling the tool) the
    caller chat gets the files and the Return to user result; ``prompt`` and
    ``files`` are what the Chat input node passes on."""
    wf = get_workflow(workflow_id)
    if wf is None:
        return {"ok": False, "error": "workflow not found", "steps": []}
    if str(wf["id"]) in _CALL_STACK.get():
        return {"ok": False, "error": "A workflow can't run itself, directly or through another workflow.", "steps": [], "id": wf["id"]}
    graph = wf.get("graph") or {}
    nodes = {str(n.get("id")): n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    flow = _Dataflow(nodes, edges, catalog.node_specs())
    starts = _start_ids(nodes, edges=edges, trigger_id=trigger_id, starter_id=starter_id, payload=payload or {}, is_step=flow.is_step)
    if not starts and (trigger_id or not flow.data_nodes):
        return {"ok": False, "error": "No start found. Leave an input unconnected or choose a start node.", "steps": [], "id": wf["id"]}
    ctx: dict[str, Any] = dict(payload or {})
    asked = {key: bool(ctx.pop(key, None)) for key in _PERSON_KEYS}  # the panel's payload says what a person did
    if prompt:
        ctx["prompt"] = prompt
    if files is not None:
        ctx["files"] = files
    ctx["caller_conv_id"] = _caller(caller_conv_id or str(ctx.get("caller_conv_id") or ""))
    ctx["workflow_name"] = str(wf.get("name") or "")
    _prepare_run_ctx(ctx, wf)
    # A chat's ducky started it (not a person in the editor) when it reports to a chat.
    flags = _start_run(asked["_person_started"] and not ctx["caller_conv_id"], asked["_spend_approved"], by_step=True)
    ident_token = _bind_hub_identity(ctx)
    stack_token = _CALL_STACK.set((*_CALL_STACK.get(), str(wf["id"])))
    cancel = _CANCEL.get() or threading.Event()
    cancel_token = _CANCEL.set(cancel)
    wid = str(wf["id"])
    run_id = uuid.uuid4().hex[:12]
    live_token = _LIVE.set((wid, run_id))
    with _ACTIVE_LOCK:
        _ACTIVE.setdefault(wid, set()).add(cancel)
    started = time.time()
    ok = False
    error = ""
    steps: list[Any] = []
    try:
        _announce_run(wf, phase="working", detail="Running")
        _push({
            "type": "workflow_run", "id": wid, "run": run_id, "state": "started",
            # The chat that started it shows a live card: its name, every step, which one is running.
            "conv": str(ctx.get("caller_conv_id") or ""), "name": str(wf.get("name") or ""),
            "plan": _plan_steps(edges, starts, flow),
        })
        steps, ok, error, _seen = _walk(nodes, edges, ctx, starts, flow=flow) if starts else ([], True, "", 0)
        if ok:
            ok, error = flow.run_sinks(ctx)
        if not ok and not cancel.is_set():
            notify.on_failure(nodes, flow.outputs, flow.order, error, ctx)
        steps = list(flow.order)
        if flow.warnings:
            steps.append({"ok": True, "label": "Note", "result": {"warnings": flow.warnings}, "warning": " ".join(flow.warnings)})
    finally:
        with _ACTIVE_LOCK:
            running = _ACTIVE.get(wid, set())
            running.discard(cancel)
            if not running:
                _ACTIVE.pop(wid, None)
        stopped = cancel.is_set()
        _push({"type": "workflow_run", "id": wid, "run": run_id, "state": "stopped" if stopped else "done" if ok else "error", **({"error": error} if error else {})})
        _LIVE.reset(live_token)
        _CANCEL.reset(cancel_token)
        _CALL_STACK.reset(stack_token)
        _end_run(flags)
        if ident_token is not None:
            from backend.workspace import identity

            identity.reset(ident_token)
        _announce_run(
            wf,
            phase="done" if ok else "error",
            detail=(error or "Finished") if ok else (error or "Failed"),
            run_id=str(int(started)),
        )
        # A run the person started (not a trigger, not a chat's tool call, not a
        # nested workflow.call, not stopped by them): tell their phone.
        if not stopped and not trigger_id and not ctx.get("caller_conv_id") and not _CALL_STACK.get():
            _notify_phone(wf, ok)
    ended = time.time()
    node_outputs = flow.summary()
    run = {
        "started": started,
        "ended": ended,
        "ok": ok,
        "error": error,
        "trigger_id": trigger_id,
        "steps": steps,
        "node_outputs": node_outputs,
    }
    append_run(wf["id"], run)
    return {
        "ok": ok,
        "error": error,
        "id": wf["id"],
        "steps": steps,
        "node_outputs": node_outputs,
        "conv_id": ctx.get("conv_id"),
        "files": ctx.get("files") or [],
        "text": ctx.get("text") or ctx.get("assistant_text") or "",
        "outputs": dict(ctx.get(_RETURN_KEY) or {}),
        **({"needs_review": True} if not ok and _needs_review(steps) else {}),
    }


_PLAN_ORDER = {"main": 0, "each": 1, "true": 2, "done": 3}


def _plan_steps(edges: list[dict[str, Any]], starts: list[str], flow: "_Dataflow", limit: int = 80) -> list[dict[str, str]]:
    """The run's happy path, for the calling chat's live workflow card: depth-first from
    the starts along main/each/true/done wires, End last. False branches (failure
    reports) are left out; the card adds any step that actually runs."""
    out: list[dict[str, str]] = []
    ends: list[dict[str, str]] = []
    seen: set[str] = set()

    def visit(nid: str) -> None:
        if nid in seen or len(out) >= limit:
            return
        seen.add(nid)
        if flow.is_step(nid):
            node_type = str((flow.nodes.get(nid) or {}).get("type") or "")
            step = {"node": nid, "label": flow._name(nid), "type": node_type}
            (ends if node_type == "flow.end" else out).append(step)
        wires = [e for e in edges if str(e.get("source")) == nid and str(e.get("kind") or "main") in _PLAN_ORDER]
        for e in sorted(wires, key=lambda e: _PLAN_ORDER[str(e.get("kind") or "main")]):
            visit(str(e.get("target") or ""))

    for start in starts:
        visit(start)
    return (out + ends)[:limit]


def _caller(explicit: str) -> str:
    """The chat this run reports to: given, or the chat whose ducky called the tool."""
    caller = (explicit or "").strip()
    if caller:
        return caller
    try:
        from backend.workspace.identity import current

        bound = current()
    except Exception:
        bound = None
    return str(bound.conv_id) if bound and bound.conv_id else ""


def public_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    """A run payload from outside (an agent's tool call, a trigger) without the fields
    only the app sets for what a person did."""
    return {key: value for key, value in (payload or {}).items() if key not in _PERSON_KEYS}


def started_by_person() -> bool:
    """A person pressed play or Test on the run going now (or on the one that runs it)."""
    return _PERSON.get()


def spend_approved() -> bool:
    """A person pressed play on the run going now and so approved its paid steps."""
    return _SPEND.get()


def run_chat() -> str:
    """The chat whose ducky started the run going now ('' when none): who called, as the
    app knows it, never a chat named in the call or the run's fields."""
    return _RUN_CHAT.get()


def _calling_chat() -> str:
    try:
        from backend.workspace.identity import current

        bound = current()
    except Exception:
        bound = None
    return str(bound.conv_id or "").strip() if bound else ""


def _start_run(person: bool, spend: bool, *, by_step: bool = False) -> tuple[contextvars.Token, ...] | None:
    """Fix who started the run, at the outermost run only: a nested run keeps its caller's.
    by_step: a step started this run (a tool such as run_workflow or a trigger): it keeps
    the chat it runs for, but no person pressed play on it."""
    if _CALL_STACK.get():
        return (_PERSON.set(False), _SPEND.set(False), _RUN_CHAT.set(_RUN_CHAT.get())) if by_step else None
    return _PERSON.set(bool(person)), _SPEND.set(bool(spend)), _RUN_CHAT.set(_calling_chat())


def _end_run(tokens: tuple[contextvars.Token, ...] | None) -> None:
    if tokens:
        _RUN_CHAT.reset(tokens[2])
        _SPEND.reset(tokens[1])
        _PERSON.reset(tokens[0])


def _needs_review(steps: list[Any]) -> bool:
    """A Custom code step (here or in a workflow this one ran) waits for a person's review."""
    for step in steps or []:
        if isinstance(step, dict) and (step.get("needs_review") or _needs_review(step.get("substeps") or [])):
            return True
    return False


def emit_trigger(trigger_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run every enabled workflow on this PC whose trigger node matches ``trigger_id``."""
    tid = (trigger_id or "").strip()
    if not tid:
        return {"ok": False, "error": "trigger_id required", "runs": []}
    payload = public_payload(payload)
    runs: list[dict[str, Any]] = []
    for wf in all_workflows():
        if not wf.get("enabled") or not runs_here(wf):
            continue
        nodes = (wf.get("graph") or {}).get("nodes") or []
        if any(_node_matches_trigger(n, tid, payload) for n in nodes if isinstance(n, dict)):
            runs.append(run_workflow(str(wf["id"]), trigger_id=tid, payload=payload or {}))
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
    edges: list[dict[str, Any]] | None = None,
    is_step: Any = None,
) -> list[str]:
    if starter_id and starter_id in nodes:
        return [starter_id]
    step = is_step or (lambda _nid: True)
    starters = catalog.starter_types()
    if trigger_id:
        hits = [nid for nid, n in nodes.items() if _node_matches_trigger(n, trigger_id, payload)]
        if hits:
            return hits
        return []
    incoming = {str(e.get("target")) for e in edges or [] if str(e.get("source")) in nodes and str(e.get("kind") or "") != DATA_KIND}
    explicit = [nid for nid, node in nodes.items() if node.get("type") in starters]
    return explicit or [nid for nid in nodes if nid not in incoming and step(nid)]


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
    origin: str = "",
    flow: _Dataflow | None = None,
) -> tuple[list[dict[str, Any]], bool, str, int]:
    steps: list[dict[str, Any]] = []
    queue = list(start_ids)
    visited: set[str] = set()
    came_from: dict[str, str] = {sid: origin for sid in start_ids} if origin else {}
    ok = True
    error = ""
    while queue and seen < _MAX_STEPS:
        if _cancelled():
            return steps, False, STOPPED, seen
        nid = queue.pop(0)
        if not nid or nid == stop_at:
            continue
        node = nodes.get(nid)
        if node is None or nid in visited:
            continue
        visited.add(nid)
        seen += 1
        ntype = str(node.get("type") or "")
        _live_step(nid, "running", came_from=came_from.get(nid, ""), label=str(node.get("label") or ntype))
        if ntype == "flow.foreach":
            cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
            field = str(cfg.get("field") or "cards")
            items = _foreach_items(ctx, field)
            _live_step(nid, "ok")
            steps.append(
                {
                    "ok": True,
                    "id": nid,
                    "type": ntype,
                    "label": str(node.get("label") or ntype),
                    "result": {"count": len(items), "field": field},
                }
            )
            if flow is not None:
                flow.order.append(steps[-1])
            each_ids = _next_ids(nid, edges, "each")
            for item in items:
                _apply_foreach_item(ctx, item)
                nested, n_ok, n_err, seen = _walk(
                    nodes, edges, ctx, each_ids, stop_at=nid, seen=seen, origin=nid, flow=flow
                )
                steps.extend(nested)
                if not n_ok:
                    return steps, False, n_err, seen
            for target in _next_ids(nid, edges, "done"):
                came_from.setdefault(target, nid)
                queue.append(target)
            continue
        if ntype == "flow.repeat":
            nested, r_ok, r_err, seen = _repeat(nid, node, nodes, edges, ctx, seen=seen, flow=flow)
            steps.extend(nested)
            if not r_ok:
                return steps, False, r_err, seen
            for target in _next_ids(nid, edges, "done"):
                came_from.setdefault(target, nid)
                queue.append(target)
            continue
        if flow is not None and not flow.is_step(nid):
            continue  # a data node runs when its value is pulled, never along white wires
        try:
            inputs = flow.resolve(node, ctx) if flow is not None else None
        except _DataError as exc:
            inputs, step = None, {"ok": False, "id": nid, "type": ntype, "label": str(node.get("label") or ntype), "error": str(exc)}
        else:
            step = _exec_stoppable(node, ctx, inputs)
        if flow is not None:
            flow.record(nid, step, ctx)
            flow.order.append(step)
        steps.append(step)
        _live_step(nid, "ok" if step.get("ok", True) else "stopped" if step.get("stopped") else "error", error=str(step.get("error") or ""))
        if not step.get("ok", True):
            ok = False
            error = str(step.get("error") or "step failed")
            break
        if step.get("result") and isinstance(step["result"], dict):
            ctx.update({k: v for k, v in step["result"].items() if k != "ok" and k not in _PERSON_KEYS})
        if ntype in _END_TYPES or step.get("stop"):
            continue
        kind = "true" if step.get("branch") else "false" if "branch" in step else "main"
        for target in _next_ids(nid, edges, kind):
            came_from.setdefault(target, nid)
            queue.append(target)
    if seen >= _MAX_STEPS and queue:
        return steps, False, "step budget exceeded", seen
    return steps, ok, error, seen


def _repeat_tries(cfg: dict[str, Any]) -> int:
    raw = cfg.get("max")
    number = as_number(3 if raw in (None, "") else raw)
    if isinstance(number, float) and not math.isfinite(number):
        raise ValueError("Repeat until: Max tries must be a number.")
    return min(max(int(number), 1), _REPEAT_CAP)


def _repeat(
    nid: str,
    node: dict[str, Any],
    nodes: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    ctx: dict[str, Any],
    *,
    seen: int,
    flow: _Dataflow | None,
) -> tuple[list[dict[str, Any]], bool, str, int]:
    """Repeat until: run the each-wire, then check Until (a wired yes/no, else the
    condition); go again until it says yes or the tries run out, then follow done.

    Each pass starts fresh: what the steps in the loop made last pass is cleared, so
    their values are worked out again ({{nodes.<id>.<pin>}} keeps the latest, for a
    report after the loop). A pass whose step fails counts as not passed and the next
    one starts (Stop still ends the run)."""
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    label = str(node.get("label") or "Repeat until")
    steps: list[dict[str, Any]] = []

    def fail(error: str) -> tuple[list[dict[str, Any]], bool, str, int]:
        step = {"ok": False, "id": nid, "type": "flow.repeat", "label": label, "error": error}
        steps.append(step)
        if flow is not None:
            flow.order.append(step)
        _live_step(nid, "error", error=error)
        return steps, False, error, seen

    try:
        tries = _repeat_tries(cfg)
    except ValueError as exc:
        return fail(str(exc))
    each_ids = _next_ids(nid, edges, "each")
    before = set(flow.outputs) if flow is not None else set()
    attempt, passed, last_error = 0, False, ""
    while attempt < tries:
        attempt += 1
        if flow is not None:
            for key in [k for k in flow.outputs if k not in before and k != nid]:
                flow.outputs.pop(key, None)
            flow.outputs[nid] = {"attempt": attempt, "passed": False}
        ctx["attempt"] = attempt
        nested, n_ok, n_err, seen = _walk(nodes, edges, ctx, each_ids, stop_at=nid, seen=seen, origin=nid, flow=flow)
        steps.extend(nested)
        if n_err == STOPPED or _cancelled():
            return steps, False, STOPPED, seen
        if not n_ok and n_err == "step budget exceeded":
            return steps, False, n_err, seen
        last_error = "" if n_ok else n_err
        if not n_ok:
            continue
        try:
            passed = _repeat_done(nid, cfg, ctx, flow)
        except (ExprError, ValueError, _DataError) as exc:
            return fail(f"Until: {exc}")
        if passed:
            break
    _live_step(nid, "ok")
    result = {"attempts": attempt, "passed": passed, "max_tries": tries, **({"last_error": last_error} if last_error else {})}
    step = {"ok": True, "id": nid, "type": "flow.repeat", "label": label, "outputs": {"attempt": attempt, "passed": passed}, "result": result}
    steps.append(step)
    if flow is not None:
        flow.record(nid, step, ctx)
        flow.order.append(step)
    ctx.update(result)
    return steps, True, "", seen


def _repeat_done(nid: str, cfg: dict[str, Any], ctx: dict[str, Any], flow: _Dataflow | None) -> bool:
    wire = flow.feeds.get(nid, {}).get("until") if flow is not None else None
    if wire and flow is not None:
        source, pin = wire
        if flow.is_step(source) and source not in flow.outputs:
            return False  # that step didn't run this pass (it went the other way)
        return truthy(flow.pull(source, pin, ctx, for_node=nid))
    expression = str(cfg.get("expression") or "").strip()
    if expression:
        return truthy(evaluate(expression, _expression_scope({}, ctx)))
    return False  # nothing to check: every try runs (Repeat N times)


_INPUTS_CAP = 16000


def _shrink(value: Any, cap: int, depth: int = 0) -> Any:
    if is_file_ref(value):
        return value
    if isinstance(value, str):
        return value if len(value) <= cap else value[:cap] + "…"
    if isinstance(value, (list, tuple)):
        return [_shrink(item, cap, depth + 1) for item in list(value)[:50]]
    if isinstance(value, dict):
        if depth > 4:
            return "…"
        return {str(key): _shrink(item, cap, depth + 1) for key, item in list(value.items())[:50]}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _shrink(str(value), cap, depth)


def _bounded_inputs(values: dict[str, Any]) -> dict[str, Any]:
    """The input values a step ran with, for its run record: about 16 KB at most, long
    text cut, file refs kept whole."""
    for cap in (4000, 1000, 200, 40):
        out = {str(key): _shrink(value, cap) for key, value in values.items()}
        if len(json.dumps(out, ensure_ascii=False, default=str)) <= _INPUTS_CAP:
            return out
    return {str(key): "…" for key in values}


def _exec_stoppable(node: dict[str, Any], payload: dict[str, Any], inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one step; its record keeps the input values it ran with."""
    step = _exec_stoppable_step(node, payload, inputs)
    return step if inputs is None else {**step, "inputs": _bounded_inputs(inputs)}


def _exec_stoppable_step(node: dict[str, Any], payload: dict[str, Any], inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one step on its own thread so Stop ends the run at once; a step that was
    still working (a tool, UEFN, a ducky) finishes in the background, unused."""
    cancel = _CANCEL.get()
    if cancel is None:
        return _exec_node(node, payload, inputs)
    box: dict[str, Any] = {}
    done = threading.Event()
    step_ctx = contextvars.copy_context()

    def work() -> None:
        try:
            box["step"] = step_ctx.run(_exec_node, node, payload, inputs)
        except BaseException as exc:  # _exec_node already turns errors into failed steps
            box["step"] = {"ok": False, "id": node.get("id"), "type": node.get("type"), "label": str(node.get("label") or node.get("type") or ""), "error": str(exc)}
        finally:
            done.set()

    threading.Thread(target=work, name="workflow-step", daemon=True).start()
    while not done.wait(0.05):
        if cancel.is_set():
            return {"ok": False, "stopped": True, "id": node.get("id"), "type": node.get("type"), "label": str(node.get("label") or node.get("type") or ""), "error": STOPPED}
    return box["step"]


def _uefn_node(node: dict[str, Any], ntype: str, label: str, cfg: dict[str, Any]) -> dict[str, Any]:
    timeout = float(cfg.get("timeout") or 180)
    project = str(cfg.get("project") or "").strip() or None
    if ntype in ("uefn.open_project", "uefn.launch"):
        from backend.automations.uefn import open_project

        result: dict[str, Any] = open_project(project, timeout=timeout, wait=ntype == "uefn.open_project")
    elif ntype == "uefn.close":
        from frontend.window_view import close_uefn

        result = close_uefn()
    elif ntype == "uefn.restart":
        from backend.automations.uefn import until_stopped
        from frontend.window_view import restart_uefn_project

        result = until_stopped(restart_uefn_project, project, wait=True, timeout=timeout)
    elif ntype == "uefn.wait_ready":
        from backend.automations.uefn import until_stopped
        from frontend.window_view import wait_uefn_ready

        result = until_stopped(wait_uefn_ready, timeout=timeout, root=project)
    else:
        from backend.automations.uefn import until_stopped
        from backend.tools.core.uefn_windows import wait_uefn_window

        result = until_stopped(wait_uefn_window, str(cfg.get("title_regex") or ""), timeout=timeout)
    ok = bool(result.get("ok"))
    step: dict[str, Any] = {
        "ok": ok,
        "id": node.get("id"),
        "type": ntype,
        "label": label,
        "result": result,
    }
    if not ok:
        step["error"] = str(result.get("error") or "step failed")
    return step


def _play_node(ntype: str, cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    from backend.automations import play

    if ntype == "uefn.check":
        return play.check_uefn(cfg)
    if ntype == "uefn.game.start":
        return play.start_game(cfg)
    if ntype == "uefn.game.stop":
        return play.stop_game(cfg)
    if ntype == "uefn.player.wait":
        return play.wait_player(cfg)
    return play.expect_log(cfg, payload)


def _exec_node(node: dict[str, Any], payload: dict[str, Any], inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    ntype = str(node.get("type") or "")
    cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
    label = str(node.get("label") or ntype)
    values = dict(inputs or {})
    if ntype == "pipeline.finish" and label in ("Finish", ntype):
        label = "Return to user"
    elif ntype == "start.chat" and label in ("Chat", ntype):
        label = "Chat input"
    try:
        if spend_approved() and (ntype in media.BACKENDS):
            cfg = {**cfg, "spend": True}  # a person pressed play on this run: that is the approval
        if ntype in media.BACKENDS:
            return {**media.run_media(ntype, cfg, values, _node_folder(node)), "id": node.get("id"), "type": ntype, "label": label}
        if ntype in _FOLDER_OPS:
            return {**_FOLDER_OPS[ntype](cfg, values, _node_folder(node)), "id": node.get("id"), "type": ntype, "label": label}
        if ntype in _DATA_HANDLERS:
            return {**_DATA_HANDLERS[ntype](cfg, values, payload), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "code.js":
            from backend.automations import code_node

            return {**code_node.run_step(node, payload, values), "id": node.get("id"), "type": ntype, "label": label}
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
        if ntype == "flow.repeat":
            return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "result": {"max_tries": _repeat_tries(cfg)}}
        if ntype == "flow.branch":
            return {**_branch_node(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "tool.call":
            called = _call_tool(cfg, payload, str(node.get("id") or ""))
            result = called.get("result")
            text = result.get("text") if isinstance(result, dict) and isinstance(result.get("text"), str) else _as_text(result)
            return {**called, "outputs": {"result": result, "text": text}, "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "pipeline.agent":
            # _seat: run again (a Repeat until pass), it is the same ducky. context: the wired text.
            step = _pipeline_agent({**cfg, "_seat": str(node.get("id") or ""), "context": as_text(values.get("context"))}, payload)
            done = step.get("result") if isinstance(step.get("result"), dict) else {}
            files = [with_url(file_ref(f["path"], kind_of(f["path"]))) for f in done.get("files") or [] if isinstance(f, dict) and f.get("path")]
            return {**step, "outputs": {"text": str(done.get("text") or ""), "files": files}, "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "pipeline.finish":
            return {**_pipeline_finish(cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "flow.end":
            return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "result": {"ended": True}}
        if ntype == "flow.input":
            step = _input_node(cfg, payload)
            return {**step, "outputs": dict(step.get("result") or {}), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "flow.output":
            return {**_output_node(cfg, payload, values), "id": node.get("id"), "type": ntype, "label": label}
        if ntype == "workflow.call":
            live = _LIVE.get()
            target = (*live, str(node.get("id") or "")) if live else None
            output_token = _OUTPUT_PARENTS.set((*_OUTPUT_PARENTS.get(), target) if target else _OUTPUT_PARENTS.get())
            try:
                step = _call_workflow(cfg, payload, values)
            finally:
                _OUTPUT_PARENTS.reset(output_token)
            returned = (step.get("result") or {}).get("returned") if step.get("ok") else None
            return {**step, **({"outputs": dict(returned)} if isinstance(returned, dict) else {}), "id": node.get("id"), "type": ntype, "label": label}
        if ntype in ("uefn.open_project", "uefn.launch", "uefn.close", "uefn.restart", "uefn.wait_ready", "uefn.wait_window"):
            return _uefn_node(node, ntype, label, cfg)
        if ntype in _PLAY_TYPES:
            return {**_play_node(ntype, cfg, payload), "id": node.get("id"), "type": ntype, "label": label}
        handler = plugin.get_handler(ntype)
        if handler is None:
            # Starters / plugin triggers need no handler — just pass the payload on.
            if ntype.startswith("start.") or ntype not in _ACTION_TYPES:
                return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "result": dict(payload)}
            return {"ok": False, "id": node.get("id"), "type": ntype, "label": label, "error": f"no handler for {ntype}"}
        result = handler(_plugin_ctx(cfg, payload, node, values))
        if isinstance(result, dict) and result.get("ok") is False:
            return {"ok": False, "id": node.get("id"), "type": ntype, "label": label, "error": result.get("error") or "plugin node failed", "result": result}
        return {"ok": True, "id": node.get("id"), "type": ntype, "label": label, "result": result}
    except (ExprError, ValueError) as exc:  # what the user wrote or wired: say what is wrong, no traceback
        return {"ok": False, "id": node.get("id"), "type": ntype, "label": label, "error": str(exc)}
    except Exception as exc:
        _log.exception("automation node %s failed", ntype)
        return {"ok": False, "id": node.get("id"), "type": ntype, "label": label, "error": str(exc)}


def _file_refs(raw: Any, kind: str) -> list[dict[str, Any]]:
    """Picked files as file refs: {kind, path, name}."""
    out: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else [raw] if raw else []:
        path = item.get("path") if isinstance(item, dict) else item
        if not path:
            continue
        text = str(path)
        name = item.get("name") if isinstance(item, dict) and item.get("name") else text.replace("\\", "/").rsplit("/", 1)[-1]
        out.append({"kind": kind, "path": text, "name": str(name)})
    return out


def _input_value(ntype: str, cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("value")
    if ntype == "input.text":
        return {"text": "" if raw is None else str(raw)}
    if ntype == "input.number":
        number = as_number(raw)
        return {"number": None if isinstance(number, float) and math.isnan(number) else number}
    if ntype == "input.boolean":
        return {"value": raw in (True, "true", "yes", "1", 1)}
    if ntype == "input.json":
        if isinstance(raw, str):
            try:
                return {"value": json.loads(raw) if raw.strip() else None}
            except ValueError as exc:
                raise ValueError(f"That isn't valid JSON: {exc}") from exc
        return {"value": raw}
    kind = ntype.split(".", 1)[1]
    if kind == "images":
        return {"images": _file_refs(raw, "image")}
    refs = _file_refs(raw, kind)
    return {kind: refs[0] if refs else None}


def _input_node_value(cfg: dict[str, Any], _inputs: dict[str, Any], _payload: dict[str, Any], *, ntype: str) -> dict[str, Any]:
    outputs = _input_value(ntype, cfg)
    return {"ok": True, "outputs": outputs, "result": outputs}


def _expression_scope(inputs: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    scope = {key: value for key, value in payload.items() if isinstance(key, str) and not key.startswith("_")}
    scope.update(inputs)
    return scope


def _if_node(cfg: dict[str, Any], inputs: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    result = truthy(evaluate(str(cfg.get("expression") or ""), _expression_scope(inputs, payload)))
    return {"ok": True, "branch": result, "outputs": {"result": result}, "result": {"result": result}}


def _expression_node(cfg: dict[str, Any], inputs: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    value = evaluate(str(cfg.get("expression") or ""), _expression_scope(inputs, payload))
    return {"ok": True, "outputs": {"result": value}}


def _compare_node(cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    a, b = inputs.get("a"), inputs.get("b")
    op = str(cfg.get("op") or "equals")
    if op == "not_equals":
        result = not _loose_equal(a, b)
    elif op in ("greater", "less"):
        left, right = (a, b) if isinstance(a, str) and isinstance(b, str) else (as_number(a), as_number(b))
        result = left > right if op == "greater" else left < right
    elif op == "contains":
        result = _value_contains(a, as_text(b))
    elif op == "starts":
        result = as_text(a).startswith(as_text(b))
    elif op == "matches":
        try:
            result = re.search(as_text(b), as_text(a)) is not None
        except re.error as exc:
            raise ValueError(f"B isn't a valid pattern: {exc}") from exc
    elif op == "empty":
        result = a in (None, "") or (isinstance(a, (list, dict)) and not a)
    else:
        result = _loose_equal(a, b)
    return {"ok": True, "outputs": {"result": bool(result)}}


def _loose_equal(a: Any, b: Any) -> bool:
    return bool(evaluate("a == b", {"a": a, "b": b}))


def _template_node(cfg: dict[str, Any], inputs: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    template = str(cfg.get("template") or "")
    if not template.strip():
        template = " ".join("{{" + name + "}}" for name in inputs)
    return {"ok": True, "outputs": {"text": _as_text(_template(template, {**payload, **inputs}))}}


def _preview_node(_cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "outputs": {"value": inputs.get("value")}}


def _model_choice(cfg: dict[str, Any]) -> tuple[str, str]:
    """(gateway, model) for Ask a model: the node's pick, else the app's default model."""
    model = str(cfg.get("model") or "").strip()
    if not model:
        try:
            from frontend.ui_web.panel_api import _first_available_api_model
            from frontend.ui_web.panel_settings import PanelSettings

            model = str(getattr(PanelSettings.load(), "default_model", "") or "").strip()
            if not model:
                first = _first_available_api_model()
                if first:
                    return first
        except Exception:
            model = ""
    if not model:
        raise ValueError("Pick a model in this node's details.")
    try:
        # The full picker saves "backend:model": a gateway (openai, ollama…) or an agent (cursor, claude_code…).
        from frontend.favorite_models import parse_selection

        picked = parse_selection(model)
    except Exception:
        picked = None
    if picked is not None:
        return picked.backend, picked.model_id
    from backend.agent.model_pricing import resolve_provider_for_model

    provider = resolve_provider_for_model(model)
    if ":" in model and model.split(":", 1)[0].lower() == provider:
        model = model.split(":", 1)[1]
    return provider, model


def _ask_node(cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    from backend.automations.llm_complete import complete_prompt

    prompt = _as_text(inputs.get("prompt")).strip()
    if not prompt:
        return {"ok": False, "error": "Nothing in Prompt: wire text in or type it in the details."}
    # Instructions go as the system prompt, apart from the wired text they work on.
    parts = [_as_text(inputs.get("context")).strip(), prompt]
    provider, model = _model_choice(cfg)
    out = complete_prompt(provider, "\n\n".join(part for part in parts if part), model, system=str(cfg.get("system") or "").strip())
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "The model didn't answer.")}
    text = str(out.get("text") or "")
    return {"ok": True, "outputs": {"text": text}, "result": {"model": model}}


def _model_text(inputs: dict[str, Any], pin: str) -> str:
    value = inputs.get(pin)
    return (value if isinstance(value, str) else _as_text(value)).strip()


def _vision_node(cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    """Ask about an image: the picture(s) and a question to a model that can see."""
    from backend.agent.model_capabilities import model_in_cache, supports_vision
    from backend.automations.llm_complete import complete_prompt

    raw = inputs.get("image")
    pictures = [media.path_of(item) for item in (raw if isinstance(raw, list) else [raw]) if media.path_of(item)]
    if not pictures:
        return {"ok": False, "error": "Nothing in Image: wire an image in or pick one in the details."}
    question = _model_text(inputs, "prompt") or "Describe this image in detail."
    provider, model = _model_choice(cfg)
    if model_in_cache(provider, model) and not supports_vision(provider, model):
        return {"ok": False, "error": f"{model} can't see images. Pick a model with vision in this node's details."}
    out = complete_prompt(provider, question, model, system=str(cfg.get("system") or "").strip(), images=pictures)
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "The model didn't answer.")}
    return {"ok": True, "outputs": {"text": str(out.get("text") or "")}, "result": {"model": model}}


def _json_in(text: str) -> Any:
    """The JSON object or list in a model's answer (it may wrap it in ``` or words)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except ValueError:
                continue
    raise ValueError("The model's answer wasn't JSON. Try a stronger model or simpler fields.")


def _extract_node(cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    """Extract data: the model reads the text and fills the fields named in the details."""
    from backend.automations.llm_complete import complete_prompt

    text = _model_text(inputs, "text")
    if not text:
        return {"ok": False, "error": "Nothing in Text: wire text in or type it in the details."}
    fields = [str(name).strip() for name in cfg.get("names") or [] if str(name).strip()]
    if not fields:
        return {"ok": False, "error": "Name the fields to fill in this node's details."}
    ask = (f"Read the text below and fill these fields: {', '.join(fields)}. "
           "Answer with only one JSON object with exactly those keys; use null when the text doesn't say."
           f"\n\nText:\n{text}")
    provider, model = _model_choice(cfg)
    out = complete_prompt(provider, ask, model, system=str(cfg.get("system") or "").strip())
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "The model didn't answer.")}
    data = _json_in(str(out.get("text") or ""))
    if not isinstance(data, dict):
        data = {fields[0]: data}
    filled = {name: data.get(name) for name in fields}
    return {"ok": True, "outputs": {"data": filled, **filled}, "result": {"model": model}}


def _translate_node(cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    from backend.automations.llm_complete import complete_prompt

    text = _model_text(inputs, "text")
    if not text:
        return {"ok": False, "error": "Nothing in Text: wire text in or type it in the details."}
    language = str(inputs.get("language") or cfg.get("language") or "").strip() or "English"
    provider, model = _model_choice(cfg)
    out = complete_prompt(provider, f"Translate this into {language}. Answer with only the translation, keeping its formatting.\n\n{text}", model)
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "The model didn't answer.")}
    return {"ok": True, "outputs": {"text": str(out.get("text") or "").strip()}, "result": {"model": model, "language": language}}


def _save_file_node(cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    """Save file: copy what is wired in into a folder picked in the details."""
    import shutil

    folder = str(inputs.get("folder") or cfg.get("folder") or "").strip()
    if not folder:
        return {"ok": False, "error": "Choose the folder to save into in this node's details."}
    raw = inputs.get("file")
    items = [item for item in (raw if isinstance(raw, list) else [raw]) if media.path_of(item)]
    if not items:
        return {"ok": False, "error": "Nothing in File: wire a file in."}
    dest = Path(folder).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    name = str(cfg.get("name") or "").strip()
    saved: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        source = Path(media.path_of(item))
        if not source.is_file():
            return {"ok": False, "error": f"{source.name} isn't on this PC any more."}
        stem = (name if len(items) == 1 else f"{name}-{index + 1}") if name else source.stem
        target = dest / f"{Path(stem).stem}{source.suffix}"
        if cfg.get("overwrite") is not True:
            n = 2
            while target.exists() and target.resolve() != source.resolve():
                target = dest / f"{Path(stem).stem} ({n}){source.suffix}"
                n += 1
        if target.resolve() != source.resolve():
            shutil.copy2(source, target)
        saved.append(with_url(file_ref(target, kind_of(target))))
    return {"ok": True, "outputs": {"file": saved[0] if len(saved) == 1 else saved, "path": str(saved[0]["path"])}, "result": {"saved": [ref["path"] for ref in saved]}}


def _describe_item(item: Any) -> str:
    if isinstance(item, dict) and item.get("path"):
        return " · ".join(str(item.get(key)) for key in ("name", "description", "caption") if item.get(key))
    return _as_text(item)[:300]


def _pick_node(cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    """Find by description: the model keeps the items of a list that match what you ask."""
    from backend.automations.llm_complete import complete_prompt

    items = listops.as_list(inputs.get("list"))
    if not items:
        return {"ok": True, "outputs": {"list": [], "item": None, "count": 0}}
    wanted = _model_text(inputs, "prompt")
    if not wanted:
        return {"ok": False, "error": "Nothing in Prompt: say what to look for."}
    numbered = "\n".join(f"{n}. {_describe_item(item)}" for n, item in enumerate(items[:500]))
    ask = (f"Which of these items match: {wanted}\nAnswer with only a JSON array of their numbers, best match first "
           f"(an empty array if none match).\n\n{numbered}")
    provider, model = _model_choice(cfg)
    out = complete_prompt(provider, ask, model)
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "The model didn't answer.")}
    picked = _json_in(str(out.get("text") or ""))
    numbers = [int(n) for n in (picked if isinstance(picked, list) else []) if isinstance(n, (int, float)) and 0 <= int(n) < len(items)]
    kept = [items[n] for n in dict.fromkeys(numbers)]
    return {"ok": True, "outputs": {"list": kept, "item": kept[0] if kept else None, "count": len(kept)}, "result": {"model": model}}


def _origin_text_node(cfg: dict[str, Any], inputs: dict[str, Any], payload: dict[str, Any], folder: Path) -> dict[str, Any]:
    """Text to origin: the model turns "pivot at the bottom centre" into Set origin choices."""
    from backend.automations.llm_complete import complete_prompt

    wanted = _model_text(inputs, "instruction") or str(cfg.get("instruction") or "").strip()
    if not wanted:
        return {"ok": False, "error": "Say where the origin goes, like: bottom centre, or the back left corner."}
    ask = ("A 3D model is Y-up: X is left (min) to right (max), Y is bottom (min) to top (max), Z is back (min) to front (max). "
           f"Where should its origin go: {wanted}\nAnswer with only JSON like {{\"x\": \"center\", \"y\": \"min\", \"z\": \"center\"}}; "
           "each one of min, center, max, mass, keep.")
    provider, model = _model_choice(cfg)
    out = complete_prompt(provider, ask, model)
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "The model didn't answer.")}
    picks = _json_in(str(out.get("text") or ""))
    if not isinstance(picks, dict):
        return {"ok": False, "error": "The model's answer wasn't origin choices."}
    choice = {axis: str(picks.get(axis) or "keep").lower() for axis in ("x", "y", "z")}
    step = glbops.set_origin(choice, {"mesh": inputs.get("mesh")}, folder)
    return {**step, "result": {**step.get("result", {}), "model": model}}


def _auto_scale_node(cfg: dict[str, Any], inputs: dict[str, Any], folder: Path) -> dict[str, Any]:
    """Auto Transform Mesh: real-world height from what it is, scaled, origin at the bottom center."""
    from backend.automations.llm_complete import complete_prompt

    mesh = inputs.get("mesh")
    gltf, binary, source = glbops._open({"mesh": mesh})
    lo, hi = glbops.bounds(gltf, binary)
    size = [round(hi[i] - lo[i], 4) for i in range(3)]
    what = _model_text(inputs, "description") or source.stem.replace("_", " ").replace("-", " ")
    ask = (f"A 3D model of: {what}. Its width : height : depth are {size[0]} : {size[1]} : {size[2]} (Y is up). "
           "How tall is this object in the real world, in meters? Answer with only JSON like {\"height_m\": 1.2}.")
    provider, model = _model_choice(cfg)
    out = complete_prompt(provider, ask, model)
    if not out.get("ok"):
        return {"ok": False, "error": str(out.get("error") or "The model didn't answer.")}
    try:
        picked = _json_in(str(out.get("text") or ""))
    except ValueError:
        picked = None
    height = picked.get("height_m") if isinstance(picked, dict) else None
    if not isinstance(height, (int, float)) or not 0.001 <= float(height) <= 10000:
        return {"ok": False, "error": "The model didn't give a sensible height; describe the object in What it is."}
    fitted = glbops.fit_box({}, {"mesh": mesh, "height": float(height)}, folder / "fit")
    placed = glbops.set_origin({"x": "center", "y": "min", "z": "center"}, {"mesh": fitted["outputs"]["mesh"]}, folder)
    return {"ok": True, "outputs": {"mesh": placed["outputs"]["mesh"], "height": float(height)},
            "result": {"model": model, "height_m": float(height), "object": what}}


def _node_folder(node: dict[str, Any]) -> Path:
    """This run's folder for one node's files (AppData workflow_media)."""
    live = _LIVE.get()
    wid, run_id = live if live else ("adhoc", time.strftime("%Y%m%d-%H%M%S"))
    return run_folder(wid, run_id, str(node.get("id") or "node"))


# Nodes that write files into the run's folder: (config, inputs, folder) → step.
_FOLDER_OPS: dict[str, Any] = {
    **imageops.OPS,
    **glbops.OPS,
    **pdfops.OPS,
    "blender.render": media.blender_render,
    "blender.export": media.blender_export,
    "mesh.origin_text": lambda cfg, inputs, folder: _origin_text_node(cfg, inputs, {}, folder),
    "mesh.auto_scale": _auto_scale_node,
}

_INPUT_TYPES = ("input.text", "input.number", "input.boolean", "input.json", "input.image", "input.images",
                "input.audio", "input.video", "input.mesh", "input.pdf", "input.svg", "input.file")
_DATA_HANDLERS: dict[str, Any] = {
    **{ntype: (lambda cfg, inputs, payload, _t=ntype: _input_node_value(cfg, inputs, payload, ntype=_t)) for ntype in _INPUT_TYPES},
    "logic.if": _if_node,
    "logic.expression": _expression_node,
    "logic.compare": _compare_node,
    "llm.ask": _ask_node,
    "text.template": _template_node,
    "util.preview": _preview_node,
    "llm.vision": _vision_node,
    "llm.extract": _extract_node,
    "llm.translate": _translate_node,
    "util.save_file": _save_file_node,
    "llm.pick": _pick_node,
    "uefn.import": lambda cfg, inputs, _payload: media.send_to_uefn(cfg, inputs),
    "blender.open": lambda cfg, inputs, _payload: media.open_in_blender(cfg, inputs),
    "fortnite.servers": lambda cfg, _inputs, _payload: servers.wait_for_servers(cfg, cancelled=_cancelled),
    "notify.message": notify.message_me,
    "ui.spotlight": lambda cfg, inputs, payload: _spotlight_node(cfg, inputs, payload),
    **listops.HANDLERS,
}


def _spotlight_node(cfg: dict[str, Any], inputs: dict[str, Any], _payload: dict[str, Any]) -> dict[str, Any]:
    """Desktop spotlight. Same function as ducky_ui_show and api.spotlight."""
    import json as _json

    from backend.tools.panel.panel_ui import show

    kwargs: dict[str, Any] = {
        "window": str(inputs.get("window") or cfg.get("window") or "uefn"),
        "title": str(inputs.get("title") or cfg.get("title") or ""),
        "body": str(inputs.get("body") or cfg.get("body") or ""),
        "click": bool(cfg.get("click")),
        "wait": cfg.get("wait", True) not in (False, 0, "0", "false"),
    }
    raw_steps = str(cfg.get("steps") or "").strip()
    if raw_steps:
        parsed = _json.loads(raw_steps)
        if not isinstance(parsed, list):
            raise ValueError("steps must be a JSON list")
        kwargs["steps"] = parsed
    else:
        kwargs["box"] = {"x": cfg.get("x"), "y": cfg.get("y"), "w": cfg.get("w"), "h": cfg.get("h")}
    out = show(**kwargs)
    if not isinstance(out, dict) or out.get("error"):
        raise ValueError(str((out or {}).get("error") or "spotlight failed"))
    return {
        "ok": True,
        "outputs": {"reason": str(out.get("reason") or ""), "step": out.get("step") or 1},
        "result": out,
    }


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
        run_id = _run_message(conv.id, prompt, str(cfg.get("mode") or "agent"), str(cfg.get("model") or ""),
                              parent=str(payload.get("caller_conv_id") or ""))
    return {"ok": True, "result": {"conv_id": conv.id, "run_id": run_id}}


def _run_message(conv_id: str, text: str, mode: str, model: str, *, parent: str = "") -> str:
    from frontend.ui_web.agent_modes import run_message

    return str(run_message(conv_id, text, mode, model, parent=parent) or "")


def _cancel_agent_on_stop(conv_id: str) -> Callable[[], None]:
    """Stop also stops the ducky an Agent step is waiting for.

    _exec_stoppable only stops waiting: the ducky kept working, and on Oct 2 2026 a
    stopped demo's launch ducky still started a Fortnite session minutes later.
    Returns the call that ends the watch once the ducky has answered.
    """
    cancel = _CANCEL.get()
    if cancel is None or not conv_id:
        return lambda: None
    finished = threading.Event()

    def watch() -> None:
        while not finished.is_set():
            if cancel.wait(0.5):
                if not finished.is_set():
                    try:
                        from frontend.ui_web.agent_modes import cancel_agent

                        cancel_agent(conv_id)
                    except Exception:
                        pass
                return

    threading.Thread(target=watch, name="workflow-agent-stop", daemon=True).start()
    return finished.set


def _bare_model(model: str) -> str:
    """The model id a run passes on: the picker's "backend:model" ("codex:gpt-6-sol") is
    for choosing the ducky's gateway / agent; the CLI or API only takes "gpt-6-sol"."""
    try:
        from frontend.favorite_models import parse_selection

        picked = parse_selection(model)
    except Exception:
        picked = None
    return picked.model_id if picked is not None else model


def _run_message_and_wait(
    conv_id: str,
    text: str,
    mode: str,
    model: str,
    *,
    timeout_sec: float,
    parent: str = "",
    attachments: list[dict[str, Any]] | None = None,
    started_by: str | None = None,
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
            started_by=started_by,
        )
        or {}
    )


def _prepare_run_ctx(ctx: dict[str, Any], wf: dict[str, Any]) -> None:
    caller = str(ctx.get("caller_conv_id") or "").strip()
    if caller and not ctx.get("artifact_dir"):
        from backend.automations.artifacts import caller_run_dir

        run_id = str(ctx.get("pipeline_run_id") or uuid.uuid4())
        ctx["pipeline_run_id"] = run_id
        ctx["artifact_dir"] = str(caller_run_dir(caller, run_id))
    ctx.setdefault("files", [])
    if _needs_group(wf, caller):
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


def _needs_group(wf: dict[str, Any], caller: str) -> bool:
    """Only swarm tiles need a hub: Agent nodes always, Spawn ducky only when run
    from a chat (a scheduled spawn must not open a group every tick). Image/device
    graphs must not kidnap the chat."""
    types = {str(n.get("type") or "") for n in (wf.get("graph") or {}).get("nodes") or [] if isinstance(n, dict)}
    return "pipeline.agent" in types or (bool(caller) and "ducky.spawn" in types)


def _ensure_pipeline_group(ctx: dict[str, Any], wf: dict[str, Any]) -> None:
    if str(ctx.get("group_id") or "").strip():
        return
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    caller = str(ctx.get("caller_conv_id") or "").strip()
    parent_folder, _already_grouped = _caller_group_home(caller)
    try:
        from backend.tools.panel.ducky_panel import _panel_api

        created = _panel_api().group_find_or_create(
            name=str(wf.get("name") or "Workflow"),
            folder_id=parent_folder,
            open_tab=False,
        )
    except Exception as exc:
        _log.warning("workflow group_create failed: %s", exc)
        return
    if not created.get("ok"):
        _log.warning("workflow group_create failed: %s", created.get("error"))
        return
    hub_id = str(created.get("id") or "").strip()
    ctx["group_id"] = hub_id
    ctx["group_folder_id"] = str(created.get("folder_id") or "")
    if not hub_id:
        return
    # ponytail: leader pointer only — do not move the caller's chat into the hub.
    # A reused group keeps the leader it already has.
    if caller:
        hub = load_conversation(hub_id)
        if hub is not None and not (getattr(hub, "leader_conv_id", None) or "").strip():
            hub.leader_conv_id = caller
            save_conversation(hub)


def _plugin_ctx(
    cfg: dict[str, Any], payload: dict[str, Any], node: dict[str, Any], inputs: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "config": cfg,
        "payload": payload,
        "node": node,
        # The node's input pins: what its wires carried, else what is set in its details.
        "inputs": dict(inputs or {}),
        # Plugin API field from before workflows: "pipeline" = run from a chat.
        "kind": "pipeline" if payload.get("caller_conv_id") else "automation",
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

    node_id, context = str(cfg.get("_seat") or ""), str(cfg.get("context") or "")

    ducky = str(cfg.get("ducky") or cfg.get("profile_id") or payload.get("ducky") or "").strip()
    kwargs: dict[str, Any] = {}
    existing = ducky.startswith("chat:")
    # Run again in this run (a Repeat until pass): the same ducky, which remembers its last pass.
    seats = payload.setdefault("_agent_seats", {}) if node_id else {}
    if node_id and node_id in seats and not existing:
        seat = dict(seats[node_id])
    elif existing:
        seat = _existing_pipeline_ducky(ducky[5:], payload)
    elif ducky in ("", "__new__", "__blank__"):
        seat = _create_pipeline_ducky(cfg, payload)
    else:
        profile = _resolve_profile(ducky)
        if profile is None:
            return {"ok": False, "error": f"Assigned ducky is unavailable: {ducky}. Choose another in the Agent node."}
        kwargs = _agent_spawn_kwargs(profile)
        seat = _seat_agent_cluster(cfg, payload, profile, kwargs)
    if not seat.get("ok"):
        return seat
    conv_id = str(seat.get("conv_id") or "")
    if not conv_id:
        return {"ok": False, "error": "group_invite did not return a member"}
    if node_id and not existing:
        seats[node_id] = seat
    worker_dir = chat_dir(conv_id)
    incoming = payload.get("files") or []
    copy_files_into(incoming, worker_dir)
    prompt = _as_text(_template(str(cfg.get("prompt") or payload.get("prompt") or ""), payload))
    if context.strip():
        prompt = f"{prompt}\n\n{context.strip()}" if prompt.strip() else context.strip()
    timeout = min(max(float(cfg.get("timeout_sec") or 180.0), 1.0), _AGENT_WAIT_CAP_S)
    caller = str(payload.get("caller_conv_id") or "").strip()
    release = _cancel_agent_on_stop(conv_id)
    try:
        wait = _run_message_and_wait(
            conv_id,
            prompt,
            str(cfg.get("mode") or "agent"),
            _bare_model(str(cfg.get("model") or kwargs.get("model") or "")),
            timeout_sec=timeout,
            parent="" if existing else caller,
            # A seat this run made or reused: covered by the chat that ran the workflow only.
            started_by=None if existing else caller,
            attachments=_files_as_attachments(incoming),
        )
    finally:
        release()
    if str(wait.get("status") or "") != "done":
        if node_id:
            seats.pop(node_id, None)  # it may still be busy: a next pass gets a fresh ducky
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


def _existing_pipeline_ducky(conv_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from frontend.ui_web.agent_modes import is_agent_running
    from frontend.ui_web.project_chats import load_conversation

    conv = load_conversation(conv_id)
    if conv is None or getattr(conv, "is_group", False):
        return {"ok": False, "error": "Assigned ducky is unavailable. Choose another in the Agent node."}
    if conv_id == str(payload.get("caller_conv_id") or ""):
        return {"ok": False, "error": "This ducky is running the workflow. Assign another ducky or choose Create new when workflow runs."}
    if is_agent_running(conv_id):
        return {"ok": False, "error": "Assigned ducky is busy. Try again when it finishes or choose Create new when workflow runs."}
    return {"ok": True, "conv_id": conv_id}


def _create_pipeline_ducky(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    from frontend.favorite_models import ResolveErr
    from frontend.settings import PanelSettings
    from frontend.ui_web.agent_modes import notify_chats_changed
    from frontend.ui_web.panel_api import resolve_model_selection
    from frontend.ui_web.project_chats import create_conversation

    settings = PanelSettings.load()
    model = str(cfg.get("model") or "").strip()
    resolved = resolve_model_selection([model] if model else None, settings)
    if isinstance(resolved, ResolveErr):
        return {"ok": False, "error": resolved.message}
    conv = create_conversation(
        settings,
        str(payload.get("group_folder_id") or ""),
        title=str(cfg.get("title") or "Workflow ducky"),
        ducky_name="Ducky",
        model=resolved.model,
        provider=resolved.provider or None,
        coding_agent=resolved.coding_agent,
        parent_conv_id=str(payload.get("group_id") or ""),
    )
    notify_chats_changed(conv.id, conv.title, conv.folder_id, open_tab=False)
    return {"ok": True, "conv_id": conv.id, "group_id": payload.get("group_id") or "", "group_folder_id": conv.folder_id}


def _seat_agent_cluster(
    cfg: dict[str, Any],
    payload: dict[str, Any],
    profile: dict[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    del kwargs  # the profile's own model; a node model override goes through group_seat_profile
    if not str(payload.get("group_id") or "").strip():
        _ensure_pipeline_group(payload, {"name": str(payload.get("workflow_name") or "Workflow")})
    group_id = str(payload.get("group_id") or "").strip()
    if not group_id:
        return {"ok": False, "error": "workflow group_create failed"}
    pid = str(profile.get("id") or "").strip()
    try:
        from backend.tools.panel.ducky_panel import _panel_api

        seated = _panel_api().group_seat_profile(group_id, pid, model=str(cfg.get("model") or ""))
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not seated.get("ok"):
        return {"ok": False, "error": str(seated.get("error") or "group_invite failed")}
    member = seated.get("member") if isinstance(seated.get("member"), dict) else {}
    return {
        "ok": True,
        "conv_id": str(member.get("member_conv_id") or ""),
        "group_id": group_id,
        "group_folder_id": str(payload.get("group_folder_id") or ""),
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
    text = _as_text(_template(cfg.get("message") or payload.get("text") or payload.get("assistant_text") or "Workflow complete.", payload))
    files = payload.get("files") or []
    if not caller:
        return {"ok": True, "result": {"posted": False, "text": text, "files": files}}
    from frontend.ui_web.project_chats import append_message, load_conversation

    conv = load_conversation(caller)
    if conv is None:
        return {"ok": False, "error": f"caller chat not found: {caller}"}
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
        # Case-insensitive so a boolean result matches "true" as typed in the node.
        return str(value).strip().lower() == str(cfg.get("equals")).strip().lower()
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


def _terminal_output_stream(session_id: str, node_id: str) -> tuple[threading.Event, threading.Thread] | None:
    """Publish bounded terminal snapshots to the command and each calling node."""
    live = _LIVE.get()
    if not live or not node_id or not session_id:
        return None
    from frontend.ui_web.terminal.manager import get_terminal_manager

    manager = get_terminal_manager()
    targets = (*_OUTPUT_PARENTS.get(), (*live, node_id))
    stopped = threading.Event()

    def watch() -> None:
        previous: str | None = None
        while True:
            try:
                output = manager.read_output(session_id, max_chars=16000)
                text = str(output.get("output") or "")
                if text != previous:
                    previous = text
                    for wid, run_id, target in targets:
                        _push({"type": "workflow_output", "id": wid, "run": run_id, "node": target,
                               "session_id": session_id, "output": text})
            except Exception:
                _log.debug("Terminal output snapshot failed", exc_info=True)
            if stopped.wait(0.25):
                # Read once more after the tool returns, including its final lines.
                try:
                    output = manager.read_output(session_id, max_chars=16000)
                    for wid, run_id, target in targets:
                        _push({"type": "workflow_output", "id": wid, "run": run_id, "node": target,
                               "session_id": session_id, "output": str(output.get("output") or "")})
                except Exception:
                    _log.debug("Final terminal output snapshot failed", exc_info=True)
                return

    thread = threading.Thread(target=watch, name="workflow-terminal-output", daemon=True)
    thread.start()
    return stopped, thread


def typed_command_approved(command: str) -> bool:
    """ducky_terminal_run: this exact command is typed into the Local workflow running it."""
    typed = _TYPED_COMMAND.get()
    return bool(typed) and typed == command


def _typed_command(name: str, cfg: dict[str, Any], raw: dict[str, Any]) -> str:
    """The command typed into this Run terminal step of a Local workflow, else ''."""
    if _FROM_CODE.get():
        return ""
    if name != "ducky_terminal_run" or (cfg.get("arguments") is None and not cfg.get("arguments_json")):
        return ""
    command = raw.get("command")
    if not isinstance(command, str) or not command.strip() or _PLACEHOLDER.search(command):
        return ""
    stack = _CALL_STACK.get()
    wf = get_workflow(stack[-1]) if stack else None
    if wf is None or str((wf.get("owner") or {}).get("kind") or LOCAL) != LOCAL:
        return ""
    return command


def _call_tool(cfg: dict[str, Any], payload: dict[str, Any], node_id: str = "") -> dict[str, Any]:
    name =str(cfg.get("name") or payload.get("tool") or "").strip()
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
    return _invoke_tool(name, _template(raw, payload), node_id, _typed_command(name, cfg, raw))


def _invoke_tool(name: str, arguments: dict[str, Any], node_id: str = "", typed: str = "") -> dict[str, Any]:
    """Call a host or plugin MCP tool by name (sync tools only), streaming a terminal
    command's output live into the node. ``typed``: the command that may skip the pop-up."""
    import inspect

    from backend.server import mcp

    tool = mcp._tool_manager.get_tool(name)
    if tool is None:
        return {"ok": False, "error": f"unknown tool: {name}"}
    fn = getattr(tool, "fn", None)
    if fn is None:
        return {"ok": False, "error": f"tool has no fn: {name}"}
    stream = _terminal_output_stream(str(arguments.get("session_id") or ""), node_id) if name == "ducky_terminal_run" else None
    typed_token = _TYPED_COMMAND.set(typed)
    try:
        result = fn(**arguments)
    finally:
        _TYPED_COMMAND.reset(typed_token)
        if stream:
            stream[0].set()
            stream[1].join(timeout=1)
    if inspect.isawaitable(result):
        if inspect.iscoroutine(result):
            result.close()
        return {"ok": False, "error": "async tool — register a sync plugin node instead"}
    out: dict[str, Any] = {"tool": name, "output": result}
    if isinstance(result, dict):
        out["data"] = result
    if isinstance(result, str) and result.lstrip().startswith("{"):
        # Most host tools return tool_json(...) text; expose it so a Branch can
        # read e.g. data.compile.numErrors.
        try:
            parsed = json.loads(result)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            out["data"] = parsed
    data = out.get("data")
    if isinstance(data, dict) and data.get("ok") is False:
        return {"ok": False, "error": str(data.get("error") or f"{name} failed"), "result": out}
    return {"ok": True, "result": out}


# --------------------------------------------------------------------------- reusable workflows


def _template(value: Any, payload: dict[str, Any]) -> Any:
    """``{{field.path}}`` reads an earlier step's value: alone it keeps the value's
    type (a list stays a list), inside other text it becomes text."""
    if isinstance(value, dict):
        return {key: _template(item, payload) for key, item in value.items()}
    if isinstance(value, list):
        return [_template(item, payload) for item in value]
    if not isinstance(value, str):
        return value
    whole = _PLACEHOLDER.fullmatch(value.strip())
    if whole:
        return _payload_get(payload, whole.group(1))
    return _PLACEHOLDER.sub(lambda m: _as_text(_payload_get(payload, m.group(1))), value)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _named_rows(raw: Any) -> list[tuple[str, dict[str, Any]]]:
    rows = raw if isinstance(raw, list) else []
    return [(str(row.get("name") or "").strip(), row) for row in rows if isinstance(row, dict) and str(row.get("name") or "").strip()]


def _input_node(cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Each declared input: the value passed in, else its default (a Test run)."""
    values: dict[str, Any] = {}
    for name, row in _named_rows(cfg.get("inputs")):
        passed = payload.get(name)
        values[name] = passed if passed not in (None, "") else str(row.get("default") or "")
    return {"ok": True, "result": values}


def _output_node(cfg: dict[str, Any], payload: dict[str, Any], wired: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect the return values: a wired pin first, else its value (a blank value
    returns the field of the same name)."""
    values: dict[str, Any] = {}
    for name, row in _named_rows(cfg.get("outputs")):
        raw = row.get("value")
        if wired and name in wired:
            values[name] = wired[name]
        else:
            values[name] = payload.get(name) if raw in (None, "") else _template(raw, payload)
    earlier = payload.get(_RETURN_KEY) if isinstance(payload.get(_RETURN_KEY), dict) else {}
    return {"ok": True, "result": {_RETURN_KEY: {**earlier, **values}}}


def _call_workflow(cfg: dict[str, Any], payload: dict[str, Any], wired: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run another workflow like a function and merge what it returns into this run.

    ``share`` runs it on a copy of this run's fields and brings every field back, as
    if its nodes sat here (a group turned into a workflow). If it has Return nodes and
    none is reached, this path stops, like a path that ended inside the group."""
    wid = str(cfg.get("workflow_id") or "").strip()
    if not wid:
        return {"ok": False, "error": "Choose the workflow to run."}
    stack = _CALL_STACK.get()
    if wid in stack:
        return {"ok": False, "error": "A workflow can't run itself, directly or through another workflow."}
    if len(stack) >= _CALL_DEPTH_CAP:
        return {"ok": False, "error": f"Workflows can run other workflows at most {_CALL_DEPTH_CAP} deep."}
    wf = get_workflow(wid)
    if wf is None:
        return {"ok": False, "error": "The workflow this node runs was deleted or isn't shared with you."}
    name = str(wf.get("name") or wid)
    graph = wf.get("graph") or {}
    nodes = {str(n.get("id")): n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    flow = _Dataflow(nodes, edges, catalog.node_specs())
    starts = [nid for nid, node in nodes.items() if node.get("type") == "flow.input"]
    starts = starts or _start_ids(nodes, edges=edges, trigger_id="", starter_id="", is_step=flow.is_step)
    if not starts and not flow.data_nodes:
        return {"ok": False, "error": f"{name} has no start."}
    args = cfg.get("args") if isinstance(cfg.get("args"), dict) else {}
    share = bool(cfg.get("share"))
    if share:
        ctx: dict[str, Any] = {key: value for key, value in payload.items() if key != _RETURN_KEY}
    else:
        ctx = {key: payload[key] for key in _RUN_PLUMBING if key in payload}
    ctx.update({str(key).strip(): _template(value, payload) for key, value in args.items() if str(key).strip()})
    ctx.update({str(key): value for key, value in (wired or {}).items()})  # wired pins win over typed values
    for key in _PERSON_KEYS:
        ctx.pop(key, None)  # who started the run comes with the run, never as a field
    _prepare_run_ctx(ctx, wf)
    new_hub = ctx.get("group_id") and ctx.get("group_id") != payload.get("group_id")
    ident_token = _bind_hub_identity(ctx) if new_hub else None
    stack_token = _CALL_STACK.set((*stack, wid))
    live_token = _LIVE.set((wid, uuid.uuid4().hex[:12]))  # its own steps light up in its own editor
    started = time.time()
    try:
        steps, ok, error, _seen = _walk(nodes, edges, ctx, starts, flow=flow) if starts else ([], True, "", 0)
        if ok:
            ok, error = flow.run_sinks(ctx)
        steps = list(flow.order)
    finally:
        _LIVE.reset(live_token)
        _CALL_STACK.reset(stack_token)
        if ident_token is not None:
            from backend.workspace import identity

            identity.reset(ident_token)
    # The called workflow's own log shows this run, so it can be debugged on its own.
    append_run(wid, {"started": started, "ended": time.time(), "ok": ok, "error": error,
                     "trigger_id": "workflow.call", "steps": steps})
    if not ok:
        return {"ok": False, "error": f"{name}: {error}", "substeps": steps}
    returned = dict(ctx.get(_RETURN_KEY) or {})
    back = {key: value for key, value in ctx.items() if key not in _NOT_SHARED_BACK} if share else {}
    out: dict[str, Any] = {"ok": True, "result": {**back, **returned, "returned": returned}, "substeps": steps}
    if _RETURN_KEY not in ctx and any(node.get("type") == "flow.output" for node in nodes.values()):
        out["stop"] = True  # no Return reached: the caller's path ends here too
    return out
