"""A moved chat or group takes its team rows, leader and parent with it (sync_membership)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend.ui_web import group_orchestrator
from frontend.ui_web.chats import conversations as _convs
from frontend.ui_web.chats import groups
from frontend.ui_web.chats import saves as _saves


class _Island:
    def __init__(self) -> None:
        self.folders: list = []
        self.convs: dict = {}

    def group(self, hub, folder, parent_folder="", leader="", members=()):
        self.folders.append(SimpleNamespace(id=folder, name=hub, parent_id=parent_folder, group_hub_id=hub))
        self.convs[hub] = self._conv(hub, folder, is_group=True, leader_conv_id=leader,
                                     group_members=[{"member_conv_id": m, "name": self.name(m)} for m in members])

    def chat(self, cid, folder="", parent="", title=""):
        self.convs[cid] = self._conv(cid, folder, parent_conv_id=parent, title=title or cid)

    def name(self, cid):
        conv = self.convs.get(cid)
        return conv.title if conv is not None else cid

    @staticmethod
    def _conv(cid, folder, **kw):
        base = dict(id=cid, title=cid, folder_id=folder, parent_conv_id="", is_group=False, leader_conv_id="",
                    group_members=[], ducky_name="", ducky_style="", model="", coding_agent="", profile_id="")
        base.update(kw)
        return SimpleNamespace(**base)

    def members(self, hub):
        return [r["member_conv_id"] for r in self.convs[hub].group_members]


@pytest.fixture
def island(monkeypatch):
    s = _Island()
    monkeypatch.setattr(groups, "island_for_conversation", lambda cid, root=None: (list(s.convs.values()), s.folders))
    monkeypatch.setattr(_convs, "load_conversation", lambda cid, root=None: s.convs.get(cid))
    monkeypatch.setattr(_saves, "save_conversation", lambda conv, root=None, **kw: s.convs.__setitem__(conv.id, conv))
    monkeypatch.setattr(_saves, "flush_chat_saves", lambda *a, **kw: True)
    monkeypatch.setattr(group_orchestrator, "save_group_roster",
                        lambda conv, rows, root=None, **kw: s.convs.__setitem__(conv.id, conv))
    monkeypatch.setattr("frontend.ui_web.chats.nesting_notice.tell_moves", lambda *a, **kw: [])
    return s


def test_moving_a_member_into_another_groups_folder_moves_its_row(island):
    island.chat("la", "fA", "A")
    island.chat("m", "fB", "A")  # dragged from A's folder into B's
    island.chat("lb", "fB", "B")
    island.group("A", "fA", leader="la", members=["la", "m"])
    island.group("B", "fB", leader="lb", members=["lb"])

    out = groups.sync_membership(None, ["m"])

    assert island.members("A") == ["la"] and island.members("B") == ["lb", "m"]
    assert island.convs["m"].parent_conv_id == "B"
    assert out["m"]["left"] == "A" and out["m"]["joined"] == "B" and out["m"]["leader_changed"] is False


def test_moving_the_leader_out_hands_the_lead_to_the_one_member_left(island):
    island.chat("m", "fB", "A")
    island.chat("n", "fA", "A")
    island.group("A", "fA", leader="m", members=["m", "n"])
    island.group("B", "fB", members=[])

    out = groups.sync_membership(None, ["m"])

    assert island.convs["A"].leader_conv_id == "n" and island.members("A") == ["n"]
    assert out["m"]["leader_changed"] is True and out["m"]["old_leader"] == "m"


def test_moving_the_leader_out_of_a_group_of_several_clears_its_lead(island):
    island.chat("m", "", "A")  # dragged to the top level
    island.chat("n", "fA", "A")
    island.chat("o", "fA", "A")
    island.group("A", "fA", leader="m", members=["m", "n", "o"])

    groups.sync_membership(None, ["m"])

    assert island.convs["A"].leader_conv_id == "" and island.members("A") == ["n", "o"]
    assert island.convs["m"].parent_conv_id == ""


def test_the_memory_fix_team_ends_with_its_own_lead_as_its_only_leader(island):
    # Nov 5 2026: group 0cadd26f sat in its own folder but still listed the 1.2.469 lead.
    island.chat("5d789e53", "f469", "R469", title="Developer")
    island.chat("88390a68", "fMem", "", title="Lead: Memory fix")  # dragged into its own folder
    island.group("R469", "f469", leader="5d789e53", members=["5d789e53"])
    island.group("0cadd26f", "fMem", leader="5d789e53", members=["5d789e53"])

    out = groups.sync_membership(None, ["88390a68"])

    assert island.convs["0cadd26f"].leader_conv_id == "88390a68"
    assert island.members("0cadd26f") == ["88390a68"]
    assert island.convs["R469"].leader_conv_id == "5d789e53"  # the old team keeps its lead
    assert out["88390a68"]["joined"] == "0cadd26f" and out["88390a68"]["leader_changed"] is True


def test_a_nested_group_dragged_to_the_top_level_leaves_its_outer_group(island):
    # Nov 5 2026: the Rust launcher team was dragged out of Island Media Kit but kept it as parent.
    island.chat("imk", "fIMK", "IMK")
    island.chat("lead", "fRust", "RUST")
    island.group("IMK", "fIMK", leader="imk", members=["imk"])
    island.group("RUST", "fRust", parent_folder="", leader="lead", members=["lead"])
    island.convs["RUST"].parent_conv_id = "IMK"

    out = groups.sync_membership(None, ["RUST"])

    assert island.convs["RUST"].parent_conv_id == ""
    assert island.convs["RUST"].leader_conv_id == "lead" and island.members("RUST") == ["lead"]
    assert island.convs["IMK"].leader_conv_id == "imk" and island.members("IMK") == ["imk"]
    assert out["RUST"]["left"] == "IMK"


def test_a_nested_group_dragged_into_another_group_points_at_it(island):
    island.chat("a", "fA", "A")
    island.chat("b", "fB", "B")
    island.group("A", "fA", leader="a", members=["a"])
    island.group("B", "fB", leader="b", members=["b"])
    island.group("N", "fN", parent_folder="fB", members=[])
    island.convs["N"].parent_conv_id = "A"

    groups.sync_membership(None, ["N"])

    assert island.convs["N"].parent_conv_id == "B"


def test_a_chat_leading_its_own_group_from_the_top_level_keeps_it(island):
    island.chat("solo", "")
    island.chat("w", "fX", "X")
    island.group("X", "fX", leader="solo", members=["solo", "w"])

    assert groups.sync_membership(None, ["solo", "w"]) == {}
    assert island.convs["X"].leader_conv_id == "solo" and island.members("X") == ["solo", "w"]
