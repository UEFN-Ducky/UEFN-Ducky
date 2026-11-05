"""ducky_swarm_create: ask once, size the tree, preview, edits, mute, plan-first build, suggest."""

from __future__ import annotations

import json

import pytest

from backend.tools.panel import ducky_swarm as sw

GAME8 = ["Narrative: story", "Research: references", "Modeling: props", "Texture: props", "Verse: combat",
         "UI: HUD", "Tester: playtest", "Reviewer: code review"]
GAME12 = GAME8 + ["Level: layout", "Sound: sfx", "NPC: enemies", "VFX: hits"]


@pytest.fixture(autouse=True)
def _no_settings(monkeypatch):
    monkeypatch.setattr(sw, "_routing_context", lambda: (None, None))


# Jobs that split into parts need one path per part: parts get disjoint slices of their job's paths.
PART_PATHS = {
    "Modeling: props": ["src/modeling/blockout/**", "src/modeling/props/**", "src/modeling/set/**"],
    "Texture: props": ["src/texture/materials/**", "src/texture/decals/**"],
    "Verse: combat": ["src/verse/systems/**", "src/verse/devices/**"],
}


def _jobs(names, model="m1", paths=None):
    paths = paths or {}
    return [{"name": n, "purpose": "", "task": "", "steps": [f"{n} step 1", f"{n} step 2"],
             "paths": paths.get(n) or [f"src/{n.split(':')[0].lower()}/**"], "model": model} for n in names]


class FakeApi:
    def __init__(self):
        self.calls: list[tuple] = []
        self.n = 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}{self.n}"

    def caller(self):
        return "lead"

    def group_create(self, name, parent_folder_id=""):
        self.calls.append(("group_create", name, parent_folder_id))
        gid = self._id("g")
        return {"id": gid, "folder_id": f"f-{gid}"}

    def add_leader(self, group_id, conv_id):
        self.calls.append(("add_leader", group_id, conv_id))

    def rename_chat(self, conv_id, title):
        self.calls.append(("rename_chat", conv_id, title))

    def ensure_plan(self, chat_id, goal):
        self.calls.append(("ensure_plan", chat_id))

    def plan_add_node(self, chat_id, content, parent_id="", kind="step", body_markdown=""):
        self.calls.append(("plan_add_node", content, parent_id, kind))
        return self._id("n")

    def invite(self, group_id, profile, name, model, write_allowed):
        self.calls.append(("invite", group_id, name, model, write_allowed))
        return self._id("c")

    def set_muted(self, conv_id, muted):
        self.calls.append(("set_muted", conv_id, muted))

    def assign(self, chat_id, node_id, assignee):
        self.calls.append(("assign", node_id, assignee))


def _create(names, model="m1", paths=None, **kw):
    kw.setdefault("detail", 2)
    return sw.swarm_create("Shop island", _jobs(names, model, paths), **kw)


# ---- s4-ask -------------------------------------------------------------------------------

def test_missing_answers_asks_agents_limits_and_each_model():
    api = FakeApi()
    out = sw.swarm_create("Shop island", _jobs(["Narrative: story", "Modeling: props", "Texture: props"], ""),
                          api=api)
    assert out["status"] == "needs_answers"
    ids = [q["id"] for q in out["questions"]]
    assert ids == ["detail", "agents", "size_limits", "model:0", "model:1", "model:2"]
    limits = out["questions"][2]
    assert limits["options"][0]["id"] == "8/5" and "Art: assets" in limits["options"][0]["description"]
    assert "Modeling" in out["questions"][4]["prompt"]
    assert api.calls == []


def test_given_model_is_not_asked_and_all_answers_go_on():
    jobs = _jobs(["Narrative: story", "Modeling: props"], "")
    jobs[0]["model"] = "claude-opus-5-5"
    out = sw.swarm_create("Shop island", jobs, agents="auto", max_agents=8, per_group=5, detail=2)
    assert [q["id"] for q in out["questions"]] == ["model:1"]
    out = _create(["Narrative: story", "Modeling: props"], agents="auto", max_agents=8, per_group=5)
    assert out["status"] == "needs_confirm"


# ---- s4-split -----------------------------------------------------------------------------

def _routed(names):
    return sw.normalize_jobs(_jobs(names))


def test_three_jobs_auto_is_flat():
    tree = sw.plan_tree(_routed(["Narrative: story", "Modeling: props", "Texture: props"]), "auto", 8, 5)
    assert [n.get("name") for n in tree] == ["Narrative lead", "Modeling lead", "Texture lead"]
    assert not list(sw.walk_groups(tree))


