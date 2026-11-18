"""A tool call that cannot write starts no git process; calls that can are still scanned."""
import subprocess

import pytest

from backend.agent.coding_agents import file_changes
from backend.agent.coding_agents.file_changes import TurnFileChanges
from backend.agent.coding_agents.test_file_changes import env, event  # noqa: F401 — env is a fixture
from backend.workspace.identity import RunContext


@pytest.fixture
def git_calls(monkeypatch):
    calls: list[list[str]] = []
    real = subprocess.run

    def counting(cmd, *args, **kwargs):
        if cmd and cmd[0] == "git":
            calls.append(list(cmd))
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(file_changes.subprocess, "run", counting)
    return calls


def _reads(tracker: TurnFileChanges, count: int) -> list[dict]:
    dones = []
    for i in range(count):
        args = {"section": "edges", "query": f"q{i}"}
        tracker.process(event("tool", "ducky_get_plan", args, id=f"read{i}"))
        done = event("tool_done", "ducky_get_plan", args, id=f"read{i}")
        tracker.process(done)
        dones.append(done)
    return dones


def test_a_read_tool_starts_no_git_process(env, git_calls) -> None:  # noqa: F811
    root, ledger, ctx = env
    tracker = TurnFileChanges(str(root), ctx, ledger)
    git_calls.clear()
    (root / "file.txt").write_text("changed meanwhile\n", encoding="utf-8")
    dones = _reads(tracker, 50)
    assert git_calls == []
    assert all(d["tool"]["fileEdits"] == [] for d in dones)


def test_a_shell_call_is_still_scanned(env, git_calls) -> None:  # noqa: F811
    root, ledger, ctx = env
    tracker = TurnFileChanges(str(root), ctx, ledger)
    _reads(tracker, 3)
    args = {"command": "a command that rewrites file.txt"}
    tracker.process(event("tool", "command_execution", args, id="sh"))
    (root / "file.txt").write_bytes(b"by the shell\n")
    git_calls.clear()
    done = event("tool_done", "command_execution", args, id="sh")
    tracker.process(done)
    assert git_calls  # status / diff / check-ignore ran
    assert [e["after"] for e in done["tool"]["fileEdits"]] == ["by the shell\n"]


def test_an_edit_by_the_cli_is_still_attributed(env) -> None:  # noqa: F811
    root, ledger, ctx = env
    tracker = TurnFileChanges(str(root), ctx, ledger)
    _reads(tracker, 5)
    args = {"file_path": "file.txt", "old_string": "before", "new_string": "after"}
    tracker.process(event("tool", "Edit", args, id="edit"))
    (root / "file.txt").write_bytes(b"edited\n")
    done = event("tool_done", "Edit", args, id="edit")
    tracker.process(done)
    assert [e["after"] for e in done["tool"]["fileEdits"]] == ["edited\n"]
    assert ledger.index_stamp("file.txt", project_root=str(root))["conv_id"] == ctx.conv_id


def test_two_chats_editing_one_repo_keep_their_own_files(env) -> None:  # noqa: F811
    root, ledger, _ctx = env
    a = TurnFileChanges(str(root), RunContext(run_id="run-a", conv_id="chat-a", coding_agent="claude"), ledger)
    b = TurnFileChanges(str(root), RunContext(run_id="run-b", conv_id="chat-b", coding_agent="claude"), ledger)
    a_args = {"file_path": "file.txt"}
    b_args = {"file_path": "dirty.txt"}
    a.process(event("tool", "Edit", a_args, id="a1"))
    b.process(event("tool", "Edit", b_args, id="b1"))
    (root / "file.txt").write_text("from a\n", encoding="utf-8")
    _reads(b, 2)  # b's reads in between change nothing for either chat
    (root / "dirty.txt").write_text("from b\n", encoding="utf-8")
    a_done, b_done = event("tool_done", "Edit", a_args, id="a1"), event("tool_done", "Edit", b_args, id="b1")
    a.process(a_done)
    b.process(b_done)
    assert [e["path"].rsplit("/", 1)[-1] for e in a_done["tool"]["fileEdits"]] == ["file.txt"]
    assert [e["path"].rsplit("/", 1)[-1] for e in b_done["tool"]["fileEdits"]] == ["dirty.txt"]
    assert ledger.index_stamp("file.txt", project_root=str(root))["conv_id"] == "chat-a"
    assert ledger.index_stamp("dirty.txt", project_root=str(root))["conv_id"] == "chat-b"
