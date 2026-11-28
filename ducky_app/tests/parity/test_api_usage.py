"""Anonymous usage stats: both backends keep the same events from the same reports, queue
them in the same table, and send a fake site on this PC the same batch from the same
queue, with nothing of the account in it. Nothing here reaches the real Store."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from .parity_harness import plain

KIND = "usage_queue"
ROUTE = "/api/v1/plugins/uefn-ducky-store/collect/usage"
_LOGIN = {
    "email": "ducky@example.com", "device_key": "device-token-1", "device_key_id": "key-mine",
    "session_name": "duckyos_session_x", "session_value": "sess-1", "csrf_name": "duckyos_csrf_x", "csrf_value": "csrf-1",
}  # fmt: skip
_SECRETS = ("device-token-1", "sess-1", "csrf-1", "ducky@example.com")


def _row(name: str, props: dict, ts: float) -> dict:
    """A queued event, as both backends store one."""
    return {"ts": ts, "source": name, "payload": {"name": name, "props": props, "ts": ts}}


_THREE = (
    _row("feature.open", {"tab": "chat"}, 100.5),
    _row("app.heartbeat", {"uefn_up": "yes"}, 101.0),
    _row("feature.open", {"tab": "plan"}, 102.0),
)


@pytest.fixture
def usage(fake, rust_store, python, login, monkeypatch):
    """``usage.each(run, rows)``: ``run(backend)`` for each backend from the same queue
    (``rows``), login and settings; the answer, the site's calls, the queue left (name and
    props per row) and the login row after. ``arm()`` runs before each backend's turn."""
    from backend.agent import secrets
    from backend.store.repos import events, settings
    from backend.store.repos.secrets import LOGIN_NAME
    from frontend import duckyos_account, usage_events, usage_stats

    monkeypatch.setattr(usage_stats, "_test_copy", lambda: False)  # the site here is the fake one
    monkeypatch.setattr(usage_stats, "send_override", None)
    monkeypatch.setattr(usage_stats, "_enabled_cache", None)
    monkeypatch.setattr(usage_stats, "_refused_in_a_row", 0)

    def forget() -> None:
        secrets.forget_login_stamp()
        duckyos_account.forget_account_key()

    class Usage:
        site = fake

        @staticmethod
        def validate(backend: Any, name: Any, props: Any) -> Any:
            if backend is python:
                return usage_events.validate(name, props)
            return rust_store.request("/__rs/usage/validate", {"name": name, "props": props})[1]

        @staticmethod
        def track(backend: Any, name: str, props: dict) -> Any:
            if backend is python:
                return usage_stats.track(name, **props)
            return rust_store.request("/__rs/usage/track", {"name": name, "props": props})[1]

        @staticmethod
        def flush(backend: Any) -> Any:
            return usage_stats.flush() if backend is python else rust_store.request("/__rs/usage/flush", {})[1]

        @staticmethod
        def answers(*answers: tuple[int, Any]) -> Any:
            """The usage route answers these (status, body), one per call; the last one stays."""
            return lambda: fake.paths.__setitem__(ROUTE, [(status, {}, json.dumps(body).encode()) for status, body in answers])

        @staticmethod
        def each(run: Any, rows: Any = (), signed_in: bool = True, on: bool = True, arm: Any = None) -> tuple[Any, list, list, dict]:
            seen = []
            for backend in (python, rust_store):
                if signed_in:
                    login({"base_url": fake.url + "/", **_LOGIN})
                else:
                    secrets.clear_key(LOGIN_NAME)
                    forget()
                settings.replace_all({} if on else {"usage_stats": False}, {})
                usage_stats._enabled_cache = None
                usage_stats._refused_in_a_row = 0
                usage_stats.clear_queue()
                events.insert_many(KIND, list(rows))
                fake.calls.clear()
                if arm:
                    arm()
                answer = plain(run(backend))
                usage_stats.queued()  # the rows Python queued reach the table
                left = [(row["payload"]["name"], row["payload"]["props"]) for row in events.oldest(KIND, limit=10000)]
                forget()
                row = json.loads(secrets.get_key_fresh(LOGIN_NAME) or "{}")
                seen.append((answer, list(fake.calls), left, row))
            theirs, ours = seen
            for name, mine, yours in zip(("answer", "calls", "queue", "login row"), ours, theirs):
                assert mine == yours, f"{name}:\n rust   {mine}\n python {yours}"
            for secret in _SECRETS:
                assert secret not in json.dumps(theirs[1]), "the account went with a batch"
            return theirs

    return Usage


