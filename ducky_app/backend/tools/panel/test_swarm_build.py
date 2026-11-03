"""swarm_build: validate first, roll back, ping seats, start later, outputs, build from the plan, hand-overs."""

from __future__ import annotations

import json
import os

import pytest

from backend.tools.panel import swarm_build as sb


class Api:
    """Live groups and chats, files under a temp project root."""

    def __init__(self, root, fail_invite_at=0, answers=True, plan=None):
        self.root, self.fail_invite_at, self.answers, self.plan = str(root), fail_invite_at, answers, plan
        self.calls: list[tuple] = []
        self.live: set[str] = set()
        self.groups: dict[str, dict] = {}
        self.n = self.invites = 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    def caller(self):
        return "lead"

    def project_root(self):
        return self.root

    def group_create(self, name, parent_folder_id=""):
        if name in self.groups:  # find-or-create by name, like group_find_or_create
            return {**self.groups[name], "reused": True}
        gid = self._id("g")
        self.calls.append(("group_create", name, parent_folder_id))
        self.live.add(gid)
        self.groups[name] = {"id": gid, "folder_id": f"f-{gid}"}
        return dict(self.groups[name])

    def load_plan(self, chat_id):
        return self.plan

    def is_chat(self, conv_id):
        return conv_id.startswith("chat-")

    def add_member(self, group_id, conv_id):
        self.calls.append(("add_member", group_id, conv_id))

    def set_lane(self, group_id, conv_id, paths):
        self.calls.append(("set_lane", conv_id, paths))

    def trust_drafts(self, group_ids):
        self.calls.append(("trust_drafts", sorted(group_ids)))

    def chat_home(self, conv_id):
        return {"folder_id": "home", "parent_conv_id": ""}

    def restore_home(self, conv_id, home):
        self.calls.append(("restore_home", conv_id, home["folder_id"]))

    def add_leader(self, group_id, conv_id):
        self.calls.append(("add_leader", group_id, conv_id))

    def rename_chat(self, conv_id, title):
        pass

    def ensure_plan(self, chat_id, goal):
        pass

    def plan_add_node(self, chat_id, content, parent_id="", kind="step", body_markdown=""):
        self.calls.append(("plan_add_node", content))
        return self._id("n")

    def plan_delete_node(self, chat_id, node_id):
        self.calls.append(("plan_delete_node", node_id))

    def invite(self, group_id, profile, name, model, write_allowed):
        self.invites += 1
        if self.invites == self.fail_invite_at:
            raise ValueError("invite failed")
        cid = self._id("c")
        self.calls.append(("invite", group_id, name, write_allowed))
        self.live.add(cid)
        return cid

    def set_muted(self, conv_id, muted):
        self.calls.append(("set_muted", conv_id, muted))

    def ping(self, conv_id, text, timeout_s, later=None):
        self.calls.append(("ping", conv_id))
        return {"ok": True} if self.answers else {"ok": False, "error": "Cursor SDK request failed"}

    def assign(self, chat_id, node_id, assignee):
        self.calls.append(("assign", node_id, assignee))

        def walk(nodes):
            for n in nodes:
                if n.get("id") == node_id:
                    n["assignee"] = assignee
                walk(n.get("children") or [])

        walk((self.plan or {}).get("nodes") or [])

    def archive(self, conv_id):
        self.live.discard(conv_id)

    def save_file(self, rel, text):
        full = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)

    def read_records(self):
        return sb.read_records(self.root)

    def names(self, call):
        return [c for c in self.calls if c[0] == call]


def _step(i, sid=""):
    return {"id": sid or f"st{i}", "content": f"step {i}", "body_markdown": ""}


def _section(sid, title, body, kids):
    return {"id": sid, "content": title, "kind": "subplan", "body_markdown": body, "children": kids}


def _seat_sec(sid, title, files, extra=""):
    return _section(sid, title, f"**Files:** `{files}`\nDucky: developer\nModel: m1\n{extra}", [_step(sid)])


def _plan(*nodes, title="Speed"):
    return {"id": "plan1", "title": title, "nodes": list(nodes)}


