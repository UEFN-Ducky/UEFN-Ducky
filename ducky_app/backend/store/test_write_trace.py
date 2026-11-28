"""A slow write goes to the perf log with who held the lock and who waited (db.write_txn)."""

from __future__ import annotations

import threading
import time

import pytest

from backend.store import db

_HERE = "test_write_trace.py:"


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    db.reset_for_tests()
    yield
    db.reset_for_tests()


@pytest.fixture
def traced(monkeypatch):
    """The db_write rows this file's own writes produced, as (caller, meta)."""
    from frontend import perf_trace

    rows: list[tuple[str, dict]] = []

    def trace(kind, name="", duration_ms=0.0, **meta):
        if kind == "db_write" and name.startswith(_HERE):
            rows.append((name, {**meta, "duration_ms": duration_ms}))

    db.connect()  # migrations write before the limit drops
    monkeypatch.setattr(perf_trace, "trace", trace)
    monkeypatch.setattr(db, "WRITE_TRACE_S", 0.05)
    return rows


def test_a_write_held_past_the_limit_is_traced_with_its_caller(traced) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        time.sleep(0.12)
    assert len(traced) == 1
    caller, meta = traced[0]
    assert caller.startswith(_HERE) and int(caller.split(":")[1]) > 0
    assert meta["held_ms"] >= 100 and meta["wait_local_ms"] < 50 and meta["wait_sqlite_ms"] < 50
    assert meta["thread"] == threading.current_thread().name
    assert meta["duration_ms"] >= meta["held_ms"] - 1  # held_ms is rounded, the duration is not


def test_a_fast_write_is_not_traced(traced) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("CREATE TABLE quick(x)")
    assert traced == []


def test_a_nested_write_is_counted_once_by_the_outer_one(traced) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        with db.write_txn(conn):  # a SAVEPOINT of the outer transaction
            time.sleep(0.12)
    assert len(traced) == 1


def test_a_write_that_rolls_back_is_still_traced(traced) -> None:
    conn = db.connect()
    with pytest.raises(ValueError):
        with db.write_txn(conn):
            time.sleep(0.12)
            raise ValueError("nope")
    assert len(traced) == 1 and traced[0][1]["held_ms"] >= 100
    assert not conn.in_transaction


def test_a_writer_queued_behind_another_thread_reports_the_wait(traced) -> None:
    holding, release = threading.Event(), threading.Event()

    def holder() -> None:
        conn = db.connect()
        with db.write_txn(conn):
            holding.set()
            release.wait(5)

    thread = threading.Thread(target=holder, name="holder")
    thread.start()
    assert holding.wait(5)
    threading.Timer(0.15, release.set).start()
    conn = db.connect()
    with db.write_txn(conn):  # waits here until the holder commits
        pass
    thread.join(5)
    waits = [meta for _caller, meta in traced if meta["thread"] != "holder"]
    assert len(waits) == 1 and waits[0]["wait_local_ms"] >= 100 and waits[0]["held_ms"] < 50
    held = [meta for _caller, meta in traced if meta["thread"] == "holder"]
    assert len(held) == 1 and held[0]["held_ms"] >= 100
