"""Remote requests run once however often a dropped link resends them (remote_link.requests)."""

from __future__ import annotations

import random
import threading

import pytest

from frontend.ui_web.remote_link.requests import RequestLedger, clean_request_id


def test_ids_are_checked() -> None:
    assert clean_request_id("ph-1:17") == "ph-1:17"
    assert clean_request_id("bad id") == ""
    assert clean_request_id("x" * 200) == ""
    assert clean_request_id(None) == ""


def test_no_id_runs_every_time() -> None:
    led = RequestLedger()
    calls: list[int] = []
    assert led.run("", lambda: calls.append(1) or len(calls)) == (1, False)
    assert led.run("", lambda: calls.append(1) or len(calls)) == (2, False)


def test_1000_requests_across_5_drops_delivered_once_each() -> None:
    """Each drop loses the answers in flight; the viewer resends those ids after redialing."""
    led = RequestLedger()
    effects: dict[str, int] = {}
    delivered: set[str] = set()
    rng = random.Random(3)
    ids = [f"ph:{i}" for i in range(1000)]
    drop_at = set(rng.sample(range(1000), 5))
    unacked: list[str] = []

    def effect(rid: str):
        effects[rid] = effects.get(rid, 0) + 1
        return {"done": rid}

    for i, rid in enumerate(ids):
        result, _ = led.run(rid, lambda rid=rid: effect(rid))
        if i in drop_at:
            # The link dropped: this answer and the last few never reached the phone.
            unacked.extend(ids[max(0, i - 3): i + 1])
            continue
        assert result == {"done": rid}
        delivered.add(rid)
    for rid in unacked:  # after the reconnect
        result, replayed = led.run(rid, lambda rid=rid: effect(rid))
        assert replayed and result == {"done": rid}
        delivered.add(rid)
    assert len(delivered) == 1000
    assert all(n == 1 for n in effects.values()) and len(effects) == 1000
    assert led.replays == len(unacked)


def test_resend_while_running_waits_for_the_same_run() -> None:
    led = RequestLedger()
    gate = threading.Event()
    runs: list[int] = []
    out: list[tuple] = []

    def slow():
        runs.append(1)
        gate.wait(2.0)
        return "ok"

    first = threading.Thread(target=lambda: out.append(led.run("a1", slow)))
    first.start()
    while not runs:
        threading.Event().wait(0.005)
    second = threading.Thread(target=lambda: out.append(led.run("a1", slow)))
    second.start()
    gate.set()
    first.join()
    second.join()
    assert len(runs) == 1
    assert sorted(out) == [("ok", False), ("ok", True)]


def test_a_failed_call_may_run_again() -> None:
    led = RequestLedger()
    with pytest.raises(RuntimeError):
        led.run("f1", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert led.run("f1", lambda: "second") == ("second", False)


def test_call_once_replies_like_panel_api(monkeypatch) -> None:
    from frontend.ui_web.remote_link import requests as rq

    monkeypatch.setattr(rq, "_ledger", RequestLedger())
    sends: list[int] = []
    status, first = rq.call_once("send-9", {}, lambda: sends.append(1) or "sent")
    assert status == 200 and first == {"ok": True, "result": "sent", "request_id": "send-9", "replayed": False}
    # The link dropped before that reply: the phone sends it again (id in the body this time).
    status, again = rq.call_once(None, {"request_id": "send-9"}, lambda: sends.append(1) or "sent")
    assert again["replayed"] is True and again["result"] == "sent" and sends == [1]
    assert rq.call_once(None, None, lambda: "plain") == (200, {"ok": True, "result": "plain"})

    def missing():
        raise AttributeError("no such method")

    assert rq.call_once(None, {}, missing)[0] == 404
    assert rq.call_once(None, {}, lambda: 1 / 0) == (200, {"ok": False, "error": "division by zero"})


def test_old_answers_are_forgotten() -> None:
    clock = {"t": 0.0}
    led = RequestLedger(keep_s=10.0, max_kept=3, clock=lambda: clock["t"])
    led.run("o1", lambda: 1)
    clock["t"] = 20.0
    assert led.run("o1", lambda: 2) == (2, False)
    for i in range(10):
        led.run(f"m{i}", lambda: i)
    assert len(led._rows) <= 4
