"""Frontend adapters on the write pipeline: history attribution, editor sync, run identity."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.workspace import identity, runtime
from backend.workspace.identity import RunContext
from backend.workspace.writer import ProjectWriter
from frontend.ui_web import workspace_bootstrap
from frontend.ui_web.verse_editor import file_history


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setattr(file_history, "default_app_data_dir", lambda: tmp_path / "appdata")
    root = tmp_path / "Proj"
    (root / "Content" / "Verse").mkdir(parents=True)
    return root


@pytest.fixture
def writer(project: Path) -> ProjectWriter:
    w = ProjectWriter.for_root(str(project), observers=[workspace_bootstrap.FileHistoryObserver()])
    runtime.reset_for_tests(w)
    yield w
    runtime.reset_for_tests(None)


def test_agent_write_lands_in_history_with_identity(project: Path, writer: ProjectWriter) -> None:
    token = identity.bind(RunContext(run_id="r1", conv_id="c1", ducky_name="Hacker", model="m"))
    try:
        writer.write_text("Content/Verse/a.verse", "one\n", tool="workspace_write_file")
        writer.write_text("Content/Verse/a.verse", "two\n", tool="workspace_write_file")
    finally:
        identity.reset(token)
    entries = file_history.list_entries("Content/Verse/a.verse", project_root=str(project))
    # first write: agent version; second write: restore point (dedup with first) + agent version
    sources = [e["source"] for e in entries]
    assert sources == ["agent", "agent"]
    assert all(e["ducky_name"] == "Hacker" and e["run_id"] == "r1" for e in entries)


def test_user_write_records_only_restore_point(project: Path, writer: ProjectWriter, monkeypatch) -> None:
    for key in RunContext().to_env():
        monkeypatch.delenv(key, raising=False)
    writer.write_text("Content/Verse/a.verse", "one\n", tool="panel_save")
    assert file_history.list_entries("Content/Verse/a.verse", project_root=str(project)) == []
    writer.write_text("Content/Verse/a.verse", "two\n", tool="panel_save")
    [entry] = file_history.list_entries("Content/Verse/a.verse", project_root=str(project))
    assert entry["source"] == "" and file_history.read_entry(
        "Content/Verse/a.verse", entry["id"], project_root=str(project)
    )["content"] == "one\n"


def test_delete_leaves_removed_text_recoverable(project: Path, writer: ProjectWriter) -> None:
    target = project / "Content" / "Verse" / "gone.verse"
    target.write_text("bye\n", encoding="utf-8")
    writer.path_op("delete", "Content/Verse/gone.verse", tool="delete_project_entry", perform=target.unlink)
    [entry] = file_history.list_entries("Content/Verse/gone.verse", project_root=str(project))
    assert file_history.read_entry("Content/Verse/gone.verse", entry["id"], project_root=str(project))["content"] == "bye\n"


def test_editor_sync_skips_user_writes_and_forwards_agent_writes(monkeypatch) -> None:
    calls: list[tuple] = []
    from frontend.ui_web.verse_editor import agent_sync

    monkeypatch.setattr(agent_sync, "emit_for_bridge_tool", lambda *a, **k: calls.append((a, k)))
    obs = workspace_bootstrap.EditorSyncObserver()
    base = dict(
        path="Content/Verse/a.verse", from_path="", before="x", after="y", before_hash="", after_hash="",
        existed_before=True, ctx=None, ts=0.0, lines_added=1, lines_removed=1, project_root="", abs_path="",
    )
    from backend.workspace.policy import ALLOW
    from backend.workspace.writer import WriteRecord, WriteResult

    obs.on_write(WriteRecord(op="write", tool="panel_save", writer={"source": "user"}, decision=ALLOW, **base), WriteResult())
    assert calls == []
    obs.on_write(
        WriteRecord(op="write", tool="mcp__uefn__workspace_write_file", writer={"source": "agent", "conv_id": "c"}, decision=ALLOW, **base),
        WriteResult(),
    )
    assert len(calls) == 1
    (tool, args, raw), kw = calls[0]
    assert tool == "mcp__uefn__workspace_write_file" and args["content"] == "y"
    assert raw["data"]["before_content"] == "x" and kw["conv_id"] == "c"


def test_install_is_idempotent(monkeypatch, project: Path) -> None:
    runtime.reset_for_tests(ProjectWriter.for_root(str(project)))
    workspace_bootstrap.reset_for_tests()
    try:
        workspace_bootstrap.install()
        workspace_bootstrap.install()
        observers = runtime.get_writer()._observers  # noqa: SLF001
        kinds = [type(o).__name__ for o in observers]
        assert kinds == ["FileHistoryObserver", "EditorSyncObserver"]
        assert type(runtime.get_writer().journal).__name__ == "FileChangeJournal"
        assert [p.name for p in runtime.get_writer().policies] == ["lane"]
    finally:
        workspace_bootstrap.reset_for_tests()
        runtime.reset_for_tests(None)
        from backend.workspace import events, lanes

        events.reset_for_tests()
        lanes.reset_for_tests()


def test_changesets_storage_climbs_from_content_to_project_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path / "appdata")
    root = tmp_path / "Island"
    (root / "Content").mkdir(parents=True)
    (root / "Island.uefnproject").write_text("{}", encoding="utf-8")
    from frontend.ui_web.project_chats import project_slug

    # Pre-fix orphan ledger keyed by the Content folder; must fold into the project ledger.
    orphan = tmp_path / "appdata" / "changesets" / project_slug(str(root / "Content"))
    (orphan / "runs").mkdir(parents=True)
    (orphan / "runs" / "r1.json").write_text("{}", encoding="utf-8")
    (orphan / "catalog.json").write_text('{"r1": {"conv_id": "c"}}', encoding="utf-8")
    workspace_bootstrap._folded.clear()  # noqa: SLF001
    # Writer/MCP hand the Verse workspace folder; the Changes tab hands the project root.
    storage = workspace_bootstrap._changesets_storage(str(root / "Content"))  # noqa: SLF001
    assert storage == workspace_bootstrap._changesets_storage(str(root))  # noqa: SLF001
    assert storage.name.startswith("Island_")
    assert (storage / "runs" / "r1.json").is_file() and not orphan.exists()
    assert json.loads((storage / "catalog.json").read_text(encoding="utf-8")) == {"r1": {"conv_id": "c"}}


def test_build_run_context_resolves_group_and_leader(monkeypatch) -> None:
    hub = SimpleNamespace(id="hub", is_group=True, leader_conv_id="lead")
    monkeypatch.setattr("frontend.ui_web.project_chats.load_conversation", lambda cid: hub if cid == "hub" else None)
    monkeypatch.setattr("frontend.ui_web.group_orchestrator.is_group_conversation", lambda c: bool(getattr(c, "is_group", False)))
    member = SimpleNamespace(
        id="lead", parent_conv_id="hub", profile_id="architect", ducky_name="Architect", title="t",
        model="anthropic:x", provider="anthropic", coding_agent="ducky",
    )
    ctx = workspace_bootstrap.build_run_context(member, run_id="r")
    assert ctx.group_id == "hub" and ctx.leader_conv_id == "lead" and ctx.is_leader is True
    assert ctx.profile_id == "architect" and ctx.model == "anthropic:x"
    solo = SimpleNamespace(id="solo", parent_conv_id="", profile_id="", ducky_name="", title="Chat", model="", provider="openai", coding_agent="claude_code")
    ctx2 = workspace_bootstrap.build_run_context(solo, run_id="r2", model="gpt")
    assert ctx2.group_id == "" and ctx2.is_leader is False and ctx2.ducky_name == "Chat"
    assert ctx2.coding_agent == "claude_code" and ctx2.model == "gpt"
    titled = SimpleNamespace(
        id="c", parent_conv_id="", profile_id="verse-coder", ducky_name="Verse Coder",
        title="Animation Engineer", model="", provider="", coding_agent="claude_code",
    )
    named = workspace_bootstrap.build_run_context(titled, run_id="r3")
    assert named.ducky_name == "Animation Engineer" and named.profile_id == "verse-coder"


def test_record_external_edits_attributes_native_writes(project: Path, monkeypatch) -> None:
    from frontend.ui_web.verse_editor import io

    (project / "Content" / "Verse" / "ext.verse").write_text("from cli\n", encoding="utf-8")
    monkeypatch.setattr(io, "read_file", lambda rel: {"path": rel, "content": (project / rel).read_text(encoding="utf-8")})
    monkeypatch.setattr(file_history, "_active_project_root", lambda: str(project))
    ctx = RunContext(run_id="r", conv_id="c", ducky_name="Hacker", coding_agent="claude_code")
    blocks = [
        {"type": "tool_call", "name": "Write", "arguments": {"file_path": "Content/Verse/ext.verse"}, "status": "success"},
        {"type": "tool_call", "name": "Read", "arguments": {"file_path": "Content/Verse/ext.verse"}},
        {"type": "tool_call", "name": "Edit", "arguments": {"file_path": "Content/Verse/missing.verse"}},
        {"type": "text", "text": "done"},
    ]
    recorded = workspace_bootstrap.record_external_edits(blocks, ctx)
    assert recorded == ["Content/Verse/ext.verse"]
    [entry] = file_history.list_entries("Content/Verse/ext.verse", project_root=str(project))
    assert entry["source"] == "agent" and entry["coding_agent"] == "claude_code" and entry["tool"] == "Write"
