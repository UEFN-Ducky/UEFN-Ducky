"""The schema upgrade: Rust's migrator against Python's, and Python handing the upgrade over."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from backend.store import db

from .parity_harness import RustBackend, backend_exe


@pytest.fixture
def exe() -> Path:
    found = backend_exe()
    if found is None:
        pytest.skip("Ducky-Backend is not built: run `cargo build -p backend` in native_launcher/")
    return found


def _open(path: Path) -> sqlite3.Connection:
    """A connection set up as the app's are (foreign keys on, WAL)."""
    raw = sqlite3.connect(str(path), isolation_level=None)
    db._configure(raw, True)
    return raw


def _state(path: Path) -> list:
    """Everything an upgrade leaves: the schema text, its version and what the tables hold."""
    raw = sqlite3.connect(str(path), isolation_level=None)
    try:
        schema = raw.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name").fetchall()
        return [db.user_version(raw), schema, db.fingerprint_summary(raw)]
    finally:
        raw.close()


def _copies(folder: Path) -> list[str]:
    return sorted(p.name.split("-", 3)[3] for p in db.snapshot_dir(folder).glob("ducky-*.db"))


def _never(*_args, **_kwargs):
    raise AssertionError("Python must not upgrade the schema itself")


def test_every_older_schema_upgrades_to_the_same_database(exe: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(db.BACKEND_EXE_ENV, raising=False)
    files = db.migration_files()
    head = len(files)
    assert head >= 28
    for version in range(head):
        ours, theirs = tmp_path / f"v{version}" / "rust", tmp_path / f"v{version}" / "python"
        theirs.mkdir(parents=True)
        raw = _open(theirs / db.DB_NAME)
        with monkeypatch.context() as patch:  # the database as the app at that version left it
            patch.setattr(db, "migration_files", lambda: files[:version])
            assert db.migrate(raw) == version
        if version:
            raw.execute("INSERT INTO settings(key, value, updated) VALUES ('theme', '\"dark\"', 7)")
        raw.close()
        shutil.copytree(theirs, ours)

        raw = _open(theirs / db.DB_NAME)
        assert db.migrate(raw) == head
        raw.close()
        ran = subprocess.run([str(exe), "migrate", str(ours / db.DB_NAME)], capture_output=True, text=True)
        assert (ran.returncode, json.loads(ran.stdout)) == (0, {"ok": True, "version": head}), ran.stderr

        assert _state(ours / db.DB_NAME) == _state(theirs / db.DB_NAME), f"from schema {version}"
        # Each made one copy of what was there before, and none of an empty file.
        expected = [f"pre-migrate-v{version}.db"] if version else []
        assert _copies(ours) == _copies(theirs) == expected


def test_the_command_refuses_what_it_cannot_do(exe: Path, tmp_path: Path) -> None:
    path = tmp_path / db.DB_NAME

    def run(*args: str) -> tuple[int, dict]:
        ran = subprocess.run([str(exe), "migrate", *args], capture_output=True, text=True)
        return ran.returncode, json.loads(ran.stdout)

    head = db.head_version()
    assert run(str(path), "--to", "4") == (0, {"ok": True, "version": 4})
    assert run(str(path), "--to", str(head + 1)) == (1, {"ok": False, "error": f"this Ducky-Backend knows schema up to {head}, not {head + 1}"})
    code, reply = run(str(path), "--to", "2")
    assert code == 1 and "written by a newer UEFN-Ducky" in reply["error"]
    assert run() == (1, {"ok": False, "error": "usage: Ducky-Backend migrate <ducky.db> [--to N]"})


def test_python_hands_the_upgrade_to_the_rust_migrator(exe: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("DUCKY_TESTS_ISOLATED_APPDATA", "1")
    monkeypatch.setenv(db.BACKEND_EXE_ENV, str(exe))
    monkeypatch.setattr(db, "_split_statements", _never)
    monkeypatch.setattr(db, "_pre_migrate_copy", _never)
    files = db.migration_files()
    head = len(files)
    db.reset_for_tests()
    try:
        with monkeypatch.context() as patch:  # an app two schema steps ago
            patch.setattr(db, "migration_files", lambda: files[: head - 2])
            conn = db.connect()
            assert db.user_version(conn) == head - 2
            conn.execute("INSERT INTO settings(key, value, updated) VALUES ('theme', '\"dark\"', 7)")
            assert _copies(db.app_root()) == []
        db.reset_for_tests()
        conn = db.connect()
        assert db.user_version(conn) == head
        assert conn.execute("SELECT value FROM settings WHERE key='theme'").fetchone()[0] == '"dark"'
        assert _copies(db.app_root()) == [f"pre-migrate-v{head - 2}.db"]
        # A file from a newer app is still refused here, before anything is run.
        conn.execute(f"PRAGMA user_version={head + 1}")
        with pytest.raises(db.NewerSchemaError):
            db.migrate(conn)
        conn.execute(f"PRAGMA user_version={head}")
    finally:
        db.reset_for_tests()


def test_python_keeps_saying_it_is_still_at_it_and_reports_a_failure(tmp_path: Path, monkeypatch) -> None:
    """The launcher ends a backend that says nothing for 20 s; a large copy takes longer."""
    script = tmp_path / "migrator.py"
    script.write_text("import time\ntime.sleep(0.5)\nprint('{\"ok\": true, \"version\": 0}')\n", encoding="utf-8")
    raw = sqlite3.connect(str(tmp_path / "scratch.db"), isolation_level=None)
    ticks: list[int] = []
    monkeypatch.setattr(db, "COPY_TICK_S", 0.05)
    monkeypatch.setattr(db, "still_copying", lambda: ticks.append(1))
    try:
        assert db._rust_migrate(raw, [sys.executable, str(script)], 0) == 0
        assert len(ticks) >= 2
        with pytest.raises(db.StoreError, match="left ducky.db at schema 0, not 3"):
            db._rust_migrate(raw, [sys.executable, str(script)], 3)
        script.write_text("import sys\nprint('{\"ok\": false, \"error\": \"the disk is full\"}')\nsys.exit(1)\n", encoding="utf-8")
        with pytest.raises(db.StoreError, match="could not upgrade ducky.db: the disk is full"):
            db._rust_migrate(raw, [sys.executable, str(script)], 0)
        script.write_text("raise SystemExit('no such table')\n", encoding="utf-8")
        with pytest.raises(db.StoreError, match="could not upgrade ducky.db: no such table"):
            db._rust_migrate(raw, [sys.executable, str(script)], 0)
    finally:
        raw.close()


def test_without_the_rust_backend_python_still_upgrades(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(db.BACKEND_EXE_ENV, str(tmp_path / "nope"))
    assert db.rust_migrator() is None
    monkeypatch.delenv(db.BACKEND_EXE_ENV)
    assert db.rust_migrator() is None  # a source run only uses the one it is pointed at
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "UEFN-Ducky.exe"))
    assert db.rust_migrator() is None
    beside = tmp_path / ("Ducky-Backend.exe" if sys.platform == "win32" else "Ducky-Backend")
    beside.write_bytes(b"")
    assert db.rust_migrator() == beside


def test_the_rust_backend_waits_while_python_is_still_opening(rust_store) -> None:
    answers: list = []
    caller = threading.Thread(target=lambda: answers.append(rust_store.store("account_rows", "claimer")))
    with db._first_open_lock(db.app_root()):
        caller.start()
        caller.join(0.6)
        assert caller.is_alive() and not answers
    caller.join(20)
    assert answers == [""]


def test_rust_makes_the_database_when_it_opens_it_first(exe: Path, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("DUCKY_TESTS_ISOLATED_APPDATA", "1")
    monkeypatch.delenv(db.BACKEND_EXE_ENV, raising=False)
    db.reset_for_tests()
    backend = RustBackend(exe)
    try:
        assert not db.db_path().exists()
        assert backend.store("account_rows", "claimer") == ""
        monkeypatch.setattr(db, "_split_statements", _never)
        conn = db.connect()  # nothing left for Python to do
        assert db.user_version(conn) == db.head_version()
        assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 0
    finally:
        backend.close()
        db.reset_for_tests()
