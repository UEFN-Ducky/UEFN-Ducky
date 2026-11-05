"""The keeper wakes an idle team's coordinator, backs off when a wake starts no work."""

from __future__ import annotations

import pytest

from frontend.ui_web import team_keeper as keeper

MIN = 60.0
_REAL_ENQUEUE, _REAL_WAITING = keeper._enqueue, keeper._waiting
_REAL_READERS = (keeper._blocked_reason, keeper._reply_mark, keeper._said_committed)
_REAL_ORPHANED = keeper._orphaned
PLAN = {"chat_id": "coord", "status": "open", "progress": {"pending": 3, "in_progress": 1, "completed": 2}}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    keeper.reset_for_tests()
    monkeypatch.setattr(keeper, "_tell_person", lambda cid, minutes: None)
    monkeypatch.setattr(keeper, "_long_job_chats", lambda: set())  # no real terminals or workflow runs
    # Member wakes start at once unless a test uses the real seat queue.
    monkeypatch.setattr(keeper, "_enqueue", lambda member, start: (start(), "started")[1])
    monkeypatch.setattr(keeper, "_waiting", lambda member: False)
    saved: dict[str, str] = {}  # stands in for the app database across "restarts"
    monkeypatch.setattr(keeper, "_load_parked", lambda cid: saved.get(cid))
    monkeypatch.setattr(keeper, "_save_parked",
                        lambda cid, moves: saved.pop(cid, None) if moves is None else saved.__setitem__(cid, moves))
    # Task texts never read the real project memory (it adds a "Before you build" line).
    from backend.memory import project as memory

    monkeypatch.setattr(memory, "list_entries", lambda project_root="": [])
    monkeypatch.setattr(memory, "suggest_entries", lambda text, project_root="", *, limit=3: [])
    yield saved
    keeper.reset_for_tests()


def test_status_says_what_the_keeper_does_for_a_plan(monkeypatch):
    """The Plan bar shows it: off for a plan it doesn't keep (paused, no team), watching,
    waiting while work runs, parked after its wakes."""
    assert keeper.status("coord") == {"state": "off"}
    now = 10_000.0
    monkeypatch.setattr(keeper.time, "time", lambda: now)
    keeper._teams["coord"] = {"quiet_since": now - 10 * MIN, "wakes": 0.0, "next_wake": 0.0}
    assert keeper.status("coord") == {"state": "watching", "next_wake": now - 8 * MIN}
    keeper._teams["coord"]["quiet_since"] = now - 5  # this tick saw the team or a workflow busy
    assert keeper.status("coord")["state"] == "waiting"
    keeper._teams["coord"]["wakes"] = float(keeper._PARK_AFTER)
    assert keeper.status("coord") == {"state": "parked"}


@pytest.fixture(autouse=True)
def _world(monkeypatch):
    """Members are the owners themselves, nobody is blocked, the broker allows work."""
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(keeper, "_member_chat", lambda owner, root: owner)
    monkeypatch.setattr(keeper, "_orphaned", lambda owner, root: False)  # every owner chat exists
    monkeypatch.setattr(keeper, "_blocked_reason", lambda member: "")
    monkeypatch.setattr(keeper, "_reply_mark", lambda member: 0)
    monkeypatch.setattr(keeper, "_said_committed", lambda member: False)
    monkeypatch.setattr(keeper, "_tell_lead", lambda plan, member, work, reason: told.append((member, reason)))
    monkeypatch.setattr("frontend.ui_web.context_limit.over_limit", lambda cid, step=None, project_root=None: None)
    monkeypatch.setattr("frontend.ui_web.context_limit.requested_handoff", lambda cid: None)
    return told


@pytest.fixture(autouse=True)
def _links(monkeypatch):
    """Who each started member runs under, and the person's "team waits on you" alerts."""
    seen: dict[str, list] = {"links": [], "waiting": []}
    monkeypatch.setattr(keeper, "_inherit_lead", lambda member, lead: seen["links"].append((member, lead)))
    monkeypatch.setattr(keeper, "_alert_waiting", lambda lead: seen["waiting"].append(lead))
    return seen


SUB = {**PLAN, "id": "p1", "title": "Ship it", "nodes": [
    {"id": "sec", "content": "Effects", "assignee": "builder", "status": "in_progress",
     "body_markdown": "Files: fx.py", "children": [
         {"id": "fx", "content": "Glow", "status": "in_progress", "body_markdown": "Done when: glow test passes"},
         {"id": "blur", "content": "Blur", "status": "pending"}]},
    {"id": "later", "content": "Ship", "assignee": "builder2", "status": "pending", "after": ["sec"]},
]}


def _run(now: float, running: list[str], woke: list[tuple[str, str]], plans=None) -> list[str]:
    return keeper.tick(
        now=now,
        plans=[PLAN] if plans is None else plans,
        running=running,
        wake=lambda cid, text: woke.append((cid, text)),
    )


def test_a_quiet_team_with_open_work_wakes_its_coordinator() -> None:
    woke: list[tuple[str, str]] = []
    assert _run(0, ["builder"], woke) == []  # working
    assert _run(1 * MIN, [], woke) == []  # quiet for 1 minute: not yet
    assert _run(2 * MIN, [], woke) == ["coord"]
    assert "4 open steps" in woke[0][1] and "2 minutes" in woke[0][1]


def test_wakes_that_start_no_work_back_off_then_park_and_real_work_resets_it() -> None:
    woke: list[tuple[str, str]] = []
    times = []
    for minute in range(0, 240):
        if _run(minute * MIN, [], woke):
            times.append(minute)
    # Waits 2, then 4 minutes; three wakes that moved nothing park it (no hourly wakes
    # forever for a plan whose last open step is the user's).
    assert times == [2, 4, 8]
    # The coordinator answering on its own is not progress: it stays parked.
    _run(240 * MIN, ["coord"], woke)
    assert _run(300 * MIN, [], woke) == []
    # A member working is progress: the next wake is 2 quiet minutes away again.
    _run(301 * MIN, ["builder"], woke)
    assert _run(302 * MIN, [], woke) == []
    assert _run(303 * MIN, [], woke) == ["coord"]


def test_a_step_that_moves_unparks_the_keeper() -> None:
    woke: list[tuple[str, str]] = []
    plan = {**PLAN, "nodes": [{"id": "s1", "content": "Build", "status": "in_progress"}, {"id": "s2", "content": "Ship", "status": "pending"}]}
    times = [m for m in range(0, 120) if _run(m * MIN, [], woke, plans=[plan])]
    assert times == [2, 4, 8]
    # The user ticks a step (or hands it to someone): the team has news, so it wakes again.
    moved = {**plan, "nodes": [{"id": "s1", "content": "Build", "status": "completed"}, {"id": "s2", "content": "Ship", "status": "pending"}]}
    assert _run(120 * MIN, [], woke, plans=[moved]) == ["coord"]
    # A note alone is not a move.
    noted = {**moved, "nodes": [{"id": "s1", "content": "Build", "status": "completed", "body_markdown": "x"}, {"id": "s2", "content": "Ship", "status": "pending"}]}
    later = [m for m in range(121, 240) if _run(m * MIN, [], woke, plans=[noted])]
    assert later == [122, 126]  # 2 and 4 minutes on, then parked again


def test_any_running_agent_keeps_the_keeper_quiet() -> None:
    woke: list[tuple[str, str]] = []
    for minute in range(0, 19):  # 20 busy minutes with no finished step is a circle (below)
        _run(minute * MIN, ["someone"], woke)
    assert woke == []