def test_eight_jobs_nest_into_art_and_code():
    tree = sw.plan_tree(_routed(GAME8), "auto", 8, 5)
    groups = {g["group"]: [s["name"] for s in g["seats"]] for g in sw.walk_groups(tree)}
    assert groups == {"Art: assets": ["Modeling lead", "Texture lead"], "Code: gameplay": ["Verse lead", "UI lead"]}
    top = [n["name"] for n in tree if "group" not in n]
    assert top == ["Narrative lead", "Research lead", "Tester", "Reviewer"]


def test_twelve_jobs_capped_at_eight_and_no_group_over_five():
    tree = sw.plan_tree(_routed(GAME12), "auto", 8, 5)
    seats = list(sw.walk_seats(tree))
    assert len(seats) == 8
    steps = sum(s["sub_plan"]["total"] for s in seats)
    assert steps == 24  # merged, nothing dropped
    assert all(len(g["seats"]) <= 5 for g in sw.walk_groups(tree))
    assert len([n for n in tree if "group" not in n]) <= 5


def test_big_area_splits_and_depth_stays_three():
    names = [f"Modeling: part {i}" for i in range(7)]
    tree = sw.plan_tree(_routed(names), "auto", 8, 3)
    art = tree[0]
    assert art["group"] == "Art: assets" and not art["seats"]
    assert all(len(g["seats"]) <= 3 and not g["groups"] for g in art["groups"])


# ---- preview, edits, mute -----------------------------------------------------------------

def test_preview_builds_nothing_and_marks_nested_groups_muted():
    api = FakeApi()
    out = _create(GAME8, agents="auto", max_agents=8, per_group=5, api=api)
    assert out["status"] == "needs_confirm" and api.calls == []
    preview = out["preview"]
    assert preview["agents"] == 8
    assert preview["muted"] == ["Art: assets", "Code: gameplay"]
    assert preview["lanes"]["Verse lead"] == ["src/verse/**"]
    seat = next(s for s in sw.walk_seats(preview["tree"]) if s["name"] == "Verse lead")
    assert seat["profile"] == "verse-coder" and seat["model"] == "m1"
    json.dumps(out)


def test_flat_swarm_has_nothing_to_mute():
    api = FakeApi()
    out = _create(["Narrative: story", "Modeling: props", "Texture: props"], agents="auto", max_agents=8,
                  per_group=5, edits=[{"drop": "Texture: props"}], confirm=True, api=api)
    assert out["status"] == "done"
    assert [s["name"] for s in sw.walk_seats(out["tree"])] == ["Narrative lead", "Modeling lead"]
    assert not [c for c in api.calls if c[0] == "set_muted"]


def test_edits_show_in_preview_and_in_built_tree():
    edits = [{"rename": "UI: HUD", "to": "UI: shop HUD"}, {"seat": "Tester", "model": "codex:gpt-5-mini"},
             {"unmute": "Art: assets"}]
    out = _create(GAME8, agents="auto", max_agents=8, per_group=5, edits=edits)
    preview = out["preview"]
    seats = {s["name"]: s for s in sw.walk_seats(preview["tree"])}
    assert seats["UI lead"]["job"] == "UI: shop HUD"
    assert seats["Tester"]["model"] == "codex:gpt-5-mini"
    assert preview["muted"] == ["Code: gameplay"]

    api = FakeApi()
    built = _create(GAME8, agents="auto", max_agents=8, per_group=5, edits=edits, confirm=True, api=api)
    assert built["status"] == "done"
    bseats = {s["name"]: s for s in sw.walk_seats(built["tree"])}
    assert set(bseats) == set(seats)
    assert ("plan_add_node", "UI: shop HUD", "", "subplan") in api.calls
    assert any(c[0] == "invite" and c[2] == "Tester" and c[3] == "codex:gpt-5-mini" for c in api.calls)
    groups = {g["group"]: g for g in sw.walk_groups(built["tree"])}
    muted = [c[1] for c in api.calls if c[0] == "set_muted"]
    assert muted == [groups["Code: gameplay"]["group_id"]]
    assert groups["Art: assets"]["muted"] is False


def test_bad_edit_names_the_choices():
    with pytest.raises(ValueError, match="No seat called"):
        _create(GAME8, agents="auto", max_agents=8, per_group=5, edits=[{"seat": "Nobody", "model": "x"}])


# ---- s4-build -----------------------------------------------------------------------------