def _flat_plan(n, lane=lambda i: f"src/s{i}/**"):
    return _plan(*[_seat_sec(f"s{i}", f"Seat {i}", lane(i)) for i in range(1, n + 1)])


# ---- 74856ec6e5 validate first, then build or none ---------------------------------------

def test_a_failure_at_seat_9_leaves_no_groups_and_no_chats(tmp_path):
    api = Api(tmp_path, fail_invite_at=9)
    with pytest.raises(sb.SwarmBuildError) as err:
        sb.build_swarm(api=api, from_plan=_flat_plan(10))
    assert err.value.result == {"ok": False, "error": "invite failed", "created": 0}
    assert api.live == set() and api.names("ping") == [] and api.names("assign") == []
    assert ("restore_home", "lead", "home") in api.calls


def test_a_lane_outside_the_project_fails_before_anything_is_made(tmp_path, monkeypatch):
    from backend.workspace import lanes

    other = "C:/Repos/DuckyOS"
    monkeypatch.setattr(lanes, "_roots_source", lambda: [str(tmp_path), other])
    # A lane in another of the person's projects is seated as it is: full path, no project switch.
    api = Api(tmp_path)
    sb.build_swarm(api=api, from_plan=_flat_plan(2, lane=lambda i: f"{other}/plugins/plugin-meshy/**"
                                                 if i == 2 else f"src/s{i}/**"))
    lanes_by_seat = {c[2]: c[3] for c in api.names("invite")}
    assert f"{other}/plugins/plugin-meshy/**" in lanes_by_seat["Seat 2"]
    assert lanes.in_lane(lanes_by_seat["Seat 2"], f"{other}/plugins/plugin-meshy/src/main.ts")
    assert not lanes.in_lane(lanes_by_seat["Seat 2"], f"{other}/plugins/other/x.ts")
    # A path in no known project still fails before anything is made.
    api = Api(tmp_path)
    plan = _flat_plan(9, lane=lambda i: "C:/elsewhere/x/**" if i == 9 else f"src/s{i}/**")
    with pytest.raises(sb.SwarmBuildError, match="seat 9: lane outside project"):
        sb.build_swarm(api=api, from_plan=plan)
    assert api.calls == []


def test_a_seat_with_no_model_inherits_the_leads(tmp_path):
    class LeadApi(Api):
        def seat_model(self, lead, profile):
            return "claude_code:claude-opus-5-5" if profile == "developer" else ""

        def invite(self, group_id, profile, name, model, write_allowed):
            self.calls.append(("model", name, model))
            return super().invite(group_id, profile, name, model, write_allowed)

    plan = _plan(_section("a", "A", "Ducky: developer", [_step(1)]),
                 _section("b", "B", "Ducky: developer\nModel: codex:gpt-6", [_step(2)]))
    api = LeadApi(tmp_path)
    sb.build_swarm(api=api, from_plan=plan)
    assert dict((c[1], c[2]) for c in api.names("model")) == {"A": "claude_code:claude-opus-5-5",
                                                               "B": "codex:gpt-6"}


def test_a_full_path_lane_inside_the_project_becomes_relative(tmp_path):
    api = Api(tmp_path)
    root = str(tmp_path).replace("\\", "/")
    sb.build_swarm(api=api, from_plan=_flat_plan(1, lane=lambda i: f"{root}/src/a"))
    lane = api.names("invite")[0][3]
    assert lane[0] == "src/a/**" and lane[1].startswith(".ducky/swarms/")


def test_a_step_in_two_seats_fails_validation_with_nothing_created(tmp_path):
    api = Api(tmp_path)
    plan = _plan(_section("a", "A", "Files: a/**\nDucky: d\nModel: m", [_step(1, "dup")]),
                 _section("b", "B", "Files: b/**\nDucky: d\nModel: m", [_step(2, "dup")]))
    with pytest.raises(sb.SwarmBuildError, match="step dup is already A's"):
        sb.build_swarm(api=api, from_plan=plan)
    assert api.calls == []


