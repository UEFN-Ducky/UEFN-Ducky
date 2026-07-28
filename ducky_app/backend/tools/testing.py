"""MCP tools for the Tester Ducky suite — device graph sim, Verse harness, session probes."""

from __future__ import annotations

import json
from typing import Any

from backend.bridge import send_command
from backend.json_util import tool_json
from backend.tools.plugin_gate import plugin_mcp_tool
from backend.testing.device_sim import (
    device_graph_audit as _audit,
    scan_verse_devices_from_files,
    simulate_device_event as _simulate,
)
from backend.testing.verse_harness import (
    add_verse_test_case,
    compare_simulation_effects,
    list_harness_tests,
    load_simulation_scenario,
    parse_test_results,
    save_simulation_scenario,
    scaffold_content,
)


def _project_root() -> str:
    try:
        from frontend.settings import PanelSettings
        from frontend.ui_web.verse_editor.lsp.project_root import normalize_verse_lsp_project_root

        return normalize_verse_lsp_project_root(PanelSettings.load().uefn_project_root.strip())
    except Exception:
        return ""


def _snapshot_live(**kwargs: Any) -> dict:
    return send_command("device_graph_snapshot", kwargs)


@plugin_mcp_tool("tester")
def device_graph_snapshot(
    label_filter: str = "",
    class_filter: str = "",
    limit: int = 100,
    include_editables: bool = True,
    include_events: bool = True,
    pretty: bool = False,
) -> str:
    """Snapshot placed devices as nodes + wiring edges for offline simulation.

    Listener required. Returns nodes (label/class/kind/editables) and edges
    (verse @editable refs + creative event bindings). Use with simulate_device_event
    and device_graph_audit — no play session needed.
    """
    result = _snapshot_live(
        label_filter=label_filter,
        class_filter=class_filter,
        limit=limit,
        include_editables=include_editables,
        include_events=include_events,
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("tester")
def simulate_device_event(
    device: str,
    event: str = "InteractedWithEvent",
    snapshot_json: str = "",
    pretty: bool = False,
) -> str:
    """Offline: propagate an event through the device wiring graph.

    Pass a prior device_graph_snapshot as snapshot_json (JSON string), or leave
    empty to fetch a fresh snapshot (listener required). Returns an ordered
    trace of what fires and player-visible effects (grant_item, teleport, …).
    """
    if snapshot_json.strip():
        snapshot = json.loads(snapshot_json)
    else:
        snapshot = _snapshot_live(limit=100)
    result = _simulate(snapshot, device, event)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("tester")
def device_graph_audit(snapshot_json: str = "", pretty: bool = False) -> str:
    """Audit a device graph for unwired refs, orphans, cycles, missing spawn pads.

    Pass snapshot_json from device_graph_snapshot, or leave empty to fetch live.
    """
    if snapshot_json.strip():
        snapshot = json.loads(snapshot_json)
    else:
        snapshot = _snapshot_live(limit=100)
    return tool_json(_audit(snapshot), pretty=pretty)


@plugin_mcp_tool("tester")
def verse_test_scaffold(overwrite: bool = False, pretty: bool = False) -> str:
    """Write Verse/DuckyTests/ducky_test_device.verse assert harness (ExpectEqual/True/InRange).

    Place the device in the level after compile. Tests Print [DUCKY-TEST] PASS|FAIL lines
    on OnBegin — collect with verse_test_results after a play session.
    """
    from backend.bridge import resolve_workspace_path
    import os

    rel = "Verse/DuckyTests/ducky_test_device.verse"
    path = resolve_workspace_path(rel)
    if os.path.isfile(path) and not overwrite:
        return tool_json(
            {
                "ok": True,
                "path": rel,
                "created": False,
                "note": "already exists — pass overwrite=true to replace",
            },
            pretty=pretty,
        )
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    content = scaffold_content()
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return tool_json({"ok": True, "path": rel, "created": True, "bytes": len(content)}, pretty=pretty)


@plugin_mcp_tool("tester")
def verse_test_run(start_session: bool = False, pretty: bool = False) -> str:
    """Compile Verse, push changes, optionally start a play session for the harness.

    After the session runs OnBegin tests, call verse_test_results to parse PASS/FAIL.
    """
    from backend.tools import verse_diagnostics

    compile_raw = verse_diagnostics.workspace_compile_verse(pretty=False)
    try:
        compile_data = json.loads(compile_raw)
    except json.JSONDecodeError:
        compile_data = {"raw": compile_raw}
    push_data: dict[str, Any] = {}
    try:
        push_raw = verse_diagnostics.workspace_push_verse_changes(pretty=False)
        push_data = json.loads(push_raw)
    except Exception as exc:
        push_data = {"ok": False, "error": str(exc)}

    session: dict[str, Any] = {"started": False}
    if start_session:
        try:
            session = send_command("play_in_editor", {})
            session["started"] = True
        except Exception as exc:
            session = {"started": False, "error": str(exc)}

    return tool_json(
        {"compile": compile_data, "push": push_data, "session": session},
        pretty=pretty,
    )


@plugin_mcp_tool("tester")
def verse_test_results(
    last_n: int = 500,
    since_offset: int = 0,
    pretty: bool = False,
) -> str:
    """Parse [DUCKY-TEST] PASS|FAIL lines from the editor log into structured results."""
    log = send_command(
        "get_editor_log",
        {
            "last_n": last_n,
            "since_offset": since_offset,
            "regex": r"\[DUCKY-TEST\]",
        },
    )
    lines = list(log.get("lines") or [])
    parsed = parse_test_results(lines)
    parsed["log_offset"] = log.get("offset")
    parsed["log_file"] = log.get("file")
    if log.get("error"):
        parsed["log_error"] = log["error"]
    return tool_json(parsed, pretty=pretty)


@plugin_mcp_tool("tester")
def session_status(pretty: bool = False) -> str:
    """Whether a play/PIE session is active, plus world name."""
    return tool_json(send_command("session_status", {}), pretty=pretty)


@plugin_mcp_tool("tester")
def actor_state_snapshot(
    labels: list[str] | None = None,
    label_filter: str = "",
    limit: int = 50,
    pretty: bool = False,
) -> str:
    """Capture transforms of devices/actors for before/after movement checks."""
    return tool_json(
        send_command(
            "actor_state_snapshot",
            {"labels": labels or [], "label_filter": label_filter, "limit": limit},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("tester")
def actor_state_diff(
    before_json: str,
    after_json: str,
    epsilon: float = 1.0,
    pretty: bool = False,
) -> str:
    """Diff two actor_state_snapshot payloads (location/rotation/scale deltas)."""
    before = json.loads(before_json)
    after = json.loads(after_json)
    return tool_json(
        send_command(
            "actor_state_diff",
            {"before": before, "after": after, "epsilon": epsilon},
        ),
        pretty=pretty,
    )


@plugin_mcp_tool("tester")
def tester_list_devices(pretty: bool = False) -> str:
    """List devices for the Tester panel: live graph when listener online, else Verse sources."""
    root = _project_root()
    live: dict[str, Any] | None = None
    try:
        live = _snapshot_live(limit=100)
    except Exception as exc:
        live = None
        offline_err = str(exc)
    else:
        offline_err = None

    workspace = scan_verse_devices_from_files(root) if root else {"nodes": [], "count": 0}
    audit = _audit(live) if live else None
    return tool_json(
        {
            "ok": True,
            "listener_online": live is not None,
            "live": live,
            "workspace": workspace,
            "audit": audit,
            "error": offline_err,
        },
        pretty=pretty,
    )


@plugin_mcp_tool("tester")
def tester_list_tests(pretty: bool = False) -> str:
    """List harness cases (Verse/DuckyTests) and saved simulation scenarios (.ducky/tests).

    Call this to see what tests already exist before creating new ones.
    """
    root = _project_root()
    if not root:
        return tool_json({"ok": False, "error": "No UEFN project root configured", "tests": []}, pretty=pretty)
    tests = list_harness_tests(root)
    return tool_json({"ok": True, "tests": tests, "count": len(tests)}, pretty=pretty)


@plugin_mcp_tool("tester")
def tester_create_simulation(
    name: str,
    device: str,
    event: str = "InteractedWithEvent",
    expect_effects: list[str] | None = None,
    pretty: bool = False,
) -> str:
    """Create a saved offline simulation test under `.ducky/tests/<name>.json`.

    ``expect_effects`` are effect kinds from simulate_device_event (e.g. grant_item,
    teleport, score, movement, hud, cinematic). Run later with tester_run_simulation.
    """
    root = _project_root()
    if not root:
        return tool_json({"ok": False, "error": "No UEFN project root configured"}, pretty=pretty)
    if not name.strip() or not device.strip():
        return tool_json({"ok": False, "error": "name and device are required"}, pretty=pretty)
    result = save_simulation_scenario(
        root,
        name.strip(),
        device.strip(),
        event=event or "InteractedWithEvent",
        expect_effects=list(expect_effects or []),
    )
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("tester")
def tester_run_simulation(
    name: str = "",
    device: str = "",
    event: str = "InteractedWithEvent",
    expect_effects: list[str] | None = None,
    snapshot_json: str = "",
    pretty: bool = False,
) -> str:
    """Run an offline simulation test and compare effects to expectations.

    Pass ``name`` of a saved `.ducky/tests` scenario, OR pass device/event/expect_effects
    inline. Returns simulate trace + pass/fail against expected effect kinds.
    """
    root = _project_root()
    scenario: dict[str, Any] = {}
    if name.strip():
        if not root:
            return tool_json({"ok": False, "error": "No UEFN project root configured"}, pretty=pretty)
        loaded = load_simulation_scenario(root, name.strip())
        if not loaded.get("ok"):
            return tool_json(loaded, pretty=pretty)
        scenario = dict(loaded.get("scenario") or {})
        scenario["_path"] = loaded.get("path")
    else:
        if not device.strip():
            return tool_json(
                {"ok": False, "error": "pass name= (saved scenario) or device="},
                pretty=pretty,
            )
        scenario = {
            "name": device,
            "device": device,
            "event": event or "InteractedWithEvent",
            "expect_effects": list(expect_effects or []),
        }

    if snapshot_json.strip():
        snapshot = json.loads(snapshot_json)
    else:
        try:
            snapshot = _snapshot_live(limit=100)
        except Exception as exc:
            return tool_json({"ok": False, "error": f"snapshot failed: {exc}"}, pretty=pretty)

    sim = _simulate(
        snapshot,
        str(scenario.get("device") or device),
        str(scenario.get("event") or event or "InteractedWithEvent"),
    )
    expected = list(scenario.get("expect_effects") or expect_effects or [])
    comparison = compare_simulation_effects(sim, expected) if expected else {
        "ok": bool(sim.get("ok")),
        "expected": [],
        "got": sorted({str(e.get("kind") or "") for e in (sim.get("effects") or [])}),
        "missing": [],
        "note": "no expect_effects — reporting trace only",
    }
    return tool_json(
        {
            "ok": bool(comparison.get("ok")),
            "scenario": {
                "name": scenario.get("name"),
                "device": scenario.get("device"),
                "event": scenario.get("event"),
                "path": scenario.get("_path"),
            },
            "simulation": sim,
            "comparison": comparison,
            "status": "PASS" if comparison.get("ok") else "FAIL",
        },
        pretty=pretty,
    )


@plugin_mcp_tool("tester")
def verse_test_add_case(
    name: str,
    kind: str = "equal",
    actual: str = "",
    expected: str = "",
    condition: str = "",
    lo: str = "",
    hi: str = "",
    setup_line: str = "",
    pretty: bool = False,
) -> str:
    """Add one assert to Verse/DuckyTests/ducky_test_device.verse (creates scaffold if needed).

    kind: equal | true | in_range
    - equal: pass actual= (Verse expr) and expected= (literal/expr)
    - true: pass condition= (Verse logic expr)
    - in_range: pass actual=, lo=, hi=
    Optional setup_line= inserts a Verse line above the Expect (e.g. `Xp := 100`).

    Then call verse_test_run + verse_test_results (after a play session) to see PASS/FAIL.
    For custom full test files, use workspace_write_file under Verse/DuckyTests/.
    """
    root = _project_root()
    if not root:
        return tool_json({"ok": False, "error": "No UEFN project root configured"}, pretty=pretty)
    if not name.strip():
        return tool_json({"ok": False, "error": "name is required"}, pretty=pretty)
    try:
        result = add_verse_test_case(
            root,
            name.strip(),
            kind=kind,
            actual=actual,
            expected=expected,
            condition=condition,
            lo=lo,
            hi=hi,
            setup_line=setup_line,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("tester")
def tester_get_results(
    last_n: int = 500,
    since_offset: int = 0,
    pretty: bool = False,
) -> str:
    """View structured [DUCKY-TEST] PASS/FAIL results from the editor log (alias of verse_test_results)."""
    return verse_test_results(last_n=last_n, since_offset=since_offset, pretty=pretty)