def test_build_order_plan_first_then_seats_then_mute_then_assign():
    api = FakeApi()
    out = _create(GAME8, agents="auto", max_agents=8, per_group=5, confirm=True, api=api)
    assert out["status"] == "done"
    kinds = [c[0] for c in api.calls]
    assert kinds[:4] == ["group_create", "add_leader", "rename_chat", "ensure_plan"]
    assert api.calls[0] == ("group_create", "Shop island", "")
    assert api.calls[2] == ("rename_chat", "lead", "Lead: Shop island")
    last_plan = max(i for i, k in enumerate(kinds) if k == "plan_add_node")
    first_invite = kinds.index("invite")
    assert last_plan < first_invite < kinds.index("set_muted") < kinds.index("assign")
    assert max(i for i, k in enumerate(kinds) if k == "invite") < kinds.index("set_muted")
    assert kinds.count("plan_add_node") == 8 * 3  # a section + 2 steps per job
    assert kinds.count("assign") == 8 and kinds.count("set_muted") == 2
    # lanes go on at invite; nested groups sit in the root group's folder
    assert ("invite", out["root_group_id"], "Tester", "m1", ["src/tester/**"]) in api.calls
    assert ("group_create", "Art: assets", f"f-{out['root_group_id']}") in api.calls
    # result shape SwarmCard reads
    art = out["tree"][0]
    assert art["group"] == "Art: assets" and art["group_id"] and art["created"] and art["muted"]
    seat = art["seats"][0]
    assert {"name", "conv_id", "profile", "model", "created", "sub_plan"} <= set(seat)
    assert seat["sub_plan"]["title"] == "Modeling: props" and seat["sub_plan"]["total"] == 2
    assigned = {c[2] for c in api.calls if c[0] == "assign"}
    assert assigned == {s["conv_id"] for s in sw.walk_seats(out["tree"])}


def test_build_failure_reports_partial_tree():
    class Broken(FakeApi):
        def invite(self, *a):
            raise ValueError("lane overlaps")

    out = _create(GAME8, agents="auto", max_agents=8, per_group=5, confirm=True, api=Broken())
    assert out["status"] == "error" and "lane overlaps" in out["error"]


# ---- detail level -------------------------------------------------------------------------

SHOP = ["Modeling: props", "Texture: props", "Verse: combat", "Narrative: story"]


def test_no_detail_asks_it_with_agent_counts_per_level():
    out = sw.swarm_create("Shop island", _jobs(SHOP, paths=PART_PATHS), agents="auto", max_agents=8, per_group=5)
    assert out["status"] == "needs_answers" and [q["id"] for q in out["questions"]] == ["detail"]
    q = out["questions"][0]
    assert q["prompt"] == "How finely should the work be split?"
    assert [o["id"] for o in q["options"]] == ["1", "2", "3", "4"]
    assert q["options"][1]["label"] == "2 Jobs (suggested)"
    assert [o["description"] for o in q["options"]] == [
        "3 agents", "4 agents", "8 agents in 1 sub-group", "8 agents in 3 sub-groups"]


def test_detail_3_previews_parts_and_every_level():
    api = FakeApi()
    out = _create(SHOP, agents="auto", max_agents=8, per_group=5, detail=3, api=api, paths=PART_PATHS)
    p = out["preview"]
    assert out["status"] == "needs_confirm" and api.calls == []
    assert p["detail"] == 3
    assert [(lv["detail"], lv["agents"], lv["groups"]) for lv in p["levels"]] == [
        (1, 3, 0), (2, 4, 0), (3, 8, 1), (4, 8, 3)]
    seats = {s["name"]: s for s in sw.walk_seats(p["tree"])}
    assert seats["Blockout lead"]["job"] == "Modeling: blockout"
    assert seats["Blockout lead"]["profile"] == "3d-modeler"
    assert seats["Blockout lead"]["paths"] == ["src/modeling/blockout/**"]
    assert seats["Set dressing lead"]["paths"] == ["src/modeling/set/**"]  # disjoint slices
    blockout = next(s for s in sw.build_preview("g", sw.normalize_jobs(_jobs(SHOP, paths=PART_PATHS)),
                                                "auto", 8, 5, None, 3)["jobs"]
                    .items() if s[0] == "Blockout lead")[1]
    assert [s["content"] for s in blockout["steps"]] == ["blockout: Modeling: props step 1",
                                                        "blockout: Modeling: props step 2"]


def test_detail_change_is_a_new_preview_and_limits_still_cap():
    api = FakeApi()
    one = _create(SHOP, agents="auto", max_agents=8, per_group=5, detail=1, api=api, paths=PART_PATHS)["preview"]
    assert [s["name"] for s in sw.walk_seats(one["tree"])] == ["Art lead", "Code lead", "Story lead"]
    art = next(s for s in sw.walk_seats(one["tree"]) if s["name"] == "Art lead")
    assert art["sub_plan"]["total"] == 4  # Modeling's and Texture's steps both kept
    four = _create(SHOP, agents="auto", max_agents=8, per_group=5, detail=4, api=api, paths=PART_PATHS)["preview"]
    assert sorted(g["group"] for g in sw.walk_groups(four["tree"])) == [
        "Modeling: parts", "Texture: parts", "Verse: parts"]
    assert four["muted"] and api.calls == []
    capped = _create(SHOP, agents="auto", max_agents=5, per_group=5, detail=3, paths=PART_PATHS)["preview"]
    assert capped["agents"] == 5


