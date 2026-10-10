"""Outside edits produce durable cards and real Changes/Revert entries."""
from pathlib import Path
import subprocess
import sys

import pytest

from backend.agent.coding_agents.file_changes import MAX_BYTES, TurnFileChanges
from backend.workspace import runtime
from backend.workspace.identity import RunContext
from backend.workspace.journal import FileChangeJournal
from backend.workspace.writer import ProjectWriter


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def repo(root):
    root.mkdir()
    git(root, "init")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    (root / "file.txt").write_bytes(b"before\r\nkeep\r\n")
    (root / "dirty.txt").write_text("committed\n", encoding="utf-8")
    (root / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "initial")
    return root


@pytest.fixture
def env(tmp_path, monkeypatch):
    from backend.workspace import writer, journal as journal_module
    monkeypatch.setattr(writer, "_is_folder_project_root", lambda root: True)
    monkeypatch.setattr(journal_module, "_use_db", lambda: False)
    root = repo(tmp_path / "repo")
    ledger = FileChangeJournal(lambda path: tmp_path / "ledger" / Path(path).name)
    ctx = RunContext(run_id="turn", conv_id="chat", coding_agent="codex")
    runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=ledger))
    yield root, ledger, ctx
    runtime.reset_for_tests(None)


def event(kind, name="file_change", args=None, id="call"):
    return {"type": kind, "tool": {"id": id, "name": name, "arguments": args or {}, "status": "success"}}


@pytest.mark.parametrize("tool,args", [
    ("file_change", {"paths": ["file.txt"], "path": "file.txt"}),
    ("command_execution", {"command": "a command that rewrites file.txt"}),
    ("Edit", {"file_path": "file.txt", "old_string": "before", "new_string": "after"}),
])
def test_edit_card_journal_and_exact_revert(env, tool, args):
    root, ledger, ctx = env
    # An existing dirty baseline must be preserved byte-for-byte, not HEAD.
    before = b"dirty before\r\nkeep\r\n"
    (root / "file.txt").write_bytes(before)
    tracker = TurnFileChanges(str(root), ctx, ledger)
    tracker.process(event("tool", tool, args))
    if tool == "command_execution":
        subprocess.run([sys.executable, "-c",
                        "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'after\\r\\nkeep\\r\\n')",
                        str(root / "file.txt")], check=True)
    else:
        (root / "file.txt").write_bytes(b"after\r\nkeep\r\n")
    done = event("tool_done", tool, args)
    tracker.process(done)
    edit = done["tool"]["fileEdit"]
    assert done["tool"]["fileEdits"] == [edit]
    assert edit == {"path": "abs:" + (root / "file.txt").as_posix(),
                    "before": before.decode(), "after": "after\r\nkeep\r\n",
                    "linesAdded": 1, "linesRemoved": 1, "kind": "write"}
    blocks = [{"type": "tool_call", "id": "call", "name": tool}]
    tracker.enrich_blocks(blocks)
    assert blocks[0]["file_edits"] == [edit]
    tracker.close("done")
    run = ledger.get_run("turn", project_root=str(root))
    assert len(run["entries"]) == 1
    assert run["entries"][0]["tool"] == tool
    reverted = ledger.revert_entry("turn", 1, project_root=str(root))
    assert reverted["ok"], reverted
    assert (root / "file.txt").read_bytes() == before


def test_clean_head_baseline_and_multiple_edits_on_one_row(env):
    root, ledger, ctx = env
    tracker = TurnFileChanges(str(root), ctx, ledger)
    # Codex may supply a synthesized start only after the edit has landed.
    (root / "file.txt").write_text("new\n", encoding="utf-8")
    (root / "new file.txt").write_text("created\n", encoding="utf-8")
    args = {"paths": ["file.txt", "new file.txt"]}
    tracker.process(event("tool", args=args))
    done = event("tool_done", args=args)
    tracker.process(done)
    edits = done["tool"]["fileEdits"]
    assert len(edits) == 2
    assert edits[0]["before"] == "before\r\nkeep\r\n"
    assert edits[1]["kind"] == "create" and edits[1]["before"] == ""
    assert len(ledger.get_run("turn", project_root=str(root))["entries"]) == 2


