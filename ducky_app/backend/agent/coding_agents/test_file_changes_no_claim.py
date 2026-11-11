"""A shell call never claims a change it can't be tied to; wrong nested stamps are repaired."""
from pathlib import Path
import subprocess

import pytest

from backend.agent.coding_agents import file_changes
from backend.agent.coding_agents.file_changes import TurnFileChanges
from backend.workspace import identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import SOURCE_UNKNOWN, FileChangeJournal
from backend.workspace.writer import ProjectWriter


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True)
    git(root, "init")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    (root / "file.txt").write_text("committed\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend.workspace import journal as journal_module

    monkeypatch.setattr(journal_module, "_use_db", lambda: False)
    outer = tmp_path / "GitHub"
    inner = make_repo(outer / "Repo")
    ledger = FileChangeJournal(lambda path: tmp_path / "ledger" / Path(path).name)
    monkeypatch.setattr(file_changes, "_ledger_roots", lambda: [outer.resolve(), inner.resolve()])
    runtime.reset_for_tests(ProjectWriter.for_root(str(inner), journal=ledger))
    yield outer, inner, ledger
    runtime.reset_for_tests(None)


def shell(kind: str, id: str = "sh") -> dict:
    return {"type": kind, "tool": {"id": id, "name": "mcp__uefn__ducky_terminal_run",
                                   "arguments": {"command": "py -3 -m pytest"}, "status": "success"}}


def test_shell_call_does_not_claim_another_chats_write_from_an_outer_ledger(env) -> None:
    outer, inner, ledger = env
    chats = TurnFileChanges(str(inner), RunContext(run_id="chats-run", conv_id="chats"), ledger)
    chats.process(shell("tool"))
    # The Guards seat's write, journaled (old behaviour) in the enclosing project's ledger.
    guards = ProjectWriter.for_root(str(outer), journal=ledger)
    token = identity.bind(RunContext(run_id="guards-run", conv_id="guards", ducky_name="Guards"))
    try:
        guards.write_text("Repo/new_guard.txt", "guard\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    done = shell("tool_done")
    chats.process(done)
    assert done["tool"]["fileEdits"] == []
    stamp = ledger.index_stamp("new_guard.txt", project_root=str(inner))
    assert stamp["conv_id"] == "" and stamp["source"] == SOURCE_UNKNOWN
    with pytest.raises(ValueError, match="not found"):
        ledger.get_run("chats-run", project_root=str(inner))  # nothing was journaled as theirs


def test_change_made_before_the_shell_call_started_is_nobodys(env) -> None:
    _outer, inner, ledger = env
    turn = TurnFileChanges(str(inner), RunContext(run_id="r1", conv_id="me"), ledger)
    (inner / "file.txt").write_text("someone else, before my call\n", encoding="utf-8")
    turn.process(shell("tool"))
    done = shell("tool_done")
    turn.process(done)
    assert done["tool"]["fileEdits"] == []
    stamp = ledger.index_stamp("file.txt", project_root=str(inner))
    assert stamp["conv_id"] == "" and stamp["source"] == SOURCE_UNKNOWN


def test_shell_call_still_claims_what_it_wrote(env) -> None:
    _outer, inner, ledger = env
    turn = TurnFileChanges(str(inner), RunContext(run_id="r1", conv_id="me"), ledger)
    turn.process(shell("tool"))
    (inner / "file.txt").write_bytes(b"mine\n")
    done = shell("tool_done")
    turn.process(done)
    assert [e["after"] for e in done["tool"]["fileEdits"]] == ["mine\n"]
    assert ledger.index_stamp("file.txt", project_root=str(inner))["conv_id"] == "me"


def test_repair_moves_a_taken_stamp_back_to_the_real_writer(env) -> None:
    outer, inner, ledger = env
    guards = ProjectWriter.for_root(str(outer), journal=ledger)
    token = identity.bind(RunContext(run_id="guards-run", conv_id="guards", ducky_name="Guards"))
    try:
        guards.write_text("Repo/a.txt", "guard a\n", tool="workspace_write_file")
        guards.write_text("Repo/b.txt", "guard b\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    # The Chats seat's shell call took a.txt (same content); b.txt it changed afterwards.
    chats = TurnFileChanges(str(inner), RunContext(run_id="chats-run", conv_id="chats", ducky_name="Chats"), ledger)
    (inner / "b.txt").write_text("chats rewrote b\n", encoding="utf-8")
    for name in ("a.txt", "b.txt"):
        path = inner / name
        snap = file_changes._read(path)
        chats._record(inner.resolve(), path.resolve(), file_changes.Snapshot(), snap, "mcp__uefn__ducky_terminal_run")
    assert ledger.index_stamp("a.txt", project_root=str(inner))["conv_id"] == "chats"

    fixed = ledger.repair_nested_stamps(project_root=str(inner), outer_root=str(outer))
    assert fixed == [{"path": "a.txt", "from_conv_id": "chats", "to_conv_id": "guards", "to_ducky": "Guards"}]
    a = ledger.index_stamp("a.txt", project_root=str(inner))
    assert a["conv_id"] == "guards" and a["run_id"] == "guards-run"
    assert ledger.index_stamp("b.txt", project_root=str(inner))["conv_id"] == "chats"
    assert ledger.repair_nested_stamps(project_root=str(inner), outer_root=str(outer)) == []