def test_confirm_builds_the_chosen_level():
    api = FakeApi()
    out = _create(SHOP, agents="auto", max_agents=8, per_group=5, detail=4, confirm=True, api=api,
                  paths=PART_PATHS)
    assert out["status"] == "done" and out["agents"] == 8
    made = [c[1] for c in api.calls if c[0] == "group_create"]
    assert made == ["Shop island", "Modeling: parts", "Texture: parts", "Verse: parts"]
    assert [c[1] for c in api.calls if c[0] == "plan_add_node" and c[3] == "subplan"][:3] == [
        "Modeling: blockout", "Modeling: hero props", "Modeling: set dressing"]
    assert len([c for c in api.calls if c[0] == "set_muted"]) == 3


# ---- the plan in the preview --------------------------------------------------------------

def test_preview_lists_each_seats_steps_and_the_plan():
    out = _create(["Verse: combat", "Tester: playtest"], agents="auto", max_agents=8, per_group=5)
    p = out["preview"]
    verse = next(s for s in sw.walk_seats(p["tree"]) if s["name"] == "Verse lead")
    assert [s["content"] for s in verse["sub_plan"]["steps"]] == ["Verse: combat step 1", "Verse: combat step 2"]
    assert all("Done when" in s["body_markdown"] for s in verse["sub_plan"]["steps"])
    assert p["plan"]["title"] == "Shop island"
    first = p["plan"]["sections"][0]
    assert (first["seat"], first["title"]) == ("Verse lead", "Verse: combat")
    assert [s["content"] for s in first["steps"]] == ["Verse: combat step 1", "Verse: combat step 2"]


def test_step_edits_show_in_preview_and_build_makes_exactly_them():
    edits = [{"seat": "Verse lead", "add_step": "Tests"},
             {"seat": "Verse lead", "rename_step": "Verse: combat step 1", "to": "Damage system"},
             {"seat": "Verse lead", "drop_step": "Verse: combat step 2"}]
    p = _create(["Verse: combat", "Tester: playtest"], agents="auto", max_agents=8, per_group=5, edits=edits)["preview"]
    section = p["plan"]["sections"][0]
    assert [s["content"] for s in section["steps"]] == ["Damage system", "Tests"]
    assert next(s for s in sw.walk_seats(p["tree"]) if s["name"] == "Verse lead")["sub_plan"]["total"] == 2

    api = FakeApi()
    _create(["Verse: combat", "Tester: playtest"], agents="auto", max_agents=8, per_group=5, edits=edits,
            confirm=True, api=api)
    adds = [c for c in api.calls if c[0] == "plan_add_node"]
    assert [c[1] for c in adds[:3]] == ["Verse: combat", "Damage system", "Tests"]
    assert adds[1][2] == adds[2][2] == "n2" and adds[3][1] == "Tester: playtest"  # n2 = the Verse section


def test_bad_step_edits_name_the_steps():
    with pytest.raises(ValueError, match="has no step"):
        _create(["Verse: combat"], agents="auto", max_agents=8, per_group=5,
                edits=[{"seat": "Verse lead", "drop_step": "nope"}])
    with pytest.raises(ValueError, match="at least one step"):
        _create(["Verse: combat"], agents="auto", max_agents=8, per_group=5,
                edits=[{"seat": "Verse lead", "drop_step": "Verse: combat step 1"},
                       {"seat": "Verse lead", "drop_step": "Verse: combat step 2"}])


# ---- suggest_swarm ------------------------------------------------------------------------

def _section(name, files, n=2):
    return {"id": name, "content": name, "body_markdown": f"Files: {files}",
            "children": [{"id": f"{name}-{i}", "content": f"{name} step {i}"} for i in range(n)]}


def test_five_sections_get_a_suggestion():
    plan = {"title": "Shop island", "nodes": [
        _section("Verse: combat", "Content/Verse/Combat/**"), _section("UI: shop HUD", "Content/UI/**"),
        _section("Modeling: props", "Content/Meshes/**"), _section("Texture: props", "Content/Textures/**"),
        _section("Tester: playtest", "tests/**")]}
    out = sw.suggest_swarm(plan)
    assert out["agents"] == 5 and out["jobs"][0] == "Verse: combat"
    assert out["routes"][0]["profile"] == "verse-coder"
    assert out["call"].startswith("ducky_swarm_create(") and "Run this as a swarm of 5?" == out["ask"]


