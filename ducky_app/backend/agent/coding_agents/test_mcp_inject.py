"""Per-run MCP argv so Cursor does not reuse one bridge across duckies."""

from __future__ import annotations

import json

from backend.agent.coding_agents.mcp_inject import bootstrap_system_prompt, stamp_mcp_identity
from backend.workspace.identity import RunContext


def test_stamp_mcp_identity_uniques_argv_and_inner_bridge() -> None:
    ctx = RunContext(run_id="run-a", conv_id="chat-a", ducky_name="Hacker")
    uefn = {
        "command": "node",
        "args": ["host.mjs"],
        "env": {"DUCKY_BRIDGE_ARGV": json.dumps(["UEFN-Ducky.exe", "bridge", "--port", "9"])},
    }
    stamp_mcp_identity(uefn, ctx, "chat-a")
    assert uefn["args"][-2:] == ["--ducky-run-id", "run-a"]
    assert json.loads(uefn["env"]["DUCKY_BRIDGE_ARGV"])[-2:] == ["--ducky-run-id", "run-a"]
    assert uefn["env"]["DUCKY_RUN_ID"] == "run-a"
    assert uefn["env"]["DUCKY_CONV_ID"] == "chat-a"


def test_two_runs_get_different_argv() -> None:
    a = stamp_mcp_identity({"args": ["bridge"], "env": {}}, RunContext(run_id="1"))
    b = stamp_mcp_identity({"args": ["bridge"], "env": {}}, RunContext(run_id="2"))
    assert a["args"] != b["args"]


def test_bootstrap_includes_chat_report_template() -> None:
    text = bootstrap_system_prompt(
        project_root="/tmp/p",
        listener_online=False,
        conv_id="chat-a",
    )
    assert "## Chat replies" in text
    assert "## Inventory" in text
    assert "> **Loose end:**" in text
    assert "never dump a prose changelog" in text
