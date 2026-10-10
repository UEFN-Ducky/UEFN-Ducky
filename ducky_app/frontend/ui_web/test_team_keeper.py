"""The keeper wakes an idle team's coordinator, backs off when a wake starts no work."""

from __future__ import annotations

import pytest

from frontend.ui_web import team_keeper as keeper

MIN = 60.0
PLAN = {"chat_id": "coord", "status": "open", "progress": {"pending": 3, "in_progress": 1, "completed": 2}}


@pytest.fixture(autouse=True)
def _fresh():
    keeper.reset_for_tests()
    yield
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
    assert _run(9 * MIN, [], woke) == []  # quiet for 9 minutes: not yet
    assert _run(10 * MIN, [], woke) == ["coord"]
    assert "4 open steps" in woke[0][1] and "10 minutes" in woke[0][1]


def test_wakes_that_start_no_work_back_off_and_real_work_resets_it() -> None:
    woke: list[tuple[str, str]] = []
    times = []
    for minute in range(0, 141):
        if _run(minute * MIN, [], woke):
            times.append(minute)
    assert times == [10, 20, 40, 80, 140]  # waits 10, 20, 40, then at most 60 minutes
    # The coordinator answering a wake on its own is not progress: the backoff holds.
    _run(141 * MIN, ["coord"], woke)
    assert _run(199 * MIN, [], woke) == []
    assert _run(200 * MIN, [], woke) == ["coord"]
    # A member working is progress: the next wake is 10 quiet minutes away again.
    _run(201 * MIN, ["builder"], woke)
    assert _run(210 * MIN, [], woke) == []
    assert _run(211 * MIN, [], woke) == ["coord"]


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
    assert keeper.tick(now=10 * MIN, plans=[PLAN], running=[], wake=_boom) == []
    assert keeper.tick(now=11 * MIN, plans=[PLAN], running=[], wake=lambda c, t: None) == ["coord"]
    assert calls == ["coord"]
