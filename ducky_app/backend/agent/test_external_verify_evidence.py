"""Regression tests for verification across external MCP requests/processes."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys

import pytest

from backend.agent import verify_evidence as ve
from backend.agent.coding_agents import plans
from backend.bridge import shared_mcp
from backend.server import ProtectedFastMCP
from backend.workspace import identity


@pytest.fixture
def checks(monkeypatch, tmp_path):
    monkeypatch.setattr(ve, "_receipt_path", lambda scope: tmp_path / (
        hashlib.sha256("\0".join(scope).encode()).hexdigest() + ".json"))
    monkeypatch.setattr(ve, "_evidence", {})
    monkeypatch.setattr("backend.workspace.ai_ignore.require_safe_tool", lambda *_: None)
    monkeypatch.setattr("backend.panel.rpc.wait_for_question_answers", lambda *_: None)
    monkeypatch.setattr("backend.agent.chat_title.require_self_name", lambda *_: None)
    server = ProtectedFastMCP("verification-regression")
    server._ducky_skip_plugin_wait = True

    @server.tool(name="changeset_list")
    def check(result: dict) -> str:
        return json.dumps(result)

    @server.tool(name="run_workflow")
    def workflow(result: dict) -> str:
        return json.dumps(result)

    @server.tool(name="ducky_terminal_run")
    def terminal(result: dict, wait: bool = True, background: bool = False) -> str:
        return json.dumps(result)

    @server.tool(name="ducky_plugin_validate")
    def validate(result: dict) -> str:
        return json.dumps(result)

    @server.tool(name="ducky_plugin_test")
    def plugin_test(result: dict) -> str:
        return json.dumps(result)

    @server.tool(name="ducky_plan_update_node")
    def tick(chat_id: str) -> str:
        # The gate never refuses: a tick without evidence is noted on the step. Raise on that
        # note here, so each case below still shows whether the check counted.
        node = {"id": "verify", "content": "Verify"}
        status = plans._apply_status_gate(node, "completed", chat_id)
        if plans._NO_CHECK_NOTE in str(node.get("body_markdown") or ""):
            raise ValueError("ticked with no check tool this turn")
        return status

    return server


def call(server, name, args, *, conv="same-name-a", run="run-a", shared=False):
    ctx = identity.RunContext(conv_id=conv, run_id=run, ducky_name="New ducky2")
    if shared:
        return asyncio.run(shared_mcp._call_tool(server, name, args, ctx))
    token = identity.bind(ctx)
    try:
        return asyncio.run(server.call_tool(name, args))
    finally:
        identity.reset(token)


def text(raw):
    if isinstance(raw, tuple):
        raw = raw[0]
    if isinstance(raw, dict):
        raw = raw["content"]
    return "\n".join(b.get("text", "") if isinstance(b, dict) else b.text for b in raw)


@pytest.mark.parametrize("shared", [False, True])
def test_check_then_tick_in_separate_requests(checks, shared):
    with pytest.raises(Exception, match="check tool"):
        call(checks, "ducky_plan_update_node", {"chat_id": "same-name-a"}, shared=shared)
    call(checks, "changeset_list", {"result": {"ok": True}}, shared=shared)
    assert "completed" in text(call(
        checks, "ducky_plan_update_node", {"chat_id": "same-name-a"}, shared=shared))
    assert identity.current() is None


@pytest.mark.parametrize("conv,run,target", [
    ("same-name-b", "run-a", "same-name-b"),
    ("same-name-a", "run-b", "same-name-a"),
    ("same-name-a", "run-a", "same-name-b"),
])
def test_another_conversation_or_run_cannot_use_check(checks, conv, run, target):
    call(checks, "changeset_list", {"result": {"ok": True}}, shared=True)
    with pytest.raises(Exception, match="check tool"):
        call(checks, "ducky_plan_update_node", {"chat_id": target}, conv=conv, run=run, shared=True)


@pytest.mark.parametrize("result", [
    {"ok": False}, {"success": False}, {"error": "failed"}, {"cancelled": True},
])
def test_failed_check_cannot_tick(checks, result):
    call(checks, "changeset_list", {"result": result})
    with pytest.raises(Exception, match="check tool"):
        call(checks, "ducky_plan_update_node", {"chat_id": "same-name-a"})


@pytest.mark.parametrize("tool,args", [
    ("run_workflow", {"result": {"ok": True, "steps": [{"ok": True, "id": "tests"}]}}),
    ("ducky_terminal_run", {"result": {"ok": True, "exit_code": 0}}),
    # Oct 12 2026: plugin teams could not tick any Verify step; no plugin check counted.
    ("ducky_plugin_validate", {"result": {"ok": True, "id": "imk"}}),
    ("ducky_plugin_test", {"result": {"ok": True, "id": "imk", "failed": []}}),
])
def test_completed_workflow_and_terminal_count(checks, tool, args):
    call(checks, tool, args, shared=True)
    assert "completed" in text(call(checks, "ducky_plan_update_node", {"chat_id": "same-name-a"}))


@pytest.mark.parametrize("tool,args", [
    ("run_workflow", {"result": {"ok": False, "steps": [{"ok": True}]}}),
    ("run_workflow", {"result": {"ok": True, "steps": [{"ok": False}]}}),
    ("run_workflow", {"result": {"ok": True, "steps": []}}),
    ("run_workflow", {"result": {"ok": True}}),
    ("run_workflow", {"result": {"ok": True, "steps": [{"ok": True}], "stopped": True}}),
    ("ducky_terminal_run", {"result": {"ok": True}}),
    ("ducky_terminal_run", {"result": {"ok": True, "exit_code": 1}}),
    ("ducky_terminal_run", {"result": {"ok": True, "exit_code": 0}, "wait": False}),
    ("ducky_terminal_run", {"result": {"ok": True, "exit_code": 0}, "background": True}),
    ("ducky_plugin_validate", {"result": {"ok": False, "errors": ["x"]}}),
    ("ducky_plugin_test", {"result": {"ok": False, "failed": ["tools"]}}),
])
def test_incomplete_or_failed_operation_does_not_count(checks, tool, args):
    call(checks, tool, args)
    with pytest.raises(Exception, match="check tool"):
        call(checks, "ducky_plan_update_node", {"chat_id": "same-name-a"})


@pytest.mark.parametrize("raw", [
    {"content": [{"type": "text", "text": '{"ok":true}'}], "isError": True},
    [{"type": "text", "text": "Error: failed"}],
    ([{"type": "text", "text": '{"ok":false}'}], {"ok": True}),
])
def test_protocol_and_content_errors_are_not_evidence(checks, raw):
    token = identity.bind(identity.RunContext(conv_id="same-name-a", run_id="run-a"))
    try:
        ve.record_result("changeset_list", {}, raw)
        assert not ve.has_evidence()
    finally:
        identity.reset(token)


def test_check_evidence_survives_a_separate_bridge_process(tmp_path):
    script = """
