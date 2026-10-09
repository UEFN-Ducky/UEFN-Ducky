"""One place for a desktop plugin's errors, and a test run of an AI plugin draft.

- ``ducky_plugin_errors(id, since)``: backend load errors, panel crashes, errors and
  ``console.error`` inside its panels, exceptions from its MCP tools and errors from
  its workflow nodes; newest first.
- ``ducky_plugin_test(id)``: installs the draft the usual way, then calls each of its
  MCP tools with safe sample input (skipping the ones it marks destructive), runs each
  workflow node it registers with a minimal ctx, opens each of its panels and collects
  what went wrong there, and checks its UI files. A pass / fail / skip report per
  check. The whole test runs on a throwaway copy of that plugin's data
  (:func:`scopes.sandbox_begin`: every thread, the plugin's own too; other plugins
  untouched), so nothing it does reaches the user's data. An AI plugin the user
  hasn't confirmed answers ``needs_trust`` like turning it on does; it never runs.

The recorders (:func:`record_tool_error`, :func:`record_node_error`,
:func:`record_panel_error`) are what the MCP server, the workflow runner and the
panel bridge call; load errors and panel crashes were recorded before this module.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json

# events kind → what ducky_plugin_errors calls it.
SOURCES = {
    "plugin_load_error": "load",
    "ui_crash": "panel_crash",
    "plugin_panel_error": "panel",
    "plugin_tool_error": "tool",
    "plugin_node_error": "node",
}
_KEEP = 500
_MAX_AGE_S = 30 * 86400
_FILE = "plugin_errors.jsonl"
_PANEL_SETTLE_S = 4.0
_TOOL_TIMEOUT_S = 30.0
# How long a timed-out test call keeps the plugin's data on the test copy.
_STRAGGLER_WAIT_S = 600.0


# --------------------------------------------------------------------------- recording


def record(kind: str, plugin_id: str, message: str, **detail: Any) -> None:
    """One plugin error into the events log (``plugin_errors.jsonl`` when the log
    isn't in the database). Never raises."""
    pid = str(plugin_id or "").strip().lower()
    if not pid or kind not in SOURCES:
        return
    from backend.util.privacy import scrub

    now = time.time()
    row = {"ts": now, "plugin_id": pid, "message": scrub(str(message or ""))[:2000],
           **{k: (scrub(v) if isinstance(v, str) else v) for k, v in detail.items() if v not in (None, "")}}
    try:
        from backend.store.switch import use_db

        if use_db("events"):
            from backend.store.repos import events

            events.insert(kind, ts=now, source=pid, message=row["message"], payload=row)
            events.trim(kind, older_than=now - _MAX_AGE_S, keep=_KEEP)
            return
    except Exception:
        pass
    try:
        from frontend.settings import default_app_data_dir

        path = default_app_data_dir() / _FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, **row}, ensure_ascii=False) + "\n")
    except Exception:
        pass


def record_tool_error(tool_name: str, exc: BaseException) -> None:
    """An MCP tool raised: attribute it to the plugin that registered the tool."""
    try:
        from backend.uefn_plugins.host import is_plugin_enabled, plugin_for_tool

        pid = plugin_for_tool(tool_name)
        if pid == "ducky" or not is_plugin_enabled(pid):
            return  # core tools, and the "plugin is off" refusal, aren't the plugin's errors
    except Exception:
        return
    record("plugin_tool_error", pid, f"{type(exc).__name__}: {exc}", tool=tool_name)


def record_node_error(node_type: str, error: Any) -> None:
    """A workflow node a plugin registered raised or returned an error."""
    try:
        from backend.automations.plugin import handler_plugin_id

        pid = handler_plugin_id(node_type)
    except Exception:
        return
    message = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error or "")
    record("plugin_node_error", pid, message or "node failed", node=node_type)


def record_panel_error(plugin_id: str, panel_id: str, kind: str, message: str, stack: str = "") -> None:
    """An uncaught error, a rejected promise or ``console.error`` inside a plugin panel
    (reported by the script webview.py puts in every panel page)."""
    record("plugin_panel_error", plugin_id, message, panel=str(panel_id or "")[:64],
           kind=str(kind or "error")[:16], stack=str(stack or "")[:4000])


