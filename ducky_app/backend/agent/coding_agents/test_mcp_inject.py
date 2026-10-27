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
    assert "## Ducky tools only" in text
    # Codex runs tools from a code runner and reads other projects by absolute path.
    assert "tools.mcp__uefn__workspace_search" in text
    assert "other Ducky projects" in text
    for shell_read in ("Get-Content", "Select-String", "rg", "git grep"):
        assert shell_read in text


@pytest.mark.parametrize("native_skills", [False, True])
@pytest.mark.parametrize("listener_online", [False, True])
def test_every_agent_bootstrap_uses_ducky_tools_only(native_skills, listener_online) -> None:
    # runner builds this common prompt before dispatching to any CLI adapter.
    text = bootstrap_system_prompt(
        project_root="/tmp/project",
        listener_online=listener_online,
        conv_id="chat-a",
        native_skills=native_skills,
    )
    for tool in ("`workspace_edit_file`", "`ducky_terminal_open`", "`ducky_terminal_run(session_id, command)`",
                 "`ducky_terminal_read_output(session_id)`", "`view_image`", "`web_search`",
                 "`ducky_spawn_chat`", "`ducky_ask_user`"):
        assert tool in text
    assert "never edit a file with Codex `apply_patch`" in text
    assert "background: true" in text
    assert "Never edit files by running a script or command that rewrites them." in text
    # The old split (own shell for builds, own edit tool in other projects) is gone.
    assert "Use the shell for builds" not in text
    assert "your own edit" not in text


def test_core_tools_load_up_front_and_the_rest_stay_deferred() -> None:
    # Claude Code's per-tool switch: _meta["anthropic/alwaysLoad"] in tools/list.
    import asyncio

    import backend.tools  # noqa: F401 - registers every core tool and marks them
    from backend.agent.coding_agents.core_tools import ALWAYS_LOAD_META_KEY, CORE_TOOLS
    from backend.bridge.shared_mcp import _snapshot_tools, _tool_row
    from backend.server import mcp

    assert len(CORE_TOOLS) == 15
    # Dedicated stdio (FastMCP tools/list) and the shared daemon both carry the flag.
    listed = {t.name: t for t in asyncio.run(mcp.list_tools())}
    rows = {row["name"]: row for row in (_tool_row(t) for t in _snapshot_tools(mcp))}
    for name in CORE_TOOLS:
        assert (listed[name].meta or {}).get(ALWAYS_LOAD_META_KEY) is True, name
        assert rows[name]["_meta"][ALWAYS_LOAD_META_KEY] is True, name
    for name in ("workspace_tree", "workspace_delete_file", "ping"):
        if name in rows:
            assert ALWAYS_LOAD_META_KEY not in (rows[name].get("_meta") or {}), name


def test_mark_always_load_keeps_existing_meta_and_is_idempotent() -> None:
    from types import SimpleNamespace

    from backend.agent.coding_agents.core_tools import ALWAYS_LOAD_META_KEY, mark_always_load

    tool = SimpleNamespace(meta={"connection_state": "ok"})
    mcp = SimpleNamespace(_tool_manager=SimpleNamespace(_tools={"view_image": tool}))
    assert mark_always_load(mcp) == ["view_image"]
    first = tool.meta
    assert first == {"connection_state": "ok", ALWAYS_LOAD_META_KEY: True}
    mark_always_load(mcp)
    assert tool.meta is first  # unchanged object: the catalog watcher sees no change


def test_uefn_server_config_leaves_server_level_always_load_unset(monkeypatch) -> None:
    from backend.agent.coding_agents import mcp_inject

    monkeypatch.setattr(
        mcp_inject,
        "build_uefn_server_block",
        lambda _s: {"type": "stdio", "command": "node", "args": [], "alwaysLoad": False},
    )
    servers = mcp_inject.build_uefn_mcp_servers(settings=object())
    assert "alwaysLoad" not in servers["uefn"]


@pytest.mark.parametrize("folder,online,offline_rules,listener_line", [
    (False, False, True, "UEFN listener: offline (only live-editor tools wait for it)"),
    (False, True, False, "UEFN listener: online"),
    (True, False, False, None),  # a folder project never mentions the listener
])
def test_coding_agents_hear_what_still_works_offline(monkeypatch, folder, online, offline_rules, listener_line):
    monkeypatch.setattr("frontend.project_kind.is_folder_project", lambda root: folder)
    text = bootstrap_system_prompt(project_root="/tmp/p", listener_online=online, conv_id="chat-a")
    assert ("No global UEFN gate" in text) is offline_rules
    if listener_line is None:
        assert "UEFN listener:" not in text and "### UEFN popups" not in text
    else:
        assert listener_line in text and "### UEFN popups" in text


def test_bootstrap_names_the_up_front_tools() -> None:
    from backend.agent.coding_agents.core_tools import CORE_TOOLS

    text = bootstrap_system_prompt(project_root="/tmp/p", listener_online=False, conv_id="chat-a")
    assert "load up front, with no ToolSearch" in text
    for name in CORE_TOOLS:
        assert f"`{name}`" in text


def test_team_plan_skill_requires_the_same_tools() -> None:
    app_root = Path(__file__).resolve().parents[3]
    skill = (app_root / "frontend/skill_packs/ducky/SKILL.md").read_text(encoding="utf-8")
    team_rules = " ".join(skill.split("### Team plans", 1)[1].split())
    assert "Ducky's tools only" in team_rules
    assert "`workspace_*` edit tools" in team_rules
    assert "`ducky_terminal_run`" in team_rules
    assert "their own edit tool" not in team_rules