def test_only_started_open_team_plans_are_kept(monkeypatch) -> None:
    rows = [
        {**PLAN, "chat_id": "coord"},
        {**PLAN, "chat_id": "paused", "status": "paused"},
        {**PLAN, "chat_id": "finished", "status": "finished"},
        {**PLAN, "chat_id": "unstarted", "progress": {"pending": 5}},
        {**PLAN, "chat_id": "done", "progress": {"completed": 5}},
        {**PLAN, "chat_id": "solo"},  # not the leader of any group
    ]
    import backend.agent.coding_agents.plans as plans

    monkeypatch.setattr(plans, "list_plans", lambda project_root=None: rows)
    monkeypatch.setattr(keeper, "_project_roots", lambda root: ["C:/island"])
    # "unstarted" leads a group with no live member: no team yet.
    monkeypatch.setattr(keeper, "_team_leaders", lambda root: {"coord", "paused", "finished", "done"})
    assert [p["chat_id"] for p in keeper.kept_plans()] == ["coord"]


def test_archived_and_dormant_team_plans_are_left_alone(monkeypatch) -> None:
    # Oct 12 2026: scanning recent projects woke six chats of three earlier teams at once.
    import time

    import backend.agent.coding_agents.plans as plans

    now = time.time()
    rows = [
        {**PLAN, "chat_id": "live", "updated_at": now - 600},
        {**PLAN, "chat_id": "archived", "status": "archived", "updated_at": now - 600},
        {**PLAN, "chat_id": "old", "updated_at": now - 3 * 3600},
    ]
    monkeypatch.setattr(plans, "list_plans", lambda project_root=None: rows)
    monkeypatch.setattr(keeper, "_project_roots", lambda root: ["C:/island"])
    monkeypatch.setattr(keeper, "_team_leaders", lambda root: {"live", "archived", "old"})
    assert [p["chat_id"] for p in keeper.kept_plans()] == ["live"]


def test_a_failed_wake_is_retried_next_tick() -> None:
    calls: list[str] = []

    def _boom(cid: str, text: str) -> None:
        calls.append(cid)
        raise RuntimeError("panel busy")

    keeper.tick(now=0, plans=[PLAN], running=[], wake=_boom)
    assert keeper.tick(now=2 * MIN, plans=[PLAN], running=[], wake=_boom) == []
    assert keeper.tick(now=11 * MIN, plans=[PLAN], running=[], wake=lambda c, t: None) == ["coord"]
    assert calls == ["coord"]


@pytest.mark.parametrize("stopped", [False, True])
def test_keeper_respects_broker_cooldown_and_explicit_stop(monkeypatch, stopped):
    from backend.agent import a2a_broker as broker
    monkeypatch.setattr(broker, "_stopped", set(), raising=False)
    monkeypatch.setattr(broker, "_cooldowns", {}, raising=False)
    monkeypatch.setattr(broker, "_held", {}, raising=False)
    monkeypatch.setattr(broker, "_uncertain_chats", set(), raising=False)
    monkeypatch.setattr(broker, "_account_key", lambda cid: "test-account", raising=False)
    monkeypatch.setattr(broker, "_retry_queue_later", lambda cid: None)
    monkeypatch.setattr(broker, "send_notice", lambda **kw: None)
    if stopped:
        broker.on_agent_cancelled_by_user("coord")
    else:
        broker.on_agent_stopped("coord", "error", detail='{"error":{"type":"rate_limit_error"}}')
    woke = []
    assert _run(0, [], woke) == []
    assert _run(2 * MIN, [], woke) == []
    assert _run(20 * MIN, [], woke) == []
    assert woke == []


def test_two_minute_wake_names_work_members_and_reports(monkeypatch):
    from types import SimpleNamespace
    from frontend.ui_web.chats import conversations as chat_convs
    from backend.agent.coding_agents import plans
    monkeypatch.setattr(chat_convs, "list_all_conversation_metadata", lambda _: [SimpleNamespace(id="builder", title="Builder", is_group=False)])
    monkeypatch.setattr(plans, "_chat_and_group_ids", lambda cid, root: [cid, "group-a"])
    plan = {**PLAN, "nodes": [{"id": "section", "content": "Modes", "assignee": "group-a", "children": [
        {"id": "next", "content": "Next adapter", "status": "pending"}]}],
        "team_reports": [{"body": "Builder completed previous adapter"}]}
    monkeypatch.setattr(keeper, "_member_chat", lambda owner, root: "")  # no chat can take the work
    woke = []
    assert _run(0, ["unrelated"], woke, [plan]) == []
    assert _run(119, ["unrelated"], woke, [plan]) == []
    assert _run(120, ["unrelated"], woke, [plan]) == ["coord"]
    assert all(t in woke[0][1] for t in ["Next adapter (next)", "Builder (builder)", "completed previous adapter"])
    assert _run(240, ["builder"], woke, [plan]) == []
    assert _run(359, [], woke, [plan]) == []
    assert _run(360, [], woke, [plan]) == ["coord"]


def test_a_parked_team_stays_parked_after_the_app_restarts(_fresh) -> None:
    # Each app start used to allow three more wakes of a plan waiting on the user.
    woke: list[tuple[str, str]] = []
    assert [m for m in range(0, 60) if _run(m * MIN, [], woke)] == [2, 4, 8]
    assert "coord" in _fresh
    keeper.reset_for_tests()  # the app restarts; the database keeps the parking
    assert [m for m in range(60, 200) if _run(m * MIN, [], woke)] == []
    # A member working after the restart unparks it, in memory and in the database.
    _run(200 * MIN, ["builder"], woke)
    assert "coord" not in _fresh
    assert _run(202 * MIN, [], woke) == ["coord"]


def test_a_busy_team_that_finishes_nothing_gets_one_circle_notice_per_20_minutes(monkeypatch) -> None:
    # Oct 11 2026: six agents worked for an hour, sent 292 messages and finished no step.
    told: list[int] = []
    monkeypatch.setattr(keeper, "_tell_person", lambda cid, minutes: told.append(minutes))
    woke: list[tuple[str, str]] = []
    times = [m for m in range(0, 50) if _run(m * MIN, ["builder"], woke)]
    assert times == [20, 40]
    assert "20 minutes and no plan step has finished (2 of 6 done)" in woke[0][1]
    assert "ask the person with ducky_ask_user" in woke[0][1] and "Don't message members" in woke[0][1]
    assert told == [20, 40]


def test_a_finished_step_restarts_the_circle_clock() -> None:
    woke: list[tuple[str, str]] = []
    for minute in range(0, 15):
        _run(minute * MIN, ["builder"], woke)
    progressed = {**PLAN, "progress": {"pending": 3, "in_progress": 0, "completed": 3}}
    later = [m for m in range(15, 50) if _run(m * MIN, ["builder"], woke, plans=[progressed])]
    assert later == [35]  # 20 minutes after the step finished at minute 15


def test_each_member_gets_its_sub_plan_as_one_task_and_the_lead_waits(monkeypatch) -> None:
    # Oct 12 2026 owner: leads dispatched, resumed and chased members by message; members
    # reported back and waited. Like sub-agents, each member now gets its sub plan as one task.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[SUB])
    assert [cid for cid, _ in woke] == ["builder"]  # builder2's step waits for the sub plan "sec"
    task = woke[0][1]
    assert "sub plan “Effects” in the plan “Ship it”" in task and "Files: fx.py" in task
    assert "`fx` Glow (in_progress)" in task and "Done when: glow test passes" in task
    assert "`blur` Blur (pending)" in task and "BLOCKED:" in task and "no messages" in task
    for minute in range(1, 15):
        _run(minute * MIN, ["builder"], woke, plans=[SUB])  # working: no nudges, no lead wake
    assert [cid for cid, _ in woke] == ["builder"]


