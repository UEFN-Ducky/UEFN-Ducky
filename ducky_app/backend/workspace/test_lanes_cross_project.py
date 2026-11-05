"""Lanes that name paths in the person's other Ducky projects, and stored lanes on roster saves."""

from __future__ import annotations

import pytest

from backend.workspace import lanes
from backend.workspace.identity import RunContext
from backend.workspace.policy import WriteRequest

HOME = "C:/Repos/Core"
DUCKYOS = "C:/Repos/DuckyOS"
OTHER = "C:/Repos/OtherRepo"


@pytest.fixture(autouse=True)
def _roots():
    lanes.reset_for_tests()
    lanes.set_project_roots_source(lambda: [HOME, DUCKYOS.replace("/", "\\"), OTHER])
    yield
    lanes.reset_for_tests()


def _check(lane, path, other_project=""):
    ctx = RunContext(run_id="r", conv_id="store", lane=tuple(lane))
    request = WriteRequest(op="write", paths=(path,), tool="workspace_write_file", ctx=ctx, other_project=other_project)
    return lanes.LanePolicy(mode=lambda: "enforce").check(request)


def test_a_full_path_inside_a_ducky_project_is_a_lane() -> None:
    assert lanes.normalize_lane([f"{DUCKYOS}\\plugins\\x"]) == [f"{DUCKYOS}/plugins/x/**"]
    assert lanes.normalize_glob("c:/repos/duckyos/plugins/a.rs") == "C:/repos/duckyos/plugins/a.rs"


@pytest.mark.parametrize("pattern", ["C:/Elsewhere/x", "/etc/passwd", f"{DUCKYOS}/../Secrets", "C:/Repos/DuckyOSX/a"])
def test_a_full_path_outside_every_project_is_refused(pattern) -> None:
    with pytest.raises(lanes.LaneGlobError):
        lanes.normalize_glob(pattern)
    with pytest.raises(ValueError):
        lanes.set_member_lane([{"member_conv_id": "store"}], "store", [pattern], set_by="lead", now=1.0)


def test_writes_in_the_named_project_pass_and_other_repos_are_refused() -> None:
    lane = lanes.normalize_lane([f"{DUCKYOS}/plugins/x", "src/app.py"])
    assert _check(lane, "plugins/x/src/asset_packs.rs", other_project=DUCKYOS).allow
    assert _check(lane, f"{DUCKYOS}/plugins/x/lib.rs").allow  # a full path from the agent's tool call
    assert not _check(lane, "plugins/y/lib.rs", other_project=DUCKYOS).allow
    assert not _check(lane, "plugins/x/lib.rs", other_project=OTHER).allow
    # Project-relative globs still cover the open project only.
    assert _check(lane, "src/app.py").allow
    assert not _check(lane, "src/app.py", other_project=OTHER).allow
    assert not _check(lane, "src/other.py").allow


def test_a_full_path_lane_into_the_open_project_covers_it() -> None:
    lane = lanes.normalize_lane([f"{HOME}/ducky_app/backend"])
    assert _check(lane, "ducky_app/backend/x.py").allow
    assert not _check(lane, "ducky_app/frontend/x.py").allow


def test_a_project_that_left_the_list_covers_nothing() -> None:
    lane = lanes.normalize_lane([f"{OTHER}/a"])
    lanes.reset_for_tests()  # no project roots any more
    assert not _check(lane, "a/b.txt", other_project=OTHER).allow


def test_a_team_keeps_its_lane_when_the_person_selects_another_project() -> None:
    # Oct 28: the person selected LimoRide while a team worked in the Ducky repo, and every
    # write of the team was refused (its relative globs then meant LimoRide).
    rows, _ = lanes.set_member_lane([{"member_conv_id": "m"}], "m", ["ducky_app/backend"], set_by="lead", now=1.0)
    assert rows[0]["lane_root"] == HOME and rows[0]["write_allowed"] == ["ducky_app/backend/**"]
    lane = lanes.anchor_lane(rows[0]["write_allowed"], rows[0]["lane_root"])
    assert lane == (f"{HOME}/ducky_app/backend/**",)

    lanes.set_project_roots_source(lambda: [OTHER, HOME, DUCKYOS])  # the person selected OtherRepo
    assert _check(lane, "ducky_app/backend/x.py", other_project=HOME).allow
    assert _check(lane, f"{HOME}/ducky_app/backend/x.py").allow
    assert not _check(lane, "ducky_app/frontend/x.py", other_project=HOME).allow
    # A relative write now lands in the selected project: never the team's lane.
    assert not _check(lane, "ducky_app/backend/x.py").allow