import sys
from backend.agent import verify_evidence as ve
from backend.agent.coding_agents import plans
if sys.argv[1] == 'check':
    ve.record_result('changeset_list', {}, [{'type': 'text', 'text': '{"ok":true}'}])
else:
    node = {'id': 'verify'}
    print(plans._apply_status_gate(node, 'completed', 'cross-process'), node.get('body_markdown', ''))
"""
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    env.update(identity.RunContext(conv_id="cross-process", run_id="process-run").to_env())
    for action in ("check", "tick"):
        out = subprocess.run([sys.executable, "-c", script, action], env=env,
                             capture_output=True, text=True, timeout=20)
        assert out.returncode == 0, out.stderr
    assert "completed" in out.stdout and "no check tool" not in out.stdout
    env.update(identity.RunContext(conv_id="cross-process", run_id="next-run").to_env())
    out = subprocess.run([sys.executable, "-c", script, "tick"], env=env,
                         capture_output=True, text=True, timeout=20)
    assert out.returncode == 0, out.stderr
    assert "no check tool ran this turn" in out.stdout  # ticked, and noted


def test_embedded_turn_reset_and_persisted_receipt(checks):
    token = ve.bind_conversation("reset-conv", "reset-run")
    try:
        ve.record_ok("changeset_list")
        assert ve.has_evidence("reset-conv")
        ve._evidence.clear()
        assert ve.has_evidence("reset-conv")
    finally:
        ve.reset_conversation(token)
    token = ve.bind_conversation("reset-conv", "reset-run")
    try:
        assert not ve.has_evidence()
    finally:
        ve.reset_conversation(token)


def test_actual_plan_update_rejects_foreign_chat(checks, tmp_path):
    for conv in ("same-name-a", "same-name-b"):
        plans.create_plan(conv, title="Check", nodes=[{"id": "verify", "content": "Verify"}],
                          project_root=str(tmp_path))
    call(checks, "changeset_list", {"result": {"ok": True}})
    token = identity.bind(identity.RunContext(conv_id="same-name-a", run_id="run-a"))
    try:
        mine = plans.update_node("same-name-a", "verify", status="completed", project_root=str(tmp_path))
        assert mine["nodes"][0]["status"] == "completed"
        assert plans._NO_CHECK_NOTE not in (mine["nodes"][0].get("body_markdown") or "")
        # Another chat's check is not this one's: the tick lands, noted as unchecked.
        other = plans.update_node("same-name-b", "verify", status="completed", project_root=str(tmp_path))
        assert plans._NO_CHECK_NOTE in other["nodes"][0]["body_markdown"]
    finally:
        identity.reset(token)


def test_mcp_protocol_handler_records_checks(checks):
    from mcp.types import CallToolRequest, CallToolRequestParams

    handler = checks._mcp_server.request_handlers[CallToolRequest]

    async def request(name, arguments):
        return await handler(CallToolRequest(
            method="tools/call", params=CallToolRequestParams(name=name, arguments=arguments)))

    token = identity.bind(identity.RunContext(conv_id="protocol", run_id="protocol-run"))
    try:
        assert not asyncio.run(request("changeset_list", {"result": {"ok": True}})).root.isError
        result = asyncio.run(request("ducky_plan_update_node", {"chat_id": "protocol"}))
        assert not result.root.isError
        assert "completed" in str(result)
    finally:
        identity.reset(token)


@pytest.mark.parametrize("raw", [
    "ok: true\nexit_code: 0",
    {"content": [{"type": "text", "text": "ok: true\nexit_code: 0"}]},
])
def test_toon_check_results(checks, raw):
    token = identity.bind(identity.RunContext(conv_id="toon", run_id="toon-run"))
    try:
        ve.record_result("ducky_terminal_run", {}, raw)
        assert ve.has_evidence("toon")
    finally:
        identity.reset(token)


def test_external_request_without_run_id_cannot_reuse_checks(checks):
    token = identity.bind(identity.RunContext(conv_id="anonymous"))
    try:
        ve.record_ok("changeset_list")
        assert not ve.has_evidence("anonymous")
    finally:
        identity.reset(token)


def test_corrupt_receipt_cannot_unlock_plan(checks):
    token = identity.bind(identity.RunContext(conv_id="corrupt", run_id="corrupt-run"))
    try:
        ve._receipt_path(("corrupt", "corrupt-run")).write_text("{bad", encoding="utf-8")
        assert not ve.has_evidence()
        ve._receipt_path(("corrupt", "corrupt-run")).write_text(json.dumps({
            "conv_id": "another-chat", "run_id": "corrupt-run", "tool": "changeset_list",
        }), encoding="utf-8")
        assert not ve.has_evidence()
    finally:
        identity.reset(token)


@pytest.mark.parametrize("uefn_open,complete,errors,folder,ticks", [
    (False, None, 0, False, True),    # UEFN closed: the complete, clean offline scan is the check
    (False, False, 0, False, False),  # a scan that ran out of time checked nothing
    (True, None, 0, False, False),    # UEFN open: the real compile is the check
    (False, None, 1, False, False),   # a scan that lists errors is a failing check
    (False, None, 0, True, False),    # a folder project has no Verse: "no files" proves nothing
])
def test_offline_verse_scan_ticks_verify_only_while_uefn_is_closed(checks, monkeypatch, uefn_open, complete, errors,
                                                                   folder, ticks):
    # Oct 22 2026: a Claude 5.5 team with UEFN closed. workspace_list_verse_errors showed 0 errors,
    # the compile could not run, the tick was refused three times, the member ended "BLOCKED: needs
    # UEFN" and the lead cancelled a step whose done-when was met.
    @checks.tool(name="workspace_list_verse_errors")
    def scan(result: dict) -> str:
        return json.dumps(result)

    monkeypatch.setattr(ve, "verse_build_available", lambda: uefn_open)
    payload = {"files": [{"path": "Content/Verse/Shop/shop.verse", "errors": errors}]}
    if folder:
        payload = {"ok": True, "folder_project": True, "files": []}
    if complete is not None:
        payload["complete"] = complete
    call(checks, "workspace_list_verse_errors", {"result": payload}, shared=True)
    if ticks:
        assert "completed" in text(call(checks, "ducky_plan_update_node", {"chat_id": "same-name-a"}))
    else:
        with pytest.raises(Exception, match="check tool"):
            call(checks, "ducky_plan_update_node", {"chat_id": "same-name-a"})
