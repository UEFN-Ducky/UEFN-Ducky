"""``ducky.db`` — open, configure, migrate, snapshot (ADR 0003).

One connection per thread, WAL, ``busy_timeout``; writers use ``BEGIN
IMMEDIATE`` through :func:`write_txn`. Schema version is ``PRAGMA
user_version``; migrations are numbered SQL files under ``migrations/`` applied
forward-only, one transaction each. A database written by a newer app is
refused, never downgraded.

This module is the only place in the tree allowed to import :mod:`sqlite3`
(``test_fitness.py``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DB_NAME = "ducky.db"
SIDECAR_NAMES = (DB_NAME + "-wal", DB_NAME + "-shm")
SNAPSHOT_DIR_NAME = "snapshots"
KEEP_SNAPSHOTS = 3
BUSY_TIMEOUT_MS = 5000

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

REQUIRED_COMPILE_OPTIONS = ("ENABLE_FTS5", "ENABLE_JSON1")
# JSON1 is built in unconditionally from SQLite 3.38 and no longer listed as a
# compile option; we probe json_extract() directly instead of the flag.


class StoreError(RuntimeError):
    """Raised for refusals: UNC path, newer schema, failed integrity."""


class NewerSchemaError(StoreError):
    pass


class UnsupportedFilesystemError(StoreError):
    pass


_thread_local = threading.local()
_guard = threading.Lock()
_log = logging.getLogger("uefn_ducky.store")
_migrated: set[str] = set()


# --------------------------------------------------------------------------- paths


def app_root() -> Path:
    from frontend.app_paths import resolve_app_data_dir

    return resolve_app_data_dir()


def db_path(root: Path | None = None) -> Path:
    return (root or app_root()) / DB_NAME


def snapshot_dir(root: Path | None = None) -> Path:
    return (root or app_root()) / SNAPSHOT_DIR_NAME


def _refuse_network_path(path: Path) -> None:
    s = str(path)
    if s.startswith("\\\\") or s.startswith("//"):
        raise UnsupportedFilesystemError(
            f"ducky.db cannot live on a network path ({s}); WAL mode needs a local disk."
        )


# --------------------------------------------------------------------------- connect


RESTORE_PENDING_NAME = DB_NAME + ".restore-pending"


def _apply_pending_restore(path: Path) -> None:
    """Settings → Database → Restore stages a snapshot as ``ducky.db.restore-pending``;
    the first process to open the store after that swaps it in. If another
    process still holds the live file the swap fails and stays pending."""
    pending = path.parent / RESTORE_PENDING_NAME
    if not pending.is_file():
        return
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if path.exists():
            path.replace(path.parent / f"{DB_NAME}.replaced-{stamp}")
        for name in SIDECAR_NAMES:
            side = path.parent / name
            if side.exists():
                side.unlink()
        pending.replace(path)
        _log.warning("ducky.db restored from a staged snapshot (previous file kept as %s.replaced-%s)", DB_NAME, stamp)
    except OSError as exc:
        _log.warning("staged ducky.db restore could not be applied yet: %s", exc)


DatabaseError = sqlite3.DatabaseError


def sqlite_library_version() -> str:
    return sqlite3.sqlite_version


def check_db_file(path: Path) -> str:
    """``PRAGMA integrity_check`` on a database file that is not the live store (read-only)."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "no result"
    finally:
        conn.close()


def record_integrity(conn: sqlite3.Connection, result: str) -> None:
    with write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value, updated) VALUES ('last_integrity', ?, ?)",
            (json.dumps({"result": result, "ts": time.time()}), time.time()),
        )


def _refuse_real_appdata_under_pytest(path: Path) -> None:
    """A test that escapes the conftest redirect must fail loudly instead of
    migrating, importing into, or pruning the developer's live database.
    ``DUCKY_TESTS_REAL_APPDATA=1`` opts out deliberately."""
    if "PYTEST_CURRENT_TEST" not in os.environ or os.environ.get("DUCKY_TESTS_REAL_APPDATA") == "1":
        return
    try:
        real = (Path.home() / "AppData" / "Local" / "UEFN-Ducky").resolve()
        here = path.parent.resolve() if path.parent.exists() else path.parent
    except OSError:
        return
    if here == real:
        raise RuntimeError(
            f"refusing to open the real ducky.db ({path}) from a test; run pytest from the repo root "
            "so conftest.py redirects LOCALAPPDATA, or set DUCKY_TESTS_REAL_APPDATA=1 on purpose"
        )