def test_a_members_task_carries_the_leads_spec_and_says_not_to_search(monkeypatch) -> None:
    # Oct 15 2026: members given only their own points put files at the project root instead of
    # the spec's pocket_ledger/ and renamed a field; most of their tool calls went to
    # find_workflows, skill reads and folder listings.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    spec = "Package pocket_ledger/ with models.py. Transaction keeps amount_cents (int)."
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[{**SUB, "overview": "A pocket ledger CLI.", "body_markdown": spec}])
    task = woke[0][1]
    assert "Spec from your lead" in task and "A pocket ledger CLI." in task and "amount_cents (int)" in task
    assert task.index("Spec from your lead") < task.index("Files: fx.py") < task.index("Points:")
    assert "don't search workflows or skills (find_workflows" in task


def test_the_spec_is_read_from_the_plan_and_capped(monkeypatch) -> None:
    import backend.agent.coding_agents.plans as plans

    # The keeper's plan rows carry no description: it comes from the stored plan.
    monkeypatch.setattr(plans, "load_plan", lambda cid, root=None: {"overview": "Ledger.", "body_markdown": "Use pocket_ledger/."}
                        if (cid, root) == ("coord", "C:/island") else None)
    assert "Ledger.\n\nUse pocket_ledger/." in keeper._spec({"chat_id": "coord", "project_root": "C:/island"})
    assert keeper._spec({"chat_id": "coord"}) == ""  # no description: no spec block


def test_a_long_spec_keeps_hard_rules_and_the_sub_plans_files_done_when_and_not_yours(monkeypatch) -> None:
    # A 6 kB spec used to be cut at character 1200, mid-rule.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    filler = " ".join(["word"] * 300)
    spec = (f"Opening {filler}\n\n## Hard rules\nNo secret in git, no .env, no stray files.\n\n"
            f"## Background\n{filler}\n\n## Decisions\nOne writer thread.\n\n## Notes\n{filler}\n\n"
            f"## Contract\nfx(level: int) -> str\n\n## History\n{filler}")
    assert len(spec) > 6000
    plan = {**SUB, "body_markdown": spec, "nodes": [{**SUB["nodes"][0],
            "body_markdown": "Files: fx.py\nDone when: glow test passes\nNot yours: ui.py"}, SUB["nodes"][1]]}
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[plan])
    task = woke[0][1]
    for kept in ("## Hard rules\nNo secret in git, no .env, no stray files.", "## Decisions\nOne writer thread.",
                 "## Contract\nfx(level: int) -> str", "Files: fx.py", "Done when: glow test passes", "Not yours: ui.py"):
        assert kept in task
    assert 'section="History"' in task  # soft sections drop whole, with where to read them


def test_a_member_whose_turn_ends_with_points_left_is_started_again(monkeypatch) -> None:
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    woke: list[tuple[str, str]] = []
    member = lambda: [cid for cid, _ in woke if cid == "builder"]  # noqa: E731
    _run(0, [], woke, plans=[SUB])
    _run(60, ["builder"], woke, plans=[SUB])
    _run(120, [], woke, plans=[SUB])  # its turn ended
    _run(200, [], woke, plans=[SUB])
    assert member() == ["builder"]  # idle 80 s: not yet
    _run(215, [], woke, plans=[SUB])
    assert member() == ["builder", "builder"]
    assert "Points left in your sub plan “Effects”" in woke[-1][1] and "`blur` Blur (pending)" in woke[-1][1]
    for minute in range(4, 40):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert len(member()) == 3  # started again twice, then it waits for the plan to move
    assert "coord" in [cid for cid, _ in woke]  # nothing can move on its own: now the lead decides
    moved = {**SUB, "nodes": [{**SUB["nodes"][0], "children": [
        {**SUB["nodes"][0]["children"][0], "status": "completed"}, SUB["nodes"][0]["children"][1]]},
        SUB["nodes"][1]]}
    _run(41 * MIN, [], woke, plans=[moved])
    assert len(member()) == 4


def test_a_blocked_member_is_left_alone_and_its_lead_told_once(monkeypatch, _world) -> None:
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    said = {"text": ""}
    monkeypatch.setattr(keeper, "_blocked_reason", lambda member: said["text"])
    woke: list[tuple[str, str]] = []
    member = lambda: [cid for cid, _ in woke if cid == "builder"]  # noqa: E731
    _run(0, [], woke, plans=[SUB])
    said["text"] = "the panel capture of revision 45"
    for minute in range(1, 8):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert _world == [("builder", "the panel capture of revision 45")]
    assert member() == ["builder"]  # no "continue" nudges for a blocked member
    assert "coord" not in [cid for cid, _ in woke]  # the notice is the lead's wake
    # The lead changed the plan: the member goes on, and the same block is not told again.
    moved = {**SUB, "nodes": [*SUB["nodes"], {"id": "cap", "content": "Capture", "assignee": "builder2",
                                              "status": "completed"}]}
    _run(10 * MIN, [], woke, plans=[moved])
    assert member() == ["builder", "builder"]
    assert len(_world) == 1


def test_a_member_blocked_again_goes_to_the_person_not_the_lead(monkeypatch, _world) -> None:
    # Oct 12 2026: a lead answered each block with one more step asking for the same live-panel
    # evidence; the worker blocked again, and the lead was woken again, for an hour.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(keeper, "_ask_person", lambda plan, member, work, reason: asked.append((member, reason)))
    said = {"text": ""}
    monkeypatch.setattr(keeper, "_blocked_reason", lambda member: said["text"])
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[SUB])
    said["text"] = "need the live panel on screen"
    _run(1 * MIN, [], woke, plans=[SUB])
    assert _world == [("builder", "need the live panel on screen")] and asked == []
    # The lead added a step; the member goes on, finishes nothing and blocks on it again.
    moved = {**SUB, "nodes": [{**SUB["nodes"][0], "children": [*SUB["nodes"][0]["children"],
             {"id": "again", "content": "Capture the live panel", "status": "pending"}]}, SUB["nodes"][1]]}
    _run(5 * MIN, [], woke, plans=[moved])
    said["text"] = "still need the live panel on screen"
    for minute in range(7, 40):
        _run(minute * MIN, [], woke, plans=[moved])
    assert asked == [("builder", "still need the live panel on screen")]
    assert len(_world) == 1  # the lead was not told a second time
    assert "coord" not in [cid for cid, _ in woke]  # and not woken for it either


def test_members_run_under_their_leads_approvals(monkeypatch, _links) -> None:
    # Oct 15 2026: Ducky started members with no "started by" link, so a lead's Allow everything
    # never reached them and a member's test command waited on a card nobody saw.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[SUB])  # its task
    _run(60, ["builder"], woke, plans=[SUB])
    _run(120, [], woke, plans=[SUB])  # its turn ended with points left
    _run(215, [], woke, plans=[SUB])  # idle 95 s: continue
    assert [cid for cid, _ in woke] == ["builder", "builder"]
    assert _links["links"] == [("builder", "coord"), ("builder", "coord")]


def test_a_lead_that_rewrites_a_blocked_point_starts_the_member_again(monkeypatch, _world, _links) -> None:
    # Oct 15 2026: the lead answered a block by rewriting the member's point without changing its
    # status; that was "no change", so the member never ran again and the team sat idle.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    said = {"text": ""}
    monkeypatch.setattr(keeper, "_blocked_reason", lambda member: said["text"])
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[SUB])
    said["text"] = "ducky_terminal_run approval timed out"
    _run(1 * MIN, [], woke, plans=[SUB])
    assert _world == [("builder", "ducky_terminal_run approval timed out")]
    point = SUB["nodes"][0]["children"][0]
    rewritten = {**SUB, "nodes": [{**SUB["nodes"][0], "children": [
        {**point, "body_markdown": "Run the tests once more: approvals are fixed."},
        SUB["nodes"][0]["children"][1]]}, SUB["nodes"][1]]}
    _run(3 * MIN, [], woke, plans=[rewritten])
    assert [cid for cid, _ in woke] == ["builder", "builder"]
    assert "Points left" in woke[-1][1]


