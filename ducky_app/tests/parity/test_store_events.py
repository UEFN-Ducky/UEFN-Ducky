"""The events repo (rolling logs) and the perf events the Rust backend traces."""

from __future__ import annotations

import pytest

from backend.store.repos import events

from .parity_harness import plain
from .test_store_chats import _sql

_ROWS = [
    {"ts": 10.0, "source": "a", "message": "first", "payload": {"n": 1, "é": [1.5, None, "x"]}},
    {"ts": 30.0, "source": "b", "message": "third"},
    {"ts": 20.0, "source": "a", "message": "second", "payload": {}},
    {"ts": 30.0, "source": "a", "message": "fourth", "payload": ["list", 2]},
    {"message": "bare"},
    {"ts": 0, "source": None, "message": None, "payload": None},
]


def _stored() -> list:
    return _sql("SELECT kind, ts, source, message, payload FROM events ORDER BY id")


def _same_reads(rust_store, kind: str) -> None:
    for limit, since in ((100, None), (2, None), (100, 20.0), (0, None), (100, 99.0)):
        assert rust_store.store("events", "newest", kind=kind, limit=limit, since=since) == plain(
            events.newest(kind, limit=limit, since=since)
        )
        for source in ("a", "b", "", "nobody"):
            assert rust_store.store("events", "newest_from", kind=kind, source=source, limit=limit, since=since) == plain(
                events.newest_from(kind, source, limit=limit, since=since)
            )
    for limit in (0, 2, 100):
        assert rust_store.store("events", "oldest", kind=kind, limit=limit) == plain(events.oldest(kind, limit=limit))
    assert rust_store.store("events", "count", kind=kind) == events.count(kind)
    assert rust_store.store("events", "last_message", kind=kind) == events.last_message(kind)


def test_rows_written_by_one_read_the_same_in_both(rust_store) -> None:
    assert rust_store.store("events", "insert_many", kind="r", rows=_ROWS) == len(_ROWS) == events.insert_many("p", _ROWS)
    rust_store.store("events", "insert", kind="r", ts=40.5, source="s", message="one", payload={"k": "é"})
    events.insert("p", ts=40.5, source="s", message="one", payload={"k": "é"})
    rust_store.store("events", "insert", kind="r", ts=41.0)
    events.insert("p", ts=41.0)
    ours, theirs = [row[1:] for row in _stored() if row[0] == "r"], [row[1:] for row in _stored() if row[0] == "p"]
    assert ours == theirs and len(ours) == len(_ROWS) + 2
    _sql("INSERT INTO events(kind, ts, source, message, payload) VALUES ('r', 5, 'a', 'not json', '{oops'), ('p', 5, 'a', 'not json', '{oops')")
    for kind in ("r", "p", "none"):
        _same_reads(rust_store, kind)
    assert rust_store.store("events", "newest", kind="r", limit=100) == plain(events.newest("p", limit=100))


@pytest.mark.parametrize(("older_than", "keep"), [(0.0, 100), (25.0, 100), (0.0, 3), (25.0, 1), (0.0, 0), (99.0, 5), (0.0, -1)])
def test_a_trim_drops_the_same_rows(rust_store, older_than: float, keep: int) -> None:
    for kind in ("r", "p", "other"):
        events.insert_many(kind, _ROWS)
    assert rust_store.store("events", "trim", kind="r", older_than=older_than, keep=keep) == events.trim(
        "p", older_than=older_than, keep=keep
    )
    assert plain(events.newest("r", limit=100)) == plain(events.newest("p", limit=100))
    assert events.count("other") == len(_ROWS)


def test_a_long_trim_is_done_in_slices(rust_store) -> None:
    events.insert_many("perf", [{"ts": float(i), "message": str(i)} for i in range(2500)])
    events.insert_many("other", _ROWS)
    assert rust_store.store("events", "trim", kind="perf", older_than=1200.0, keep=300) == 2200  # 1,200 by age, 1,000 past the newest 300
    assert [row["message"] for row in events.oldest("perf", limit=1)] == ["2200"] and events.count("perf") == 300
    assert events.count("other") == len(_ROWS)


def test_clearing_and_deleting_by_id(rust_store) -> None:
    for kind in ("r", "p", "kept"):
        events.insert_many(kind, _ROWS)
    ours, theirs = (events.oldest(kind, limit=3) for kind in ("r", "p"))
    assert rust_store.store("events", "delete_ids", ids=[row["id"] for row in ours] + [999_999]) == 3
    assert events.delete_ids([row["id"] for row in theirs] + [999_999]) == 3
    assert rust_store.store("events", "delete_ids", ids=[]) == 0 == events.delete_ids([])
    assert plain(events.newest("r", limit=100)) == plain(events.newest("p", limit=100)) and events.count("r") == 3
    rust_store.store("events", "clear", kind="r")
    events.clear("p")
    assert events.count("r") == 0 == events.count("p") and events.count("kept") == len(_ROWS)


def test_what_rust_traces_reads_as_a_python_perf_row(rust_store) -> None:
    from frontend import perf_trace

    rust_store.store("perf", "trace", kind="rs_slow", name="/api/chats", duration_ms=2100.4567, meta={"route": "/api/chats", "skip": None})
    rust_store.store("perf", "trace", kind="rs_slow", name="/api/fast", duration_ms=3.0, meta={})  # not notable: never stored
    rust_store.store("perf", "trace", kind="conv_save", name="", duration_ms=80.0, meta={"queue_depth": 2})
    assert events.count("perf") == 0, "rows wait for the flush"
    assert rust_store.store("perf", "flush") == 2 and rust_store.store("perf", "flush") == 0
    newest, slow = events.newest("perf", limit=10)
    assert slow["message"] == "/api/chats" and slow["source"].startswith("session-rs-")
    assert slow["payload"] == {"ts": slow["ts"], "kind": "rs_slow", "name": "/api/chats", "duration_ms": 2100.457, "route": "/api/chats"}
    assert newest["payload"]["name"] == "conv_save" and newest["payload"]["queue_depth"] == 2
    # The per-process report reads them like any Python process's rows.
    assert perf_trace._pid_of(slow["source"]) == rust_store.proc.pid
    block = perf_trace.per_process_block([newest, slow], 1.0)
    assert block["conv_saves"] == 1 and block["conv_save_max_ms"] == 80.0