def test_lane_root_is_only_kept_for_relative_globs_and_known_projects() -> None:
    rows, _ = lanes.set_member_lane([{"member_conv_id": "m"}], "m", [f"{DUCKYOS}/plugins"], set_by="lead", now=1.0)
    assert rows[0]["lane_root"] == ""  # full paths already mean one project
    rows, _ = lanes.set_member_lane([{"member_conv_id": "m"}], "m", [], set_by="lead", now=1.0)
    assert rows[0]["lane_root"] == ""  # read-only
    assert lanes.anchor_lane(None, HOME) is None
    assert lanes.anchor_lane(("src/**",), "") == ("src/**",)
    assert lanes.anchor_lane(("src/**",), "C:/Gone") == ("src/**",)  # unknown: old meaning, never wider
    assert lanes.anchor_lane(("src/**", f"{DUCKYOS}/a/**"), HOME.replace("/", "\\")) == (
        f"{HOME}/src/**", f"{DUCKYOS}/a/**")


def test_lane_root_survives_a_stale_roster_save() -> None:
    stored, _ = lanes.set_member_lane([{"member_conv_id": "m"}], "m", ["src"], set_by="lead", now=5.0)
    kept = lanes.keep_stored_lanes([{"member_conv_id": "m", "write_allowed": None}], stored)
    assert kept[0]["lane_root"] == HOME and kept[0]["write_allowed"] == ["src/**"]


# ------------------------------------------------- stored lanes on roster saves


def test_read_only_lane_survives_a_stale_roster_save() -> None:
    stored, _ = lanes.set_member_lane(
        [{"member_conv_id": "a"}, {"member_conv_id": "b"}], "b", [], set_by="lead", now=5.0
    )
    stale = [{"member_conv_id": "a", "write_allowed": None}, {"member_conv_id": "b", "write_allowed": None, "lane_set_by": ""}]
    kept = lanes.keep_stored_lanes(stale, stored)
    assert kept[1]["write_allowed"] == [] and kept[1]["lane_set_by"] == "lead" and kept[1]["lane_set_at"] == 5.0
    assert kept[0]["write_allowed"] is None  # None stays None


def test_only_the_member_being_set_changes_its_lane() -> None:
    stored = [
        {"member_conv_id": "a", "write_allowed": None},
        {"member_conv_id": "b", "write_allowed": [], "lane_set_by": "lead", "lane_set_at": 5.0},
    ]
    rows, _ = lanes.set_member_lane(
        [{**stored[0]}, {**stored[1], "write_allowed": None}], "a", ["src/x"], set_by="lead", now=6.0
    )
    with lanes.setting_lane("a"):
        kept = lanes.keep_stored_lanes(rows, stored)
    assert kept[0]["write_allowed"] == ["src/x/**"] and kept[1]["write_allowed"] == []
    # Outside the block the newer lane_set_at wins (A's fresh lane, B's stored one), and a
    # new member keeps what it brings.
    kept = lanes.keep_stored_lanes([*rows, {"member_conv_id": "c", "write_allowed": ["docs"]}], stored)
    assert kept[0]["write_allowed"] == ["src/x/**"] and kept[1]["write_allowed"] == [] and kept[2]["write_allowed"] == ["docs"]
    # An older copy never wins over a newer stored lane, even when it carries a lane.
    older = [{"member_conv_id": "b", "write_allowed": ["Content"], "lane_set_by": "user", "lane_set_at": 1.0}]
    assert lanes.keep_stored_lanes(older, stored)[0]["write_allowed"] == []


SANDBOXES = "C:/Users/me/AppData/Local/UEFN-Ducky/sandboxes"