def test_a_team_left_waiting_after_its_lead_had_the_block_tells_the_person_once(monkeypatch, _world, _links) -> None:
    # The lead heard of the block and ended its turn without changing the plan: nothing can
    # move until the person acts, and before this nobody told them.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    said = {"text": ""}
    monkeypatch.setattr(keeper, "_blocked_reason", lambda member: said["text"])
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[SUB])
    said["text"] = "need the person's login"
    _run(1 * MIN, [], woke, plans=[SUB])
    _run(2 * MIN, ["coord"], woke, plans=[SUB])  # the lead's turn on the block notice
    _run(3 * MIN, [], woke, plans=[SUB])
    assert _links["waiting"] == []  # it only just ended
    for minute in range(4, 30):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert _links["waiting"] == ["coord"]  # once, after two quiet minutes
    assert [cid for cid, _ in woke] == ["builder"]  # nobody woken in a loop


def test_a_block_said_while_waiting_on_a_teammate_is_over_when_the_teammate_finishes(monkeypatch, _world) -> None:
    # Oct 15 2026: the reports member finished its first point and ended "BLOCKED: needs the CLI"
    # while its second point waited on the CLI. When the CLI was done, Ducky read that old line
    # as a new block, told the lead, and parked the member.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"reports", "cli"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    said = {"text": ""}
    monkeypatch.setattr(keeper, "_blocked_reason", lambda member: said["text"] if member == "reports" else "")

    def ledger(r1: str, cli: str) -> dict:
        return {**PLAN, "id": "p3", "title": "Ledger", "nodes": [
            {"id": "cli", "content": "CLI", "assignee": "cli", "status": cli},
            {"id": "reports", "content": "Reports", "assignee": "reports", "status": "in_progress", "children": [
                {"id": "r1", "content": "Text reports", "status": r1},
                {"id": "r2", "content": "CSV report option", "status": "pending", "after": ["cli"]}]}]}

    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[ledger("pending", "in_progress")])
    _run(60, ["reports", "cli"], woke, plans=[ledger("pending", "in_progress")])
    said["text"] = "r2 needs the CLI owner to finish cli.py"
    for minute in range(2, 6):  # r1 done; r2 waits on the CLI, which is still being built
        _run(minute * MIN, ["cli"], woke, plans=[ledger("completed", "in_progress")])
    for minute in range(6, 10):  # the CLI is done: r2 can start
        _run(minute * MIN, [], woke, plans=[ledger("completed", "completed")])
    assert _world == []  # the lead heard of no block
    assert [cid for cid, text in woke if cid == "reports"][-1] == "reports"
    assert "`r2` CSV report option" in [text for cid, text in woke if cid == "reports"][-1]


def test_the_same_block_said_again_in_a_new_turn_reaches_the_lead(monkeypatch, _world) -> None:
    # Oct 28 2026: the block was cleared when its point became startable, the member ran again,
    # hit the same problem in the same words, and was nudged twice and left; its lead never heard.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"reports", "cli"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    said, mark = {"text": ""}, {"n": 0}
    monkeypatch.setattr(keeper, "_blocked_reason", lambda member: said["text"] if member == "reports" else "")
    monkeypatch.setattr(keeper, "_reply_mark", lambda member: mark["n"] if member == "reports" else 0)

    def ledger(r1: str, cli: str) -> dict:
        return {**PLAN, "id": "p4", "title": "Ledger", "nodes": [
            {"id": "cli", "content": "CLI", "assignee": "cli", "status": cli},
            {"id": "reports", "content": "Reports", "assignee": "reports", "status": "in_progress", "children": [
                {"id": "r1", "content": "Text reports", "status": r1},
                {"id": "r2", "content": "CSV report option", "status": "pending", "after": ["cli"]}]}]}

    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[ledger("pending", "in_progress")])
    _run(60, ["reports", "cli"], woke, plans=[ledger("pending", "in_progress")])
    said["text"], mark["n"] = "r2 needs the recipe seat's fix", 1
    for minute in range(2, 6):  # r1 done, r2 waits on the CLI
        _run(minute * MIN, ["cli"], woke, plans=[ledger("completed", "in_progress")])
    for minute in range(6, 10):  # the CLI is done: r2 can start, the old line is answered
        _run(minute * MIN, [], woke, plans=[ledger("completed", "completed")])
    assert _world == []
    mark["n"] = 2  # its next turn ends blocked again, in the same words
    for minute in range(10, 16):
        _run(minute * MIN, [], woke, plans=[ledger("completed", "completed")])
    assert _world == [("reports", "r2 needs the recipe seat's fix")]


def test_a_group_leader_with_its_own_sub_plan_gets_one_task(monkeypatch, _links) -> None:
    # The group's sub plan goes to the group's leader, who also owns a sub plan of its own:
    # two owners, one chat. The task used to switch between them on every tick.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"leader"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    monkeypatch.setattr(keeper, "_member_chat", lambda owner, root: "leader" if owner in ("core", "leader") else owner)
    plan = {**PLAN, "id": "p2", "title": "Ledger", "nodes": [
        {"id": "core", "content": "Core", "assignee": "core", "status": "pending", "children": [
            {"id": "setup", "content": "Package setup", "status": "pending"},
            {"id": "models", "content": "Models", "assignee": "leader", "status": "pending", "children": [
                {"id": "m1", "content": "Transaction", "status": "pending"}]}]}]}
    woke: list[tuple[str, str]] = []
    for minute in range(0, 12):
        _run(minute * MIN, [], woke, plans=[plan])
    tasks = [text for cid, text in woke if cid == "leader" and "Your task" in text]
    assert len(tasks) == 1
    assert "`setup` Package setup" in tasks[0] and "`m1` Transaction" in tasks[0]
    assert len([cid for cid, _ in woke if cid == "leader"]) <= 3  # its task, then two continues at most


def test_a_member_past_its_context_limit_starts_fresh_with_its_whole_task(monkeypatch) -> None:
    from frontend.ui_web import context_limit

    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    steps: list = []

    def over(cid, step=None, project_root=None):
        steps.append(step)
        return (612_000, 600_000) if cid == "builder" else None

    fresh: list[tuple] = []
    monkeypatch.setattr(context_limit, "over_limit", over)
    monkeypatch.setattr(context_limit, "start_fresh", lambda cid, text, **kw: fresh.append((cid, text, kw)))
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[SUB])
    assert "builder" not in [c for c, _ in woke]  # no plain wake: the fresh start carries the task
    assert [f[0] for f in fresh] == ["builder"] and fresh[0][2]["used"] == 612_000 and fresh[0][2]["limit"] == 600_000
    assert "Your task" in fresh[0][1] and "`fx` Glow" in fresh[0][1]
    assert steps[0]["id"] == "fx"  # the limit is read for the step it works now


