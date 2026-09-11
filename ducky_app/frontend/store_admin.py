"""Settings → General → App Data → Database: inspect and manage ``ducky.db`` (ADR 0003).

Everything the panel shows about the store comes from here, so the UI never
holds SQL. Reads are cheap (counts + stat); actions are the same primitives the
``db`` CLI and boot maintenance use. Secret material is never returned: DPAPI
blobs, encrypted plugin rows and API-key-looking settings are masked.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any

# table -> (label, group, description, clearable)
TABLES: dict[str, tuple[str, str, str, bool]] = {
    "folders": ("Chat folders", "chats", "Sidebar folders per project.", False),
    "conversations": ("Conversations", "chats", "One row per chat; state JSON holds profile, model and context.", False),
    "messages": ("Messages", "chats", "Every turn, searchable by body text.", False),
    "snapshots": ("Prompt snapshots", "chats", "Deduplicated skill and context snapshots chats point at.", False),
    "settings": ("Settings", "settings", "One row per panel setting; absence means default.", False),
    "secrets": ("Secrets", "settings", "API keys and tokens, DPAPI-encrypted per row.", False),
    "projects": ("Projects", "settings", "Recent projects and their slugs.", False),
    "workspace_state": ("Workspace state", "settings", "Window bounds, dock layout, open editor tabs.", False),
    "plugin_kv": ("Plugin data", "settings", "Per-plugin key/value data; sensitive rows are encrypted.", False),
    "mcp_servers": ("MCP servers", "settings", "Nested MCP server blocks (mcp.json is an export).", False),
    "meta": ("Store metadata", "settings", "Import flags, clean-boot counter, integrity history.", False),
    "runs": ("Change runs", "changes", "Agent write runs the Changes tab can revert.", False),
    "run_entries": ("Change entries", "changes", "Per-file before/after within a run.", False),
    "run_seen": ("Seen marks", "changes", "Which changes you have reviewed.", False),
    "path_index": ("Path index", "changes", "Latest change per file.", False),
    "blobs": ("Content blobs", "changes", "Deduplicated file contents for reverts and history.", False),
    "file_versions": ("File history", "changes", "Local undo snapshots of Verse files (newest 100 per file).", True),
    "watch_index": ("Watch index", "changes", "Hashes the external-change watcher compares against.", True),
    "plans": ("Plans", "plans", "Plans and plan templates.", False),
    "tasks": ("Tasks", "plans", "Agent tasks and phases.", False),
    "memory_entries": ("Memory", "plans", "Project memory notes, searchable.", False),
    "verse_diagnostics": ("Verse diagnostics", "verse", "Problems cache per Verse file; rebuilt by the next scan.", True),
    "digest_files": ("Verse digests", "verse", "Indexed digest files (one row per file).", True),
    "digest_lines": ("Digest lines", "verse", "Trigram-searchable digest lines behind search_verse_digest.", True),
    "events": ("Events", "logs", "Errors, activity, crashes, plugin load errors, perf traces.", True),
    "usage_calls": ("Usage ledger", "logs", "Provider calls with tokens and cost (90-day retention).", True),
    "cache_docs": ("Cache documents", "logs", "Model lists, store catalog, skill manifests, perf reports.", True),
    "captures": ("Capture index", "logs", "Screenshots under tool_captures/, pruned by row.", True),
}

GROUPS: dict[str, str] = {
    "chats": "Chats",
    "settings": "Settings & plugins",
    "changes": "Changes & file history",
    "plans": "Plans & memory",
    "verse": "Verse",
    "logs": "Logs & caches",
}

_SENSITIVE_KEY = re.compile(r"(key|token|secret|password|credential|cookie)", re.IGNORECASE)
RESTORE_PENDING_NAME = "ducky.db.restore-pending"


def _db():
    from backend.store import db

    return db


def _kv():
    from backend.store.repos import kv

    return kv


# --------------------------------------------------------------------------- overview


def _fmt_meta_json(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _walk(path: Path) -> tuple[int, int]:
    size = files = 0
    if not path.exists():
        return 0, 0
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                size += p.stat().st_size
                files += 1
        except OSError:
            continue
    return size, files


def overview() -> dict[str, Any]:
    db = _db()
    from backend.store.switch import STORES, use_db

    root = db.app_root()
    path = db.db_path(root)
    out: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "sqlite_version": db.sqlite_library_version(),
        "backends": {store: ("rows" if use_db(store) else "files") for store in STORES},
        "size_bytes": 0,
        "wal_bytes": 0,
        "schema_version": 0,
        "head_version": db.head_version(),
        "tables": [],
        "snapshots": [],
        "legacy": None,
        "importers": [],
        "integrity": None,
        "last_restore": None,
        "restore_pending": (root / RESTORE_PENDING_NAME).exists(),
        "clean_boots": 0,
        "clean_boots_needed": 3,
        "error": "",
    }
    if not use_db("settings"):
        out["error"] = "The row store is switched off (DUCKY_STORE_BACKEND=files)."
        return out
    try:
        conn = db.connect(root)
        out["size_bytes"] = path.stat().st_size if path.exists() else 0
        wal = root / "ducky.db-wal"
        out["wal_bytes"] = wal.stat().st_size if wal.exists() else 0
        out["schema_version"] = db.user_version(conn)
        out["page_size"] = int(conn.execute("PRAGMA page_size").fetchone()[0])
        out["journal_mode"] = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        tables: list[dict[str, Any]] = []
        for name, (label, group, desc, clearable) in TABLES.items():
            try:
                rows = int(conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            except db.DatabaseError:
                rows = -1
            tables.append(
                {
                    "name": name,
                    "label": label,
                    "group": group,
                    "group_label": GROUPS[group],
                    "description": desc,
                    "rows": rows,
                    "clearable": clearable,
                }
            )
        out["tables"] = tables
        kv = _kv()
        out["integrity"] = _fmt_meta_json(kv.meta_get("last_integrity"))
        out["last_restore"] = kv.meta_get("last_restore")
        out["clean_boots"] = int(kv.meta_get("clean_boots") or 0)
        importers: list[dict[str, Any]] = []
        for key, raw, updated in conn.execute(
            "SELECT key, value, updated FROM meta WHERE key LIKE 'imported:%' ORDER BY updated"
        ).fetchall():
            importers.append({"name": str(key)[len("imported:"):], "report": _fmt_meta_json(raw), "ts": float(updated or 0)})
        out["importers"] = importers
    except Exception as exc:  # noqa: BLE001 — the tab must render even when the DB is unhappy
        out["error"] = f"{type(exc).__name__}: {exc}"
    # snapshots + legacy come from the filesystem and work even when the DB does not open
    snaps: list[dict[str, Any]] = []
    snap_dir = db.snapshot_dir(root)
    if snap_dir.is_dir():
        for p in sorted(snap_dir.glob("ducky-*.db"), key=lambda q: q.stat().st_mtime, reverse=True):
            try:
                st = p.stat()
            except OSError:
                continue
            snaps.append({"name": p.name, "bytes": st.st_size, "ts": st.st_mtime})
    out["snapshots"] = snaps
    legacy_dir = root / "legacy"
    if legacy_dir.is_dir():
        stores: list[dict[str, Any]] = []
        total_bytes = total_files = 0
        for child in sorted(legacy_dir.iterdir()):
            size, files = _walk(child)
            total_bytes += size
            total_files += files
            stores.append({"name": child.name, "bytes": size, "files": files})
        out["legacy"] = {"rel": "legacy", "bytes": total_bytes, "files": total_files, "stores": stores}
    return out


# --------------------------------------------------------------------------- preview


def _mask_row(table: str, row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for col, val in row.items():
        if isinstance(val, (bytes, bytearray, memoryview)):
            out[col] = f"<{len(val)} bytes>"
            continue
        if table == "secrets" and col != "name":
            out[col] = "••••••••" if val else val
            continue
        if table == "plugin_kv" and col == "value" and row.get("encrypted"):
            out[col] = "•••••••• (encrypted)" if val else val
            continue
        if table == "settings" and col == "value" and _SENSITIVE_KEY.search(str(row.get("key") or "")):
            out[col] = "••••••••" if val not in (None, "", "null") else val
            continue
        if isinstance(val, str) and len(val) > 400:
            out[col] = val[:400] + f"… (+{len(val) - 400} chars)"
            continue
        out[col] = val
    return out


def table_preview(table: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    if table not in TABLES:
        return {"ok": False, "error": f"unknown table: {table}", "columns": [], "rows": [], "total": 0}
    db = _db()
    conn = db.connect()
    limit = max(1, min(int(limit or 50), 500))
    offset = max(0, int(offset or 0))
    total = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    cur = conn.execute(f"SELECT * FROM {table} LIMIT ? OFFSET ?", (limit, offset))
    columns = [d[0] for d in cur.description]
    rows = [_mask_row(table, {c: r[c] for c in columns}) for r in cur.fetchall()]
    return {"ok": True, "table": table, "columns": columns, "rows": rows, "total": total, "offset": offset, "limit": limit}


# --------------------------------------------------------------------------- actions


def record_integrity(conn: Any, result: str) -> None:
    _db().record_integrity(conn, result)


def _clear_table(table: str) -> dict[str, Any]:
    if table not in TABLES or not TABLES[table][3]:
        return {"ok": False, "error": "not clearable"}
    db = _db()
    conn = db.connect()
    removed_files = 0
    with db.write_txn(conn):
        if table == "captures":
            names = [str(r[0]) for r in conn.execute("SELECT filename FROM captures").fetchall()]
            from frontend.ui_web.tool_captures import tool_captures_dir

            directory = tool_captures_dir()
            for name in names:
                try:
                    (directory / name).unlink()
                    removed_files += 1
                except OSError:
                    pass
        if table == "digest_files":
            conn.execute("DELETE FROM digest_lines")
        cur = conn.execute(f"DELETE FROM {table}")
        removed = cur.rowcount
    if table in ("digest_files", "digest_lines"):
        try:
            from backend.tools.verse import verse_digests

            verse_digests.clear_cache()
        except Exception:  # noqa: BLE001
            pass
    if table == "verse_diagnostics":
        try:
            from frontend.ui_web.verse_editor.lsp import diagnostics_cache

            diagnostics_cache.clear()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "removed": removed, "removed_files": removed_files}


def export_table(table: str) -> dict[str, Any]:
    """Write the whole table as JSON lines under ``exports/`` (secrets masked)."""
    if table not in TABLES:
        return {"ok": False, "error": f"unknown table: {table}"}
    db = _db()
    conn = db.connect()
    root = db.app_root()
    out_dir = root / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{table}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    cur = conn.execute(f"SELECT * FROM {table}")
    columns = [d[0] for d in cur.description]
    n = 0
    with target.open("w", encoding="utf-8") as fh:
        for r in cur:
            fh.write(json.dumps(_mask_row(table, {c: r[c] for c in columns}), ensure_ascii=False, default=str) + "\n")
            n += 1
    return {"ok": True, "path": str(target), "rows": n}


def delete_project_rows(project_id: str) -> dict[str, int]:
    """Remove every row scoped to one project (chats cascade to messages, runs to entries)."""
    pid = (project_id or "").strip()
    if not pid:
        return {}
    db = _db()
    conn = db.connect()
    removed: dict[str, int] = {}
    with db.write_txn(conn):
        # Cascades (messages, run entries, seen marks) are counted up front so the
        # report says what really went, then the parents go and take them along.
        for child, sql in (
            ("messages", "SELECT count(*) FROM messages WHERE conv_id IN (SELECT id FROM conversations WHERE project_id=?)"),
            ("run_entries", "SELECT count(*) FROM run_entries WHERE run_id IN (SELECT run_id FROM runs WHERE project_id=?)"),
            ("run_seen", "SELECT count(*) FROM run_seen WHERE run_id IN (SELECT run_id FROM runs WHERE project_id=?)"),
        ):
            n = int(conn.execute(sql, (pid,)).fetchone()[0])
            if n:
                removed[child] = n
        for table in TABLES:
            cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "project_id" in cols:
                cur = conn.execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
                if cur.rowcount:
                    removed[table] = cur.rowcount
        cur = conn.execute("DELETE FROM projects WHERE id=?", (pid,))
        if cur.rowcount:
            removed["projects"] = cur.rowcount
    return removed


def project_row_counts() -> dict[str, dict[str, int]]:
    """``{project_id: {table: rows}}`` for every table that carries a project_id."""
    db = _db()
    conn = db.connect()
    out: dict[str, dict[str, int]] = {}
    for table in TABLES:
        cols = {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "project_id" not in cols:
            continue
        for pid, n in conn.execute(f"SELECT project_id, count(*) FROM {table} GROUP BY project_id").fetchall():
            out.setdefault(str(pid), {})[table] = int(n)
    for pid, n in conn.execute(
        "SELECT c.project_id, count(*) FROM messages m JOIN conversations c ON c.id = m.conv_id GROUP BY c.project_id"
    ).fetchall():
        out.setdefault(str(pid), {})["messages"] = int(n)
    return out


def stage_restore(snapshot_name: str) -> dict[str, Any]:
    """Copy a snapshot next to the database; the next boot swaps it in before
    opening (other threads and the bridge process may hold the live file)."""
    db = _db()
    root = db.app_root()
    src = db.snapshot_dir(root) / Path(snapshot_name).name
    if not src.is_file() or not src.name.startswith("ducky-") or src.suffix != ".db":
        return {"ok": False, "error": "unknown snapshot"}
    result = db.check_db_file(src)
    if result != "ok":
        return {"ok": False, "error": f"snapshot fails integrity_check: {result}"}
    shutil.copy2(src, root / RESTORE_PENDING_NAME)
    return {"ok": True, "staged": src.name, "restart_required": True}


def cancel_restore() -> dict[str, Any]:
    pending = _db().app_root() / RESTORE_PENDING_NAME
    try:
        pending.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True}


def delete_snapshot(snapshot_name: str) -> dict[str, Any]:
    db = _db()
    target = db.snapshot_dir() / Path(snapshot_name).name
    if not target.is_file() or not target.name.startswith("ducky-"):
        return {"ok": False, "error": "unknown snapshot"}
    target.unlink()
    return {"ok": True}


def retire_legacy_now() -> dict[str, Any]:
    root = _db().app_root()
    legacy = root / "legacy"
    if not legacy.is_dir():
        return {"ok": True, "removed": 0}
    size, files = _walk(legacy)
    shutil.rmtree(legacy, ignore_errors=True)
    if legacy.exists():
        return {"ok": False, "error": "some legacy files are in use; try again after a restart"}
    return {"ok": True, "removed": files, "bytes": size}


def action(name: str, arg: str = "") -> dict[str, Any]:
    """Dispatch for the panel: every mutation the Database tab can perform."""
    db = _db()
    try:
        if name == "check":
            conn = db.connect()
            result = db.integrity_check(conn)
            record_integrity(conn, result)
            return {"ok": result == "ok", "result": result}
        if name == "vacuum":
            conn = db.connect()
            before = db.db_path().stat().st_size
            conn.execute("VACUUM")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return {"ok": True, "before": before, "after": db.db_path().stat().st_size}
        if name == "snapshot":
            return {"ok": True, "snapshot": db.snapshot(label="manual").name}
        if name == "restore":
            return stage_restore(arg)
        if name == "cancel_restore":
            return cancel_restore()
        if name == "delete_snapshot":
            return delete_snapshot(arg)
        if name == "clear_table":
            return _clear_table(arg)
        if name == "export_table":
            return export_table(arg)
        if name == "sweep_blobs":
            from backend.store.repos import ledger

            return {"ok": True, "removed": ledger.sweep_blobs()}
        if name == "retire_legacy":
            return retire_legacy_now()
        if name == "import_now":
            from backend.store.importers.boot import ensure_all_stores

            return {"ok": True, "reports": ensure_all_stores()}
        if name == "optimize":
            conn = db.connect()
            conn.execute("PRAGMA optimize")
            for fts in ("message_fts", "memory_fts", "digest_fts"):
                try:
                    conn.execute(f"INSERT INTO {fts}({fts}) VALUES('optimize')")
                except db.DatabaseError:
                    pass
            return {"ok": True}
        return {"ok": False, "error": f"unknown action: {name}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
