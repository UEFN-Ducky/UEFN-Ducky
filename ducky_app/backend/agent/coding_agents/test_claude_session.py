"""A Claude session that never listed uefn tools must not be resumed."""

from __future__ import annotations

import json
from types import SimpleNamespace

from backend.agent.coding_agents import runner


def test_claude_project_slug_matches_claude_code() -> None:
    root = r"C:\Users\tas13\Documents\Fortnite Projects\ExampleProject1"
    assert runner.claude_project_slug(root) == (
        "C--Users-tas13-Documents-Fortnite-Projects-ExampleProject1"
    )


def test_session_cleared_when_log_still_lists_uefn_pending(monkeypatch, tmp_path) -> None:
    log = tmp_path / "03612016.jsonl"
    log.write_text(
        json.dumps(
            {
                "attachment": {
                    "type": "deferred_tools_delta",
                    "pendingMcpServers": ["uefn"],
                    "failedMcpServers": [],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "claude_session_log", lambda root, sid: log)
    conv = SimpleNamespace(
        upstream_session_id="claude_code:03612016",
        messages=[
            {
                "role": "assistant",
                "blocks": [{"type": "text", "text": "open Settings"}],
            }
        ],
    )
    assert runner.drop_dead_claude_session(conv, "claude_code", r"C:\proj") == ""
    assert conv.upstream_session_id == ""
    log.write_text(
        json.dumps({"attachment": {"pendingMcpServers": [], "failedMcpServers": ["uefn"]}})
        + "\n",
        encoding="utf-8",
    )
    conv.upstream_session_id = "claude_code:03612016"
    assert runner.drop_dead_claude_session(conv, "claude_code", r"C:\proj") == ""


def test_session_kept_when_last_turn_called_uefn(monkeypatch, tmp_path) -> None:
    log = tmp_path / "ok.jsonl"
    log.write_text(
        json.dumps({"attachment": {"pendingMcpServers": ["uefn"], "failedMcpServers": []}})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "claude_session_log", lambda root, sid: log)
    conv = SimpleNamespace(
        upstream_session_id="claude_code:ok",
        messages=[
            {
                "role": "assistant",
                "blocks": [{"type": "tool_use", "name": "mcp__uefn__ducky_plugin_list"}],
            }
        ],
    )
    assert runner.drop_dead_claude_session(conv, "claude_code", r"C:\proj") == "ok"
    assert conv.upstream_session_id == "claude_code:ok"