# --------------------------------------------------------------------------- reading


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _view(source: str, ts: float, message: str, payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"ts": ts, "when": _when(ts), "source": source, "message": message}
    for key in ("panel", "tool", "node", "kind", "surface"):
        if payload.get(key):
            out[key] = payload[key]
    stack = payload.get("stack")
    if stack:
        out["stack"] = str(stack)[:1500]
    return out


def _from_db(pid: str, since: float, limit: int) -> list[dict[str, Any]]:
    from backend.store.repos import events

    rows: list[dict[str, Any]] = []
    for kind, source in SOURCES.items():
        if kind == "ui_crash":
            # Crash rows are keyed by surface; the plugin is in the payload.
            found = [r for r in events.newest(kind, limit=_KEEP, since=since)
                     if str((r.get("payload") or {}).get("pluginId") or "").lower() == pid][:limit]
        else:
            found = events.newest_from(kind, pid, limit=limit, since=since)
        for r in found:
            payload = r.get("payload") if isinstance(r.get("payload"), dict) else {}
            rows.append(_view(source, float(r["ts"]), str(r["message"] or payload.get("error") or ""), payload))
    return rows


def _from_files(pid: str, since: float) -> list[dict[str, Any]]:
    """The same when the events log isn't in the database (``DUCKY_STORE_BACKEND=files``)."""
    from frontend.settings import default_app_data_dir

    rows: list[dict[str, Any]] = []
    for name in ("uefn_plugin_load_errors.jsonl", "ui_crashes.jsonl", _FILE):
        try:
            lines = (default_app_data_dir() / name).read_text(encoding="utf-8").splitlines()[-2000:]
        except OSError:
            continue
        for line in lines:
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            owner = str(raw.get("plugin_id") or raw.get("pluginId") or "").lower()
            ts = float(raw.get("ts") or 0)
            if owner != pid or ts < since:
                continue
            kind = str(raw.get("kind") or ("ui_crash" if name == "ui_crashes.jsonl" else "plugin_load_error"))
            rows.append(_view(SOURCES.get(kind, kind), ts, str(raw.get("message") or raw.get("error") or ""), raw))
    return rows


def plugin_errors(plugin_id: str, *, since: float = 0.0, limit: int = 50) -> dict[str, Any]:
    """A plugin's errors from every source, newest first (``since``: epoch seconds)."""
    from backend.store.switch import use_db
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    since = max(0.0, float(since or 0))
    limit = max(1, min(int(limit or 50), 200))
    rows = _from_db(pid, since, limit) if use_db("events") else _from_files(pid, since)
    rows.sort(key=lambda r: r["ts"], reverse=True)
    rows = rows[:limit]
    return {"ok": True, "id": pid, "since": since, "errors": rows, "counts": dict(Counter(r["source"] for r in rows))}


# --------------------------------------------------------------------------- test run


def _sample(schema: Any) -> Any:
    """Safe sample input for one JSON-schema property: its default, else an empty value."""
    if not isinstance(schema, dict):
        return ""
    if "default" in schema:
        return schema["default"]
    if schema.get("enum"):
        return schema["enum"][0]
    kinds = schema.get("type")
    if isinstance(kinds, list):
        kinds = next((k for k in kinds if k != "null"), "string")
    if not kinds and isinstance(schema.get("anyOf"), list):
        return _sample(next((s for s in schema["anyOf"] if s.get("type") != "null"), {}))
    return {"string": "", "integer": 0, "number": 0, "boolean": False, "array": [], "object": {}}.get(
        str(kinds or "string"), ""
    )


def sample_args(parameters: Any) -> dict[str, Any]:
    """Required arguments only, each with a safe sample value."""
    if not isinstance(parameters, dict):
        return {}
    props = parameters.get("properties") if isinstance(parameters.get("properties"), dict) else {}
    return {name: _sample(props.get(name)) for name in parameters.get("required") or [] if name in props}