def _samples(spec: dict) -> tuple[dict, dict, dict]:
    """Props for one event: every prop right, every bucket by its label with the rest wrong,
    and values of the wrong kind."""
    from frontend import usage_events as ev

    good: dict[str, Any] = {}
    labels: dict[str, Any] = {}
    odd: dict[str, Any] = {"not_listed": "x"}
    for prop, rule in spec.items():
        if rule == ev.YES_NO:
            good[prop], labels[prop], odd[prop] = True, " OK ", "No"
        elif rule[0] == "bucket":
            good[rule[2]], labels[prop], odd[rule[2]] = 7, rule[1][-1][1], "abc"
        elif rule[0] == "slug":
            good[prop], labels[prop], odd[prop] = " A-b.c_9 ", "bad slug!", 7
        elif rule[0] == "version":
            good[prop], labels[prop], odd[prop] = " 1.2.3 ", "1.2", 123
        else:
            good[prop], labels[prop], odd[prop] = f" {rule[0].upper()} ", "nope", None
    return good, labels, odd


def test_every_event_keeps_the_same_props(usage, python, rust_store):
    from frontend import usage_events as ev

    def same(name: Any, props: Any) -> Any:
        theirs = usage.validate(python, name, props)
        ours = usage.validate(rust_store, name, props)
        assert ours == theirs and list(ours or {}) == list(theirs or {}), f"{name} {props}: rust {ours} != python {theirs}"
        return theirs

    for name, spec in ev.EVENTS.items():
        good, labels, odd = _samples(spec)
        assert len(same(name, good)) == len(spec), name
        same(name, labels)
        assert "not_listed" not in same(name, odd), name
    assert same("no.such", {"a": 1}) is None and same("", {}) is None

    numbers = [0, 10, 10.0001, 60, -5, 1e12, "61", " 1e3 ", "inf", "-inf", "nan", "", "abc", True, False, None, [1], {}]
    kept = [same("chat.turn", {"duration_s": n}).get("duration") for n in numbers]
    assert kept == ["10s", "10s", "1m", "1m", "10s", "more", "5m", "20m", "more", "10s", None, None, None, "10s", "10s", None, None, None]
    for props in ({"duration": "5m"}, {"duration": "nope"}, {"duration": 5}, {"duration": "5m", "duration_s": 1}, {"duration": "5m", "duration_s": "x"}):
        same("chat.turn", props)
    flags = [True, False, 0, 1, 2.5, "yes", " TRUE ", "1", "ok", "no", "", None, [], [0], {}, {"a": 1}]
    said = [same("app.heartbeat", {"uefn_up": flag})["uefn_up"] for flag in flags]
    assert said == ["yes", "no", "no", "yes", "yes", "yes", "yes", "yes", "yes", "no", "no", "no", "no", "yes", "no", "yes"]
    for slug in ("a", "a" * 64, "a" * 65, "_a", "a b", "Ünï", "tool.name-1", True, 1.5, ""):
        same("skill.used", {"skill": slug})
    for version in ("1.2.3", "1.2.3.4", "1..3", "v1.2.3", "1.2.3-beta", "01.002.0003", 1.2, None):
        same("update.installed", {"from": version, "to": version})


def test_the_page_reports_only_its_own_events(usage):
    reports = [
        ("feature.open", {"tab": " Chat ", "path": "C:/Users/me/island"}),
        ("feature.open", {"tab": "nope"}),
        ("feature.open",),
        ("feature.open", "junk"),
        ("feature.open", None),
        ("setup.rating", {"stars": 5}),
        ("tour.end", {"tour": "First_Run", "how": "done", "at": "step-2"}),
        ("tool.call", {"tool": "x", "ok": True}),
        ("no.such", {}),
        ("", {}),
        (None,),
        (7, {}),
    ]

    def run(backend: Any) -> list:
        answers = [backend.call("usage_track", *report) for report in reports]
        assert all(status == 200 and reply["ok"] is True for status, reply in answers), answers
        return [reply["result"] for _, reply in answers]

    answers, calls, queue, _ = usage.each(run)
    assert answers == [True] * 7 + [False] * 5 and calls == []
    assert queue == [
        ("feature.open", {"tab": "chat"}), ("feature.open", {}), ("feature.open", {}), ("feature.open", {}), ("feature.open", {}),
        ("setup.rating", {"stars": "5"}), ("tour.end", {"tour": "first_run", "how": "done", "at": "step-2"}),
    ]  # fmt: skip


def test_a_queue_goes_out_as_one_batch_with_no_account_in_it(usage):
    from frontend import usage_events as ev

    rows = [
        _row("feature.open", {"tab": "chat"}, 100.5),
        _row("app.heartbeat", {"uefn_up": "yes"}, 101.0),
        _row("feature.open", {"tab": "chat"}, 99.25),
        _row("tool.call", {"ok": "yes", "tool": "t"}, 102.0),
        _row("tool.call", {"tool": "t", "ok": "yes"}, 103.0),
    ]
    answers, calls, queue, row = usage.each(lambda backend: [usage.flush(backend), usage.flush(backend)], rows)
    assert answers == [True, True] and queue == [] and row["device_key"] == "device-token-1"
    (call,) = calls
    assert call["path"] == ROUTE and call["headers"]["origin"] == usage.site.url
    assert not {"authorization", "cookie", "x-csrf-token"} & call["headers"].keys()
    body = call["body"]
    assert list(body) == ["installId", "appVersion", "platform", "events"]
    assert len(body["installId"]) == 32 and int(body["installId"], 16) >= 0 and body["platform"] in ev.PLATFORMS
    assert body["events"] == [
        {"name": "feature.open", "props": {"tab": "chat"}, "ts": 100.5, "t0": 99.25, "n": 2},
        {"name": "app.heartbeat", "props": {"uefn_up": "yes"}, "ts": 101.0, "t0": 101.0, "n": 1},
        {"name": "tool.call", "props": {"ok": "yes", "tool": "t"}, "ts": 103.0, "t0": 102.0, "n": 2},
    ]


