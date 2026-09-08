"""Roster-backed lanes: normalize_member fields, the provider, and the panel API."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.workspace import events, lanes as lane_engine
from frontend.ui_web import lanes as roster_lanes
from frontend.ui_web import panel_api as _pa
from frontend.ui_web.group_orchestrator import normalize_member
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


@pytest.fixture(autouse=True)
def _clean():
    lane_engine.reset_for_tests()
    events.reset_for_tests()
    yield
    lane_engine.reset_for_tests()
    events.reset_for_tests()


def test_normalize_member_keeps_and_normalizes_lane() -> None:
    row = normalize_member({"member_conv_id": "c1", "write_allowed": ["Content/Verse/Shop", "Content/Verse/Shop"]})
    assert row["write_allowed"] == ["Content/Verse/Shop/**"] and row["lane_set_by"] == "" and row["lane_set_at"] == 0.0
    assert normalize_member({"member_conv_id": "c1"})["write_allowed"] is None
    assert normalize_member({"member_conv_id": "c1", "write_allowed": []})["write_allowed"] == []
    assert normalize_member({"member_conv_id": "c1", "write_allowed": ["../x"]})["write_allowed"] is None
    assert normalize_member({"member_conv_id": "c1", "write_allowed": "a/b, a/c"})["write_allowed"] == ["a/b/**", "a/c/**"]


def _world():
    hub = SimpleNamespace(
        id="hub", is_group=True, leader_conv_id="lead", title="Hub", folder_id="",
        group_members=[
            {"member_conv_id": "lead", "name": "Architect"},
            {"member_conv_id": "hacker", "name": "Hacker", "write_allowed": ["Content/Verse/Shop/**"]},
            {"member_conv_id": "artist", "name": "Artist"},
        ],
    )
    convs = {
        "hub": hub,
        "lead": SimpleNamespace(id="lead", parent_conv_id="hub"),
        "hacker": SimpleNamespace(id="hacker", parent_conv_id="hub"),
        "artist": SimpleNamespace(id="artist", parent_conv_id="hub"),
        "solo": SimpleNamespace(id="solo", parent_conv_id=""),
    }
    return hub, convs


def test_roster_provider_resolves_member_lane(monkeypatch) -> None:
    hub, convs = _world()
    monkeypatch.setattr(roster_lanes, "_load", lambda cid: convs.get(cid))
    assert roster_lanes.lane_for_member("hacker") == ("Content/Verse/Shop/**",)
    assert roster_lanes.lane_for_member("artist") is None
    assert roster_lanes.lane_for_member("solo") is None
    assert roster_lanes.lane_for_member("ghost") is None
    roster_lanes.install()
    assert lane_engine.resolve_lane("hacker") == ("Content/Verse/Shop/**",)


def test_lane_mode_falls_back_to_shadow(monkeypatch) -> None:
    from frontend.settings import PanelSettings

    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls: SimpleNamespace(write_lanes_mode="ENFORCE")))
    assert roster_lanes.lane_mode() == "enforce"
    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls: SimpleNamespace(write_lanes_mode="bogus")))
    assert roster_lanes.lane_mode() == "shadow"


@pytest.fixture
def panel(monkeypatch):
    hub, convs = _world()
    saved: list = []
    notified: list = []
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: convs.get(cid))
    monkeypatch.setattr(_pa, "save_conversation", lambda conv: saved.append(conv))
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **k: notified.append(a))
    api = PanelApiChatsMixin()
    return api, hub, saved, notified


def test_group_set_member_lane_persists_and_emits(panel) -> None:
    api, hub, saved, notified = panel
    seen: list[dict] = []
    events.register_sink(seen.append)
    res = api.group_set_member_lane("hub", "artist", ["Content/Verse/Hub"], set_by="lead")
    assert res["ok"] and res["warnings"] == []
    artist = next(m for m in res["group_members"] if m["member_conv_id"] == "artist")
    assert artist["write_allowed"] == ["Content/Verse/Hub/**"] and artist["lane_set_by"] == "lead"
    assert hub.group_members is res["group_members"] and saved == [hub] and notified
    assert res["lanes"]["lanes"]["artist"]["write_allowed"] == ["Content/Verse/Hub/**"]
    assert seen[-1] == {"type": "lane_changed", "group_id": "hub", "member_conv_id": "artist", "write_allowed": ["Content/Verse/Hub/**"]}


def test_group_set_member_lane_refuses_overlap_and_supports_force_and_clear(panel) -> None:
    api, hub, saved, _ = panel
    res = api.group_set_member_lane("hub", "artist", ["Content/Verse/**"])
    assert not res["ok"] and "overlaps" in res["error"] and saved == []
    res = api.group_set_member_lane("hub", "artist", ["Content/*/Shop/**"])
    assert not res["ok"] and "force=true" in res["error"]
    res = api.group_set_member_lane("hub", "artist", ["Content/*/Shop/**"], force=True)
    assert res["ok"] and res["warnings"]
    res = api.group_set_member_lane("hub", "artist", None)
    assert res["ok"] and next(m for m in res["group_members"] if m["member_conv_id"] == "artist")["write_allowed"] is None
    assert not api.group_set_member_lane("hub", "ghost", [])["ok"]
    assert api.group_set_member_lane("solo", "x", [])["error"] == "Not a group chat"


def test_group_get_and_check_lanes(panel) -> None:
    api, hub, _, _ = panel
    view = api.group_get_lanes("hub")
    assert view["ok"] and view["schema_version"] == 1 and view["lanes"]["hacker"]["write_allowed"] == ["Content/Verse/Shop/**"]
    check = api.group_check_lane("hub", "artist", "Content/Verse/Shop/shop.verse")
    assert not check["ok"] and check["errors"] and check["normalized"] == ["Content/Verse/Shop/shop.verse"]
    check = api.group_check_lane("hub", "artist", ["Content/Verse/Hub"])
    assert check["ok"] and check["normalized"] == ["Content/Verse/Hub/**"] and check["warnings"] == []
    bad = api.group_check_lane("hub", "artist", ["../escape"])
    assert not bad["ok"] and bad["normalized"] is None