def connect(root: Path | None = None) -> sqlite3.Connection:
    """Return this thread's connection to ``ducky.db``, migrating on first open.

    Connections are cached per thread and per resolved path, so a test that
    repoints ``LOCALAPPDATA`` gets a fresh database.
    """
    path = db_path(root)
    key = str(path.resolve()) if path.parent.exists() else str(path)
    cache: dict[str, sqlite3.Connection] = getattr(_thread_local, "conns", None) or {}
    conn = cache.get(key)
    if conn is not None:
        return conn
    _refuse_network_path(path)
    _refuse_real_appdata_under_pytest(path)
    if key not in _migrated:
        _apply_pending_restore(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.row_factory = sqlite3.Row
    if os.environ.get("DUCKY_DB_TRACE"):
        _install_trace(conn, path)
    try:
        _configure(conn)
        with _guard:
            if key not in _migrated:
                migrate(conn)
                _migrated.add(key)
    except BaseException:
        conn.close()  # never leave a corrupt file held open (restore must rename it)
        raise
    cache[key] = conn
    _thread_local.conns = cache
    return conn


def _install_trace(conn: sqlite3.Connection, path: Path) -> None:
    """DUCKY_DB_TRACE=1: print every mutating statement with its thread (diagnostics)."""
    import sys

    def _trace(sql: str) -> None:
        head = sql.lstrip()[:6].upper()
        if head.startswith(("INSERT", "UPDATE", "DELETE")):
            line = f"[ducky.db {threading.current_thread().name} {path.parent.parent.name}] {sql[:160]}"
            sys.stderr.write(line + chr(10))

    conn.set_trace_callback(_trace)


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-32768")
    assert_capabilities(conn)


def assert_capabilities(conn: sqlite3.Connection) -> None:
    opts = {r[0] for r in conn.execute("PRAGMA compile_options")}
    if "ENABLE_FTS5" not in opts:
        raise StoreError("this SQLite build lacks FTS5; chat and memory search need it")
    try:
        conn.execute("SELECT json_extract('{\"a\":1}', '$.a')").fetchone()
    except sqlite3.OperationalError as exc:  # pragma: no cover - defensive
        raise StoreError("this SQLite build lacks JSON functions") from exc


def close_thread_connections() -> None:
    cache: dict[str, sqlite3.Connection] = getattr(_thread_local, "conns", None) or {}
    for conn in cache.values():
        try:
            conn.close()
        except Exception:
            pass
    _thread_local.conns = {}


def reset_for_tests() -> None:
    """Drop cached connections and the migrated-path memo (tests only)."""
    close_thread_connections()
    with _guard:
        _migrated.clear()


# --------------------------------------------------------------------------- transactions


@contextmanager
def write_txn(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """``BEGIN IMMEDIATE`` … ``COMMIT``; rolls back on any exception.

    Never hold one of these across an HTTP, LLM or listener call.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def data_version(conn: sqlite3.Connection) -> int:
    """Changes whenever another connection commits; cheap cache revalidation."""
    return int(conn.execute("PRAGMA data_version").fetchone()[0])


# --------------------------------------------------------------------------- migrations


def migration_files() -> list[tuple[int, Path]]:
    out: list[tuple[int, Path]] = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _MIGRATION_RE.match(p.name)
        if not m:
            raise StoreError(f"bad migration filename: {p.name}")
        out.append((int(m.group(1)), p))
    nums = [n for n, _ in out]
    if nums != list(range(1, len(nums) + 1)):
        raise StoreError(f"migrations must be numbered 1..N without gaps, got {nums}")
    return out


def head_version() -> int:
    files = migration_files()
    return files[-1][0] if files else 0


def user_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations forward. Returns the resulting ``user_version``."""
    current = user_version(conn)
    head = head_version()
    if current > head:
        raise NewerSchemaError(
            f"ducky.db is at schema {current} but this app knows up to {head}; "
            "it was written by a newer UEFN-Ducky. Update the app instead of downgrading."
        )
    if current == head:
        return current
    if current > 0:
        try:
            snapshot(Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent, label="pre-migrate")
        except Exception:
            pass
    for number, path in migration_files():
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Not executescript(): it would COMMIT behind our back.
            for statement in _split_statements(sql):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={number}")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        current = number
    return current


def _split_statements(sql: str) -> list[str]:
    """Split a migration file into statements; ``sqlite3.complete_statement`` aware."""
    out: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("--")):
            continue
        buf.append(line)
        candidate = "\n".join(buf)
        if sqlite3.complete_statement(candidate):
            out.append(candidate.strip())
            buf = []
    if "".join(buf).strip():
        raise StoreError("migration ends with an incomplete statement")
    return out


# --------------------------------------------------------------------------- health


def integrity_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "no result"


def snapshot(root: Path | None = None, *, label: str = "") -> Path:
    """``VACUUM INTO`` a consistent copy under ``snapshots/``; keeps the newest N."""
    root = root or app_root()
    target_dir = snapshot_dir(root)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    suffix = f"-{label}" if label else ""
    target = target_dir / f"ducky-{stamp}{suffix}.db"
    n = 1
    while target.exists():
        n += 1
        target = target_dir / f"ducky-{stamp}{suffix}-{n}.db"
    conn = connect(root)
    conn.execute("VACUUM INTO ?", (str(target),))
    _prune_snapshots(target_dir)
    return target


def _prune_snapshots(target_dir: Path) -> None:
    files = sorted(target_dir.glob("ducky-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP_SNAPSHOTS:]:
        try:
            old.unlink()
        except OSError:
            pass


def newest_snapshot(root: Path | None = None) -> Path | None:
    d = snapshot_dir(root)
    if not d.is_dir():
        return None
    files = sorted(d.glob("ducky-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def restore_snapshot(root: Path | None = None) -> Path:
    """Replace a corrupt ``ducky.db`` with the newest snapshot. Loud on failure."""
    root = root or app_root()
    src = newest_snapshot(root)
    if src is None:
        raise StoreError("ducky.db failed its integrity check and no snapshot exists to restore")
    reset_for_tests()
    dest = db_path(root)
    for name in (DB_NAME, *SIDECAR_NAMES):
        p = root / name
        if p.exists():
            p.replace(root / (name + ".corrupt"))
    shutil.copy2(src, dest)
    return src


def open_checked(root: Path | None = None) -> sqlite3.Connection:
    """Boot-time open: integrity check, restore from snapshot if it fails."""
    root = root or app_root()
    try:
        conn = connect(root)
        result = integrity_check(conn)
    except sqlite3.DatabaseError as exc:
        result = f"error: {exc}"
    if result == "ok":
        return conn
    restored = restore_snapshot(root)
    conn = connect(root)
    if integrity_check(conn) != "ok":
        raise StoreError(f"ducky.db is corrupt and the snapshot {restored.name} is too")
    _record_restore(conn, restored, result)
    return conn


def _record_restore(conn: sqlite3.Connection, restored: Path, why: str) -> None:
    with write_txn(conn):
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value, updated) VALUES ('last_restore', ?, ?)",
            (f"{restored.name}: {why}", time.time()),
        )


def is_store_file(name: str) -> bool:
    """True for the DB, its sidecars and the snapshots dir (AppData maintenance)."""
    return name in (DB_NAME, *SIDECAR_NAMES, SNAPSHOT_DIR_NAME)


__all__ = [
    "DB_NAME",
    "SIDECAR_NAMES",
    "SNAPSHOT_DIR_NAME",
    "StoreError",
    "NewerSchemaError",
    "UnsupportedFilesystemError",
    "app_root",
    "db_path",
    "connect",
    "close_thread_connections",
    "reset_for_tests",
    "write_txn",
    "data_version",
    "migrate",
    "head_version",
    "user_version",
    "integrity_check",
    "snapshot",
    "newest_snapshot",
    "restore_snapshot",
    "open_checked",
    "is_store_file",
]