def test_a_long_queue_goes_out_200_kinds_of_row_at_a_time(usage):
    rows = [_row("tool.call", {"tool": f"t{n}"}, 100.0 + n) for n in range(205)]
    answer, calls, queue, _ = usage.each(usage.flush, rows)
    assert answer is False and len(calls) == 1 and len(calls[0]["body"]["events"]) == 200
    assert queue == [("tool.call", {"tool": f"t{n}"}) for n in range(200, 205)]


def test_opted_out_queues_nothing_and_sends_nothing(usage):
    run = lambda backend: [usage.track(backend, "feature.open", {"tab": "chat"}), usage.flush(backend)]  # noqa: E731
    answers, calls, queue, _ = usage.each(run, _THREE[:1], on=False)
    assert answers == [False, False] and calls == [] and queue == [("feature.open", {"tab": "chat"})]


def test_signed_out_the_queue_waits_and_nothing_leaves_this_pc(usage):
    answer, calls, queue, row = usage.each(usage.flush, _THREE, signed_in=False)
    assert answer is False and calls == [] and len(queue) == 3 and row == {}


def test_a_site_that_is_down_keeps_the_queue_bounded(usage):
    now = time.time()
    rows = [_row("feature.open", {"tab": "chat"}, now + n / 1000) for n in range(5003)]
    run = lambda backend: [usage.flush(backend), usage.track(backend, "feature.open", {"tab": "plan"})]  # noqa: E731
    answers, calls, queue, _ = usage.each(run, rows, arm=usage.answers((503, {})))
    assert answers == [False, True] and len(calls) == 1
    assert len(queue) == 5000 and queue[-1] == ("feature.open", {"tab": "plan"})


_KEPT = [row["payload"]["name"] for row in _THREE]


@pytest.mark.parametrize(
    "answers, flushes, calls_n, left",
    [
        pytest.param([(503, {})], 1, 1, _KEPT, id="busy"),
        pytest.param([(404, {})], 1, 1, _KEPT, id="mid-swap"),
        pytest.param([(429, {"error": "slow down"})], 1, 1, _KEPT, id="rate-limited"),
        pytest.param([(200, {"error": "too soon"})], 1, 1, _KEPT, id="too-soon"),
        pytest.param([(200, {"payload": {"error": "try again: write failed"}})], 1, 1, _KEPT, id="store-failed"),
        pytest.param([(200, {"error": "bad install_id: zzz"})], 2, 2, _KEPT, id="refused-twice"),
        pytest.param([(200, {"error": "bad install_id: zzz"})], 3, 3, ["install_id"], id="refused-three-times"),
        pytest.param([(200, {"payload": {"error": "Unknown event: C:/Users/me"}})], 3, 3, ["unknown_event"], id="refused-in-payload"),
        pytest.param([(400, {"error": "wrong Origin"})], 3, 3, ["origin"], id="bad-request"),
        pytest.param([(401, {})], 3, 3, ["http_401"], id="no-login-asked-for"),
        pytest.param([(413, {}), (200, {})], 1, 3, [], id="too-big-then-halves-land"),
        pytest.param([(413, {})], 1, 3, ["too_big"], id="too-big-halves-refused"),
        pytest.param([(413, {}), (503, {}), (200, {})], 1, 3, _KEPT[:1], id="too-big-one-half-unanswered"),
    ],
)
def test_what_the_store_answers_decides_what_stays_queued(usage, answers, flushes, calls_n, left):
    run = lambda backend: [usage.flush(backend) for _ in range(flushes)]  # noqa: E731
    said, calls, queue, row = usage.each(run, _THREE, arm=usage.answers(*answers))
    assert said == [False] * flushes and len(calls) == calls_n
    # The login is as it was: a batch's answer never signs the PC out.
    assert row == {"base_url": usage.site.url + "/", **_LOGIN}
    if left and left[0] not in _KEPT:  # the batch was dropped; why is the one row queued
        ((name, props),) = queue
        assert (name, props["code"], props["reason"], props["where"]) == ("error.code", "usage_batch_refused", left[0], "store")
    else:
        assert [name for name, _ in queue] == left
