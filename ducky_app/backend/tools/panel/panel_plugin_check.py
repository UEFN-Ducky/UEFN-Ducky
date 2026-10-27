"""One place for a desktop plugin's errors, and a test run of an AI plugin draft.

- ``ducky_plugin_errors(id, since)``: backend load errors, panel crashes, errors and
  ``console.error`` inside its panels, exceptions from its MCP tools and errors from
  its workflow nodes; newest first.
- ``ducky_plugin_test(id)``: installs the draft the usual way, then calls each of its
  MCP tools with safe sample input (skipping the ones it marks destructive), runs each
  workflow node it registers with a minimal ctx, opens each of its panels and collects
  what went wrong there, and checks its UI files. A pass / fail / skip report per
  check. The test's calls run on a throwaway copy of that plugin's data
  (:func:`scopes.sandbox_begin`: the test's calls and the threads they start; other
  agents' calls and other plugins untouched), so nothing it does reaches the user's
  data or hides it from anyone. The user's copy is fingerprinted before and after the
  tool and node calls: a call that reached it anyway (a worker the plugin started
  outside the test, a path of its own) fails the "user data untouched" check. An AI plugin the user
  hasn't confirmed answers ``needs_trust`` like turning it on does; it never runs.
  A draft linked to a repo (``repo=`` once, remembered) is first synced from the
  repo's working tree, so the test never runs on files that fell behind.

The recorders (:func:`record_tool_error`, :func:`record_node_error`,
:func:`record_panel_error`) are what the MCP server, the workflow runner and the
panel bridge call; load errors and panel crashes were recorded before this module.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import subprocess
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
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
    "plugin_sync_error": "sync",
}
_KEEP = 500
_MAX_AGE_S = 30 * 86400
_FILE = "plugin_errors.jsonl"
_PANEL_SETTLE_S = 4.0
_TOOL_TIMEOUT_S = 30.0
# How long a timed-out test call keeps the plugin's data on the test copy.
_STRAGGLER_WAIT_S = 600.0
# A tool or node that waits by design (it takes a timeout) gets this one in the test, so
# it answers well inside _TOOL_TIMEOUT_S; one that ignores it still fails as a hang.
_WAIT_TEST_S = 3
_TIMEOUT_FIELDS = {"timeout": 1, "timeout_s": 1, "timeout_sec": 1, "timeout_seconds": 1, "timeout_ms": 1000}


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


# --------------------------------------------------------------------------- repo sync

def linked_repo(plugin_id: str) -> str:
    """The repo folder a draft was last tested from ("" when none)."""
    from backend.uefn_plugins.store import ai_draft_repo_links

    return ai_draft_repo_links().get(str(plugin_id or "").strip().lower(), "")


def _repo_files(repo: Path) -> list[str]:
    """The repo's working-tree files (tracked + untracked, .gitignore respected), as
    posix paths; a folder that isn't a git repo is walked."""
    from backend.tools.panel.panel_ai_plugins import _SECRET_SUFFIXES, _SKIP_DIR_NAMES

    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=repo, capture_output=True, timeout=30, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.decode("utf-8", "replace")
        rels = [r for r in out.split("\0") if r]
    except (OSError, subprocess.SubprocessError):
        rels = [p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file()]
    return sorted(
        r for r in rels
        if not any(part in _SKIP_DIR_NAMES for part in r.split("/"))
        and (Path(r).suffix or Path(r).name).lower() not in _SECRET_SUFFIXES  # ".env" has no suffix
        and (repo / r).is_file()  # deleted in the working tree but still tracked
    )


