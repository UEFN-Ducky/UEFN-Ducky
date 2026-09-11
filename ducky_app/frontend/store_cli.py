"""``UEFN-Ducky.exe db <check|vacuum|snapshot|export TABLE|stats>`` (ADR 0003, phase 5).

Also runnable as ``py -m frontend.store_cli ...`` from ``ducky_app/``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

USAGE = "usage: db check | db vacuum | db snapshot | db stats | db export <table> [--limit N]"


def _tables(conn) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%_fts%' "
            "AND name NOT LIKE '%_config' AND name NOT LIKE '%_data' AND name NOT LIKE '%_idx' AND name NOT LIKE '%_docsize' "
            "ORDER BY name"
        )
    ]


def stats() -> dict[str, Any]:
    from backend.store import db

    conn = db.connect()
    path = db.db_path()
    out: dict[str, Any] = {
        "path": str(path),
        "schema_version": db.user_version(conn),
        "head_version": db.head_version(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "wal_bytes": (path.parent / "ducky.db-wal").stat().st_size if (path.parent / "ducky.db-wal").exists() else 0,
        "tables": {},
    }
    for name in _tables(conn):
        try:
            out["tables"][name] = int(conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
        except Exception:  # noqa: BLE001
            out["tables"][name] = None
    snap = db.newest_snapshot()
    out["newest_snapshot"] = str(snap) if snap else None
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "db":
        args = args[1:]
    if not args:
        print(USAGE)
        return 2
    from backend.store import db

    cmd = args[0]
    if cmd == "check":
        conn = db.connect()
        result = db.integrity_check(conn)
        print(f"integrity_check: {result}")
        print(f"schema: {db.user_version(conn)} (app knows {db.head_version()})")
        return 0 if result == "ok" else 1
    if cmd == "vacuum":
        conn = db.connect()
        conn.execute("VACUUM")
        print("vacuumed", db.db_path())
        return 0
    if cmd == "snapshot":
        print("snapshot:", db.snapshot(label="manual"))
        return 0
    if cmd == "stats":
        print(json.dumps(stats(), indent=2))
        return 0
    if cmd == "export":
        if len(args) < 2:
            print(USAGE)
            return 2
        table = args[1]
        limit = 1000
        if "--limit" in args:
            limit = int(args[args.index("--limit") + 1])
        conn = db.connect()
        if table not in _tables(conn):
            print(f"unknown table: {table}")
            return 2
        rows = conn.execute(f"SELECT * FROM {table} LIMIT ?", (limit,)).fetchall()
        for r in rows:
            print(json.dumps({k: r[k] for k in r.keys()}, ensure_ascii=False, default=str))
        return 0
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