def test_untouched_dirty_large_ignored_and_binary_skipped(env):
    root, ledger, ctx = env
    (root / "dirty.txt").write_text("existing user edits\n", encoding="utf-8")
    (root / "untracked.txt").write_text("existing untracked\n", encoding="utf-8")
    (root / "large.txt").write_bytes(b"x" * (MAX_BYTES + 1))
    tracker = TurnFileChanges(str(root), ctx, ledger)
    (root / "large.txt").write_text("shrunk\n", encoding="utf-8")
    (root / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    (root / "binary.bin").write_bytes(b"\x00\xff")
    done = event("tool_done")
    done["tool"]["fileEdit"] = {"path": "large.txt"}  # Discard adapter guesses.
    tracker.process(done)
    assert "fileEdit" not in done["tool"]
    assert ledger.list_runs(project_root=str(root)) == []


def test_new_repo_named_by_shell_is_snapshotted_before_command(env, tmp_path):
    root, ledger, ctx = env
    other = repo(tmp_path / "another repo")
    (other / "file.txt").write_bytes(b"uncommitted before\n")
    tracker = TurnFileChanges(str(root), ctx, ledger)
    args = {"command": f"git -C '{other}' status"}
    tracker.process(event("tool", "command_execution", args))
    (other / "file.txt").write_text("after\n", encoding="utf-8")
    done = event("tool_done", "command_execution", args)
    tracker.process(done)
    assert done["tool"]["fileEdit"]["before"] == "uncommitted before\n"
    assert ledger.get_run("turn", project_root=str(other))["entries"][0]["path"] == "file.txt"


def test_named_file_outside_git_has_before_and_revert(env, tmp_path):
    root, ledger, ctx = env
    outside = tmp_path / "outside"
    outside.mkdir()
    path = outside / "note.txt"
    path.write_bytes(b"before\r\n")
    tracker = TurnFileChanges(str(root), ctx, ledger)
    args = {"file_path": str(path)}
    tracker.process(event("tool", "Edit", args))
    path.write_bytes(b"after\r\n")
    done = event("tool_done", "Edit", args)
    tracker.process(done)
    assert done["tool"]["fileEdit"]["before"] == "before\r\n"
    tracker.close("done")
    runtime.reset_for_tests(ProjectWriter.for_root(str(outside), journal=ledger))
    result = ledger.revert_entry("turn", 1, project_root=str(outside))
    assert result["ok"], result
    assert path.read_bytes() == b"before\r\n"


def test_shell_named_relative_file_outside_git(env, tmp_path):
    _, ledger, ctx = env
    outside = tmp_path / "plain"
    outside.mkdir()
    path = outside / "note.txt"
    path.write_bytes(b"before\n")
    tracker = TurnFileChanges(str(outside), ctx, ledger)
    args = {"command": "Set-Content note.txt 'after'"}
    tracker.process(event("tool", "command_execution", args))
    path.write_bytes(b"after\n")
    done = event("tool_done", "command_execution", args)
    tracker.process(done)
    assert done["tool"]["fileEdit"]["before"] == "before\n"


@pytest.mark.parametrize("before", [b"", b"delete me\r\n"])
def test_delete_and_revert_including_empty_file(env, before):
    root, ledger, ctx = env
    path = root / "file.txt"
    path.write_bytes(before)
    tracker = TurnFileChanges(str(root), ctx, ledger)
    path.unlink()
    done = event("tool_done")
    tracker.process(done)
    assert done["tool"]["fileEdit"]["after"] == ""
    tracker.close("done")
    result = ledger.revert_entry("turn", 1, project_root=str(root))
    assert result["ok"], result
    assert path.read_bytes() == before


def test_repeat_tool_callbacks_do_not_duplicate_and_next_edit_uses_last_snapshot(env):
    root, ledger, ctx = env
    tracker = TurnFileChanges(str(root), ctx, ledger)
    (root / "file.txt").write_bytes(b"first\n")
    tracker.process(event("tool_done"))
    again = event("tool_done")
    tracker.process(again)
    assert "fileEdit" not in again["tool"]
    (root / "file.txt").write_text("second\n", encoding="utf-8")
    final = event("tool_done", id="next")
    tracker.process(final)
    assert final["tool"]["fileEdit"]["before"] == "first\n"
    assert len(ledger.get_run("turn", project_root=str(root))["entries"]) == 2


def test_mcp_project_writer_entry_is_not_duplicated(env):
    from backend.workspace import identity
    root, ledger, ctx = env
    tracker = TurnFileChanges(str(root), ctx, ledger)
    token = identity.bind(ctx)
    try:
        runtime.get_writer().write_text("file.txt", "MCP edit\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    done = event("tool_done", "workspace_write_file", {"relative_path": "file.txt", "content": "MCP edit\n"})
    tracker.process(done)
    assert done["tool"]["fileEdit"]["after"] == "MCP edit\n"
    assert len(ledger.get_run("turn", project_root=str(root))["entries"]) == 1


def test_edit_and_commit_in_same_command_is_still_recorded(env):
    root, ledger, ctx = env
    tracker = TurnFileChanges(str(root), ctx, ledger)
    (root / "file.txt").write_text("committed edit\n", encoding="utf-8")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "edit")
    done = event("tool_done", "command_execution", {"command": "git commit -am edit"})
    tracker.process(done)
    assert done["tool"]["fileEdit"]["before"] == "before\r\nkeep\r\n"


def test_checkpoint_receives_enriched_cards_before_persisting(env, monkeypatch):
    from backend.agent.coding_agents import runner
    from types import SimpleNamespace
    root, ledger, ctx = env
    monkeypatch.setattr(runner, "checkpoint_coding_turn", lambda *a, **kw: None)
    checkpoint = runner._TurnCheckpoint(SimpleNamespace(id="chat"), "codex", "turn")
    tracker = TurnFileChanges(str(root), ctx, ledger)
    events = []
    push = tracker.wrap(checkpoint.wrap(events.append))
    push(event("tool"))
    (root / "file.txt").write_text("after\n", encoding="utf-8")
    push(event("tool_done"))
    assert checkpoint.blocks[0]["file_edits"] == events[-1]["tool"]["fileEdits"]
    assert checkpoint.blocks[0]["file_edit"]["before"] == "before\r\nkeep\r\n"


def test_runner_launch_wires_snapshots_live_and_into_final_blocks(env, monkeypatch):
    from types import SimpleNamespace
    from backend.agent.coding_agents import runner, readiness, mcp_inject
    from backend.agent.coding_agents.base import CodingAgentLaunchResult
    from frontend.ui_web import workspace_bootstrap, live_agent_runs
    from backend.bridge import status
    from backend.uefn_plugins import host

    root, ledger, ctx = env
    seen = []

    def launch(**kwargs):
        kwargs["push"](event("tool", "command_execution"))
        (root / "file.txt").write_bytes(b"native edit\r\n")
        kwargs["push"](event("tool_done", "command_execution", {"command": "rewrite file.txt"}))
        return CodingAgentLaunchResult(ok=True, effective_mode="agent", blocks=[{
            "type": "tool_call", "id": "call", "name": "command_execution", "status": "success",
        }])

    adapter = SimpleNamespace(label="Test CLI", detect=lambda _: SimpleNamespace(available=True),
                              capabilities=SimpleNamespace(resume=False), launch=launch)
    monkeypatch.setattr(runner, "normalize_coding_agent", lambda _: "codex")
    monkeypatch.setattr(runner, "get_adapter", lambda _: adapter)
    monkeypatch.setattr(runner, "coding_mode_launch_kwargs", lambda *a: {})
    monkeypatch.setattr(runner.PanelSettings, "load", lambda: SimpleNamespace(uefn_project_root=str(root)))
    monkeypatch.setattr(runner, "apply_workspace_env", lambda *a: None)
    monkeypatch.setattr(runner, "coding_agent_cfg", lambda *a: {})
    monkeypatch.setattr(runner, "bootstrap_system_prompt", lambda **kw: "")
    monkeypatch.setattr(runner, "write_uefn_mcp_config", lambda **kw: root / "unused-mcp")
    monkeypatch.setattr(runner, "write_prompt_file", lambda *a, **kw: root / "unused-prompt")
    monkeypatch.setattr(runner, "launch_env", lambda **kw: {})
    monkeypatch.setattr(runner, "_normalize_launch_model", lambda *a: "model")
    monkeypatch.setattr(runner, "record_coding_agent_usage", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "checkpoint_coding_turn", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "_emit_assistant", lambda *a, **kw: kw)
    monkeypatch.setattr(status, "fetch_listener_status", lambda *a, **kw: {})
    monkeypatch.setattr(host, "get_coding_agent_registration", lambda *a: {})
    monkeypatch.setattr(mcp_inject, "deployed_skill_packs", lambda *a: ("", []))
    monkeypatch.setattr(workspace_bootstrap, "build_run_context", lambda *a, **kw: ctx)
    monkeypatch.setattr(workspace_bootstrap, "build_journal", lambda: ledger)
    monkeypatch.setattr(workspace_bootstrap, "record_external_edits", lambda *a: [])
    monkeypatch.setattr(live_agent_runs, "set_live_writer", lambda *a: None)
    monkeypatch.setattr(readiness, "launch_with_ready_tools", lambda adapter, **kw: adapter.launch(**kw))
    conv = SimpleNamespace(id="chat", coding_agent="codex", messages=[], ducky_name="", ducky_personality="",
                           coding_agent_stats={}, model="model")
    result = runner._run_coding_agent_message(conv, "edit", model="model", push=seen.append, run_id="turn")
    live = next(row["tool"]["fileEdits"] for row in seen if row["type"] == "tool_done")
    assert live[0]["before"] == "before\r\nkeep\r\n"
    assert result["blocks"][0]["file_edits"] == live
    assert ledger.get_run("turn", project_root=str(root))["status"] == "done"


def plain_project(tmp_path):
    """A UEFN island folder: not a git repo, binary assets beside text files."""
    root = tmp_path / "Island"
    (root / "Content" / "Verse").mkdir(parents=True)
    (root / "Content" / "Verse" / "game.verse").write_bytes(b"before\r\n")
    (root / "Content" / "Hero.uasset").write_bytes(b"\x00binary")
    (root / ".ducky").mkdir()
    (root / ".ducky" / "plan.json").write_text("{}", encoding="utf-8")
    return root


def test_codex_edit_reported_after_it_landed_in_a_folder_outside_git(env, tmp_path):
    # Live 1.2.363 test: Codex created release_probe.txt in an island folder; its
    # file_change arrived after the edit, so there was no card and no Changes entry.
    _, ledger, ctx = env
    root = plain_project(tmp_path)
    tracker = TurnFileChanges(str(root), ctx, ledger)
    (root / "Content" / "Verse" / "game.verse").write_bytes(b"after\r\n")
    (root / "release_probe.txt").write_text("probe\n", encoding="utf-8")
    args = {"paths": [str(root / "Content" / "Verse" / "game.verse"), str(root / "release_probe.txt")]}
    tracker.process(event("tool", args=args))
    done = event("tool_done", args=args)
    tracker.process(done)
    edits = {Path(edit["path"][4:]).name: edit for edit in done["tool"]["fileEdits"]}
    assert edits["game.verse"]["before"] == "before\r\n" and edits["game.verse"]["after"] == "after\r\n"
    assert edits["release_probe.txt"]["kind"] == "create" and edits["release_probe.txt"]["before"] == ""
    tracker.close("done")
    run = ledger.get_run("turn", project_root=str(root))
    assert len(run["entries"]) == 2
    runtime.reset_for_tests(ProjectWriter.for_root(str(root), journal=ledger))
    seq = next(entry["seq"] for entry in run["entries"] if entry["path"].endswith("game.verse"))
    assert ledger.revert_entry("turn", seq, project_root=str(root))["ok"]
    assert (root / "Content" / "Verse" / "game.verse").read_bytes() == b"before\r\n"


def test_a_shell_edit_that_names_no_file_is_still_caught_outside_git(env, tmp_path):
    _, ledger, ctx = env
    root = plain_project(tmp_path)
    tracker = TurnFileChanges(str(root), ctx, ledger)
    args = {"command": "python -m tools.regenerate"}
    tracker.process(event("tool", "command_execution", args))
    (root / "Content" / "Verse" / "game.verse").write_bytes(b"generated\r\n")
    done = event("tool_done", "command_execution", args)
    tracker.process(done)
    assert [Path(edit["path"][4:]).name for edit in done["tool"]["fileEdits"]] == ["game.verse"]
    assert done["tool"]["fileEdit"]["before"] == "before\r\n"


def test_binary_assets_and_ducky_or_build_folders_are_not_edits(env, tmp_path):
    _, ledger, ctx = env
    root = plain_project(tmp_path)
    tracker = TurnFileChanges(str(root), ctx, ledger)
    (root / "Content" / "Hero.uasset").write_bytes(b"\x00changed")
    (root / ".ducky" / "plan.json").write_text('{"x": 1}', encoding="utf-8")
    (root / "Saved").mkdir()
    (root / "Saved" / "log.txt").write_text("log\n", encoding="utf-8")
    done = event("tool_done", "command_execution", {"command": "noop"})
    tracker.process(done)
    assert done["tool"]["fileEdits"] == []
    assert ledger.list_runs(project_root=str(root)) == []


def test_the_whole_profile_or_a_drive_is_never_snapshotted():
    from backend.agent.coding_agents.file_changes import _folder_worth_snapshotting

    assert not _folder_worth_snapshotting(Path.home().resolve())
    assert not _folder_worth_snapshotting(Path(Path.home().anchor))


def test_two_agents_in_one_folder_each_get_only_their_own_file(env, tmp_path):
    # Oct 10 2026 team test: Writer A's turn showed Writer B's new file under a
    # find_workflows call, and B's showed A's under a plan tick. Reverting A's turn
    # would have deleted B's file.
    from backend.workspace.identity import RunContext

    _, ledger, ctx = env
    root = plain_project(tmp_path)
    a = TurnFileChanges(str(root), ctx, ledger)
    b = TurnFileChanges(str(root), RunContext(run_id="turn-b", conv_id="chat-b", coding_agent="codex"), ledger)
    a_file, b_file = root / "Content" / "team_a.txt", root / "Content" / "team_b.txt"
    b_file.write_text("beta\n", encoding="utf-8")  # B's apply_patch lands first
    read = event("tool_done", "uefn/find_workflows", {"task": "write team_a"}, id="a-read")
    a.process(read)
    assert read["tool"]["fileEdits"] == []
    a_file.write_text("alpha\n", encoding="utf-8")
    a_patch = event("tool_done", "file_change", {"paths": [str(a_file)], "path": str(a_file)}, id="a-patch")
    a.process(a_patch)
    b_patch = event("tool_done", "file_change", {"paths": [str(b_file)], "path": str(b_file)}, id="b-patch")
    b.process(b_patch)
    tick = event("tool_done", "uefn/ducky_plan_update_node", {"node_id": "b1", "status": "completed"}, id="b-tick")
    b.process(tick)
    assert [Path(e["path"][4:]).name for e in a_patch["tool"]["fileEdits"]] == ["team_a.txt"]
    assert [Path(e["path"][4:]).name for e in b_patch["tool"]["fileEdits"]] == ["team_b.txt"]
    assert tick["tool"]["fileEdits"] == []
    paths = {e["path"] for e in ledger.get_run("turn", project_root=str(root))["entries"]}
    assert paths == {"Content/team_a.txt"}


def test_a_listed_folder_claims_nothing_and_a_shell_command_claims_what_it_wrote(env, tmp_path):
    _, ledger, ctx = env
    root = plain_project(tmp_path)
    tracker = TurnFileChanges(str(root), ctx, ledger)
    (root / "Content" / "other.txt").write_text("someone else\n", encoding="utf-8")
    listing = event("tool_done", "uefn/workspace_list_dir", {"path": "Content", "cwd": str(root)}, id="ls")
    tracker.process(listing)
    assert listing["tool"]["fileEdits"] == []
    (root / "Content" / "Verse" / "game.verse").write_bytes(b"built\r\n")
    build = event("tool_done", "command_execution", {"command": "python build.py"}, id="build")
    tracker.process(build)
    assert [Path(e["path"][4:]).name for e in build["tool"]["fileEdits"]] == ["game.verse"]