def sync_from_repo(plugin_id: str, repo: str = "") -> dict[str, Any]:
    """Copy every repo file whose bytes differ into the draft (``repo``: a folder with
    this plugin's plugin.json; empty = the linked one). Draft files the repo doesn't
    have are listed, never deleted. A given ``repo`` becomes the link. The draft is
    AppData, not a project: store.py writes it, outside the project write pipeline."""
    from backend.tools.panel.panel_ai_plugins import _draft_root
    from backend.uefn_plugins.store import link_ai_draft_repo, normalize_plugin_id, write_ai_draft_bytes

    pid = normalize_plugin_id(plugin_id)
    raw = str(repo or "").strip() or linked_repo(pid)
    if not raw:
        return {"ok": True, "repo": "", "synced": []}
    src = Path(raw).expanduser().resolve()
    try:
        theirs = json.loads((src / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": f"no plugin.json in the repo folder {src}"}
    if str((theirs or {}).get("id") or "").strip().lower() != pid:
        return {"ok": False, "error": f"{src} holds plugin {theirs.get('id')!r}, not {pid!r}"}
    draft = _draft_root(pid)
    files = _repo_files(src)
    synced: list[str] = []
    for rel in files:
        data = (src / rel).read_bytes()
        dst = draft / rel
        if dst.is_file() and dst.read_bytes() == data:
            continue
        write_ai_draft_bytes(pid, rel, data)
        synced.append(rel)
    have = set(files)
    draft_only = sorted(
        p.relative_to(draft).as_posix() for p in draft.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.relative_to(draft).as_posix() not in have
    )
    if str(repo or "").strip() and linked_repo(pid) != str(src):
        link_ai_draft_repo(pid, str(src))
    return {"ok": True, "repo": str(src), "synced": synced, **({"draft_only": draft_only} if draft_only else {})}


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


def short_timeouts(fields: Any) -> dict[str, Any]:
    """The test's short value for each numeric timeout field (``fields``: name → its
    JSON schema or workflow config field), so a wait-style tool or node answers in
    seconds instead of waiting minutes for something the test never does."""
    out: dict[str, Any] = {}
    for name, spec in (fields or {}).items() if isinstance(fields, dict) else ():
        scale = _TIMEOUT_FIELDS.get(str(name).lower())
        kind = spec.get("type") if isinstance(spec, dict) else None
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), None)
        if scale and kind in (None, "number", "integer"):
            out[name] = _WAIT_TEST_S * scale
    return out


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
        params = getattr(tool, "parameters", None)
        args = sample_args(params)
        waits = short_timeouts(params.get("properties") if isinstance(params, dict) else None)
        args.update(waits)
        try:
            result = _call(stragglers, tool.fn, **args)
        except Exception as exc:  # noqa: BLE001 — that is what the check reports
            checks.append({"check": label, "status": "fail", "input": args, "detail": f"{type(exc).__name__}: {exc}"})
            continue
        checks.append(_answered(label, result, waits, "sample input", input=args))


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
        waits = short_timeouts({f["id"]: f for f in fields if isinstance(f, dict) and "id" in f})
        config.update(waits)
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
        checks.append(_answered(label, result, waits, "an empty config"))


def _answered(label: str, result: Any, waits: dict[str, Any], given: str, **extra: Any) -> dict[str, Any]:
    """The pass row of a call that answered. A waiter that gave up after the test's
    short timeout answered cleanly: that is a pass, not an error."""
    row = {"check": label, "status": "pass", **extra, "result": _brief(result)}
    if waits:
        row["detail"] = f"waits by design: tested with {_WAIT_TEST_S} s instead of its own timeout"
    said = _returned_error(result)
    if said and not (waits and _gave_up(result)):
        row["note"] = f"answered with an error for {given}: {said}"
    return row


def _gave_up(result: Any) -> bool:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return False
    return isinstance(result, dict) and bool(result.get("timed_out") or result.get("timeout"))


