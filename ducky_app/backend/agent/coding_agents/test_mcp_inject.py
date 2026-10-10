"""Per-run MCP argv so Cursor does not reuse one bridge across duckies."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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
    # The report shape forbids echoing written files back into chat.
    assert "link them instead" in text
    assert "select:mcp__uefn__<tool>" in text
    assert "Settings" in text


def test_bootstrap_sends_file_reads_to_ducky_tools_not_the_shell() -> None:
    text = bootstrap_system_prompt(project_root="/tmp/p", listener_online=False, conv_id="chat-a")
    assert "## Files and git (no shell)" in text
    # Codex runs tools from a code runner and reads other projects by absolute path.
    assert "tools.mcp__uefn__workspace_search" in text
    assert "other Ducky projects" in text
    for shell_read in ("Get-Content", "Select-String", "rg", "git grep", "exec_command"):
        assert shell_read in text


@pytest.mark.parametrize("native_skills", [False, True])
@pytest.mark.parametrize("listener_online", [False, True])
def test_every_agent_bootstrap_requires_edit_tools(native_skills, listener_online) -> None:
    # runner builds this common prompt before dispatching to any CLI adapter.
    text = bootstrap_system_prompt(
        project_root="/tmp/project",
        listener_online=listener_online,
        conv_id="chat-a",
        native_skills=native_skills,
    )
    assert "For every file, in any folder" in text
    for editor in ("Codex `apply_patch`", "Claude Code `Edit`/`Write`", "Cursor's edit tool", "`workspace_*` edit tools"):
        assert editor in text
    assert "Never edit files by running a script or command that rewrites them." in text
    assert "Use the shell for builds, tests and git only." in text


def test_team_plan_skill_requires_the_same_edit_tools() -> None:
    app_root = Path(__file__).resolve().parents[3]
    skill = (app_root / "frontend/skill_packs/ducky/SKILL.md").read_text(encoding="utf-8")
    team_rules = " ".join(skill.split("### Team plans", 1)[1].split())
    for editor in ("Codex `apply_patch`", "Claude Code `Edit`/`Write`", "Cursor's edit tool", "`workspace_*` edit tools"):
        assert editor in team_rules
    assert "Never edit files by running a script or command that rewrites them." in team_rules
    assert "Use the shell for builds, tests and git only." in team_rules
