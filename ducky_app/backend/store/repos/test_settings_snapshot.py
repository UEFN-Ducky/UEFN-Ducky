"""The settings snapshot: one table read while nothing changed, on any thread."""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest

from backend.store import db
from backend.store.repos import settings as repo


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    db.reset_for_tests()
    repo.reset_for_tests()
    yield
    repo.reset_for_tests()
    db.reset_for_tests()


@pytest.fixture
def reads(monkeypatch) -> list[int]:
    count: list[int] = []
    real = repo._read_rows

    def counted(conn):
        count.append(1)
        return real(conn)

    monkeypatch.setattr(repo, "_read_rows", counted)
    return count


def test_two_threads_share_one_snapshot_without_rereading(reads) -> None:
    repo.save_fields({"theme": "dark"}, {"theme": "light"})
    reads.clear()
    conn = db.connect()
    with db.write_txn(conn):  # unrelated commits on this process's other connections
        conn.execute("INSERT OR REPLACE INTO meta(key, value, updated) VALUES ('noise', '1', 0)")

    def worker() -> None:
        for _ in range(50):
            assert repo.load_fields()["theme"] == "dark"
            other = db.connect()
            with db.write_txn(other):
                other.execute("INSERT OR REPLACE INTO meta(key, value, updated) VALUES ('noise', '2', 0)")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(reads) == 0  # the save's own snapshot served all 100 loads
    repo.reset_for_tests()
    for _ in range(100):
        repo.load_fields()
    assert len(reads) == 1


def test_a_save_in_this_process_is_seen_at_once(reads) -> None:
    defaults = {"theme": "light"}
    rows, version = repo.load_fields_versioned()
    repo.save_fields({"theme": "dark"}, defaults, origin_version=version)
    seen: list[str] = []
    t = threading.Thread(target=lambda: seen.append(repo.load_fields().get("theme")))
    t.start()
    t.join()
    assert seen == ["dark"] and repo.load_fields()["theme"] == "dark"


def test_a_save_by_another_connection_is_seen(reads) -> None:
    repo.save_fields({"theme": "dark"}, {"theme": "light"})
    assert repo.load_fields()["theme"] == "dark"
    other = sqlite3.connect(str(db.db_path()))
    try:
        other.execute(
            "UPDATE settings SET value=?, updated=? WHERE key='theme'", (json.dumps("blue"), time.time() + 1)
        )
        other.commit()
    finally:
        other.close()
    assert repo.load_fields()["theme"] == "blue"