def _check_panels(pid: str, manifest: dict[str, Any], started: float, checks: list[dict[str, Any]]) -> None:
    from backend.panel.rpc import panel_rpc

    contributes = manifest.get("contributes") if isinstance(manifest.get("contributes"), dict) else {}
    panels = [p for p in contributes.get("ui.panels") or contributes.get("ui_panels") or [] if isinstance(p, dict)]
    if not panels:
        return
    from backend.uefn_plugins import team_folders

    if team_folders.plugin_folders(pid):
        # Its folders are the test's while it runs, and calls from outside the test (its
        # panels' among them) are refused meanwhile: open its panels after the test.
        for panel in panels:
            checks.append({"check": f"panel {str(panel.get('id') or '').strip().lower()}", "status": "skip",
                           "detail": "this plugin keeps its own folders: open its panels after the test"})
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


def test_plugin(plugin_id: str, repo: str = "") -> dict[str, Any]:
    """Sync the draft from its repo (if linked), install it, then check its tools,
    nodes, panels and UI files."""
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
    synced = sync_from_repo(pid, repo)
    if not synced.get("ok"):
        return {"ok": False, "id": pid, "error": synced["error"]}
    if synced["repo"]:
        n = len(synced["synced"])
        checks.append({"check": "repo sync", "status": "pass",
                       "detail": f"synced {n} file{'' if n == 1 else 's'} from {Path(synced['repo']).name} before testing",
                       "files": synced["synced"][:100],
                       **({"draft_only": synced["draft_only"][:50]} if synced.get("draft_only") else {})})
    root = _draft_root(pid)
    try:
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": f"draft not found: {pid} (ducky_plugin_list shows the drafts)"}

    ui = check_ui(root, manifest)
    checks.append({"check": "ui files", "status": "fail" if ui else "pass", **({"errors": ui} if ui else {})})
    # The reinstall is an ordinary install: register() and the workers it starts serve
    # everyone, so they never belong to the test copy (a worker bound to it would lose the
    # real data when the test ends).
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
    before = _real_data(pid)
    # From here the test's own calls (and the threads they start) use a throwaway copy of
    # the plugin's data; everyone else's calls keep the real data.
    try:
        scopes.sandbox_begin(pid)
    except ValueError as exc:
        return {"ok": False, "id": pid, "error": str(exc)}
    stragglers: list[threading.Thread] = []
    try:
        def run() -> None:
            # Every tool of this plugin, whatever this chat opted into.
            set_active_uefn_agent_plugin_ids(None)
            _check_tools(pid, checks, stragglers)
            _check_nodes(pid, manifest, checks, stragglers)

        contextvars.copy_context().run(run)
        # Before the panels: their calls come from outside the test and use the real data.
        checks.append(_check_untouched(before, _real_data(pid)))
        _check_panels(pid, manifest, started, checks)
        loads = [e for e in plugin_errors(pid, since=started, limit=50).get("errors") or [] if e["source"] == "load"]
        checks.append({"check": "backend load", "status": "fail" if loads else "pass",
                       **({"errors": loads} if loads else {})})
        checks.append(_check_search(pid))
        checks.append(_check_data_names(pid))
        report = _report(pid, started, checks)
        if any(t.is_alive() for t in stragglers):
            report["note"] = (
                "A call that timed out is still running: the plugin's data stays the test copy until it "
                f"ends (at most {_STRAGGLER_WAIT_S // 60:.0f} min)."
            )
        return report
    finally:
        _end_sandbox(pid, stragglers)


def _real_data(pid: str) -> dict[str, str]:
    """A fingerprint of the user's copy of ``pid``'s data: its rows (docs, files,
    cache, prefs) in every scope but test copies, and its files on disk."""
    from backend.store import db
    from backend.uefn_plugins import scopes

    test, n = scopes.SANDBOX_PREFIX, len(scopes.SANDBOX_PREFIX)
    out: dict[str, str] = {}
    conn = db.connect()
    for table, cols in (("plugin_data", "account_id, scope_id, kind, key, sha256, rev, deleted"),
                        ("plugin_kv", "account_id, scope_id, '', key, value, encrypted, 0")):
        for row in conn.execute(f"SELECT {cols} FROM {table} WHERE plugin_id=? AND substr(scope_id, 1, ?)!=?",
                                (pid, n, test)).fetchall():
            out[f"{table} {row[1] or 'local'}/{row[2] + '/' if row[2] else ''}{row[3]}"] = repr(row[4:])
    try:
        root = scopes.scopes_root(scopes.account_id())
        for scope_dir in root.iterdir() if root.is_dir() else ():
            if scope_dir.name.startswith(test):
                continue
            for path in (scope_dir / pid).rglob("*"):
                if path.is_file():
                    st = path.stat()
                    out[f"file {path.relative_to(root).as_posix()}"] = f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        pass
    return out


