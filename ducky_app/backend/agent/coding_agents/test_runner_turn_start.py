"""What a coding-agent turn start writes and what a failed edit record leaves behind."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agent.coding_agents import mcp_inject, runner
from backend.workspace.identity import RunContext

HOST_ENV = {"DUCKY_AGENT_HOST_PIPE": "pipe", "DUCKY_AGENT_HOST_KEY": "key"}


def _servers(settings=None):
    return {"uefn": {"type": "stdio", "command": "node", "args": ["bridge.mjs"],
                     "env": {"DUCKY_BRIDGE_ARGV": json.dumps(["py", "-m", "bridge"]), "PORT": "4200"}}}


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_inject, "build_uefn_mcp_servers", _servers)
    monkeypatch.setattr(mcp_inject, "coding_agents_tmp_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def turn(tmp_path, monkeypatch, config_dir):
    """One turn of a fake CLI; returns (run it, what the CLI saw)."""
    from backend.agent.coding_agents import file_changes, readiness
    from backend.agent.coding_agents.base import CodingAgentLaunchResult
    from backend.uefn_plugins.plugin_host.testing import patch_host
    from frontend.ui_web import live_agent_runs, workspace_bootstrap

    seen: dict = {}

    def launch(**kwargs):
        seen["mcp"] = Path(kwargs["mcp_config_path"]).read_bytes()
        seen["prompt"] = kwargs["prompt"]
        return CodingAgentLaunchResult(ok=True, effective_mode="agent", reply_text="done",
                                       blocks=[{"type": "tool_call", "id": "c", "name": "Edit"}])

    class Changes:
        def __init__(self, *a):
            pass

        def wrap(self, push):
            return push

        def enrich_blocks(self, blocks):
            pass

        def close(self, status):
            pass

    adapter = SimpleNamespace(label="Test CLI", detect=lambda _: SimpleNamespace(available=True),
                              capabilities=SimpleNamespace(resume=False), launch=launch)
    monkeypatch.setattr(runner, "normalize_coding_agent", lambda _: "codex")
    monkeypatch.setattr(runner, "get_adapter", lambda _: adapter)
    monkeypatch.setattr(runner, "coding_mode_launch_kwargs", lambda *a: {})
    monkeypatch.setattr(runner.PanelSettings, "load", lambda: SimpleNamespace(uefn_project_root=str(tmp_path)))
    monkeypatch.setattr(runner, "apply_workspace_env", lambda *a: None)
    monkeypatch.setattr(runner, "coding_agent_cfg", lambda *a: {})
    monkeypatch.setattr(runner, "bootstrap_system_prompt", lambda **kw: (seen.__setitem__("system", kw), "")[1])
    monkeypatch.setattr(runner, "memory_hint", lambda *a: "")
    monkeypatch.setattr(runner, "launch_env", lambda **kw: {})
    monkeypatch.setattr(runner, "_normalize_launch_model", lambda *a: "model")
    monkeypatch.setattr(runner, "record_coding_agent_usage", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "checkpoint_coding_turn", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_emit_assistant", lambda *a, **kw: kw)
    monkeypatch.setattr(runner, "_agent_host_env", lambda: {})
    patch_host(monkeypatch, "get_coding_agent_registration", lambda *a: {})
    monkeypatch.setattr(mcp_inject, "deployed_skill_packs", lambda *a: ("", []))
    monkeypatch.setattr(workspace_bootstrap, "build_run_context",
                        lambda *a, **kw: RunContext(run_id="turn", conv_id="chat", coding_agent="codex"))
    monkeypatch.setattr(workspace_bootstrap, "build_journal", lambda: None)
    monkeypatch.setattr(workspace_bootstrap, "record_external_edits", lambda *a: [])
    monkeypatch.setattr(file_changes, "TurnFileChanges", Changes)
    monkeypatch.setattr(live_agent_runs, "set_live_writer", lambda *a: None)
    monkeypatch.setattr(readiness, "launch_with_ready_tools", lambda adapter, **kw: adapter.launch(**kw))

    def run(text="edit"):
        conv = SimpleNamespace(id="chat", coding_agent="codex", messages=[], ducky_name="",
                               ducky_personality="", coding_agent_stats={}, model="model")
        return runner._run_coding_agent_message(conv, text, model="model", push=lambda e: None, run_id="turn")

    return run, seen


def test_a_failed_edit_record_is_logged(turn, monkeypatch, caplog) -> None:
    from frontend.ui_web import workspace_bootstrap

    run, _ = turn
    clean = run()

    def locked(*a):
        raise OSError("ledger locked")

    monkeypatch.setattr(workspace_bootstrap, "record_external_edits", locked)
    with caplog.at_level(logging.WARNING, logger=runner.__name__):
        failed = run()
    def stable(outcome):
        return {k: v for k, v in outcome.items() if k not in ("push", "images_since")}

    assert stable(failed) == stable(clean)  # the turn's answer does not change
    warnings = [r for r in caplog.records if "could not record the agent's edits" in r.getMessage()]
    assert len(warnings) == 1 and "turn" in warnings[0].getMessage() and warnings[0].exc_info


def test_matched_entries_are_named_in_the_user_prompt(turn, monkeypatch) -> None:
    run, seen = turn
    asked: list = []

    def hint(root, conv_id, task):
        asked.append((conv_id, task))
        return "Memory entries that fit this message: zz-release" if "release" in task else ""

    monkeypatch.setattr(runner, "memory_hint", hint)
    run("release the app")
    assert "[Memory entries that fit this message: zz-release]" in seen["prompt"]
    assert "release the app" in seen["prompt"]
    assert "task" not in seen["system"]  # the system prompt never gets the message
    run("fix the roads")
    assert "Memory entries" not in seen["prompt"]
    assert asked == [("chat", "release the app"), ("chat", "fix the roads")]


@pytest.mark.parametrize("host", [False, True], ids=["app", "host"])
def test_the_config_is_written_once(turn, monkeypatch, host) -> None:
    run, seen = turn
    monkeypatch.setattr(runner, "_agent_host_env", lambda: HOST_ENV if host else {})
    dumps, writes, reads = [], [], []
    real_dump, real_write, real_read = json.dump, Path.write_text, Path.read_text
    monkeypatch.setattr(mcp_inject.json, "dump", lambda *a, **k: (dumps.append(1), real_dump(*a, **k))[1])
    monkeypatch.setattr(Path, "write_text", lambda self, *a, **k: (writes.append(self.name), real_write(self, *a, **k))[1])
    monkeypatch.setattr(Path, "read_text", lambda self, *a, **k: (reads.append(self.name), real_read(self, *a, **k))[1])
    run()
    mcp_writes = [n for n in writes if n.startswith("uefn-mcp-")]
    assert len(dumps) + len(mcp_writes) == 1
    assert not [n for n in reads if n.startswith("uefn-mcp-")]
    env = json.loads(seen["mcp"])["mcpServers"]["uefn"]["env"]
    assert (env.get("DUCKY_AGENT_HOST_PIPE") == "pipe") is host


@pytest.mark.parametrize("host", [False, True], ids=["app", "host"])
def test_the_config_equals_the_old_two_step_result(config_dir, monkeypatch, host) -> None:
    from frontend.agent_host import steer_host

    identity = RunContext(run_id="r1", conv_id="c1", coding_agent="claude")
    monkeypatch.setattr(steer_host, "mcp_env", lambda: HOST_ENV if host else {})
    old = mcp_inject.write_uefn_mcp_config(conv_id="c1", settings=object(), identity=identity)
    runner._add_agent_host_env(old)  # what a turn did before: write, read back, write again
    new = mcp_inject.write_uefn_mcp_config(conv_id="c1", settings=object(), identity=identity,
                                           uefn_env=runner._agent_host_env())
    assert new.read_bytes() == old.read_bytes()