def test_a_member_given_a_new_sub_plan_starts_it_in_a_fresh_session(monkeypatch) -> None:
    from frontend.ui_web import context_limit

    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    fresh: list[tuple] = []
    monkeypatch.setattr(context_limit, "start_fresh", lambda cid, text, **kw: fresh.append((cid, text, kw)))
    core = {**PLAN, "id": "p1", "title": "Plugin", "nodes": [
        {"id": "core", "content": "Plugin core", "assignee": "builder", "status": "in_progress", "children": [
            {"id": "api", "content": "API", "status": "in_progress"}]}]}
    docs = {**core, "nodes": [
        {**core["nodes"][0], "status": "completed", "children": [{"id": "api", "content": "API", "status": "completed"}]},
        {"id": "docs", "content": "Plugin docs", "assignee": "builder", "status": "pending", "children": [
            {"id": "readme", "content": "README", "status": "pending"}]}]}
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[core])
    assert [c for c, _ in woke] == ["builder"] and fresh == []  # its first sub plan: a plain wake
    _run(1 * MIN, [], woke, plans=[docs])
    assert [c for c, _ in woke] == ["builder"]
    assert [f[0] for f in fresh] == ["builder"] and fresh[0][2]["handoff"] == ""  # no handoff needed
    assert "Plugin docs" in fresh[0][1] and "`readme` README" in fresh[0][1]


def test_a_member_that_asked_for_a_fresh_session_gets_it_with_its_own_handoff(monkeypatch) -> None:
    from frontend.ui_web import context_limit

    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    asked = {"builder": "Glow done in fx.py; blur next."}
    monkeypatch.setattr(context_limit, "requested_handoff", asked.get)
    monkeypatch.setattr(context_limit, "clear_request", lambda cid: asked.pop(cid, None))
    fresh: list[tuple] = []
    monkeypatch.setattr(context_limit, "start_fresh", lambda cid, text, **kw: fresh.append((cid, text, kw)))
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[SUB])
    assert woke == [] and [f[0] for f in fresh] == ["builder"]
    assert fresh[0][2]["handoff"] == "Glow done in fx.py; blur next." and "Your task" in fresh[0][1]
    assert asked == {}  # used once


def test_a_lead_with_members_but_no_assignments_yet_is_kept(monkeypatch) -> None:
    # A lead whose turn ended after writing the plan and seating its members, before giving any
    # sub plan an owner, was never woken.
    import backend.agent.coding_agents.plans as plans

    rows = [{**PLAN, "chat_id": "staffed", "progress": {"pending": 5}},
            {**PLAN, "chat_id": "alone", "progress": {"pending": 5}}]
    monkeypatch.setattr(plans, "list_plans", lambda project_root=None: rows)
    monkeypatch.setattr(keeper, "_project_roots", lambda root: ["C:/island"])
    monkeypatch.setattr(keeper, "_team_leaders", lambda root: {"staffed"})
    assert [p["chat_id"] for p in keeper.kept_plans()] == ["staffed"]


def test_an_owner_whose_swarm_failed_works_alone_and_is_never_nagged(monkeypatch) -> None:
    # Nov 3 2026: the swarm build failed and archived its group and seats, the owner stayed
    # its leader and worked the plan alone; the keeper sent "Nothing in your plan can move"
    # and "Your team has been working for 47 minutes" with "Working: none · Idle: none".
    from types import SimpleNamespace

    from frontend.ui_web.chats import conversations as chat_convs

    def group(gid, leader, members, folder=""):
        return SimpleNamespace(id=gid, is_group=True, leader_conv_id=leader, folder_id=folder,
                               group_members=[{"member_conv_id": m} for m in members])

    convs = [
        group("g-failed", "solo", ["seat-1"], folder="archive"),  # the rolled-back swarm
        SimpleNamespace(id="seat-1", is_group=False, folder_id="archive"),
        group("g-gone", "solo2", ["deleted-seat"]),  # its only member was deleted
        group("g-live", "lead", ["member", "lead"]),
        SimpleNamespace(id="member", is_group=False, folder_id="f"),
        SimpleNamespace(id="solo", is_group=False, folder_id=""),
        SimpleNamespace(id="solo2", is_group=False, folder_id=""),
        SimpleNamespace(id="lead", is_group=False, folder_id=""),
    ]
    monkeypatch.setattr(chat_convs, "list_all_conversation_metadata", lambda root=None: convs)
    assert keeper._team_leaders("C:/island") == {"lead"}
    import backend.agent.coding_agents.plans as plans

    rows = [{**PLAN, "chat_id": cid, "progress": {"pending": 10, "completed": 7}} for cid in ("solo", "solo2", "lead")]
    monkeypatch.setattr(plans, "list_plans", lambda project_root=None: rows)
    monkeypatch.setattr(keeper, "_project_roots", lambda root: ["C:/island"])
    assert [p["chat_id"] for p in keeper.kept_plans()] == ["lead"]


def test_a_stopped_builder_is_not_woken(monkeypatch) -> None:
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: cid == "builder")
    plan = {**PLAN, "nodes": [{"id": "fx", "content": "Effects", "status": "in_progress", "assignee": "builder"}]}
    woke: list[tuple[str, str]] = []
    for minute in range(0, 10):
        _run(minute * MIN, ["coord"], woke, plans=[plan])
    assert [cid for cid, _ in woke if cid == "builder"] == []


def test_a_team_in_another_project_is_kept_even_with_no_project_open(monkeypatch) -> None:
    # Oct 12 2026: after a restart Ducky had no project open, so the keeper saw no team at all.
    import backend.agent.coding_agents.plans as plans
    from frontend.settings import PanelSettings

    monkeypatch.setattr(PanelSettings, "load", classmethod(lambda cls: type("S", (), {"uefn_project_root": ""})()))
    monkeypatch.setattr("frontend.ui_web.recent_projects.load_recent_projects", lambda: ["C:/Repo", "c:/repo/", "C:/Other"])
    assert keeper._project_roots(None) == ["C:/Repo", "C:/Other"]
    by_root = {"C:/Repo": [{**PLAN, "chat_id": "lead"}], "C:/Other": []}
    monkeypatch.setattr(plans, "list_plans", lambda project_root=None: by_root.get(project_root, []))
    leaders_read: list[str] = []
    monkeypatch.setattr(keeper, "_team_leaders", lambda root: leaders_read.append(root) or {"lead"})
    kept = keeper.kept_plans()
    assert [(p["chat_id"], p["project_root"]) for p in kept] == [("lead", "C:/Repo")]
    assert leaders_read == ["C:/Repo"]  # a project with no live plan isn't read


def test_steps_added_after_the_first_wake_resend_the_whole_task(monkeypatch) -> None:
    # A member woken on a bare step got "continue" (and "the sub plan “your steps”") once its
    # lead filled the step with points.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    bare = {**PLAN, "id": "p1", "title": "Ship it", "nodes": [
        {"id": "hud", "content": "UI: HUD", "assignee": "builder", "status": "pending"}]}
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[bare])
    assert "Your task: the sub plan “UI: HUD”" in woke[-1][1]
    points = [{"id": f"p{i}", "content": f"Point {i}", "status": "pending"} for i in range(3)]
    filled = {**bare, "nodes": [{**bare["nodes"][0], "children": points}]}
    _run(10, [], woke, plans=[filled])
    task = woke[-1][1]
    assert len(woke) == 2 and "Your task: the sub plan “UI: HUD”" in task and "your steps" not in task
    assert all(f"`p{i}` Point {i} (pending)" in task for i in range(3))
    # A tick (with its notes) is not a new task: the next start is "continue".
    ticked = {**filled, "nodes": [{**filled["nodes"][0], "children": [
        {**points[0], "status": "completed", "body_markdown": "done"}, *points[1:]]}]}
    _run(20, [], woke, plans=[ticked])
    _run(20 + 2 * keeper._MEMBER_IDLE_S, [], woke, plans=[ticked])
    assert woke[-1][1].startswith("[Ducky] Points left in your sub plan “UI: HUD”")