def _check_untouched(before: dict[str, str], after: dict[str, str]) -> dict[str, Any]:
    """The "user data untouched" row. Nothing is undone: the test can't tell a leaked
    write from a teammate's live call to the same plugin meanwhile."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    if not (added or removed or changed):
        return {"check": "user data untouched", "status": "pass",
                "detail": f"{len(before)} rows and files of the user's copy unchanged"}
    return {
        "check": "user data untouched", "status": "fail",
        "detail": (f"the user's copy changed while the tools and nodes ran ({len(added)} added, {len(changed)} "
                   f"changed, {len(removed)} removed): a test call reached the real data (a worker or path the "
                   "plugin keeps outside api data), or someone used the plugin meanwhile. Remove what the test "
                   "left and make that write go through api data."),
        **{k: v[:20] for k, v in (("added", added), ("changed", changed), ("removed", removed)) if v},
    }


def _turn_on(pid: str) -> dict[str, Any]:
    """Turn the plugin on the way ``ducky_store_set_enabled`` does. An AI plugin the user
    hasn't confirmed answers ``needs_trust`` (and Ducky asks the user), never runs."""
    from backend.tools.panel.panel_store import ducky_store_set_enabled

    from backend.uefn_plugins.store import set_uefn_plugin_enabled, swarm_trusts_draft

    if swarm_trusts_draft(pid):
        # The person trusted this swarm's drafts for testing (its Build card): no stop.
        try:
            out = set_uefn_plugin_enabled(pid, True, swarm_test=True)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        if out.get("ok"):
            from backend.tools.panel.panel_ai_plugins import _notify_plugins

            _notify_plugins()
            return out
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

            push_ui_event({"type": "plugin_scope_changed", "plugins": [pid], "status": True})
        except Exception:
            pass

    if alive:
        threading.Thread(target=end, name="plugin-test-sandbox", daemon=True).start()
    else:
        end()


def _check_data_names(pid: str) -> dict[str, Any]:
    """Can ``ducky_find`` name this plugin's docs? Docs per key prefix (``project.``) where
    most have no ``name`` / ``title`` / ``label`` are listed: agents find those only by key.
    Reads the test's own copy (what the tool calls just wrote). Never fails the test."""
    import re

    from backend.uefn_plugins.scopes import PluginData

    try:
        docs = PluginData(pid).items()
    except Exception as exc:  # noqa: BLE001
        return {"check": "findable data", "status": "skip", "detail": f"data unreadable ({type(exc).__name__})"}
    if not docs:
        return {"check": "findable data", "status": "skip", "detail": "no api.data docs to check"}
    counts: dict[str, list[int]] = {}
    for key, value in list(docs.items())[:2000]:
        prefix = re.split(r"[./:]", key, maxsplit=1)[0]
        named = isinstance(value, dict) and any(
            isinstance(value.get(f), str) and value[f].strip() for f in ("name", "title", "label"))
        seen = counts.setdefault(prefix, [0, 0])
        seen[0] += 1
        seen[1] += bool(named)
    unnamed = {p: n for p, (n, named) in sorted(counts.items()) if named * 2 < n}
    detail = f"{len(counts) - len(unnamed)} of {len(counts)} doc kinds have names ducky_find can match"
    if not unnamed:
        return {"check": "findable data", "status": "pass", "detail": detail}
    return {"check": "findable data", "status": "pass", "detail": detail, "name_these": [
        f"{p}.* ({n} docs): if people ask for these by name, add a name, title or label field"
        for p, n in unnamed.items()]}


