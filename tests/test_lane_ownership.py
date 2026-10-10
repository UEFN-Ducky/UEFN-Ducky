"""Existing roster lane claims and protected writes; no external CLI guarantee."""
from concurrent.futures import ThreadPoolExecutor
import threading
import uuid

import pytest

from backend.workspace import lanes


@pytest.mark.parametrize("alias", ["Content/Verse/./shared.verse", "Content//Verse/shared.verse", "content\\verse\\SHARED.verse"])
def test_canonical_resource_cannot_have_two_lane_owners(alias):
    members = [{"member_conv_id": "a", "write_allowed": ["Content/Verse/shared.verse"]},
               {"member_conv_id": "b", "write_allowed": []}]
    with pytest.raises(ValueError, match="overlap"):
        lanes.set_member_lane(members, "b", ["Content/Verse/new.verse", alias], set_by="lead", now=1)
    assert members[1]["write_allowed"] == []


@pytest.fixture
def roster(tmp_path, monkeypatch):
    from frontend.chat_store import Conversation
    from frontend.ui_web import project_chats, panel_api
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
    cid = uuid.uuid4().hex
    project = tmp_path / "project"
    project.mkdir()
    conv = Conversation(id=cid, title="Lane fixture", is_group=True, leader_conv_id="lead",
                        group_members=[{"member_conv_id": "a", "write_allowed": []},
                                       {"member_conv_id": "b", "write_allowed": []}])
    load = lambda key: project_chats.load_conversation(key, str(project))
    save = lambda row: project_chats.save_conversation(row, str(project))
    save(conv)
    monkeypatch.setattr(panel_api, "load_conversation", load)
    monkeypatch.setattr(panel_api, "save_conversation", save)
    monkeypatch.setattr(panel_api, "notify_chats_changed", lambda *a, **k: None)
    return PanelApiChatsMixin(), cid, load, panel_api


def test_overlapping_requests_are_atomic_and_owner_is_visible(roster, monkeypatch):
    api, cid, load, panel_api = roster
    # Force both callers to observe the same initial snapshot without a RMW
    # lock. With that lock, the first bounded wait expires and the second sees
    # the committed owner instead. The real default store persists both paths.
    barrier = threading.Barrier(2)
    def concurrent_load(key):
        row = load(key)
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return row
    monkeypatch.setattr(panel_api, "load_conversation", concurrent_load)
    def claim(owner):
        return api.group_set_member_lane(cid, owner,
            [f"Content/Verse/{owner}.verse", "Content/Verse/shared.verse"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("a", "b")))
    assert sum(result["ok"] for result in results) == 1
    winner = ("a", "b")[next(i for i, result in enumerate(results) if result["ok"])]
    loser = "b" if winner == "a" else "a"
    monkeypatch.setattr(panel_api, "load_conversation", load)
    view = api.group_get_lanes(cid)
    assert view["lanes"][winner]["write_allowed"] == [f"Content/Verse/{winner}.verse", "Content/Verse/shared.verse"]
    assert view["lanes"][loser]["write_allowed"] == []


def test_failed_multifile_request_preserves_existing_roster(roster):
    api, cid, _, _ = roster
    assert api.group_set_member_lane(cid, "a", ["Content/Verse/shared.verse"])["ok"]
    before = api.group_get_lanes(cid)
    result = api.group_set_member_lane(cid, "b", ["Content/Verse/free.verse", "Content/Verse/shared.verse"])
    assert not result["ok"]
    assert api.group_get_lanes(cid) == before


def test_concurrent_protected_writes_only_admit_lane_owner(tmp_path):
    from backend.workspace import identity
    from backend.workspace.writer import ProjectWriter, WriteDenied
    writer = ProjectWriter.for_root(str(tmp_path), policies=[lanes.LanePolicy(mode=lambda: "enforce")])
    barrier = threading.Barrier(2)
    def write(owner):
        token = identity.bind(identity.RunContext(conv_id=owner, lane=(f"Content/{owner}.txt",)))
        try:
            barrier.wait(timeout=2)
            try:
                writer.write_text("Content/a.txt", owner, tool="workspace_write_file")
                return True
            except WriteDenied:
                return False
        finally:
            identity.reset(token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(write, ("a", "b"))) == [True, False]
    assert (tmp_path / "Content/a.txt").read_text() == "a"


