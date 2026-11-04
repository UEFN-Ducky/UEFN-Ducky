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
    # The orphan-ledger fold is a files-backend repair; on rows the importer brings orphans in as-is.
    monkeypatch.setenv("DUCKY_STORE_BACKEND_LEDGER", "files")
    monkeypatch.setattr("frontend.settings.default_app_data_dir", lambda: tmp_path / "appdata")
    root = tmp_path / "Island"
    (root / "Content").mkdir(parents=True)
    (root / "Island.uefnproject").write_text("{}", encoding="utf-8")
    from frontend.ui_web.chats.store import project_slug

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
    monkeypatch.setattr("frontend.ui_web.chats.conversations.load_conversation", lambda cid: hub if cid == "hub" else None)
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


@pytest.fixture
def folder_project(tmp_path: Path, monkeypatch) -> Path:
    """A folder project open in the panel; the shared tool server was started on another one."""
    import frontend.ui_web.panel_api as pa
    from frontend.ui_web import project_switch

    for key in ("UEFN_VSCODE_WORKSPACE_FOLDERS", "UEFN_DUCKY_PROJECT_ROOT", *RunContext().to_env()):
        monkeypatch.delenv(key, raising=False)
    repo = tmp_path / "ledger-app"
    repo.mkdir()
    project_switch.set_panel_project_root(str(repo), push_ui=False)
    monkeypatch.setenv("UEFN_DUCKY_PROJECT_ROOT", str(tmp_path / "older-project"))
    monkeypatch.setenv("UEFN_VSCODE_WORKSPACE_FOLDERS", json.dumps([str(tmp_path / "older-project")]))
    monkeypatch.setattr(pa, "notify_chats_changed", lambda *a, **k: None)
    runtime.reset_for_tests(None)
    runtime.get_writer().set_journal(workspace_bootstrap.build_journal())
    yield repo
    runtime.reset_for_tests(None)


def _call_as(repo: Path, conv, run_id: str, fn, /, *args, **kwargs):
    """Call a tool as a Codex chat on the shared tool server, bound the way the daemon binds it."""
    from backend.bridge.shared_mcp import identity_from_payload

    ctx = workspace_bootstrap.build_run_context(conv, run_id=run_id, coding_agent="codex")
    payload = {**ctx.as_writer(), "leader_conv_id": ctx.leader_conv_id, "project_root": str(repo),
               "workspace_folders": json.dumps([str(repo)])}
    token, shared = identity.bind(identity_from_payload(payload)), identity.mark_shared()
    try:
        return fn(*args, **kwargs)
    finally:
        identity.reset_shared(shared)
        identity.reset(token)


