"""seat_queue — one cap on member turns at once; the rest wait in order."""

from __future__ import annotations

from collections import deque

import pytest

from frontend.ui_web import admission as adm
from frontend.ui_web import seat_queue as sq


@pytest.fixture
def q(monkeypatch):
    monkeypatch.setattr(adm, "_running", set())
    monkeypatch.setattr(adm, "_read_free", lambda: 64.0)
    monkeypatch.setattr(sq, "_line", deque())
    monkeypatch.setattr(sq, "_starting", set())
    monkeypatch.setattr(sq, "_user_cap", None)
    monkeypatch.setattr(sq, "pc_limit", lambda: 4)
    monkeypatch.setattr(sq, "_spawn", lambda fn, name: fn())  # start turns inline
    monkeypatch.setattr(sq, "_seen", set())
    monkeypatch.setattr(sq, "_first_starts", deque())
    monkeypatch.setattr(sq, "_wave_timer", False)
    monkeypatch.setattr(sq, "_starts", {})
    monkeypatch.setattr(sq, "_retried", set())
    monkeypatch.setattr(sq, "_idle_since", {})
    alive: set[str] = set()  # chats with a live agent turn
    monkeypatch.setattr(sq, "_agent_running", lambda cid: cid in alive)
    notices: list[tuple[str, str]] = []
    monkeypatch.setattr(sq, "_notify_lead", lambda cid, text: notices.append((cid, text)))
    monkeypatch.setattr(sq, "WAVE_SIZE", 100)  # the cap tests start no waves
    clock = {"t": 1000.0}
    monkeypatch.setattr(sq, "_now", lambda: clock["t"])
    timers: list[float] = []
    monkeypatch.setattr(sq, "_spawn_later", lambda delay, fn: timers.append(delay))
    started: list[str] = []

    def starter(cid):
        def go():
            started.append(cid)
            adm._running.add(cid)  # admission holds the slot until agent_stopped
            alive.add(cid)
        return go

    def end(cid, reason="done", detail=""):
        adm._running.discard(cid)  # agent_turns releases the memory slot
        alive.discard(cid)
        sq.on_turn_end(cid, reason, detail)

    return {"started": started, "starter": starter, "end": end, "clock": clock, "timers": timers,
            "notices": notices, "alive": alive}


def test_cap_one_a_cancelled_turn_frees_its_slot_at_once(q):
    sq.set_run_at_once(1)
    assert sq.request("a", q["starter"]("a")) == "started"
    assert sq.request("b", q["starter"]("b")) == "queued"
    q["alive"].discard("a")  # cancelled: the agent stops, admission's slot is still held
    sq.on_turn_end("a", "cancelled")
    assert q["started"] == ["a", "b"] and not adm.holds("a") and not sq.is_busy("a")


def test_cap_one_a_start_that_raises_frees_its_slot_at_once(q):
    sq.set_run_at_once(1)

    def boom():
        adm._running.add("a")  # it took the slot, then failed
        raise RuntimeError("backend down")

    assert sq.request("a", boom) == "started"
    assert sq.request("b", q["starter"]("b")) == "started"  # a's slot came back at once
    assert not adm.holds("a") and q["started"] == ["b"]


def test_a_slot_whose_agent_ended_unseen_is_freed_after_a_few_seconds(q):
    sq.set_run_at_once(1)
    sq.request("a", q["starter"]("a"))
    q["alive"].discard("a")  # its turn ended, but no turn-end reached the queue
    assert sq.request("b", q["starter"]("b")) == "queued"
    q["clock"]["t"] += sq.STALE_S
    assert not sq.is_busy("a")  # any look at the queue frees it
    sq.drain()
    assert q["started"] == ["a", "b"]


def test_a_running_agent_keeps_its_slot(q):
    sq.set_run_at_once(1)
    sq.request("a", q["starter"]("a"))
    q["clock"]["t"] += 600
    assert sq.is_busy("a") and sq.request("b", q["starter"]("b")) == "queued"


def test_cap_four_runs_four_and_the_fifth_starts_when_one_ends(q):
    results = [sq.request(f"s{i}", q["starter"](f"s{i}")) for i in range(10)]
    assert results == ["started"] * 4 + ["queued"] * 6
    assert q["started"] == ["s0", "s1", "s2", "s3"]
    assert sq.status()["text"] == "4 running · 6 waiting"
    q["end"]("s2")
    assert q["started"][4:] == ["s4"]  # oldest waiting seat, exactly one
    assert sq.status()["running"] == 4 and sq.status()["waiting"] == 5


def test_the_slider_lowers_the_cap_but_never_past_the_pc_limit(q):
    assert sq.set_run_at_once(2) == 2
    assert sq.set_run_at_once(99) == 4
    sq.set_run_at_once(2)
    for i in range(3):
        sq.request(f"s{i}", q["starter"](f"s{i}"))
    assert q["started"] == ["s0", "s1"]
    sq.set_run_at_once(0)  # back to the PC limit: the waiting seat starts
    assert q["started"] == ["s0", "s1", "s2"]


def test_low_memory_never_holds_a_seat(q, monkeypatch):
    monkeypatch.setattr(adm, "_read_free", lambda: 1.0)  # far under the memory floor
    for i in range(4):
        sq.request(f"s{i}", q["starter"](f"s{i}"))
    assert q["started"] == ["s0", "s1", "s2", "s3"]  # only the seat cap (4) counts


