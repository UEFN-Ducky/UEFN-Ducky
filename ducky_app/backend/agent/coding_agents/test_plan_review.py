"""Plans that can be followed: small steps with a description, or the tool says which to fix."""

from __future__ import annotations

from backend.agent.coding_agents.plans import PLAN_PROTOCOL, plan_review, with_plan_review


def _plan(*nodes: dict) -> dict:
    return {"nodes": list(nodes)}


def _full(*paths: str) -> str:
    files = ", ".join(f"`{p}`" for p in paths) or "`backend/agent/coding_agents/plan_review.py`"
    return (f"**Why:** one-line steps pass review today, so members ask the lead what to do.\n**Files:** {files}\n"
            "**Do:**\n1. Flag thin steps in `plan_review`.\n2. Keep `_REVIEW_MAX` at 8.\n"
            "**Tests:** a one-line body is flagged.\n**Done when:** code and tests committed.\n"
            "**Example:** `{\"n\": \"1.1\", \"issue\": \"Too thin…\"}`.")


def _section(sid: str, name: str, files: list[str], steps: list[dict], extra: str = "") -> dict:
    lane = ", ".join(f"`{p}`" for p in files)
    return {"id": sid, "content": name, "status": "pending", "planned_assignee": f"seat-{sid}",
            "body_markdown": f"**Files:** {lane}\n**Done when:** committed.\n**Not yours:** the rest.{extra}",
            "children": steps}


def test_a_step_that_hides_many_checks_is_flagged_and_small_described_steps_pass() -> None:
    # Oct 11 2026: one step held fifteen checks for two hours; the plan read 15/19 throughout.
    plan = _plan(
        {"id": "s", "content": "Acceptance", "status": "in_progress", "body_markdown": "",
         "children": [
             {"id": "e2e", "content": "Check all original feature paths end to end in the usable panel.",
              "status": "in_progress", "body_markdown": "Run the panel acceptance."},
             {"id": "mov", "content": "Export the test project as a 15 s MOV", "status": "pending",
              "body_markdown": _full()},
             {"id": "old", "content": "Check every screen", "status": "completed", "body_markdown": ""},
         ]},
    )
    issues = plan_review(plan)
    assert {"n": "1", "issue": "Section has no description: add one line saying what it delivers."} in issues
    assert {"n": "1.1", "issue": "Too big for one step: split it into one step per feature or check."} in issues
    assert not any(i["n"] in ("1.2", "1.3") for i in issues)  # small and described; finished steps are left alone


def test_long_titles_and_missing_descriptions_are_named() -> None:
    plan = _plan({"id": "a", "content": "x" * 140, "status": "pending", "body_markdown": ""})
    assert [i["issue"].split(":")[0] for i in plan_review(plan)] == ["Title too long", "No description"]


def test_the_tool_result_carries_the_review_only_when_needed() -> None:
    good = _plan({"id": "a", "content": "Export the MOV", "status": "pending", "body_markdown": _full()})
    assert with_plan_review({"ok": True}, good) == {"ok": True}
    bad = _plan({"id": "a", "content": "Test everything", "status": "pending", "body_markdown": ""})
    out = with_plan_review({"ok": True}, bad)
    assert len(out["plan_review"]) == 2 and "ducky_update_plan" in out["plan_review_hint"]


def test_every_prompt_gets_the_sizing_rule_without_braces() -> None:
    # PLAN_PROTOCOL is placed into prompt templates that are .format()-ted later.
    assert "15–30 minutes" in PLAN_PROTOCOL and "one step per feature or check" in PLAN_PROTOCOL
    assert "{" not in PLAN_PROTOCOL and "}" not in PLAN_PROTOCOL


def test_the_protocol_asks_for_buildable_steps_and_one_owner_per_file() -> None:
    for part in ("**Why**", "**Files**", "**Do**", "**Watch out**", "**Tests**", "**Done when**", "**Example**",
                 "**Not yours**", "**Hands over:**", "exactly one owner", "Hand-off:", "Split:", "median"):
        assert part in PLAN_PROTOCOL, part