def test_small_or_overlapping_plans_get_nothing():
    assert sw.suggest_swarm({"nodes": [{"id": str(i), "content": f"s{i}"} for i in range(4)]}) is None
    overlap = {"nodes": [_section("A", "src/**"), _section("B", "src/**"), _section("C", "docs/**")]}
    assert sw.suggest_swarm(overlap) is None


# ---- the card payload fixture -------------------------------------------------------------

def test_build_preview_has_every_fixture_field():
    from pathlib import Path

    fixture = Path(__file__).parents[3] / "frontend/ui_web/web/src/swarm/fixtures/swarm_builder.json"
    want = json.loads(fixture.read_text(encoding="utf-8"))["preview"]
    got = sw.build_preview("Shop island", sw.normalize_jobs(_jobs(GAME8)), "auto", 8, 5, None, 2)
    assert set(want) <= set(got)
    groups, seats = list(sw.walk_groups(got["tree"])), list(sw.walk_seats(got["tree"]))
    assert groups and seats
    want_group = next(sw.walk_groups(want["tree"]))
    want_seat = next(sw.walk_seats(want["tree"]))
    assert all(set(want_group) <= set(g) for g in groups)
    assert all(set(want_seat) <= set(s) and set(want_seat["sub_plan"]) <= set(s["sub_plan"]) for s in seats)
    assert all(set(want["levels"][0]) <= set(lv) for lv in got["levels"])
    assert set(want["plan"]["sections"][0]) <= set(got["plan"]["sections"][0])


def test_both_statuses_carry_the_swarm_builder_card():
    fixture = json.loads((__import__("pathlib").Path(__file__).parents[3]
                          / "frontend/ui_web/web/src/swarm/fixtures/swarm_builder.json").read_text(encoding="utf-8"))
    api = FakeApi()
    asking = sw.swarm_create("Shop island", _jobs(GAME8, ""), api=api)
    confirming = _create(GAME8, agents="auto", max_agents=8, per_group=5)
    assert api.calls == []  # unanswered: nothing built
    for out, status in ((asking, "needs_answers"), (confirming, "needs_confirm")):
        assert out["status"] == status and out["ui"] == "swarm_builder" and out["next"]
        assert {"tree", "levels", "lanes", "plan"} <= set(out["preview"]) and "jobs" not in out["preview"]
        assert out["preview"]["agents"] == 8 and len(out["seats"]) == 8
        assert all(set(fixture["seats"][0]) <= set(s) for s in out["seats"])
    assert asking["questions"] and asking["preview"]["detail"] == 2
    # Unanswered models show the suggested one per seat.
    suggested = {j["name"]: j["suggested_model"] for j in sw.normalize_jobs(_jobs(GAME8, ""))}
    assert all(s["model"] == suggested[s["job"]] for s in sw.walk_seats(asking["preview"]["tree"]))
    assert {s["group"] for s in confirming["seats"]} >= {"Art: assets", "Code: gameplay"}


# ---- card edit keys (U3's editors) --------------------------------------------------------

def _edited(*edits):
    return sw.build_preview("Shop island", sw.normalize_jobs(_jobs(GAME8)), "auto", 8, 5, list(edits), 2)


def _seat(p, name):
    return next(s for s in sw.walk_seats(p["tree"]) if s["name"] == name)


def _group(p, name):
    return next(g for g in sw.walk_groups(p["tree"]) if g["group"] == name)


def test_paths_edit_sets_the_seat_lane():
    p = _edited({"seat": "Verse lead", "paths": ["Content/Verse/Combat/**", " "]})
    assert _seat(p, "Verse lead")["paths"] == ["Content/Verse/Combat/**"]
    assert p["lanes"]["Verse lead"] == ["Content/Verse/Combat/**"]
    assert p["jobs"]["Verse lead"]["paths"] == ["Content/Verse/Combat/**"]


def test_permission_keys_land_on_seat_and_group():
    p = _edited({"seat": "Verse lead", "may_commit": True}, {"seat": "Verse lead", "may_release": False},
                {"seat": "UI lead", "allow_terminal": True}, {"group": "Art: assets", "trust_drafts": True})
    assert _seat(p, "Verse lead")["permissions"] == {"may_commit": True, "may_release": False}
    assert _seat(p, "UI lead")["permissions"] == {"allow_terminal": True}
    assert _group(p, "Art: assets")["permissions"] == {"trust_drafts": True}
    flat = {s["name"]: s for s in sw.card("needs_confirm", p)["seats"]}
    assert "may_commit" in flat["Verse lead"]["flags"] and "may_release" not in flat["Verse lead"]["flags"]