def test_overlapping_lanes_and_twin_group_names_fail_validation():
    tree = [{"group": "T", "muted": True, "seats": [{"name": "x", "profile": "d", "paths": ["a/**"],
                                                     "sub_plan": {"steps": []}}], "groups": []},
            {"group": "T", "muted": True, "seats": [{"name": "y", "profile": "d", "paths": ["a/b/**"],
                                                     "sub_plan": {"steps": []}}], "groups": []}]
    errors = sb.validate(tree, "")
    assert any("two groups called 'T'" in e for e in errors)
    tree[1]["group"] = "U"
    assert any("overlaps" in e for e in sb.validate(tree, ""))


def test_a_three_deep_nested_preview_builds_and_leads_before_lanes(tmp_path):
    def seat(n):
        return {"name": n, "job": n, "kind": "developer", "profile": "developer", "model": "m", "paths": [f"src/{n}/**"],
                "sub_plan": {"title": n, "done": 0, "total": 1, "steps": [{"content": f"{n} step", "body_markdown": ""}]}}

    deep = {"group": "C", "purpose": "", "muted": False, "seats": [seat("c")], "groups": []}
    mid = {"group": "B", "purpose": "", "muted": True, "seats": [seat("b")], "groups": [deep]}
    top = {"group": "A", "purpose": "", "muted": True, "seats": [seat("a")], "groups": [mid]}
    jobs = {n: {"purpose": "", "task": "", "paths": [f"src/{n}/**"]} for n in "abc"}
    api = Api(tmp_path)
    out = sb.build_swarm({"goal": "Deep", "tree": [top], "jobs": jobs}, api)
    groups = api.names("group_create")
    assert [g[1] for g in groups] == ["Deep", "A", "B", "C"]
    assert groups[3][2] == f"f-{mid['group_id']}"
    first_invite = next(i for i, c in enumerate(api.calls) if c[0] == "invite")
    assert any(c[0] == "add_leader" for c in api.calls[:first_invite])
    assert len(api.names("assign")) == 3 and out["failed"] == []


# ---- 0940a0446c ping before assign ------------------------------------------------------

def test_a_seat_that_never_answers_is_not_assigned(tmp_path):
    api = Api(tmp_path, answers=False)
    out = sb.build_swarm(api=api, from_plan=_flat_plan(1))
    seat = out["tree"][0]
    assert api.names("ping") and api.names("assign") == []
    assert seat["status"] == "seat_failed"
    assert out["failed"] == ["Seat 1 (m1): didn't start (Cursor SDK request failed) · Swap model"]


# ---- db3cdb42fa start later -------------------------------------------------------------

def test_start_after_seats_wait_for_their_step(tmp_path):
    engine = _section("eng", "Engine", "", [_seat_sec("e1", "Engine 1", "eng/a/**"),
                                            _seat_sec("e2", "Engine 2", "eng/b/**")])
    engine["body_markdown"] = "Start after: P0"
    api = Api(tmp_path)
    out = sb.build_swarm(api=api, from_plan=_plan(_seat_sec("p", "P0 seat", "p/**"), engine))
    assert [c[2] for c in api.names("invite")] == ["P0 seat"] and out["waiting"] == ["P0"]
    assert sb.on_step_completed("plan1", "other", api) == []
    assert sb.on_step_completed("plan1", "P0", api) == ["Engine 1", "Engine 2"]
    assert [c[2] for c in api.names("invite")] == ["P0 seat", "Engine 1", "Engine 2"]
    assert {c[1] for c in api.names("assign")} == {"p", "e1", "e2"}
    assert sb.on_step_completed("plan1", "P0", api) == []  # once only


# ---- 2f9d0d750d durable outputs ---------------------------------------------------------

def test_outputs_outlive_an_archived_group(tmp_path):
    api = Api(tmp_path)
    out = sb.build_swarm(api=api, from_plan=_flat_plan(2))
    seat = out["tree"][0]
    assert seat["outputs"].startswith(out["outputs"]) and f"{seat['outputs']}/**" in api.names("invite")[0][3]
    api.save_file(f"{seat['outputs']}/toolchain-win64.tar.xz", "x")
    api.archive(out["root_group_id"])
    assert (tmp_path / seat["outputs"] / "toolchain-win64.tar.xz").is_file()
    assert (tmp_path / out["outputs"] / "README.md").is_file()


