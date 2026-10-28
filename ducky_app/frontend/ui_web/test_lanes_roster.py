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
    # A lane that no longer parses turns read-only, never unrestricted.
    assert normalize_member({"member_conv_id": "c1", "write_allowed": ["../x"]})["write_allowed"] == []
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


def test_a_members_lane_means_the_teams_project_whichever_is_selected(monkeypatch) -> None:
    from frontend.ui_web import project_chats

    repo, limo = "C:/Repos/Ducky", "C:/Islands/LimoRide"
    lane_engine.set_project_roots_source(lambda: [limo, repo])  # LimoRide is selected now
    hub, convs = _world()
    hub.group_members[1]["lane_root"] = repo
    monkeypatch.setattr(roster_lanes, "_load", lambda cid: convs.get(cid))
    assert roster_lanes.lane_for_member("hacker") == (f"{repo}/Content/Verse/Shop/**",)
    # A lane set before lane_root existed means the project the team's hub chat lives in.
    del hub.group_members[1]["lane_root"]
    monkeypatch.setattr(project_chats, "conversation_project_slug",
                        lambda cid, project_root=None: project_chats.project_slug(repo) if cid == "hub" else None)
    assert roster_lanes.lane_for_member("hacker") == (f"{repo}/Content/Verse/Shop/**",)
    # A projectless hub: the lane keeps its old meaning (never widened).
    monkeypatch.setattr(project_chats, "conversation_project_slug", lambda cid, project_root=None: "_no_project")
    assert roster_lanes.lane_for_member("hacker") == ("Content/Verse/Shop/**",)
    assert normalize_member({"member_conv_id": "c", "lane_root": f" {repo} "})["lane_root"] == repo


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


def test_group_invite_refuses_an_overlapping_lane_before_making_a_chat(panel, monkeypatch) -> None:
    # Oct 12 2026: the refused invite still created the member's chat.
    api, hub, saved, _ = panel
    made: list = []
    monkeypatch.setattr(_pa, "create_conversation", lambda *a, **k: made.append(a))
    res = api.group_invite("hub", "builder", write_allowed=["Content/Verse/**"], name="Second")
    assert not res["ok"] and "overlaps" in res["error"]
    assert made == [] and saved == [] and len(hub.group_members) == 3


@pytest.fixture
def store(monkeypatch):
    """A chat store that hands out copies, like the real one: a caller holding an older
    copy of the hub and saving it is how teammates' lanes got lost (Oct 24 2026)."""
    import copy

    from frontend.ui_web import project_chats

    hub, _ = _world()
    docs = {"hub": copy.deepcopy(hub)}
    load = lambda cid, *a, **k: copy.deepcopy(docs.get(cid))  # noqa: E731
    monkeypatch.setattr(_pa, "load_conversation", load)
    monkeypatch.setattr(project_chats, "load_conversation_meta", load)
    monkeypatch.setattr(_pa, "save_conversation", lambda conv, *a, **k: docs.__setitem__(conv.id, copy.deepcopy(conv)))
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **k: None)

    def lanes_now():
        return {m["member_conv_id"]: (m.get("write_allowed"), m.get("lane_set_by")) for m in docs["hub"].group_members}

    return PanelApiChatsMixin(), load, lanes_now


def test_an_older_roster_copy_never_loosens_a_teammates_lane(store) -> None:
    api, load, lanes_now = store
    stale = load("hub")  # an invite / folder sync / group turn that loaded the hub earlier
    assert api.group_set_member_lane("hub", "artist", [], set_by="lead")["ok"]
    assert api.group_set_member_lane("hub", "hacker", ["Content/Verse/Hub"], set_by="lead")["ok"]
    assert lanes_now()["artist"] == ([], "lead")
    # The stale copy saves with a new member (an invite), then again (a second invite).
    api._save_roster(stale, [*stale.group_members, {"member_conv_id": "c", "name": "C"}])
    api._save_roster(load("hub"), [*load("hub").group_members, {"member_conv_id": "d", "name": "D"}])
    now = lanes_now()
    assert now["artist"] == ([], "lead") and now["hacker"] == (["Content/Verse/Hub/**"], "lead")
    assert now["lead"] == (None, "") and now["c"] == (None, "") and now["d"] == (None, "")  # None stays None
    # Setting one member again leaves the others as they are.
    assert api.group_set_member_lane("hub", "hacker", ["Content/Verse/Shop"], set_by="lead")["ok"]
    now = lanes_now()
    assert now["artist"] == ([], "lead") and now["hacker"] == (["Content/Verse/Shop/**"], "lead")


def test_a_fresh_read_only_lane_survives_a_members_refresh(store) -> None:
    api, load, lanes_now = store
    refresh = load("hub")  # group_members() loaded the roster, then set_lane ran
    assert api.group_set_member_lane("hub", "hacker", [], set_by="lead")["ok"]
    api._save_roster(refresh, refresh.group_members)
    assert lanes_now()["hacker"] == ([], "lead")
    # Clearing through set_lane still opens it: the member being set always wins.
    assert api.group_set_member_lane("hub", "hacker", None, set_by="lead")["ok"]
    assert lanes_now()["hacker"] == (None, "lead")