def test_run_at_once_sets_the_concurrency_cap():
    assert _edited({"run_at_once": 3})["run_at_once"] == 3
    assert _edited({"run_at_once": 0})["run_at_once"] == 1
    assert "run_at_once" not in _edited()


def test_move_step_moves_it_under_the_target_seat():
    p = _edited({"seat": "Verse lead", "move_step": "Verse: combat step 1", "to_seat": "UI lead"})
    verse, ui = _seat(p, "Verse lead"), _seat(p, "UI lead")
    assert [s["content"] for s in verse["sub_plan"]["steps"]] == ["Verse: combat step 2"]
    assert ui["sub_plan"]["steps"][-1]["content"] == "Verse: combat step 1" and ui["sub_plan"]["total"] == 3
    section = next(s for s in p["plan"]["sections"] if s["seat"] == "UI lead")
    assert section["steps"][-1]["content"] == "Verse: combat step 1"
    with pytest.raises(ValueError):
        _edited({"seat": "Verse lead", "move_step": "nope", "to_seat": "UI lead"})


def test_move_step_reaches_the_built_plan():
    api = FakeApi()
    out = _create(GAME8, agents="auto", max_agents=8, per_group=5, confirm=True, api=api,
                  edits=[{"seat": "Verse lead", "move_step": "Verse: combat step 1", "to_seat": "UI lead"}])
    assert out["status"] == "done"
    adds = [c for c in api.calls if c[0] == "plan_add_node"]
    ui_section = next(i for i, c in enumerate(adds) if c[3] == "subplan" and c[1].startswith("UI"))
    following = []
    for c in adds[ui_section + 1:]:
        if c[3] == "subplan":
            break
        following.append(c[1])
    assert "Verse: combat step 1" in following


def test_start_after_makes_the_group_wait():
    p = _edited({"group": "Code: gameplay", "start_after": "p0"})
    g = _group(p, "Code: gameplay")
    assert g["start_after"] == "p0" and g["after"] == ["p0"]
    with pytest.raises(ValueError):
        _edited({"group": "No such", "start_after": "p0"})


def _plan_sec(sid, name, files, n=2):
    return {"id": sid, "content": name, "body_markdown": f"**Files:** `{files}`\nDucky: developer\nModel: m1",
            "children": [{"id": f"{sid}-{i}", "content": f"{name} step {i}", "body_markdown": ""} for i in range(n)]}


INVENTORY_PLAN = {"id": "p1", "title": "Inventory UI", "nodes": [
    {"id": "g", "content": "Backend", "children": [_plan_sec("api", "API", "backend/inv/**"),
                                                 _plan_sec("store", "Store", "backend/store/**", 1)]},
    _plan_sec("ui", "Widget", "web/src/inv/**", 3),
]}


def test_from_plan_preview_matches_the_plan_outline():
    from backend.tools.panel.test_swarm_build import Api

    api = Api("C:/proj", plan=json.loads(json.dumps(INVENTORY_PLAN)))
    out = sw.swarm_create("", None, from_plan=True, api=api)
    assert out["status"] == "needs_confirm" and out["ui"] == "swarm_builder" and out["goal"] == "Inventory UI"
    p = out["preview"]
    assert p["from_plan"] and p["agents"] == 3
    assert [g["group"] for g in sw.walk_groups(p["tree"])] == ["Backend"]
    assert [(s["seat"], len(s["steps"])) for s in p["plan"]["sections"]] == [("API", 2), ("Store", 1), ("Widget", 3)]
    assert p["lanes"]["Widget"] == ["web/src/inv/**"]
    assert {s["name"]: s["group"] for s in out["seats"]} == {"API": "Backend", "Store": "Backend", "Widget": ""}
    assert api.calls == []  # a preview builds nothing


def test_from_plan_confirm_builds_through_swarm_build(tmp_path):
    from backend.tools.panel.test_swarm_build import Api

    api = Api(tmp_path, plan=json.loads(json.dumps(INVENTORY_PLAN)))
    out = sw.swarm_create("", None, from_plan=True, confirm=True, api=api)
    assert out["status"] == "done" and out["agents"] == 3
    assert sorted(c[1] for c in api.names("assign")) == ["api", "store", "ui"]
    assert not api.names("plan_add_node")  # the plan is used as it is


