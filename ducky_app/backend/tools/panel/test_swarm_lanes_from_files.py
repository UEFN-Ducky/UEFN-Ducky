"""Swarm seating: the preview names Files overlaps, lanes come from the exact Files lines."""

from __future__ import annotations

from backend.tools.panel import ducky_swarm as sw
from backend.tools.panel import swarm_build as sb
from backend.tools.panel import swarm_preview as sp
from backend.tools.panel.test_swarm_build import Api

REPO_FILES = [
    "ducky_app/frontend/ui_web/terminal/test_x.py",
    "ducky_app/frontend/ui_web/terminal/resource_guard.py",
    "ducky_app/frontend/agent_host/protocol.py",
    "ducky_app/backend/team_keeper.py",
    "a/b.py",
    "x/dup.py",
    "y/dup.py",
]


def _step(sid, files=""):
    return {"id": sid, "content": f"step {sid}", "body_markdown": f"**Files:** {files}" if files else ""}


def _sec(sid, title, files, steps):
    return {"id": sid, "content": title, "body_markdown": f"**Files:** {files}\nDucky: developer\nModel: m1",
            "children": steps}


def _plan(*secs):
    return {"id": "p", "title": "Seating", "nodes": list(secs)}


def test_two_sections_owning_a_folder_and_a_file_in_it_are_an_overlap():
    plan = _plan(_sec("i", "Seat I", "`a/**`", [_step("i1")]), _sec("i2", "Seat I2", "`a/b.py`", [_step("i21")]))
    assert sp.plan_overlaps(plan) == [{"a": "Seat I", "b": "Seat I2", "path": "a/b.py"}]


def test_preview_lists_the_overlap_and_confirm_refuses_with_the_same_list(tmp_path, monkeypatch):
    plan = _plan(_sec("i", "Seat I", "`agent_host/**`", [_step("i1")]),
                 _sec("i2", "Seat I2", "`ducky_app/frontend/agent_host/protocol.py`", [_step("i21")]))
    monkeypatch.setattr(sb, "repo_file_list", lambda repo: list(REPO_FILES))  # a fake ls-files
    out = sw.swarm_create("", None, from_plan=True, api=Api(tmp_path, plan=plan))
    want = [{"a": "Seat I", "b": "Seat I2", "path": "ducky_app/frontend/agent_host/protocol.py"}]
    assert out["status"] == "needs_confirm" and out["preview"]["overlaps"] == want
    api = Api(tmp_path, plan=plan)
    refused = sw.swarm_create("", None, from_plan=True, confirm=True, api=api)
    assert list(refused)[0] == "error" and refused["status"] == "error" and refused["overlaps"] == want
    assert api.calls == []  # nothing was made


def test_a_short_path_resolves_to_the_one_repo_file_ending_with_it():
    assert sp.resolve_path("terminal/test_x.py", REPO_FILES) == ("ducky_app/frontend/ui_web/terminal/test_x.py", "")
    assert sp.resolve_path("agent_host/**", REPO_FILES) == ("ducky_app/frontend/agent_host/**", "")
    assert sp.resolve_path("a/b.py", REPO_FILES) == ("a/b.py", "")  # already the repo's path
    path, warning = sp.resolve_path("dup.py", REPO_FILES)
    assert path == "dup.py" and "2 repo paths" in warning
    path, warning = sp.resolve_path("new/thing.py", REPO_FILES)
    assert path == "new/thing.py" and "no repo file" in warning
    assert sp.resolve_path("terminal/test_x.py", None) == ("terminal/test_x.py", "")


def test_a_step_only_file_is_in_the_lane():
    plan = _plan(_sec("c", "Seat C", "`ducky_app/backend/plans.py`",
                      [_step("c1", "`team_keeper.py`, `terminal/test_x.py` (its tests)"), _step("c2")]))
    lane = sp.seat_lanes(plan, REPO_FILES)[0]
    assert lane["lane"] == ["ducky_app/backend/plans.py", "ducky_app/backend/team_keeper.py",
                            "ducky_app/frontend/ui_web/terminal/test_x.py"]
    assert lane["warnings"] == ["ducky_app/backend/plans.py: no repo file ends with it; kept as written"]


def test_worktree_seats_get_an_unrestricted_lane_and_their_files_as_a_hint(tmp_path):
    plan = _plan(_sec("c", "Seat C", "`ducky_app/backend/team_keeper.py`", [_step("c1", "`terminal/test_x.py`")]),
                 _sec("d", "Seat D", "`a/b.py`", [_step("d1")]))
    api = Api(tmp_path, plan=plan)
    sb.build_swarm(api=api, from_plan=plan, worktree=True, repo_files=REPO_FILES)
    assert [c[3] for c in api.names("invite")] == [None, None]  # unrestricted
    pings = [c for c in api.calls if c[0] == "ping"]
    assert len(pings) == 2
    tree = sb.seats_from_plan(plan)
    sb.worktree_lanes(tree, plan, REPO_FILES)
    assert tree[0]["files_hint"] == ["ducky_app/backend/team_keeper.py", "ducky_app/frontend/ui_web/terminal/test_x.py"]
    assert tree[0]["paths"] == []
    assert "terminal/test_x.py" in sb._ping_text("s", {**tree[0], "outputs": "o"})
