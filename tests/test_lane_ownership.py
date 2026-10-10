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
