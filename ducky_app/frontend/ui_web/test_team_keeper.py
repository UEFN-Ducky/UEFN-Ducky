"""The keeper wakes an idle team's coordinator, backs off when a wake starts no work."""

from __future__ import annotations

import pytest

from frontend.ui_web import team_keeper as keeper

MIN = 60.0
PLAN = {"chat_id": "coord", "status": "open", "progress": {"pending": 3, "in_progress": 1, "completed": 2}}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    keeper.reset_for_tests()
    saved: dict[str, str] = {}  # stands in for the app database across "restarts"
    monkeypatch.setattr(keeper, "_load_parked", lambda cid: saved.get(cid))
    monkeypatch.setattr(keeper, "_save_parked",
                        lambda cid, moves: saved.pop(cid, None) if moves is None else saved.__setitem__(cid, moves))
    yield saved
    keeper.reset_for_tests()


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
    for minute in range(0, 60):
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
    monkeypatch.setattr(
        keeper, "_team_leaders", lambda root: {"coord", "paused", "finished", "unstarted", "done"}
    )
    assert [p["chat_id"] for p in keeper.kept_plans()] == ["coord"]


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
    from frontend.ui_web import project_chats
    from backend.agent.coding_agents import plans
    monkeypatch.setattr(project_chats, "list_all_conversation_metadata", lambda _: [SimpleNamespace(id="builder", title="Builder", is_group=False)])
    monkeypatch.setattr(plans, "_chat_and_group_ids", lambda cid, root: [cid, "group-a"])
    plan = {**PLAN, "nodes": [{"id": "section", "content": "Modes", "assignee": "group-a", "children": [
        {"id": "next", "content": "Next adapter", "status": "pending"}]}],
        "team_reports": [{"body": "Builder completed previous adapter"}]}
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