def test_members_tick_as_they_go_and_are_told_when_they_committed_without_ticking(monkeypatch) -> None:
    # Oct 24 2026: members ticked all their points in one burst at the end of a turn.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    committed = {"said": False}
    monkeypatch.setattr(keeper, "_said_committed", lambda member: committed["said"])
    rule = "Work one point at a time: mark it in_progress when you start it"
    woke: list[tuple[str, str]] = []
    _run(0, [], woke, plans=[SUB])
    assert rule in woke[-1][1] and "Never save ticks for the end." in woke[-1][1]
    _run(60, ["builder"], woke, plans=[SUB])
    committed["said"] = True  # its turn ended: "Committed as 1a2b3c4", nothing ticked
    _run(120, [], woke, plans=[SUB])
    _run(215, [], woke, plans=[SUB])
    text = woke[-1][1]
    assert text.startswith("[Ducky] You finished work without ticking: tick the points you completed now.")
    assert rule in text and "`blur` Blur (pending)" in text
    # It ticked one point in its next turn: no reminder.
    ticked = {**SUB, "nodes": [{**SUB["nodes"][0], "children": [
        {**SUB["nodes"][0]["children"][0], "status": "completed"}, SUB["nodes"][0]["children"][1]]},
        SUB["nodes"][1]]}
    _run(300, ["builder"], woke, plans=[ticked])
    _run(400, [], woke, plans=[ticked])
    _run(500, [], woke, plans=[ticked])
    assert woke[-1][0] == "builder" and "without ticking" not in woke[-1][1]
    assert keeper._COMMITTED_RE.search("Committed and pushed as `6181ecdd`.")
    assert not keeper._COMMITTED_RE.search("Nothing committed yet.")


def test_a_stall_wake_lists_working_and_idle_members_apart(monkeypatch) -> None:
    from types import SimpleNamespace
    from frontend.ui_web.chats import conversations as chat_convs
    from backend.agent.coding_agents import plans

    names = {"verse": "Verse lead", "ui": "UI Engineer", "test": "Tester",
             "model": "Modeling lead", "game": "Gameplay lead"}
    monkeypatch.setattr(chat_convs, "list_all_conversation_metadata", lambda _: [
        SimpleNamespace(id=cid, ducky_name=name, is_group=False) for cid, name in names.items()])
    monkeypatch.setattr(plans, "_chat_and_group_ids", lambda cid, root: [cid])
    monkeypatch.setattr(keeper, "_member_chat", lambda owner, root: "")  # no member dispatch here
    plan = {**PLAN, "nodes": [{"id": cid, "content": name, "assignee": cid, "children": [
        {"id": f"{cid}-1", "content": "Work", "status": "pending"}]} for cid, name in names.items()]}
    woke: list[tuple[str, str]] = []
    for minute in range(0, 21):
        _run(minute * MIN, ["verse", "ui", "test"], woke, plans=[plan])
    assert len(woke) == 1 and woke[0][0] == "coord"
    assert ("Working: Verse lead (verse); UI Engineer (ui); Tester (test) · "
            "Idle: Modeling lead (model); Gameplay lead (game)") in woke[0][1]


def test_a_nested_groups_leader_filed_in_the_outer_group_is_a_member(monkeypatch) -> None:
    # A workstream lead seated with as_leader stays filed in the outer group: its parent chain
    # never reaches the group that owns the sub plan, so the keeper took a busy team for idle.
    from types import SimpleNamespace
    from frontend.ui_web.chats import conversations as chat_convs
    from backend.agent.coding_agents import plans

    monkeypatch.setattr(chat_convs, "list_all_conversation_metadata", lambda _: [
        SimpleNamespace(id="root", title="Swarm", is_group=True, leader_conv_id="coord", group_members=[]),
        SimpleNamespace(id="combat", title="Verse: combat", is_group=True, leader_conv_id="verse",
                        group_members=[{"member_conv_id": "verse"}]),
        SimpleNamespace(id="verse", ducky_name="Verse lead", is_group=False)])
    monkeypatch.setattr(plans, "_chat_and_group_ids", lambda cid, root: [cid, "root"])
    monkeypatch.setattr(keeper, "_member_chat", lambda owner, root: "")  # no member dispatch here
    plan = {**PLAN, "nodes": [{"id": "combat-sec", "content": "Combat", "assignee": "combat", "children": [
        {"id": "c1", "content": "Damage", "status": "pending"}]}]}
    woke: list[tuple[str, str]] = []
    for minute in range(0, 21):
        _run(minute * MIN, ["verse"], woke, plans=[plan])
    assert len(woke) == 1 and "Working: Verse lead (verse) · Idle: none" in woke[0][1]


@pytest.mark.parametrize("jobs", [{"builder"}, {"coord"}, {"*"}])
def test_a_long_background_job_is_never_a_circle(jobs) -> None:
    # Oct 24 2026: "74 of 82 done, busy without finishing" while the Store release ran.
    # A member's or the lead's background command, or a workflow run ("*"), holds both clocks.
    woke: list[tuple[str, str]] = []
    for minute in range(0, 31):
        keeper.tick(now=minute * MIN, plans=[PLAN], running=["builder"] if minute % 2 else [],
                    wake=lambda cid, text: woke.append((cid, text)), jobs=jobs)
    assert woke == []
    # The release ends: the circle clock starts from there, not from minute 0.
    later = [m for m in range(31, 60) if keeper.tick(now=m * MIN, plans=[PLAN], running=["builder"],
                                                       wake=lambda c, t: woke.append((c, t)), jobs=set())]
    assert later == [50]


def test_an_unrelated_chats_background_job_does_not_hide_a_circle(monkeypatch) -> None:
    monkeypatch.setattr(keeper, "_team_context", lambda plan, busy=frozenset(): ({"builder"}, ""))
    woke: list[tuple[str, str]] = []
    times = [m for m in range(0, 25) if keeper.tick(now=m * MIN, plans=[PLAN], running=["builder"],
                                                      wake=lambda c, t: woke.append((c, t)), jobs={"elsewhere"})]
    assert times == [20]


def test_open_steps_only_the_person_can_do_wake_the_lead_once(monkeypatch) -> None:
    # Oct 24 2026: owner-only Google Cloud steps got "nothing can move" again and again.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, busy=frozenset(): (set(), ""))
    plan = {**PLAN, "nodes": [
        {"id": "done", "content": "Build", "assignee": "builder", "status": "completed"},
        {"id": "gcp", "content": "Create the Google Cloud key", "assignee": "person", "status": "pending"}]}
    woke: list[tuple[str, str]] = []
    assert [m for m in range(0, 240) if _run(m * MIN, [], woke, plans=[plan])] == [2]
    # The person does it (the plan changes): one more wake for the lead, then quiet again.
    moved = {**plan, "nodes": [plan["nodes"][0], {**plan["nodes"][1], "status": "in_progress"}]}
    assert [m for m in range(240, 480) if _run(m * MIN, [], woke, plans=[moved])] == [240]


def test_stopping_a_team_never_stops_its_lead(monkeypatch) -> None:
    from types import SimpleNamespace
    from frontend.ui_web import group_orchestrator
    from frontend.ui_web.agent_turns.testing import patch_turns
    from frontend.ui_web.chats import conversations as chat_convs

    convs = {
        "team": SimpleNamespace(id="team", is_group=True, leader_conv_id="coord"),
        "sub": SimpleNamespace(id="sub", is_group=True, leader_conv_id="sublead"),
    }
    rows = {"team": [{"member_conv_id": "coord"}, {"member_conv_id": "a"}, {"member_conv_id": "sub", "is_group": True}],
            "sub": [{"member_conv_id": "sublead"}, {"member_conv_id": "b"}, {"member_conv_id": "coord"}]}
    monkeypatch.setattr(chat_convs, "load_conversation", lambda cid, **kw: convs.get(cid))
    monkeypatch.setattr(group_orchestrator, "is_group_conversation", lambda conv: conv.is_group)
    monkeypatch.setattr(group_orchestrator, "group_and_nested_ids", lambda gid: [gid, "sub"])
    monkeypatch.setattr(group_orchestrator, "group_members", lambda conv: rows[conv.id])
    monkeypatch.setattr(group_orchestrator, "cancel_group_run", lambda gid: None)
    patch_turns(monkeypatch, "is_agent_running", lambda cid: True)
    cancelled: list[str] = []
    res = keeper.stop_team("team", cancelled.append)
    assert res["ok"] and "coord" not in cancelled  # the lead's turn keeps running
    assert cancelled == ["a", "sublead", "b"]  # members and nested leads stop
    assert keeper.stop_team("a", cancelled.append)["ok"] is False