# ---- 039c9f0464 build from the lead's plan ----------------------------------------------

def _three_level_plan():
    team = _section("t", "Team", "", [
        _section("t1", "Team 1", "", [_seat_sec("a", "Agent runs", "ducky_app/a.py",
                                                "**Hands over:** `ducky_app/a.py` → Agent host")]),
        _section("t2", "Team 2", "", [_section("t3", "Team 3", "", [_seat_sec("h", "Agent host", "ducky_app/h/**")])]),
    ])
    lead_step = {"id": "top", "content": "lead's own step", "children": []}
    return _plan(lead_step, team, _seat_sec("r", "Reviewer", "docs/**"))


def test_seats_from_plan_follows_the_outline_at_any_depth():
    tree = sb.seats_from_plan(_three_level_plan())
    assert [n.get("group") or n.get("name") for n in tree] == ["Team", "Reviewer"]
    team = tree[0]
    assert [g["group"] for g in team["groups"]] == ["Team 1", "Team 2"]
    assert team["groups"][1]["groups"][0]["group"] == "Team 3"
    assert team["groups"][1]["groups"][0]["seats"][0]["name"] == "Agent host"
    seat = team["groups"][0]["seats"][0]
    assert seat["paths"] == ["ducky_app/a.py"] and seat["profile"] == "developer" and seat["model"] == "m1"


def test_bold_files_line_gives_clean_paths():
    sec = _section("x", "X", "**Files:** `ducky_app/backend/tools/panel/swarm_build.py` (from S0's split) "
                             "and a new `test_swarm_build.py`.\nDucky: d\nModel: m", [_step(1)])
    assert sb.seats_from_plan(_plan(sec))[0]["paths"] == ["ducky_app/backend/tools/panel/swarm_build.py",
                                                         "test_swarm_build.py"]
    sec["body_markdown"] = "Files**: a/**, b.py\nDucky: d\nModel: m"
    assert sb.seats_from_plan(_plan(sec))[0]["paths"] == ["a/**", "b.py"]


def test_lanes_are_paths_and_globs_never_operation_names():
    # Nov 3 2026: lanes came out as ["model3d.start", "model3d.status"]; a not-yet-made folder
    # written bare with a note after it came out empty (no_lane).
    sec = _section("x", "X", "Files: `C:/r/DuckyOS/plugins/plugin-meshy/**` (new; tools `model3d.start`, "
                             "`model3d.status`, `model3d-models`)", [_step(1)])
    assert sb.seats_from_plan(_plan(sec))[0]["paths"] == ["C:/r/DuckyOS/plugins/plugin-meshy/**"]
    sec["body_markdown"] = "**Files:** C:/r/DuckyOS/plugins/plugin-meshy/** (folder made by this seat)"
    assert sb.seats_from_plan(_plan(sec))[0]["paths"] == ["C:/r/DuckyOS/plugins/plugin-meshy/**"]
    sec["body_markdown"] = "Files: `My Projects/a b/x.py`, relay.rs; see model3d.start"
    assert sb.seats_from_plan(_plan(sec))[0]["paths"] == ["My Projects/a b/x.py", "relay.rs"]


def test_build_from_plan_assigns_the_existing_sections_and_adds_no_nodes(tmp_path):
    api = Api(tmp_path)
    sb.build_swarm(api=api, from_plan=_three_level_plan())
    assert api.names("plan_add_node") == []
    assert [g[1] for g in api.names("group_create")] == ["Speed", "Team", "Team 1", "Team 2", "Team 3"]
    assigned = {c[1] for c in api.names("assign")}
    assert assigned == {"a", "h", "r"}


# ---- 195d748586 hand-overs --------------------------------------------------------------

def test_hand_over_table_is_recorded_for_the_giver_only(tmp_path):
    api = Api(tmp_path)
    out = sb.build_swarm(api=api, from_plan=_three_level_plan())
    row = {"from_section": "a", "to_section": "h", "paths": ["ducky_app/a.py"]}
    assert out["handovers"] == [row]
    assert sb.handovers_for("a", api) == [row]
    assert sb.handovers_for("h", api) == [] and sb.handovers_for("r", api) == []
    record = json.loads((tmp_path / out["outputs"] / ".." / "swarm.json").read_text(encoding="utf-8"))
    assert record["handovers"] == [row] and record["plan_id"] == "plan1"