def test_a_one_line_body_is_too_thin_and_a_full_body_passes() -> None:
    thin = plan_review(_plan({"id": "a", "content": "Fix X", "status": "pending", "body_markdown": "Do X."}))
    assert len(thin) == 1 and thin[0]["issue"].startswith("Too thin to build from (needs Files: and Done when)")
    short = plan_review(_plan({"id": "a", "content": "Fix X", "status": "pending",
                               "body_markdown": "**Files:** `a.py`\nDone when: done."}))
    assert short[0]["issue"].startswith("Too thin to build from (too short)")
    assert plan_review(_plan({"id": "a", "content": "Fix X", "status": "pending", "body_markdown": _full()})) == []


def test_a_team_sub_plan_without_files_is_flagged() -> None:
    sec = _section("a", "Plans", ["plan_review.py"], [{"id": "s", "content": "Fix X", "status": "pending",
                                                        "body_markdown": _full("plan_review.py")}])
    sec["body_markdown"] = "Does the plan review."
    assert [i["issue"].split(":")[0] for i in plan_review(_plan(sec))] == ["Sub plan has no Files"]


def test_two_sections_sharing_a_path_are_flagged_unless_one_hands_it_over() -> None:
    path = "web/src/components/ChatSidebar.tsx"
    a = _section("a", "Lists & sidebar", [path], [{"id": "s1", "content": "Fix A", "status": "pending",
                                                    "body_markdown": _full(path)}])
    b = _section("b", "G-a", [f"ducky_app/frontend/ui_web/{path}"],
                 [{"id": "s2", "content": "Fix B", "status": "pending", "body_markdown": _full(path)}])
    assert plan_review(_plan(a, b)) == [{"n": "2", "issue": f"Two owners: {path} in Lists & sidebar and G-a"}]
    a["body_markdown"] += "\n**Hands over:** `ChatSidebar.tsx` to G-a when done."
    assert plan_review(_plan(a, b)) == []


def test_a_step_naming_another_lanes_file_is_flagged() -> None:
    step = {"id": "s", "content": "Fix X", "status": "pending",
            "body_markdown": _full("backend/agent/coding_agents/plan_review.py", "backend/agent/coding_agents/plans.py")}
    sec = _section("a", "P3 review", ["ducky_app/backend/agent/coding_agents/plan_review.py", "web/src/"], [step])
    issues = plan_review(_plan(sec))
    assert [i["issue"].split(" is ")[0] for i in issues] == ["Outside its lane: backend/agent/coding_agents/plans.py"]
    step["body_markdown"] = _full("plan_review.py", "web/src/components/Plan.tsx")
    assert plan_review(_plan(sec)) == []


def test_relative_step_paths_are_read_against_a_full_path_lane(monkeypatch) -> None:
    # Nov 3 2026: steps naming src/model3d.rs under `C:/…/plugin-meshy/**` and relay.rs under
    # `…/src/ducky_ai/**` were flagged "Outside its lane".
    meshy, core = "C:/r/DuckyOS/plugins/plugin-meshy/**", "C:/r/DuckyOS/duckyos-core/src/ducky_ai/**"
    a = {"id": "a1", "content": "Start a job", "status": "pending", "body_markdown": _full("src/model3d.rs", "src/hooks.rs")}
    b = {"id": "b1", "content": "Bill a job", "status": "pending", "body_markdown": _full("relay.rs", "money.rs")}
    c = {"id": "c1", "content": "Stray", "status": "pending", "body_markdown": _full("C:/r/DuckyOS/other/x.rs")}
    issues = plan_review(_plan(_section("a", "Meshy", [meshy], [a, c]), _section("b", "Core", [core], [b])))
    assert [i["issue"].split(" is ")[0] for i in issues] == ["Outside its lane: C:/r/DuckyOS/other/x.rs"]
    from backend.tools.panel.swarm_preview import out_of_lane
    from backend.workspace import lanes

    monkeypatch.setattr(lanes, "_roots_source", lambda: ["C:/proj", "C:/r/DuckyOS"])
    seats = [{"name": "Meshy", "lane": [meshy], "steps": [
        {"content": "Start", "body": "Files: `src/model3d.rs`"},
        {"content": "Stray", "body": "Files: `C:/r/DuckyOS/other/x.rs`"}]}]
    assert [x["file"] for x in out_of_lane(seats)] == ["C:/r/DuckyOS/other/x.rs"]
