"""agent_send deliveries to team seats pass the seat cap (seat_queue), as keeper wakes do."""

from __future__ import annotations

from collections import deque

import pytest

from frontend.ui_web import admission as adm
from frontend.ui_web import seat_queue as sq
from frontend.ui_web.agent_turns import pending


@pytest.fixture
def world(monkeypatch):
    from backend.agent import a2a_broker

    alive: set[str] = set()
    started: list[str] = []
    monkeypatch.setattr(adm, "_running", set())
    monkeypatch.setattr(adm, "_read_free", lambda: 64.0)
    for name, value in (("_line", deque()), ("_starting", set()), ("_user_cap", None), ("_seen", set()),
                        ("_first_starts", deque()), ("_wave_timer", False), ("_starts", {}),
                        ("_retried", set()), ("_idle_since", {})):
        monkeypatch.setattr(sq, name, value)
    monkeypatch.setattr(sq, "pc_limit", lambda: 4)
    monkeypatch.setattr(sq, "WAVE_SIZE", 100)
    monkeypatch.setattr(sq, "_spawn", lambda fn, name: fn())  # seats start inline
    timers: list[float] = []
    monkeypatch.setattr(sq, "_spawn_later", lambda delay, fn: timers.append(delay))
    clock = {"t": 1000.0}
    monkeypatch.setattr(sq, "_now", lambda: clock["t"])
    monkeypatch.setattr(sq, "_agent_running", lambda cid: cid in alive)
    monkeypatch.setattr(pending, "is_agent_running", lambda cid: cid in alive)
    monkeypatch.setattr(pending, "_in_bridge_process", lambda: False)
    monkeypatch.setattr(pending, "_seat_capped", lambda cid: cid.startswith("m"))
    monkeypatch.setattr(pending, "_reserve_admission", lambda cid: {"transferred": True})
    monkeypatch.setattr(pending, "_check_admission", lambda cid, token: None)
    monkeypatch.setattr(pending, "_release_admission", lambda cid, token: None)
    monkeypatch.setattr(a2a_broker, "automatic_work_blocked", lambda cid, queued=False: False)
    monkeypatch.setattr(a2a_broker, "resume_by_user", lambda cid: None)
    monkeypatch.setattr(a2a_broker, "_retry_queue_later", lambda cid: None)
    held: dict[str, list[str]] = {}
    monkeypatch.setattr(pending, "_pending_agent_messages", held)

    def may_start(cid, kind, start=None, push=None):
        adm._running.add(cid)  # admission takes the turn's memory slot
        return True

    monkeypatch.setattr(adm, "may_start", may_start)

    @pending._with_admission
    def run(conv_id, user_text, mode, model, **kwargs):
        started.append(conv_id)
        alive.add(conv_id)
        return "started"

    def end(cid):
        alive.discard(cid)
        adm._running.discard(cid)  # agent_stopped frees the memory slot
        sq.on_turn_end(cid, "done")

    return {"run": run, "started": started, "end": end, "alive": alive, "timers": timers, "held": held}


def deliver(world, cid: str) -> str:
    return world["run"](cid, f"[message for {cid}]", "agent", "", queue_if_busy=True)


def test_fifty_agent_sends_start_at_most_the_cap_then_one_per_ended_turn(world):
    results = [deliver(world, f"m{i}") for i in range(50)]
    assert world["started"] == ["m0", "m1", "m2", "m3"]  # limit() == 4
    assert all(r == "queued" for r in results)  # the broker counts it delivered: it runs in its turn
    assert sq.status()["waiting"] == 46
    for i in range(46):
        world["end"](world["started"][i])
        assert len(world["started"]) == 5 + i  # one seat starts per ended turn, in order
        assert len(world["alive"]) == 4
    assert world["started"] == [f"m{i}" for i in range(50)]
    assert sq.status()["waiting"] == 0


def test_first_turns_start_in_waves(world, monkeypatch):
    monkeypatch.setattr(sq, "WAVE_SIZE", 3)
    monkeypatch.setattr(sq, "pc_limit", lambda: 8)
    for i in range(6):
        deliver(world, f"m{i}")
    assert world["started"] == ["m0", "m1", "m2"] and world["timers"] == [sq.WAVE_GAP_S]


def test_a_persons_send_and_a_solo_chat_never_wait_for_a_seat(world):
    for i in range(4):
        deliver(world, f"m{i}")
    assert world["run"]("m9", "hi", "agent", "") == "started"  # the person's own send
    assert deliver(world, "solo") == "started"  # not a team seat
    assert world["started"][-2:] == ["m9", "solo"]


def test_a_keeper_wake_through_the_queue_is_not_queued_twice(world):
    assert sq.request("m1", lambda: deliver(world, "m1")) == "started"
    assert world["started"] == ["m1"] and sq.status()["waiting"] == 0


def test_a_second_message_to_a_waiting_seat_is_held_for_its_turn(world):
    for i in range(4):
        deliver(world, f"m{i}")
    assert deliver(world, "m7") == "queued"
    deliver(world, "m7")  # the seat waits in line: this text waits for its turn
    assert world["held"]["m7"] == ["[message for m7]"]
    assert sq.status()["waiting"] == 1