def test_member_wakes_wait_for_a_seat_and_the_top_leads_never_do(monkeypatch) -> None:
    # Oct 24 2026: keeper wakes started member turns past "4 running · 6 waiting".
    from collections import deque

    from frontend.ui_web import admission
    from frontend.ui_web import seat_queue as sq

    monkeypatch.setattr(keeper, "_enqueue", _REAL_ENQUEUE)
    monkeypatch.setattr(keeper, "_waiting", _REAL_WAITING)
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    for name, value in {"_line": deque(), "_starting": set(), "_seen": set(), "_first_starts": deque(),
                        "_starts": {}, "_retried": set()}.items():
        monkeypatch.setattr(sq, name, value)
    monkeypatch.setattr(sq, "_spawn", lambda fn, name: fn())
    monkeypatch.setattr(sq, "limit", lambda: 1)
    monkeypatch.setattr(admission, "_running", {"other"})  # the one seat is taken
    lone = {**PLAN, "chat_id": "lead2"}
    woke: list[tuple[str, str]] = []
    for minute in (0, 1, 2):
        _run(minute * MIN, [], woke, plans=[SUB, lone])
    # The member's task waits in line (sent once, not again while it waits); the top lead
    # of an idle team is woken at once, queue or not.
    assert [c for c, _ in woke] == ["lead2"] and sq.status()["waiting"] == 1
    admission._running.discard("other")
    sq.on_turn_end("other")  # a turn ends: the waiting member starts with its whole task
    assert [c for c, _ in woke] == ["lead2", "builder"] and "Your task" in woke[1][1]


def _nudged(plan: dict, node_id: str, at: float) -> dict:
    """``plan`` with ``nudged_at`` on one node: what plans.update_node stamps for a lead's touch."""
    def walk(nodes):
        return [{**n, **({"nudged_at": at} if n["id"] == node_id else {}), "children": walk(n.get("children") or [])}
                for n in nodes]
    return {**plan, "nodes": walk(plan["nodes"])}


@pytest.fixture()
def _stopped(monkeypatch):
    """The broker's Stop set: automatic work is held until resume_by_user."""
    stopped: set[str] = set()
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: cid in stopped)
    monkeypatch.setattr("backend.agent.a2a_broker.resume_by_user", stopped.discard)
    return stopped


def test_a_stopped_member_with_an_in_progress_point_starts_when_its_lead_reassigns_it(_stopped) -> None:
    # Plan 14283125831b: the lead stopped the team; the member's point stayed in_progress, the
    # keeper only woke the lead, and re-setting the same assignee changed nothing.
    woke: list[tuple[str, str]] = []
    member = lambda: [cid for cid, _ in woke if cid == "builder"]  # noqa: E731
    _run(0, [], woke, plans=[SUB])
    _run(60, ["builder"], woke, plans=[SUB])
    _stopped.add("builder")  # ducky_group_stop / ducky_agent_stop while it ran
    for minute in range(2, 10):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert member() == ["builder"] and "coord" in [cid for cid, _ in woke]
    _run(10 * MIN, [], woke, plans=[_nudged(SUB, "sec", 500.0)])  # same assignee, re-set by the lead
    assert member() == ["builder", "builder"] and "builder" not in _stopped
    _run(11 * MIN, [], woke, plans=[_nudged(SUB, "sec", 500.0)])
    assert len(member()) == 2  # one nudge, one start


def test_a_leads_notes_on_an_open_point_wake_a_member_out_of_tries(_stopped) -> None:
    woke: list[tuple[str, str]] = []
    member = lambda: [cid for cid, _ in woke if cid == "builder"]  # noqa: E731
    for minute in range(0, 30):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert len(member()) == 3  # its task, then two "continue": out of tries
    _run(30 * MIN, [], woke, plans=[_nudged(SUB, "fx", 1800.0)])  # the lead rewrote the in_progress point
    assert len(member()) == 4 and "Points left" in woke[-1][1]


def test_a_turn_ducky_did_not_start_gives_the_member_fresh_tries(_stopped) -> None:
    # After ducky_recycle_member the member ran its handoff turn, then sat idle for good: the
    # keeper had spent its two "continue" tries before the recycle.
    woke: list[tuple[str, str]] = []
    member = lambda: [cid for cid, _ in woke if cid == "builder"]  # noqa: E731
    for minute in range(0, 30):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert len(member()) == 3
    _run(30 * MIN, ["builder"], woke, plans=[SUB])  # the recycle's turn
    for minute in range(31, 36):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert len(member()) == 4


def test_the_last_reply_is_read_from_the_rows_not_the_whole_chat(monkeypatch) -> None:
    # Nov 2026: the keeper loaded every idle member's whole chat each minute to read its last
    # reply, and spent a third of a core opening (DPAPI) rows and parts it never looked at.
    from backend.store import switch
    from backend.store.repos import chats as repo
    from frontend.ui_web.chats import conversations as chat_convs

    monkeypatch.setattr(switch, "use_db", lambda table: True)
    monkeypatch.setattr(chat_convs, "load_conversation", lambda *a, **k: pytest.fail("loaded the whole chat"))
    messages = [{"role": "user", "content": "go", "ts": 1.0},
                *({"role": "assistant", "content": f"step {i}", "ts": 2.0} for i in range(30)),
                {"role": "assistant", "content": "Committed abc1234.\nBLOCKED: needs the CLI", "ts": 3.0}]
    repo.conv_save("proj_keeper", {"id": "kmember", "title": "m", "created": 1.0, "updated": 1.0}, messages=messages)
    assert keeper._last_reply("kmember") == ("Committed abc1234.\nBLOCKED: needs the CLI", 31)
    blocked_reason, reply_mark, said_committed = _REAL_READERS  # _world stands in for these
    assert blocked_reason("kmember") == "needs the CLI"
    assert reply_mark("kmember") == 31
    assert said_committed("kmember") is True


def test_the_last_reply_falls_back_to_the_chat_without_the_row_store(monkeypatch) -> None:
    from types import SimpleNamespace

    from backend.store import switch
    from frontend.ui_web.chats import conversations as chat_convs

    monkeypatch.setattr(switch, "use_db", lambda table: False)
    conv = SimpleNamespace(messages=[{"role": "assistant", "content": "done"}, {"role": "assistant", "content": " "}])
    monkeypatch.setattr(chat_convs, "load_conversation", lambda cid, **kw: conv)
    assert keeper._last_reply("m") == ("done", 2)


_REAL_MEMBER_CHAT = keeper._member_chat  # taken at import, before _world stands in for it


def _archive_world(monkeypatch, archived: set[str]):
    from types import SimpleNamespace

    from frontend.ui_web.chats import conversations as chat_convs

    convs = {
        "builder": SimpleNamespace(id="builder", folder_id="archive" if "builder" in archived else ""),
        "team": SimpleNamespace(id="team", is_group=True, leader_conv_id="builder", folder_id=""),
    }
    monkeypatch.setattr(chat_convs, "load_conversation", lambda cid, **kw: convs.get(cid))
    monkeypatch.setattr(keeper, "_member_chat", _REAL_MEMBER_CHAT)