def _call(stragglers: list[threading.Thread], fn: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Call a tool or node handler, sync or async, on a worker that carries this
    context and gives up after ``_TOOL_TIMEOUT_S`` (the worker goes on ``stragglers``:
    the plugin's sandbox stays until it ends)."""
    box: dict[str, Any] = {}
    ctx = contextvars.copy_context()

    def call() -> Any:
        out = fn(*args, **kwargs)
        return asyncio.run(out) if inspect.iscoroutine(out) else out

    def run() -> None:
        try:
            box["result"] = ctx.run(call)
        except BaseException as exc:  # noqa: BLE001 — handed back to the caller
            box["error"] = exc

    worker = threading.Thread(target=run, name="plugin-test-call", daemon=True)
    worker.start()
    worker.join(_TOOL_TIMEOUT_S)
    if worker.is_alive():
        stragglers.append(worker)
        raise TimeoutError(f"no answer in {_TOOL_TIMEOUT_S:.0f} s")
    if "error" in box:
        raise box["error"]
    return box.get("result")


def _brief(value: Any, limit: int = 300) -> str:
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _returned_error(result: Any) -> str:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return ""
    if isinstance(result, dict) and result.get("ok") is False:
        return str(result.get("error") or "returned ok: false")
    return ""


def _check_tools(pid: str, checks: list[dict[str, Any]], stragglers: list[threading.Thread]) -> None:
    from backend.uefn_plugins.host import get_contributions

    meta = (get_contributions().get("agent_tools") or {}).get(pid) or {}
    names = sorted(str(n) for n in meta.get("tools") or [])
    destructive = {str(n) for n in meta.get("destructive_tools") or []}
    if not names:
        checks.append({"check": "tools", "status": "fail",
                       "detail": "No MCP tools registered. Every action needs an @api.tool()."})
        return
    for name in names:
        label = f"tool {name}"
        if name in destructive:
            checks.append({"check": label, "status": "skip", "detail": "marked destructive in agent.tools"})
            continue
        tool = mcp._tool_manager.get_tool(name)
        if tool is None:
            checks.append({"check": label, "status": "fail", "detail": "listed by the plugin but not on the MCP server"})
            continue
        args = sample_args(getattr(tool, "parameters", None))
        try:
            result = _call(stragglers, tool.fn, **args)
        except Exception as exc:  # noqa: BLE001 — that is what the check reports
            checks.append({"check": label, "status": "fail", "input": args, "detail": f"{type(exc).__name__}: {exc}"})
            continue
        row = {"check": label, "status": "pass", "input": args, "result": _brief(result)}
        said = _returned_error(result)
        if said:
            row["note"] = f"answered with an error for sample input: {said}"
        checks.append(row)


def _check_nodes(pid: str, manifest: dict[str, Any], checks: list[dict[str, Any]],
                 stragglers: list[threading.Thread]) -> None:
    from backend.automations.plugin import get_handler, node_types

    declared = {
        str(n.get("id") or ""): n
        for n in ((manifest.get("contributes") or {}).get("automations") or {}).get("nodes") or []
        if isinstance(n, dict)
    }
    mine = [ntype for ntype, owner in node_types() if owner == pid]
    if not mine:
        checks.append({"check": "workflow nodes", "status": "fail",
                       "detail": "No workflow node registered (@api.register_pipeline_node)."})
        return
    for ntype in mine:
        label = f"node {ntype}"
        handler = get_handler(ntype)
        if handler is None:
            checks.append({"check": label, "status": "fail", "detail": "no handler (is the plugin enabled?)"})
            continue
        fields = (declared.get(ntype) or {}).get("config_fields") or []
        config = {f["id"]: f["default"] for f in fields if isinstance(f, dict) and "id" in f and "default" in f}
        with tempfile.TemporaryDirectory(prefix="ducky-plugin-test-") as artifacts:
            ctx = {"config": config, "payload": {}, "node": {"id": "test", "type": ntype, "config": config},
                   "inputs": {}, "kind": "pipeline", "files": [], "artifact_dir": artifacts}
            try:
                result = _call(stragglers, handler, ctx)
            except Exception as exc:  # noqa: BLE001
                checks.append({"check": label, "status": "fail", "detail": f"{type(exc).__name__}: {exc}"})
                continue
        if not isinstance(result, dict):
            checks.append({"check": label, "status": "fail",
                           "detail": f"returned {type(result).__name__}; a node returns a dict like {{\"ok\": true}}"})
            continue
        row = {"check": label, "status": "pass", "result": _brief(result)}
        said = _returned_error(result)
        if said:
            row["note"] = f"answered with an error for an empty config: {said}"
        checks.append(row)


def _check_panels(pid: str, manifest: dict[str, Any], started: float, checks: list[dict[str, Any]]) -> None:
    from backend.panel.rpc import panel_rpc

    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    panels = [p for p in contributes.get("ui.panels") or contributes.get("ui_panels") or [] if isinstance(p, dict)]
    if not panels:
        return
    opened: list[str] = []
    for panel in panels:
        panel_id = str(panel.get("id") or "").strip().lower()
        out = panel_rpc("open_plugin_panel", {"plugin_id": pid, "panel_id": panel_id,
                                              "title": str(panel.get("title") or panel_id)}, timeout=10.0)
        if out.get("error"):
            checks.append({"check": f"panel {panel_id}", "status": "skip",
                           "detail": f"couldn't open it ({out['error']}); open Ducky and run the test again"})
            continue
        opened.append(panel_id)
    if not opened:
        return
    time.sleep(_PANEL_SETTLE_S)  # let the panels load, run their first calls and report
    found = plugin_errors(pid, since=started, limit=200).get("errors") or []
    for panel_id in opened:
        mine = [e for e in found if e["source"] in ("panel", "panel_crash") and e.get("panel", panel_id) == panel_id]
        if mine:
            checks.append({"check": f"panel {panel_id}", "status": "fail", "errors": mine[:10]})
        else:
            checks.append({"check": f"panel {panel_id}", "status": "pass", "detail": "opened with no errors"})


def test_plugin(plugin_id: str) -> dict[str, Any]:
    """Install the draft, then check its tools, nodes, panels and UI files."""
    from backend.tools.panel.panel_ai_plugins import _draft_root, install_ai_plugin
    from backend.uefn_plugins import scopes
    from backend.uefn_plugins.host import is_plugin_enabled, set_active_uefn_agent_plugin_ids, wait_plugin_toggles
    from backend.uefn_plugins.plugin_lint import check_ui
    from backend.uefn_plugins.store import normalize_plugin_id

    try:
        pid = normalize_plugin_id(plugin_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    started = time.time()
    checks: list[dict[str, Any]] = []
    root = _draft_root(pid)
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": f"draft not found: {pid} (ducky_plugin_list shows the drafts)"}

    ui = check_ui(root, manifest)
    checks.append({"check": "ui files", "status": "fail" if ui else "pass", **({"errors": ui} if ui else {})})
    # From here to the end the plugin's data, from any thread (its own too), is a throwaway
    # copy: the reinstall's register(), its tools and nodes, its panels.
    try:
        scopes.sandbox_begin(pid)
    except ValueError as exc:
        return {"ok": False, "id": pid, "error": str(exc)}
    stragglers: list[threading.Thread] = []
    try:
        installed = install_ai_plugin(pid)
        if not installed.get("ok"):
            checks.append({"check": "install", "status": "fail",
                           "errors": installed.get("errors") or [installed.get("error") or "install failed"]})
            return _report(pid, started, checks)
        checks.append({"check": "install", "status": "pass", "detail": "validated and installed"})
        if not is_plugin_enabled(pid):
            turned_on = _turn_on(pid)
            if turned_on.get("needs_trust"):
                return {**_report(pid, started, checks), **turned_on}
            if not turned_on.get("ok"):
                checks.append({"check": "turn on", "status": "fail",
                               "detail": str(turned_on.get("error") or "couldn't turn the plugin on")})
                return _report(pid, started, checks)
        wait_plugin_toggles(timeout=15.0)  # the reinstall registers the new backend in the background

        def run() -> None:
            # Every tool of this plugin, whatever this chat opted into.
            set_active_uefn_agent_plugin_ids(None)
            _check_tools(pid, checks, stragglers)
            _check_nodes(pid, manifest, checks, stragglers)

        contextvars.copy_context().run(run)
        _check_panels(pid, manifest, started, checks)
        loads = [e for e in plugin_errors(pid, since=started, limit=50).get("errors") or [] if e["source"] == "load"]
        checks.append({"check": "backend load", "status": "fail" if loads else "pass",
                       **({"errors": loads} if loads else {})})
        report = _report(pid, started, checks)
        if any(t.is_alive() for t in stragglers):
            report["note"] = (
                "A call that timed out is still running: the plugin's data stays the test copy until it "
                f"ends (at most {_STRAGGLER_WAIT_S // 60:.0f} min)."
            )
        return report
    finally:
        _end_sandbox(pid, stragglers)


def _turn_on(pid: str) -> dict[str, Any]:
    """Turn the plugin on the way ``ducky_store_set_enabled`` does. An AI plugin the user
    hasn't confirmed answers ``needs_trust`` (and Ducky asks the user), never runs."""
    from backend.tools.panel.panel_store import ducky_store_set_enabled

    try:
        out = json.loads(ducky_store_set_enabled(pid, True))
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    if out.get("needs_trust"):
        return {
            **out,
            "ok": False,
            "error": (
                "Confirm once to test this plugin: it's an unofficial plugin and runs with the app's "
                "permissions. Ducky asked in Settings → Store; run ducky_plugin_test again after the user "
                "confirms."
            ),
        }
    return out


def _end_sandbox(pid: str, stragglers: list[threading.Thread]) -> None:
    """The plugin's data is the user's again once no test call of it is still running
    (a hung one is waited for at most ``_STRAGGLER_WAIT_S``). Open panels re-read."""
    from backend.uefn_plugins import scopes

    alive = [t for t in stragglers if t.is_alive()]

    def end() -> None:
        deadline = time.monotonic() + _STRAGGLER_WAIT_S
        for worker in alive:
            worker.join(max(0.0, deadline - time.monotonic()))
        scopes.sandbox_end(pid)
        try:
            from frontend.ui_web.agent_modes import push_ui_event

            push_ui_event({"type": "plugin_scope_changed", "plugins": [pid]})
        except Exception:
            pass

    if alive:
        threading.Thread(target=end, name="plugin-test-sandbox", daemon=True).start()
    else:
        end()


def _report(pid: str, started: float, checks: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [c["check"] for c in checks if c["status"] == "fail"]
    return {
        "ok": not failed,
        "id": pid,
        "failed": failed,
        "passed": sum(1 for c in checks if c["status"] == "pass"),
        "skipped": sum(1 for c in checks if c["status"] == "skip"),
        "checks": checks,
        "errors_since": started,
        "seconds": round(time.time() - started, 1),
    }


@mcp.tool()
def ducky_plugin_errors(id: str, since: float = 0, limit: int = 50, pretty: bool = False) -> str:
    """Everything that went wrong in one desktop plugin, newest first: backend load
    errors (source "load"), panel crashes ("panel_crash"), errors and console.error
    inside its panels ("panel"), exceptions from its MCP tools ("tool") and errors from
    its workflow nodes ("node"). `since`: epoch seconds (e.g. when you installed it).
    Returns {errors: [{when, source, message, panel?, tool?, node?, stack?}], counts}.
    """
    return tool_json(plugin_errors(id, since=since, limit=limit), pretty=pretty)


@mcp.tool()
def ducky_plugin_test(id: str, pretty: bool = False) -> str:
    """Test an AI plugin draft end to end: validate + install it (same path as
    ducky_plugin_install), call each of its MCP tools with safe sample input (tools in
    agent.tools.destructive_tools are skipped), run each workflow node it registers with
    a minimal ctx, open each of its panels and collect the errors that showed up, and
    check its UI files. The whole test works on a throwaway copy of the plugin's data,
    so the user's data is never touched. A plugin that is off is turned on first; an AI
    plugin the user hasn't confirmed returns needs_trust (stop: the user confirms once
    in Settings → Store, then test again). Returns a pass / fail / skip row per check;
    fix the failures in the draft and run it again.
    """
    return tool_json(test_plugin(id), pretty=pretty)