def test_a_running_or_waiting_seat_is_busy_and_stop_drops_it(q):
    sq.set_run_at_once(1)
    assert sq.request("a", q["starter"]("a")) == "started"
    assert sq.request("a", q["starter"]("a")) == "busy"
    assert sq.request("b", q["starter"]("b")) == "queued"
    assert sq.is_busy("b")
    sq.drop("b")
    assert not sq.is_busy("b")


def test_first_turns_start_three_at_a_time_five_seconds_apart(q, monkeypatch):
    monkeypatch.setattr(sq, "WAVE_SIZE", 3)
    monkeypatch.setattr(sq, "pc_limit", lambda: 16)
    for i in range(10):
        sq.request(f"s{i}", q["starter"](f"s{i}"))
    assert q["started"] == ["s0", "s1", "s2"]
    assert q["timers"] == [sq.WAVE_GAP_S]  # one timer for the next wave
    q["clock"]["t"] += 2
    sq.drain()
    assert len(q["started"]) == 3  # still inside the 5 s gap
    q["clock"]["t"] += 3
    sq._next_wave()
    assert q["started"][3:] == ["s3", "s4", "s5"]


def test_a_later_turn_of_a_started_seat_skips_the_wave(q, monkeypatch):
    monkeypatch.setattr(sq, "WAVE_SIZE", 1)
    sq.request("a", q["starter"]("a"))
    q["end"]("a")
    assert sq.request("b", q["starter"]("b")) == "queued"  # a first turn, the wave is full
    assert sq.request("a", q["starter"]("a")) == "started"  # not a first turn: no wave


def test_a_seat_whose_tools_did_not_connect_is_idle_and_can_restart(q):
    assert sq.request("a", q["starter"]("a")) == "started"
    assert sq.is_busy("a")
    # The turn ended without a real start; nothing else freed its slot yet.
    sq._retried.add("a")  # its automatic retry is spent: the seat stays idle
    sq.on_turn_end("a", "error", DROPPED)
    assert not sq.is_busy("a") and not adm.holds("a")
    assert sq.request("a", q["starter"]("a")) == "started"
    assert q["started"] == ["a", "a"]


DROPPED = "claude_code finished with no captured reply.\n\nDucky's tools didn't connect"


def test_tools_dropped_retries_once_then_tells_the_lead_once(q):
    sq.request("a", q["starter"]("a"))
    q["end"]("a", "error", DROPPED)
    assert q["started"] == ["a", "a"] and q["notices"] == []  # exactly one retry
    q["end"]("a", "error", DROPPED)
    assert q["started"] == ["a", "a"]  # no second retry
    assert len(q["notices"]) == 1 and "tools didn't connect" in q["notices"][0][1]
    assert not sq.is_busy("a")


def test_a_good_turn_resets_the_retry(q):
    sq.request("a", q["starter"]("a"))
    q["end"]("a", "error", "agent failed to start")
    q["end"]("a", "done", "all good")
    sq.request("a", q["starter"]("a"))
    q["end"]("a", "error", DROPPED)
    assert len(q["started"]) == 4 and q["notices"] == []  # a fresh failure gets its retry


def test_pc_limit_reads_something():
    assert sq.pc_limit() >= 2 and sq.total_memory_gb() > 0


@pytest.fixture
def restarts(q, monkeypatch):
    """Seats named m* are team members; a restart's start is the fake starter."""
    monkeypatch.setattr(sq, "_restarted", {})
    monkeypatch.setattr(sq, "_team_seat", lambda cid: cid.startswith("m"))
    monkeypatch.setattr(sq, "_continue_start", lambda cid: q["starter"](cid))
    return q


def test_an_interrupted_member_restarts_once_then_is_skipped_for_ten_minutes(restarts):
    q = restarts
    assert sq.restart("m1") == "started"
    assert q["started"] == ["m1"]
    q["end"]("m1", "interrupted")  # the host died again
    assert sq.restart("m1") == "skipped"
    q["clock"]["t"] += sq.RESTART_EVERY_S
    assert sq.restart("m1") == "started"
    assert q["started"] == ["m1", "m1"]


def test_seven_restarts_at_once_keep_the_cap(restarts):
    q = restarts
    results = [sq.restart(f"m{i}") for i in range(7)]
    assert results == ["started"] * 4 + ["queued"] * 3  # pc_limit() == 4
    assert len(q["started"]) == 4
    q["end"]("m0")
    assert q["started"][-1] == "m4" and len(q["alive"]) == 4


def test_a_solo_chat_or_a_running_seat_is_skipped(restarts):
    q = restarts
    assert sq.restart("solo") == "skipped"
    sq.request("m2", q["starter"]("m2"))
    assert sq.restart("m2") == "skipped"  # its turn already runs
    assert q["started"] == ["m2"]


def test_the_restart_sends_the_continue_message(monkeypatch):
    sent: list[tuple] = []
    import frontend.ui_web.agent_turns.routing as routing

    monkeypatch.setattr(routing, "run_message", lambda *a, **kw: sent.append((a, kw)) or "started")
    sq._continue_start("m1")()
    assert sent == [(("m1", sq.RESTART_TEXT, "agent", ""), {"queue_if_busy": True})]