class FakeStore:
    """Applies plan edits to the Api's in-memory plan, like the plan tools do to the real one."""

    def __init__(self, plan):
        self.plan, self.calls = plan, []

    def _find(self, nodes, node_id, parent=None):
        for n in nodes:
            if n.get("id") == node_id:
                return n, (parent or {"children": self.plan["nodes"]})
            hit = self._find(n.get("children") or [], node_id, n)
            if hit:
                return hit
        return None

    def update(self, chat_id, node_id, **fields):
        self.calls.append(("update", node_id, fields))
        self._find(self.plan["nodes"], node_id)[0].update(fields)

    def delete(self, chat_id, node_id):
        self.calls.append(("delete", node_id))
        node, parent = self._find(self.plan["nodes"], node_id)
        parent["children"].remove(node)

    def move(self, chat_id, node_id, parent_id, index):
        self.calls.append(("move", node_id, parent_id))
        node, parent = self._find(self.plan["nodes"], node_id)
        parent["children"].remove(node)
        dest = self._find(self.plan["nodes"], parent_id)[0]["children"] if parent_id else self.plan["nodes"]
        dest.append(node)


def _plan_api(root="C:/proj"):
    from backend.tools.panel.test_swarm_build import Api

    api = Api(root, plan=json.loads(json.dumps(INVENTORY_PLAN)))
    return api, FakeStore(api.plan)


def test_from_plan_rename_edit_updates_the_real_step():
    api, store = _plan_api()
    out = sw.swarm_create("", None, from_plan=True, api=api, store=store,
                          edits=[{"seat": "API", "rename_step": "API step 0", "to": "Define the API"}])
    assert store.calls == [("update", "api-0", {"content": "Define the API"})]
    assert api.plan["nodes"][0]["children"][0]["children"][0]["content"] == "Define the API"
    section = next(s for s in out["preview"]["plan"]["sections"] if s["seat"] == "API")
    assert section["id"] == "api" and section["steps"][0]["content"] == "Define the API"
    assert out["preview"]["plan_id"] == "p1" and api.calls == []  # nothing seated


def test_from_plan_model_move_drop_and_mute_edits_change_the_plan(tmp_path):
    api, store = _plan_api(tmp_path)
    out = sw.swarm_create("", None, from_plan=True, api=api, store=store, edits=[
        {"seat": "Widget", "model": "codex:gpt-5"}, {"seat": "Widget", "to_group": "Backend"},
        {"seat": "Widget", "drop_step": "Widget step 2"}, {"mute": "Backend"}])
    widget = next(s for s in sw.walk_seats(out["preview"]["tree"]) if s["name"] == "Widget")
    assert widget["model"] == "codex:gpt-5" and widget["sub_plan"]["total"] == 2
    assert [s["name"] for s in sw.walk_groups(out["preview"]["tree"]).__next__()["seats"]] == ["API", "Store", "Widget"]
    assert "Muted: yes" in api.plan["nodes"][0]["body_markdown"]
    # confirm builds what the plan says and doesn't apply the edits twice
    store.calls.clear()
    done = sw.swarm_create("", None, from_plan=True, api=api, store=store, confirm=True,
                           edits=[{"seat": "Widget", "model": "codex:gpt-5"}])
    assert store.calls == [] and done["status"] == "done"


def test_from_plan_seat_edits_set_profile_and_lane_and_unknown_edits_are_refused():
    # Nov 3 2026: {"seat", "profile", "paths"} came back "Not a plan edit, ignored" for every row.
    api, store = _plan_api()
    meshy = "C:/Users/me/GitHub/DuckyOS/plugins/plugin-meshy/**"
    out = sw.swarm_create("", None, from_plan=True, api=api, store=store, edits=[
        {"seat": "Widget", "profile": "reviewer", "paths": [meshy.replace("/", "\\")]},
        {"seat": "api", "profile": "tester"}])  # by section id
    seats = {s["name"]: s for s in sw.walk_seats(out["preview"]["tree"])}
    assert out["status"] == "needs_confirm" and "warnings" not in out["preview"]
    # A from-plan seat works in its own worktree: unrestricted lane, its Files as files_hint.
    assert seats["Widget"]["profile"] == "reviewer" and seats["Widget"]["paths"] == []
    assert seats["Widget"]["files_hint"] == [meshy]
    assert seats["API"]["profile"] == "tester"
    assert f"Files: `{meshy}`" in api.plan["nodes"][1]["body_markdown"]
    # Sent straight with confirm=true they still land before the build.
    api2, store2 = _plan_api()
    sw.swarm_create("", None, from_plan=True, confirm=True, api=api2, store=store2,
                    edits=[{"seat": "Widget", "profile": "reviewer"}])
    assert "Ducky: reviewer" in api2.plan["nodes"][1]["body_markdown"]
    bad = sw.swarm_create("", None, from_plan=True, api=api, store=store,
                          edits=[{"seat": "Widget", "profile": "developer"}, {"wiggle": 1}])
    assert bad["status"] == "error" and "{seat, profile?, paths?, model?}" in bad["error"]
    assert "Ducky: reviewer" in api.plan["nodes"][1]["body_markdown"]  # nothing applied


