"""Start-up checks of ducky.db: quick check per start, full check weekly."""

from __future__ import annotations

import json
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


def test_a_failed_backup_is_logged_and_reported(tmp_path, monkeypatch, caplog) -> None:
    conn = db.connect()
    head = db.head_version()
    extra = tmp_path / f"{head + 1:04d}_probe.sql"
    extra.write_text("CREATE TABLE probe_after_upgrade(x INTEGER);\n", encoding="utf-8")
    files = db.migration_files()
    monkeypatch.setattr(db, "migration_files", lambda: [*files, (head + 1, extra)])

    def full_disk(conn, current):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(db, "_pre_migrate_copy", full_disk)
    with caplog.at_level("WARNING", logger=db._log.name):
        assert db.migrate(conn) == head + 1
    assert any(r.getMessage() == "pre-migration backup failed" and r.exc_info for r in caplog.records)
    note = json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (db.BACKUP_FAILED_KEY,)).fetchone()[0])
    assert note["from"] == head and note["to"] == head + 1 and "No space" in note["error"]
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='probe_after_upgrade'").fetchone()


def _count_connects(monkeypatch) -> list[str]:
    """The checkpointer's own opens: it always opens a ``file:...?mode=rw`` URI. A plain-path
    db.connect() on some other thread (one an earlier test left running, under a loaded
    release run) is not the checkpointer and used to fail this with 2 == 1."""
    opened: list[str] = []
    real = db.sqlite3.connect

    def counted(*args, **kwargs):
        if kwargs.get("uri") and str(args[0]).endswith("?mode=rw"):
            opened.append(str(args[0]))
        return real(*args, **kwargs)

    monkeypatch.setattr(db.sqlite3, "connect", counted)
    return opened


def test_the_checkpointer_opens_one_connection(monkeypatch) -> None:
    db.connect()
    path = str(db.db_path())
    opened = _count_connects(monkeypatch)
    for _ in range(5):
        db.checkpoint_passive(path)
    assert len(opened) == 1
    db.close_thread_connections()  # a file move closes it
    assert not db._ckpt_conns


def test_it_reopens_after_an_error(monkeypatch) -> None:
    db.connect()
    path = str(db.db_path())
    opened = _count_connects(monkeypatch)
    db.checkpoint_passive(path)

    class Broken:
        def execute(self, *a):
            raise db.sqlite3.OperationalError("disk I/O error")

        def close(self):
            pass

    with db._ckpt_open:
        db._ckpt_conns[path].close()
        db._ckpt_conns[path] = Broken()
    db.checkpoint_passive(path)  # fails, drops the broken one
    assert path not in db._ckpt_conns
    db.checkpoint_passive(path)
    db.checkpoint_passive(path)
    assert len(opened) == 2
