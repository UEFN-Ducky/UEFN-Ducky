"""Layer 1: the store core (ADR 0003)."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from backend.store import db


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    db.reset_for_tests()
    yield
    db.reset_for_tests()


def test_connect_creates_wal_database_at_head_version(tmp_path: Path) -> None:
    conn = db.connect()
    path = tmp_path / "UEFN-Ducky" / db.DB_NAME
    assert path.is_file()
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.user_version(conn) == db.head_version() >= 1
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "projects", "settings"} <= names


def test_capabilities_present() -> None:
    conn = db.connect()
    db.assert_capabilities(conn)
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    conn.execute("INSERT INTO t(x) VALUES ('hello duck')")
    assert conn.execute("SELECT count(*) FROM t WHERE t MATCH 'duck'").fetchone()[0] == 1


def test_one_connection_per_thread_same_path() -> None:
    a = db.connect()
    assert db.connect() is a
    seen: list[sqlite3.Connection] = []

    def worker() -> None:
        seen.append(db.connect())
        db.close_thread_connections()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen and seen[0] is not a


def test_repointing_localappdata_gives_a_fresh_database(tmp_path: Path, monkeypatch) -> None:
    conn_a = db.connect()
    with db.write_txn(conn_a):
        conn_a.execute("INSERT INTO settings(key, value, updated) VALUES ('k', '1', 0)")
    other = tmp_path / "other"
    monkeypatch.setenv("LOCALAPPDATA", str(other))
    conn_b = db.connect()
    assert conn_b is not conn_a
    assert conn_b.execute("SELECT count(*) FROM settings").fetchone()[0] == 0


def test_write_txn_commits_and_rolls_back() -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("INSERT INTO settings(key, value, updated) VALUES ('a', '1', 0)")
    with pytest.raises(RuntimeError):
        with db.write_txn(conn):
            conn.execute("INSERT INTO settings(key, value, updated) VALUES ('b', '1', 0)")
            raise RuntimeError("boom")
    keys = [r[0] for r in conn.execute("SELECT key FROM settings ORDER BY key")]
    assert keys == ["a"]
    assert conn.in_transaction is False


def test_data_version_changes_when_another_connection_commits(tmp_path: Path) -> None:
    conn = db.connect()
    v0 = db.data_version(conn)
    other = sqlite3.connect(str(db.db_path()), isolation_level=None)
    other.execute("INSERT INTO settings(key, value, updated) VALUES ('x', '1', 0)")
    other.close()
    assert db.data_version(conn) != v0


def test_newer_schema_is_refused() -> None:
    conn = db.connect()
    conn.execute(f"PRAGMA user_version={db.head_version() + 1}")
    with pytest.raises(db.NewerSchemaError):
        db.migrate(conn)


def test_unc_path_is_refused() -> None:
    with pytest.raises(db.UnsupportedFilesystemError):
        db.connect(Path(r"\\server\share\UEFN-Ducky"))


def test_migration_files_are_contiguous_and_apply_from_zero(tmp_path: Path) -> None:
    files = db.migration_files()
    assert [n for n, _ in files] == list(range(1, len(files) + 1))
    raw = sqlite3.connect(str(tmp_path / "scratch.db"), isolation_level=None)
    assert db.migrate(raw) == db.head_version()
    assert db.migrate(raw) == db.head_version()  # idempotent
    raw.close()


def test_split_statements_keeps_triggers_whole() -> None:
    sql = (
        "-- comment\n"
        "CREATE TABLE a(x);\n\n"
        "CREATE TRIGGER t AFTER INSERT ON a BEGIN\n  INSERT INTO a(x) VALUES (1);\nEND;\n"
    )
    parts = db._split_statements(sql)
    assert len(parts) == 2 and parts[1].startswith("CREATE TRIGGER")


def test_integrity_ok_and_snapshot_keeps_newest_three(tmp_path: Path) -> None:
    conn = db.connect()
    assert db.integrity_check(conn) == "ok"
    made = [db.snapshot(label=f"s{i}") for i in range(5)]
    assert all(p.is_file() for p in made[-3:])
    kept = sorted(db.snapshot_dir().glob("ducky-*.db"))
    assert len(kept) == db.KEEP_SNAPSHOTS
    assert db.newest_snapshot() is not None


def test_corrupt_database_is_restored_from_snapshot(tmp_path: Path) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("INSERT INTO settings(key, value, updated) VALUES ('keep', '1', 0)")
    db.snapshot(label="good")
    db.reset_for_tests()
    path = db.db_path()
    data = bytearray(path.read_bytes())
    data[:100] = b"\0" * 100
    path.write_bytes(bytes(data))
    for side in db.SIDECAR_NAMES:
        (path.parent / side).unlink(missing_ok=True)
    conn = db.open_checked()
    assert conn.execute("SELECT value FROM settings WHERE key='keep'").fetchone()[0] == "1"
    assert (path.parent / (db.DB_NAME + ".corrupt")).exists()
    note = conn.execute("SELECT value FROM meta WHERE key='last_restore'").fetchone()[0]
    assert "good" in note


def test_corrupt_database_without_snapshot_is_loud(tmp_path: Path) -> None:
    db.connect()
    db.reset_for_tests()
    path = db.db_path()
    path.write_bytes(b"not a database at all")
    for side in db.SIDECAR_NAMES:
        (path.parent / side).unlink(missing_ok=True)
    with pytest.raises(db.StoreError):
        db.open_checked()


def test_is_store_file_names_every_sidecar() -> None:
    for name in ("ducky.db", "ducky.db-wal", "ducky.db-shm", "snapshots"):
        assert db.is_store_file(name)
    assert not db.is_store_file("panel_settings.json")


def test_cached_connect_does_no_filesystem_work(tmp_path: Path, monkeypatch) -> None:
    """The connection cache must not stat/resolve before it can look itself up.

    ``connect()`` computed its cache key with ``path.parent.exists()`` plus
    ``path.resolve()`` on every call — three syscalls, ~0.5 ms on Windows, ahead
    of the dict lookup. Every settings read in the app paid it, and the sidebar's
    1.5 s tree poll paid it twice per file on screen.
    """
    db.connect()  # first open: resolving is expected

    calls: list[str] = []
    real_resolve = Path.resolve
    real_exists = Path.exists
    monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: (calls.append("resolve"), real_resolve(self, *a, **k))[1])
    monkeypatch.setattr(Path, "exists", lambda self, *a, **k: (calls.append("exists"), real_exists(self, *a, **k))[1])

    for _ in range(50):
        db.connect()

    assert calls == [], f"cached connect() still touched the filesystem: {calls[:6]}"


def test_repointed_appdata_still_opens_its_own_database(tmp_path: Path, monkeypatch) -> None:
    """Memoising the key must not pin one process to the first database it saw."""
    first = db.connect()
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("LOCALAPPDATA", str(other))
    second = db.connect()
    assert second is not first
    assert (other / "UEFN-Ducky" / db.DB_NAME).is_file()


def test_content_addressed_fixtures_hash_to_their_own_filenames() -> None:
    """A blob fixture's name is the hash of its bytes.

    `* text=auto` in .gitattributes checked these out with CRLF on Windows, so
    every hash was wrong. The legacy importer then stored a blob whose text did
    not match its hash, and the next INSERT OR IGNORE under that hash kept the
    corrupt row — surfacing three layers away as "history import verification
    failed". .gitattributes pins them with -text; this fails loudly if that slips.
    """
    from backend.workspace.paths import content_hash

    blobs = Path(__file__).resolve().parent / "fixtures" / "legacy" / "blobs"
    files = sorted(blobs.glob("*.txt"))
    assert files, f"no blob fixtures found under {blobs}"
    for path in files:
        text = path.read_bytes().decode("utf-8")
        assert content_hash(text) == path.stem, (
            f"{path.name} does not hash to its own name — line endings mangled on checkout?"
        )