def test_from_plan_routes_by_files_never_title_words_and_shows_the_leads_model():
    # Nov 3 2026: code sections titled "... 3D endpoint" / "... tests" got 3d-modeler / tester,
    # and the card showed model "" for seats that would run on the lead's model.
    from backend.tools.panel.test_swarm_build import Api

    class LeadApi(Api):
        def seat_model(self, lead, profile):
            return "claude_code:claude-opus-5-5"

    def sec(sid, title, body):
        return {"id": sid, "content": title, "body_markdown": body,
                "children": [{"id": f"{sid}-0", "content": "do it", "body_markdown": ""}]}

    plan = {"id": "p", "title": "Meshy", "nodes": [
        sec("a", "Ducky AI core: 3D endpoint and tests", "Files: `C:/r/DuckyOS/duckyos-core/src/ducky_ai/**`"),
        sec("b", "Meshy gateway plugin (3D models)", "Files: C:/r/DuckyOS/plugins/plugin-meshy/** (new folder)"),
        sec("c", "Verse hooks", "Files: `Content/Verse/Shop/shop.verse`"),
        {**sec("d", "Site 3D card", "Files: `site/card.tsx`"), "planned_assignee": "ui-engineer"},
    ]}
    out = sw.swarm_create("", None, from_plan=True, api=LeadApi("C:/proj", plan=plan))
    seats = {s["name"]: s for s in out["seats"]}
    assert {n: s["profile"] for n, s in seats.items()} == {
        "Ducky AI core: 3D endpoint and tests": "developer", "Meshy gateway plugin (3D models)": "developer",
        "Verse hooks": "verse-coder", "Site 3D card": "ui-engineer"}
    assert {s["model"] for s in seats.values()} == {"claude_code:claude-opus-5-5"}
    assert seats["Meshy gateway plugin (3D models)"]["lane"] == ["C:/r/DuckyOS/plugins/plugin-meshy/**"]


def _swap_case(monkeypatch, assignee, new_id):
    from backend.tools.panel import ducky_finish_team

    retired = []
    monkeypatch.setattr(ducky_finish_team, "retire_swapped_seat", lambda old, new: retired.append((old, new)) or True)
    api, store = _plan_api()
    if assignee:
        api.plan["nodes"][1]["assignee"] = assignee  # Widget is seated
    swaps = []
    store.swap_seat = lambda chat_id, seat, old, model: swaps.append((seat["name"], old, model)) or new_id
    sw.swarm_create("", None, from_plan=True, api=api, store=store, edits=[{"seat": "Widget", "model": "codex:gpt-5"}])
    assert "Model: codex:gpt-5" in api.plan["nodes"][1]["body_markdown"]
    return retired, swaps


def test_model_swap_on_a_seated_seat_retires_the_old_chat(monkeypatch):
    retired, swaps = _swap_case(monkeypatch, "chat-old", "chat-new")
    assert swaps == [("Widget", "chat-old", "codex:gpt-5")] and retired == [("chat-old", "chat-new")]


def test_model_swap_keeping_the_same_chat_retires_nothing(monkeypatch):
    retired, swaps = _swap_case(monkeypatch, "chat-old", "chat-old")
    assert len(swaps) == 1 and retired == []


def test_model_edit_on_an_unseated_seat_only_rewrites_the_line(monkeypatch):
    retired, swaps = _swap_case(monkeypatch, "", "chat-new")
    assert swaps == [] and retired == []


def test_jobs_swarm_edits_touch_no_plan():
    store = FakeStore({"nodes": []})
    out = _create(GAME8, agents="auto", max_agents=8, per_group=5, store=store,
                  edits=[{"seat": "Verse lead", "rename_step": "Verse: combat step 1", "to": "Combat core"}])
    assert out["status"] == "needs_confirm" and store.calls == []
    verse = next(s for s in sw.walk_seats(out["preview"]["tree"]) if s["name"] == "Verse lead")
    assert verse["sub_plan"]["steps"][0]["content"] == "Combat core"


def test_from_plan_without_a_plan_is_an_error():
    from backend.tools.panel.test_swarm_build import Api

    assert sw.swarm_create("", None, from_plan=True, api=Api("C:/proj", plan=None))["status"] == "error"


def test_unknown_key_warns_and_changes_nothing():
    plain, odd = _edited(), _edited({"wiggle": 1}, {"seat": "Verse lead", "colour": "red"})
    assert any("wiggle" in w for w in odd["warnings"]) and any("colour" in w for w in odd["warnings"])
    assert {k: v for k, v in odd.items() if k != "warnings"} == plain