# ---- 2c7a6836cf launch a plan ------------------------------------------------------------

def _planned(sid, title, files, planned="", assignee=""):
    sec = _section(sid, title, f"**Files:** `{files}`", [_step(sid)])
    if planned:
        sec["planned_assignee"] = planned
    if assignee:
        sec["assignee"] = assignee
    return sec


def _launch_plan():
    team = _section("t", "Team", "", [_section("t1", "Team 1", "", [
        _planned("a", "A", "a/**", planned="developer"),
        _planned("b", "B", "b/**"),  # no planned owner: nobody is seated
    ])])
    return _plan(team, _planned("c", "C", "c/**", planned="chat-x"),
                 _planned("d", "D", "d/**", planned="developer", assignee="chat-d"))


def test_launch_seats_planned_owners_in_nested_groups(tmp_path):
    api = Api(tmp_path, plan=_launch_plan())
    out = sb.launch_plan("lead", api)
    assert sorted(out["seated"]) == ["A", "C"]
    assert [g[1] for g in api.names("group_create")] == ["Speed", "Team", "Team 1"]
    invite = api.names("invite")
    assert [(c[2], c[3][0]) for c in invite] == [("A", "a/**")]  # Files lane, plus its outputs folder
    assert api.names("add_member")[0][2] == "chat-x" and api.names("set_lane")[0][2][0] == "c/**"
    assigned = {c[1]: c[2] for c in api.names("assign")}
    assert set(assigned) == {"a", "c"} and assigned["c"] == "chat-x"  # b has no owner, d was already assigned
    # The Build card's plugin-draft trust, for every group of the launched swarm.
    assert api.names("trust_drafts") == [("trust_drafts", sorted(g["id"] for g in api.groups.values()))]


def test_launch_twice_seats_nothing_new(tmp_path):
    api = Api(tmp_path, plan=_launch_plan())
    sb.launch_plan("lead", api)
    before = list(api.calls)
    out = sb.launch_plan("lead", api)
    assert out["seated"] == [] and api.calls == before


def test_a_planned_owner_alone_starts_nobody(tmp_path):
    api = Api(tmp_path, plan=_launch_plan())
    tree = sb.seats_from_plan(api.plan)
    assert api.calls == [] and tree[0]["groups"][0]["seats"][0]["planned_assignee"] == "developer"


def test_twin_group_names_anywhere_in_the_tree_fail():
    sub = {"group": "Core", "muted": True, "seats": [{"name": "y", "profile": "d", "paths": ["b/**"],
                                                     "sub_plan": {"steps": []}}], "groups": []}
    tree = [{"group": "Core", "muted": True, "seats": [{"name": "x", "profile": "d", "paths": ["a/**"],
                                                      "sub_plan": {"steps": []}}], "groups": []},
            {"group": "Engine", "muted": True, "seats": [], "groups": [sub]}]
    assert any("two groups called 'Core'" in e for e in sb.validate(tree, ""))


@pytest.mark.parametrize("line,muted", [("Muted: no", False), ("**Muted:** yes", True), ("", True)])
def test_a_group_muted_line_decides_muting(tmp_path, line, muted):
    # Fresh section, plan and fake per case: nothing is shared with other tests or cases.
    group = _section("g", "Group", line, [_seat_sec("s", "Seat", "s/**")])
    api = Api(tmp_path)
    out = sb.build_swarm(api=api, from_plan=_plan(group))
    built = out["tree"][0]
    assert built["group"] == "Group" and built["muted"] is muted
    assert api.names("set_muted") == ([("set_muted", built["group_id"], True)] if muted else [])


def test_a_hand_over_to_no_seat_fails_before_anything_is_made(tmp_path):
    api = Api(tmp_path)
    plan = _plan(_seat_sec("a", "A", "a/**", "Hands over: `a/x.py` to Nobody"))
    with pytest.raises(sb.SwarmBuildError, match="Nobody"):
        sb.build_swarm(api=api, from_plan=plan)
    assert api.calls == []
