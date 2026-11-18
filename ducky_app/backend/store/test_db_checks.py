"""Start-up checks of ducky.db: quick check per start, full check weekly."""

from __future__ import annotations

import time

import pytest

from backend.store import db


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    db.reset_for_tests()
    yield
    db.reset_for_tests()


def _count_full(monkeypatch, result: str = "ok") -> list[int]:
    calls: list[int] = []
    real = db.integrity_check

    def counted(conn):
        calls.append(1)
        return real(conn) if result == "ok" else result

    monkeypatch.setattr(db, "integrity_check", counted)
    return calls


def test_a_start_within_a_week_runs_the_quick_check_only(monkeypatch) -> None:
    full = _count_full(monkeypatch)
    db.open_checked()
    db.reset_for_tests()
    db.open_checked()
    assert len(full) == 1  # the first start ever; the second the same day is quick only


def test_a_start_after_a_week_runs_the_full_check(monkeypatch) -> None:
    conn = db.connect()
    old = time.time() - db.FULL_CHECK_EVERY_S - 60
    with db.write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value, updated) VALUES (?, ?, 0)", (db.FULL_CHECK_KEY, str(old))
        )
    full = _count_full(monkeypatch)
    conn = db.open_checked()
    assert len(full) == 1
    stamp = float(conn.execute("SELECT value FROM meta WHERE key=?", (db.FULL_CHECK_KEY,)).fetchone()[0])
    assert time.time() - stamp < 60


def test_a_failed_quick_check_runs_the_full_one(monkeypatch) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value, updated) VALUES (?, ?, 0)", (db.FULL_CHECK_KEY, str(time.time()))
        )
    monkeypatch.setattr(db, "quick_check", lambda conn: "*** in database main *** Page 7: btreeInitPage() error")
    full = _count_full(monkeypatch, result="row 3 missing from index")

    class Restored(Exception):
        pass

    def restore(root=None):
        raise Restored

    monkeypatch.setattr(db, "restore_snapshot", restore)
    with pytest.raises(Restored):
        db.open_checked()
    assert len(full) == 1  # the full check ran at once, then the recovery path