def test_a_lead_sees_what_members_of_its_nested_groups_wrote(folder_project: Path) -> None:
    # Oct 15 2026: a Codex lead built a team of nested groups (Ledger Team > Core / Interface /
    # Quality) in a folder project; its members wrote ~50 files through the shared tool server,
    # and changeset_list(group_id=<its team>) returned no runs. Each run carries the group its
    # member sits in directly, which was always a nested one.
    from backend.tools.core import changesets
    from backend.tools.core.system import workspace_write_file
    from frontend.settings import PanelSettings
    from frontend.ui_web import changesets_api
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
    from frontend.ui_web.chats.conversations import create_conversation, load_conversation
    from frontend.ui_web.chats.saves import save_conversation

    repo = folder_project
    settings = PanelSettings.load()
    api = PanelApiChatsMixin()
    team = api.group_create("Ledger Team", "", False)
    core = api.group_create("Core", team["folder_id"], False)
    # A team made before nested hubs had a parent: only the roster row says Quality is inside.
    quality = api.group_create("Quality", "", False)
    lead = create_conversation(settings, team["folder_id"], title="Ledger Lead", parent_conv_id=team["id"])
    member = create_conversation(settings, core["folder_id"], title="Verse Coder", parent_conv_id=core["id"])
    tester = create_conversation(settings, quality["folder_id"], title="Tester", parent_conv_id=quality["id"])
    hub = load_conversation(team["id"])
    hub.leader_conv_id = lead.id
    hub.group_members = [{"member_conv_id": quality["id"], "is_group": True}]
    save_conversation(hub)

    wrote = json.loads(_call_as(repo, member, "member-run", workspace_write_file, "ledger/models.py", content="x = 1\n"))
    assert wrote["changeset"]["run_id"] == "member-run"
    assert (repo / "ledger" / "models.py").is_file()
    _call_as(repo, tester, "tester-run", workspace_write_file, "tests/test_models.py", content="assert True\n")

    listed = json.loads(_call_as(repo, lead, "lead-run", changesets.changeset_list, group_id=team["id"]))["runs"]
    assert sorted((r["run_id"], r["group_id"], tuple(r["files"])) for r in listed) == [
        ("member-run", core["id"], ("ledger/models.py",)),
        ("tester-run", quality["id"], ("tests/test_models.py",)),
    ]
    # A nested group still lists only its own members' runs.
    core_only = json.loads(_call_as(repo, lead, "lead-run", changesets.changeset_list, group_id=core["id"]))["runs"]
    assert [r["run_id"] for r in core_only] == ["member-run"]
    # The Changes tab of the team, and its Archive, cover the same runs.
    assert sorted(r["run_id"] for r in changesets_api.list_changesets(group_id=team["id"])) == [
        "member-run", "tester-run",
    ]
    assert changesets_api.archive_changesets(group_id=team["id"])["updated"] == 2


def test_a_lead_reverts_its_nested_groups_runs_and_a_sub_group_leader_stays_in_its_group(
    folder_project: Path,
) -> None:
    # Everything goes through the team's lead, so it may revert what a sub-group's member wrote.
    # A sub-group's own leader reaches neither a sibling sub-group nor the outer team. Before,
    # only the leader of the run's direct group passed, and no Codex / Claude Code lead passed
    # at all: the identity from the shared tool server never set is_leader.
    from backend.tools.core import changesets
    from backend.tools.core.system import workspace_write_file
    from frontend.settings import PanelSettings
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
    from frontend.ui_web.chats.conversations import create_conversation, load_conversation
    from frontend.ui_web.chats.saves import save_conversation

    repo = folder_project
    settings = PanelSettings.load()
    api = PanelApiChatsMixin()
    team = api.group_create("Ledger Team", "", False)
    core = api.group_create("Core", team["folder_id"], False)
    interface = api.group_create("Interface", team["folder_id"], False)

    def led_by(group: dict, title: str):
        chat = create_conversation(settings, group["folder_id"], title=title, parent_conv_id=group["id"])
        hub = load_conversation(group["id"])
        hub.leader_conv_id = chat.id
        save_conversation(hub)
        return chat

    lead, core_lead, ui_lead = led_by(team, "Ledger Lead"), led_by(core, "Core Lead"), led_by(interface, "UI Lead")
    member = create_conversation(settings, core["folder_id"], title="Verse Coder", parent_conv_id=core["id"])
    _call_as(repo, member, "member-run", workspace_write_file, "ledger/models.py", content="x = 1\n")
    _call_as(repo, lead, "lead-run", workspace_write_file, "ledger/spec.md", content="spec\n")

    for tool in (changesets.changeset_revert, changesets.changeset_archive):
        with pytest.raises(ValueError, match="group leader"):
            _call_as(repo, ui_lead, "ui-run", tool, "member-run")
    with pytest.raises(ValueError, match="group leader"):
        _call_as(repo, core_lead, "core-run", changesets.changeset_revert, "lead-run")
    assert (repo / "ledger" / "models.py").is_file() and (repo / "ledger" / "spec.md").is_file()

    reverted = json.loads(_call_as(repo, lead, "lead-run-2", changesets.changeset_revert, "member-run"))
    assert reverted["ok"] and reverted["reverted"] == [1]
    assert not (repo / "ledger" / "models.py").exists()


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