def _member_check(conv_id, lane, path, other_project=""):
    ctx = RunContext(run_id="r", conv_id=conv_id, lane=tuple(lane))
    request = WriteRequest(op="write", paths=(path,), tool="workspace_write_file", ctx=ctx, other_project=other_project)
    return lanes.LanePolicy(mode=lambda: "enforce").check(request)


def test_a_swarm_whose_plan_names_a_repo_gets_lanes_in_that_repo() -> None:
    # Nov 5 2026: a UEFN-Ducky-Release plan built while another project was open put every
    # member's ducky_app/... lane inside the open project.
    from backend.tools.panel.swarm_build import anchor_in_repo, validate
    from backend.tools.panel.swarm_preview import plan_repo

    plan = {"overview": "Release", "body_markdown": f"**Repo:** `{DUCKYOS}`.\n\nBuild it.", "nodes": []}
    assert plan_repo(plan) == DUCKYOS
    assert plan_repo({"nodes": [{"body_markdown": f"Repo: {DUCKYOS.lower()}/sub"}]}) == f"{DUCKYOS}/sub"
    assert plan_repo({"overview": "Repo: C:/Elsewhere"}) == ""  # in no Ducky project
    tree = [{"name": "Seat 1", "profile": "developer", "paths": ["ducky_app/x.py", "ducky_app/web/**"],
             "sub_plan": {"id": "s1", "steps": []}}]
    anchor_in_repo(tree, plan_repo(plan), HOME)
    assert validate(tree, HOME) == []
    assert tree[0]["paths"] == [f"{DUCKYOS}/ducky_app/web/**", f"{DUCKYOS}/ducky_app/x.py"]
    rows, _ = lanes.set_member_lane([{"member_conv_id": "m"}], "m", tree[0]["paths"], set_by="lead", now=1.0)
    lane = rows[0]["write_allowed"]
    assert _member_check("m", lane, "ducky_app/x.py", other_project=DUCKYOS).allow
    assert not _member_check("m", lane, "ducky_app/x.py").allow  # the open project's file is not the repo's


def test_a_member_may_write_its_lane_inside_its_own_sandbox_worktree() -> None:
    lanes.set_sandboxes_root_source(lambda: SANDBOXES.replace("/", "\\"))
    lane = lanes.normalize_lane([f"{DUCKYOS}/ducky_app/x.py", "src/app.py"])
    mine = f"{SANDBOXES}/m1"
    assert _member_check("m1", lane, "work/a/ducky_app/x.py", other_project=mine).allow
    assert _member_check("m1", lane, f"{mine}/work/b/ducky_app/x.py").allow
    assert _member_check("m1", lane, "work/a/src/app.py", other_project=mine).allow
    assert not _member_check("m1", lane, "work/a/ducky_app/y.py", other_project=mine).allow
    assert not _member_check("m1", lane, "downloads/ducky_app/x.py", other_project=mine).allow
    # Another member's sandbox is not its own.
    assert not _member_check("m1", lane, f"{SANDBOXES}/m2/work/a/ducky_app/x.py").allow


def test_two_members_sandbox_lanes_report_no_overlap() -> None:
    a = lanes.normalize_lane([f"{DUCKYOS}/ducky_app/a/**"])
    b = lanes.normalize_lane([f"{DUCKYOS}/ducky_app/b/**"])
    assert lanes.check_lane_set({"A": a, "B": b}) == {"errors": [], "warnings": []}
    # A pattern with no literal folder relates to no real path: no "(maybe)" flood.
    verdict = lanes.check_lane_set({"A": ["**/work/*/ducky_app/a/**"], "B": ["ducky_app/b/**", "src/x.py"]})
    assert verdict == {"errors": [], "warnings": []}
    # Each member's own sandbox is checked against its own chat id only.
    lanes.set_sandboxes_root_source(lambda: SANDBOXES)
    assert _member_check("A", a, f"{SANDBOXES}/A/work/w/ducky_app/a/x.py").allow
    assert not _member_check("A", a, f"{SANDBOXES}/A/work/w/ducky_app/b/x.py").allow
    assert _member_check("B", b, f"{SANDBOXES}/B/work/w/ducky_app/b/x.py").allow