def _check_search(pid: str) -> dict[str, Any]:
    """Can skill and tool search find this plugin? Each of its skill files and tools is
    searched by its own description; anything outside the top 5 is listed with the row
    that beat it. Never fails the test: it says what to reword."""
    try:
        from backend.search import indexer, query
        from backend.store.repos import search as repo

        indexer.refresh_now()
        ids = [i for kind in ("subskill", "tool") for i in repo.find_ids(kind=kind)]
        docs = [d for d in repo.get_docs(ids).values() if d.get("plugin_id") == pid]
    except Exception as exc:  # noqa: BLE001
        return {"check": "search", "status": "skip", "detail": f"search index unavailable ({type(exc).__name__})"}
    if not docs:
        return {"check": "search", "status": "skip", "detail": "no skill files or tools to find"}
    weak: list[dict[str, Any]] = []
    all_tools = _all_tool_names()
    for doc in docs:
        meta = doc.get("meta") or {}
        text = str(meta.get("description") or doc.get("title") or "")
        if doc["kind"] == "tool":
            ctx = query.Context(allowed_tools=all_tools)
            rows = query.search(text, kinds=query.TOOL_KINDS, limit=10, context=ctx, refresh=False)
            keys = [r.get("tool") for r in rows]
            me = doc.get("tool_name")
            name = str(me)
        else:
            rows = query.search(text, limit=10, context=query.Context(), refresh=False)
            keys = [f"{r.get('pack')}/{r.get('subskill') or 'core'}" for r in rows]
            me = f"{doc.get('pack_id')}/{doc.get('subskill_id')}"
            name = str(me)
        rank = keys.index(me) + 1 if me in keys else None
        if rank is None or rank > 5:
            beaten_by = next((k for k in keys if k != me), "")
            weak.append({
                "item": name,
                "rank": rank,
                "searched": text[:120],
                "fix": ("Add a description" if not meta.get("description") else
                        f"Reword it: it reads closer to {beaten_by!r}" if beaten_by else "Reword it with the words people use"),
            })
    detail = f"{len(docs) - len(weak)} of {len(docs)} skill files and tools rank in the top 5 for their own description"
    return {"check": "search", "status": "pass", "detail": detail, **({"reword": weak} if weak else {})}


def _all_tool_names() -> set[str]:
    from backend.store.repos import search as repo

    return repo.tool_names()


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
def ducky_plugin_test(id: str, repo: str = "", pretty: bool = False) -> str:
    """Test an AI plugin draft end to end. `repo`: the plugin's repo folder (its
    plugin.json at the root); given once it is remembered, and every test first copies
    the repo's working-tree files that differ into the draft (the "repo sync" row lists
    them), so never copy files into the draft by hand. Then validate + install it (same path as
    ducky_plugin_install), call each of its MCP tools with safe sample input (tools in
    agent.tools.destructive_tools are skipped), run each workflow node it registers with
    a minimal ctx, open each of its panels and collect the errors that showed up, and
    check its UI files. The whole test works on a throwaway copy of the plugin's data,
    so the user's data is never touched. A plugin that is off is turned on first; an AI
    plugin the user hasn't confirmed returns needs_trust (stop: the user confirms once
    in Settings → Store, then test again). Returns a pass / fail / skip row per check;
    fix the failures in the draft and run it again.
    The test copy is the test's own: everyone else's calls keep their real data while it
    runs. It does reinstall the plugin, so in a team one member runs it for a batch of
    finished work; the others check their own work with ducky_plugin_validate and live
    calls, in parallel, never taking turns.
    """
    return tool_json(test_plugin(id, repo=repo), pretty=pretty)