def test_move_denial_changes_neither_source_nor_destination(tmp_path):
    from backend.workspace import identity
    from backend.workspace.writer import ProjectWriter, WriteDenied
    from unittest.mock import Mock
    source = tmp_path / "Content/source.txt"
    source.parent.mkdir()
    source.write_text("user edit")
    writer = ProjectWriter.for_root(str(tmp_path), policies=[lanes.LanePolicy(mode=lambda: "enforce")])
    perform = Mock(side_effect=AssertionError("must not mutate"))
    token = identity.bind(identity.RunContext(conv_id="a", lane=("Content/destination.txt",)))
    try:
        with pytest.raises(WriteDenied):
            writer.path_op("move", "Content/destination.txt", source="Content/source.txt", perform=perform)
    finally:
        identity.reset(token)
    perform.assert_not_called()
    assert source.read_text() == "user edit" and not (tmp_path / "Content/destination.txt").exists()


@pytest.fixture
def protected_project(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from backend.workspace import runtime
    from backend.workspace.writer import ProjectWriter
    from backend.tools.core import workspace_code
    from frontend.ui_web import project_files
    root = tmp_path / "project"
    (root / "Content/from").mkdir(parents=True)
    (root / "Content/to").mkdir()
    (root / "Content/from/a.txt").write_text("user edit\nsecond line\n", encoding="utf-8")
    journal = Mock()
    journal.record.return_value = {}
    writer = ProjectWriter.for_root(str(root), policies=[lanes.LanePolicy(mode=lambda: "enforce")], journal=journal)
    monkeypatch.setattr(runtime, "_writer", writer)
    monkeypatch.setattr(project_files, "_project_root", lambda: root)
    monkeypatch.setattr(workspace_code, "resolve_workspace_path", lambda rel: str(root / rel))
    return root, writer, journal


@pytest.mark.parametrize("operation", ["write", "edit", "multi_edit", "replace_lines"])
@pytest.mark.parametrize("allowed", [False, True])
def test_actual_text_tools_obey_lane_before_disk_and_journal(protected_project, operation, allowed):
    from backend.workspace import identity
    from backend.workspace.writer import WriteDenied
    from backend.tools.core import system, workspace_code as wc
    root, _, journal = protected_project
    rel = "Content/from/a.txt"
    target = root / rel
    before = target.read_bytes()
    calls = {
        "write": lambda: system.workspace_write_file(rel, "changed\n"),
        "edit": lambda: wc.workspace_edit_file(rel, "user edit", "changed"),
        "multi_edit": lambda: wc.workspace_multi_edit(rel, [
            {"old_text": "user edit", "new_text": "changed"},
            {"old_text": "second line", "new_text": "changed too"}]),
        "replace_lines": lambda: wc.workspace_replace_lines(rel, 1, 1, "changed"),
    }
    token = identity.bind(identity.RunContext(conv_id="owner", lane=(rel,) if allowed else ()))
    try:
        if allowed:
            calls[operation]()
        else:
            with pytest.raises(WriteDenied):
                calls[operation]()
    finally:
        identity.reset(token)
    if allowed:
        assert target.read_bytes() != before
        journal.record.assert_called_once()
    else:
        assert target.read_bytes() == before
        journal.record.assert_not_called()
    assert sorted(p.name for p in target.parent.iterdir()) == ["a.txt"]


@pytest.mark.parametrize("op", ["move", "rename"])
@pytest.mark.parametrize("allowed_paths", [(), ("Content/from/a.txt",), ("Content/to/b.txt",),
                                           ("Content/from/a.txt", "Content/to/b.txt")])
def test_actual_path_operation_checks_both_ends_before_callback(protected_project, op, allowed_paths):
    from unittest.mock import Mock
    from backend.workspace import identity
    from backend.workspace.writer import WriteDenied
    root, writer, journal = protected_project
    source, dest = root / "Content/from/a.txt", root / "Content/to/b.txt"
    before = source.read_bytes()
    perform = Mock(side_effect=lambda: source.rename(dest) and None)
    token = identity.bind(identity.RunContext(conv_id="owner", lane=allowed_paths))
    try:
        if len(allowed_paths) == 2:
            writer.path_op(op, "Content/to/b.txt", source="Content/from/a.txt", perform=perform)
        else:
            with pytest.raises(WriteDenied):
                writer.path_op(op, "Content/to/b.txt", source="Content/from/a.txt", perform=perform)
    finally:
        identity.reset(token)
    if len(allowed_paths) == 2:
        perform.assert_called_once()
        journal.record.assert_called_once()
        assert not source.exists() and dest.read_bytes() == before
    else:
        perform.assert_not_called()
        journal.record.assert_not_called()
        assert source.read_bytes() == before and not dest.exists()


@pytest.mark.parametrize("allowed", [False, True])
def test_delete_tool_preserves_or_trashes_once(protected_project, allowed):
    from backend.workspace import identity
    from backend.workspace.writer import WriteDenied
    from backend.tools.core.workspace_code import workspace_delete_file
    root, _, journal = protected_project
    rel = "Content/from/a.txt"
    before = (root / rel).read_bytes()
    token = identity.bind(identity.RunContext(conv_id="owner", lane=(rel,) if allowed else ()))
    try:
        if allowed:
            workspace_delete_file(rel)
        else:
            with pytest.raises(WriteDenied):
                workspace_delete_file(rel)
    finally:
        identity.reset(token)
    if allowed:
        assert not (root / rel).exists()
        journal.record.assert_called_once()
        assert journal.record.call_args.args[0].trash_token
    else:
        assert (root / rel).read_bytes() == before
        journal.record.assert_not_called()


@pytest.mark.parametrize("denied", ["Content/from/a.txt", "Content/to/a.txt", "Content/to/b.txt"])
def test_composite_move_refuses_final_destination_before_intermediate_write(protected_project, denied):
    from backend.workspace import identity
    from backend.workspace.writer import WriteDenied
    from backend.tools.core.workspace_code import workspace_move_file
    root, _, journal = protected_project
    source = root / "Content/from/a.txt"
    before = source.read_bytes()
    paths = ("Content/from/a.txt", "Content/to/a.txt", "Content/to/b.txt")
    token = identity.bind(identity.RunContext(conv_id="owner", lane=tuple(p for p in paths if p != denied)))
    try:
        with pytest.raises(WriteDenied):
            workspace_move_file("Content/from/a.txt", "Content/to/b.txt")
    finally:
        identity.reset(token)
    assert source.exists(), "denied composite move already removed the source"
    assert source.read_bytes() == before
    assert list((root / "Content/to").iterdir()) == []
    journal.record.assert_not_called()


@pytest.mark.parametrize("mode,lane", [
    ("enforce", ("Content/from/a.txt", "Content/to/a.txt", "Content/to/b.txt")),
    ("shadow", ()), ("off", ()), ("enforce", None)])
def test_composite_move_allowed_runs_each_step_once(protected_project, monkeypatch, mode, lane):
    from unittest.mock import Mock
    from backend.workspace import identity
    from backend.tools.core.workspace_code import workspace_move_file
    from frontend.ui_web import project_files
    root, writer, journal = protected_project
    writer._policies = [lanes.LanePolicy(mode=lambda: mode)]
    monkeypatch.setattr(lanes, "resolve_lane", lambda conv_id: None)
    move = Mock(wraps=project_files.move_project_entry)
    rename = Mock(wraps=project_files.rename_project_entry)
    monkeypatch.setattr(project_files, "move_project_entry", move)
    monkeypatch.setattr(project_files, "rename_project_entry", rename)
    source = root / "Content/from/a.txt"
    before = source.read_bytes()
    token = identity.bind(identity.RunContext(conv_id="owner", lane=lane))
    try:
        workspace_move_file("Content/from/a.txt", "Content/to/b.txt")
    finally:
        identity.reset(token)
    move.assert_called_once_with("Content/from/a.txt", "Content/to")
    rename.assert_called_once_with("Content/to/a.txt", "b.txt")
    assert not source.exists() and not (root / "Content/to/a.txt").exists()
    assert (root / "Content/to/b.txt").read_bytes() == before
    assert [c.args[0].op for c in journal.record.call_args_list] == ["move", "rename"]


@pytest.mark.parametrize("mode,lane,expected_in_lane", [
    ("shadow", (), False), ("off", (), None), ("enforce", None, None)])
def test_existing_unrestricted_and_advisory_settings_are_preserved(tmp_path, monkeypatch, mode, lane, expected_in_lane):
    from backend.workspace import identity
    from backend.workspace.writer import ProjectWriter
    monkeypatch.setattr(lanes, "resolve_lane", lambda conv_id: None)
    writer = ProjectWriter.for_root(str(tmp_path), policies=[lanes.LanePolicy(mode=lambda: mode)])
    token = identity.bind(identity.RunContext(conv_id="unassigned", lane=lane))
    try:
        result = writer.write_text("Content/a.txt", "authorized setting")
    finally:
        identity.reset(token)
    assert result.in_lane is expected_in_lane
    assert (tmp_path / "Content/a.txt").read_text() == "authorized setting"


def test_attribution_override_cannot_lift_bound_lane(protected_project):
    from backend.workspace import identity
    from backend.workspace.writer import WriteDenied
    root, writer, journal = protected_project
    before = (root / "Content/from/a.txt").read_bytes()
    token = identity.bind(identity.RunContext(conv_id="readonly", lane=()))
    try:
        with pytest.raises(WriteDenied):
            writer.write_text("Content/from/./a.txt", "spoofed", writer={"source": "user", "conv_id": "leader"})
    finally:
        identity.reset(token)
    assert (root / "Content/from/a.txt").read_bytes() == before
    journal.record.assert_not_called()


@pytest.mark.parametrize("allowed", [False, True])
def test_nested_dispatch_reaches_same_actual_write_boundary(protected_project, monkeypatch, unrestricted_tools, allowed):
    import asyncio
    import json
    from types import SimpleNamespace
    from backend.agent import tools, run_context, hammer_guard
    from backend.agent.test_hammer_guard import _patch_dispatch
    from backend.tools.core import system
    from backend.tools.panel.ducky_panel import ducky_call_tool
    from backend.workspace import identity
    root, _, journal = protected_project
    rel = "Content/from/a.txt"
    before = (root / rel).read_bytes()
    async def transport(name, args):
        assert name == "workspace_write_file"
        return [SimpleNamespace(text=system.workspace_write_file(**args))]
    _patch_dispatch(monkeypatch, transport)
    monkeypatch.setattr("frontend.ui_web.ui_rpc.wait_for_answers", lambda *a: None)
    monkeypatch.setattr("backend.agent.chat_title.require_self_name", lambda *a: None)
    monkeypatch.setattr("backend.agent.coding_agents.plans.plan_mutator_block_reason", lambda *a: None)
    # Exercise the writer independently of the advisory early dispatch check.
    monkeypatch.setattr(lanes, "current_mode", lambda: "off")
    monkeypatch.setattr(tools, "_record_tool_failure", lambda *a: None)
    hammer_guard.reset_all()
    mode = run_context.set_mode("agent")
    token = identity.bind(identity.RunContext(conv_id="owner", lane=(rel,) if allowed else ()))
    try:
        result = json.loads(asyncio.run(ducky_call_tool("workspace_write_file", {"relative_path": rel, "content": "nested"})))
    finally:
        identity.reset(token)
        run_context.reset_mode(mode)
        hammer_guard.reset_all()
    if allowed:
        assert (root / rel).read_text() == "nested"
        journal.record.assert_called_once()
    else:
        assert result["ok"] is False and "Out of lane" in result["error"]
        assert (root / rel).read_bytes() == before
        journal.record.assert_not_called()