def test_an_archived_member_or_groups_archived_leader_is_never_woken(monkeypatch) -> None:
    # Nov 5 2026: the person archived a seat and the keeper kept starting it on its open points.
    _archive_world(monkeypatch, {"builder"})
    assert keeper._member_chat("builder", None) == ""
    assert keeper._member_chat("team", None) == ""
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    woke: list[tuple[str, str]] = []
    for minute in range(0, 10):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert "builder" not in [cid for cid, _ in woke]


def test_a_live_member_is_still_woken(monkeypatch) -> None:
    _archive_world(monkeypatch, set())
    assert keeper._member_chat("builder", None) == "builder"
    assert keeper._member_chat("team", None) == "builder"
    assert not keeper._archived("builder", None)


def _ticked_off(plan: dict) -> dict:
    """``plan`` with every step completed: what rolls a plan to "finished"."""
    def walk(nodes):
        return [{**n, "status": "completed", "children": walk(n.get("children") or [])} for n in nodes]
    return {**plan, "status": "finished", "nodes": walk(plan["nodes"]),
            "progress": {"completed": 4, "pending": 0, "in_progress": 0}}


def test_a_finished_plan_stops_its_keeper_and_forgets_its_members(monkeypatch, _fresh) -> None:
    # Nov 5 2026: the person ticked the last step of a release plan, and its keeper kept going.
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    woke: list[tuple[str, str]] = []
    for minute in range(0, 10):
        _run(minute * MIN, [], woke, plans=[SUB])
    assert "builder" in [cid for cid, _ in woke] and keeper.status("coord")["state"] != "off"
    keeper._lead_told["coord"] = "x"
    _fresh["coord"] = "parked-at"  # a parked mark saved for the app's next start
    keeper._teams["coord"]["wakes"] = float(keeper._PARK_AFTER)
    done = _ticked_off(SUB)
    before = len(woke)
    for minute in range(10, 240):
        assert _run(minute * MIN, [], woke, plans=[done]) == []
    assert len(woke) == before  # no lead wake, no member "continue"
    assert keeper.status("coord") == {"state": "off"}
    assert "builder" not in keeper._members and "coord" not in keeper._lead_told and "coord" not in _fresh


def test_a_plan_with_every_step_closed_counts_as_finished_whatever_its_status() -> None:
    assert keeper._finished({"status": "finished"}) and keeper._finished({"status": "archived"})
    assert keeper._finished({"status": "open", "progress": {"completed": 2, "cancelled": 1}})
    assert not keeper._finished({"status": "open", "progress": {"completed": 2, "pending": 1}})
    assert not keeper._finished({"status": "open", "progress": {}})  # an empty plan isn't done


def test_a_plan_that_leaves_the_keeper_finished_is_forgotten_and_a_paused_one_is_not(monkeypatch) -> None:
    import backend.agent.coding_agents.plans as plans

    stored = {"coord": {"status": "finished"}, "lead2": {"status": "paused"}}
    monkeypatch.setattr(plans, "load_plan", lambda cid, project_root=None: stored.get(cid))
    monkeypatch.setattr(keeper, "_team_context", lambda plan, **_: ({"builder", "builder2"}, ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)
    woke: list[tuple[str, str]] = []
    paused = {**PLAN, "chat_id": "lead2"}
    _run(0, [], woke, plans=[SUB, paused])
    keeper._members["m2"] = {"lead": "lead2"}
    _run(1 * MIN, [], woke, plans=[])  # kept_plans no longer lists either plan
    assert "builder" not in keeper._members  # coord's plan is finished: its team is forgotten
    assert "m2" in keeper._members  # lead2's is only paused: its members keep their place


def _owner_world(monkeypatch, convs: dict) -> None:
    from frontend.ui_web.chats import conversations as chat_convs

    monkeypatch.setattr(chat_convs, "load_conversation", lambda cid, **kw: convs.get(cid))
    monkeypatch.setattr(keeper, "_orphaned", _REAL_ORPHANED)
    monkeypatch.setattr(keeper, "_member_chat", lambda owner, root: owner if owner in convs else "")
    monkeypatch.setattr(keeper, "_team_context", lambda plan, busy=frozenset(): (set(), ""))
    monkeypatch.setattr("backend.agent.a2a_broker.automatic_work_blocked", lambda cid, **kw: False)


def test_a_deleted_or_archived_owner_orphans_its_steps(monkeypatch) -> None:
    from types import SimpleNamespace

    _owner_world(monkeypatch, {"live": SimpleNamespace(folder_id=""), "shelved": SimpleNamespace(folder_id="archive")})
    assert keeper._orphaned("gone", None) and keeper._orphaned("shelved", None)
    assert not keeper._orphaned("live", None)

    def boom(cid, **kw):
        raise OSError("store busy")

    monkeypatch.setattr("frontend.ui_web.chats.conversations.load_conversation", boom)
    assert not keeper._orphaned("gone", None)  # a failed read is not a deleted chat


def test_steps_of_a_deleted_member_get_one_notice_naming_them_then_quiet(monkeypatch) -> None:
    # Nov 5 2026: the person deleted a team's old member chats; their open steps made the keeper
    # say "Nothing in your plan can move" every few minutes.
    from types import SimpleNamespace

    _owner_world(monkeypatch, {"builder": SimpleNamespace(folder_id="")})
    plan = {**PLAN, "nodes": [
        {"id": "done", "content": "Build", "assignee": "builder", "status": "completed"},
        {"id": "docs", "content": "Port the docs", "assignee": "deleted-seat", "status": "in_progress"}]}
    woke: list[tuple[str, str]] = []
    assert [m for m in range(0, 240) if _run(m * MIN, [], woke, plans=[plan])] == [2]
    assert [cid for cid, _ in woke] == ["coord"]
    assert "`docs` Port the docs: orphaned" in woke[0][1] and "only notice" in woke[0][1]
    # The lead hands it to a live member: the plan changed, so that member is started.
    moved = {**plan, "nodes": [plan["nodes"][0], {**plan["nodes"][1], "assignee": "builder"}]}
    _run(240 * MIN, [], woke, plans=[moved])
    assert woke[-1][0] == "builder" and "Port the docs" in woke[-1][1]


def test_a_person_wait_step_gets_one_notice_naming_it_then_quiet(monkeypatch) -> None:
    _owner_world(monkeypatch, {})
    plan = {**PLAN, "nodes": [
        {"id": "key", "content": "Create the API key", "assignee": "person", "status": "pending"},
        {"id": "docs", "content": "Port the docs", "assignee": "deleted-seat", "status": "pending"}]}
    woke: list[tuple[str, str]] = []
    assert [m for m in range(0, 480) if _run(m * MIN, [], woke, plans=[plan])] == [2]
    assert "`key` Create the API key: waits on the person" in woke[0][1]
    assert "`docs` Port the docs: orphaned" in woke[0][1]


def test_one_step_that_can_still_move_keeps_the_ordinary_wakes(monkeypatch) -> None:
    from types import SimpleNamespace

    _owner_world(monkeypatch, {"idle": SimpleNamespace(folder_id="")})
    plan = {**PLAN, "nodes": [
        {"id": "docs", "content": "Port the docs", "assignee": "deleted-seat", "status": "pending"},
        {"id": "open", "content": "Unowned", "status": "pending"}]}
    assert keeper._stuck_steps(plan) == []  # an unowned step: the lead can still give it out
    woke: list[tuple[str, str]] = []
    assert [m for m in range(0, 240) if _run(m * MIN, [], woke, plans=[plan])] == [2, 4, 8]
    assert "Nothing in your plan can move on its own" in woke[0][1]
