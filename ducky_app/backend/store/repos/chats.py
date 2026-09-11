"""``conversations`` / ``messages`` / ``folders`` / ``snapshots`` (ADR 0003, phase 2).

The wire shape is unchanged: every function speaks the ``Conversation.to_dict()``
document. Columns hold what lists and lookups need; ``state`` holds the rest as
JSON; messages are one row each so an append or an in-flight checkpoint writes
one row instead of the whole transcript.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from backend.store import db

# Conversation.to_dict() keys that are real columns; everything else lives in ``state``.
_COLUMNS = (
    "folder_id",
    "title",
    "created",
    "updated",
    "sort_order",
    "provider",
    "model",
    "coding_agent",
    "profile_id",
    "file_path",
    "parent_conv_id",
    "leader_conv_id",
    "is_group",
    "tool_call_count",
    "file_count",
)
_STATE_EXCLUDED = set(_COLUMNS) | {"id", "messages", "skill_snapshot"}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def message_search_text(message: dict[str, Any]) -> str:
    """What full-text search indexes for one message (mirrors workspace_search)."""
    role = message.get("role", "")
    if role == "user":
        return str(message.get("text") or message.get("content") or "")
    if role == "assistant":
        parts = [str(message.get("content") or "")]
        for block in message.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            name = block.get("name", "")
            args = block.get("arguments") or {}
            parts.append(f"{name} {args}")
            result = block.get("result")
            if isinstance(result, dict):
                parts.append(str(result.get("text") or result.get("hint") or ""))
        return " ".join(p for p in parts if p)
    return str(message.get("text") or message.get("content") or "")


# --------------------------------------------------------------------------- folders


def folders_get(project_id: str) -> list[dict[str, Any]]:
    rows = db.connect().execute(
        "SELECT id, name, parent_id, sort_order, group_hub_id FROM folders WHERE project_id=? "
        "ORDER BY parent_id, sort_order, id",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def folders_exist(project_id: str) -> bool:
    return db.connect().execute("SELECT 1 FROM folders WHERE project_id=? LIMIT 1", (project_id,)).fetchone() is not None


def folders_replace(project_id: str, folders: list[dict[str, Any]]) -> None:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM folders WHERE project_id=?", (project_id,))
        conn.executemany(
            "INSERT INTO folders(project_id, id, name, parent_id, sort_order, group_hub_id) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    project_id,
                    str(f.get("id", "")),
                    str(f.get("name", "")),
                    str(f.get("parent_id", "") or ""),
                    float(f.get("sort_order", 0.0) or 0.0),
                    str(f.get("group_hub_id", "") or ""),
                )
                for f in folders
                if f.get("id")
            ],
        )


# --------------------------------------------------------------------------- snapshots


def _snapshot_put(conn, kind: str, text: str) -> str:
    if not text:
        return ""
    h = _hash(text)
    conn.execute(
        "INSERT OR IGNORE INTO snapshots(hash, kind, text, created) VALUES (?, ?, ?, ?)",
        (h, kind, text, time.time()),
    )
    return h


def _snapshot_text(conn, h: str) -> str:
    if not h:
        return ""
    row = conn.execute("SELECT text FROM snapshots WHERE hash=?", (h,)).fetchone()
    return str(row[0]) if row else ""


# --------------------------------------------------------------------------- conversations


def _row_to_doc(conn, row, *, with_messages: bool) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    try:
        doc.update(json.loads(row["state"] or "{}"))
    except ValueError:
        pass
    for col in _COLUMNS:
        doc[col] = row[col]
    doc["is_group"] = bool(row["is_group"])
    doc["id"] = row["id"]
    doc["skill_snapshot"] = _snapshot_text(conn, row["skill_snapshot_hash"])
    doc["messages"] = messages_get(row["id"]) if with_messages else []
    return doc


def conv_get(conv_id: str, *, project_id: str | None = None, with_messages: bool = True) -> dict[str, Any] | None:
    conn = db.connect()
    if project_id is None:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND project_id=?", (conv_id, project_id)
        ).fetchone()
    if row is None:
        return None
    return _row_to_doc(conn, row, with_messages=with_messages)


def conv_project_id(conv_id: str) -> str | None:
    row = db.connect().execute("SELECT project_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return None if row is None else str(row[0])


def conv_list(project_id: str, *, with_messages: bool = False) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE project_id=? ORDER BY sort_order, updated DESC, id",
        (project_id,),
    ).fetchall()
    return [_row_to_doc(conn, r, with_messages=with_messages) for r in rows]


def conv_list_all_projects(*, with_messages: bool = False) -> list[tuple[str, dict[str, Any]]]:
    conn = db.connect()
    rows = conn.execute("SELECT * FROM conversations ORDER BY project_id, id").fetchall()
    return [(str(r["project_id"]), _row_to_doc(conn, r, with_messages=with_messages)) for r in rows]


def conv_count(project_id: str) -> int:
    return int(db.connect().execute("SELECT count(*) FROM conversations WHERE project_id=?", (project_id,)).fetchone()[0])


def conv_message_summary(conv_id: str) -> tuple[int, bool]:
    """(message count, any assistant row) without loading bodies."""
    row = db.connect().execute(
        "SELECT count(*), coalesce(max(role='assistant'), 0) FROM messages WHERE conv_id=?", (conv_id,)
    ).fetchone()
    return int(row[0]), bool(row[1])


def conv_save(project_id: str, doc: dict[str, Any], *, messages: list[dict[str, Any]] | None) -> dict[str, int]:
    """Upsert the row; when *messages* is given, sync rows by content hash.

    Returns counts: rows inserted/updated/deleted for messages. Passing
    ``messages=None`` leaves the transcript untouched (metadata-only save).
    """
    conn = db.connect()
    state = {k: v for k, v in doc.items() if k not in _STATE_EXCLUDED}
    stats = {"inserted": 0, "updated": 0, "deleted": 0, "bytes": 0}
    with db.write_txn(conn):
        snap_hash = _snapshot_put(conn, "skill_index", str(doc.get("skill_snapshot") or ""))
        values = {col: doc.get(col) for col in _COLUMNS}
        values["is_group"] = 1 if doc.get("is_group") else 0
        conn.execute(
            "INSERT INTO conversations(id, project_id, folder_id, title, created, updated, sort_order, provider, model, "
            "coding_agent, profile_id, file_path, parent_conv_id, leader_conv_id, is_group, tool_call_count, file_count, "
            "message_count, skill_snapshot_hash, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "coalesce((SELECT message_count FROM conversations WHERE id=?), 0), ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET project_id=excluded.project_id, folder_id=excluded.folder_id, "
            "title=excluded.title, created=excluded.created, updated=excluded.updated, sort_order=excluded.sort_order, "
            "provider=excluded.provider, model=excluded.model, coding_agent=excluded.coding_agent, "
            "profile_id=excluded.profile_id, file_path=excluded.file_path, parent_conv_id=excluded.parent_conv_id, "
            "leader_conv_id=excluded.leader_conv_id, is_group=excluded.is_group, tool_call_count=excluded.tool_call_count, "
            "file_count=excluded.file_count, skill_snapshot_hash=excluded.skill_snapshot_hash, state=excluded.state",
            (
                doc["id"],
                project_id,
                str(values["folder_id"] or ""),
                str(values["title"] or ""),
                float(values["created"] or 0.0),
                float(values["updated"] or 0.0),
                float(values["sort_order"] or 0.0),
                str(values["provider"] or ""),
                str(values["model"] or ""),
                str(values["coding_agent"] or "ducky"),
                str(values["profile_id"] or ""),
                str(values["file_path"] or ""),
                str(values["parent_conv_id"] or ""),
                str(values["leader_conv_id"] or ""),
                values["is_group"],
                int(values["tool_call_count"] or 0),
                int(values["file_count"] or 0),
                doc["id"],
                snap_hash,
                _dumps(state),
            ),
        )
        if messages is not None:
            stats.update(_sync_messages(conn, doc["id"], messages))
            conn.execute("UPDATE conversations SET message_count=? WHERE id=?", (len(messages), doc["id"]))
    return stats


def _sync_messages(conn, conv_id: str, messages: list[dict[str, Any]]) -> dict[str, int]:
    existing = {int(r[0]): str(r[1]) for r in conn.execute("SELECT seq, hash FROM messages WHERE conv_id=?", (conv_id,))}
    inserted = updated = deleted = nbytes = 0
    for seq, message in enumerate(messages):
        body = _dumps(message)
        h = _hash(body)
        if existing.get(seq) == h:
            continue
        nbytes += len(body)
        params = (
            h,
            str(message.get("role") or "") if isinstance(message, dict) else "",
            float(message.get("ts") or 0.0) if isinstance(message, dict) else 0.0,
            str(message.get("run_id") or "") if isinstance(message, dict) else "",
            message_search_text(message) if isinstance(message, dict) else "",
            body,
        )
        if seq in existing:
            conn.execute(
                "UPDATE messages SET hash=?, role=?, ts=?, run_id=?, text=?, body=? WHERE conv_id=? AND seq=?",
                (*params, conv_id, seq),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO messages(conv_id, seq, hash, role, ts, run_id, text, body) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (conv_id, seq, *params),
            )
            inserted += 1
    if len(existing) > len(messages):
        conn.execute("DELETE FROM messages WHERE conv_id=? AND seq>=?", (conv_id, len(messages)))
        deleted = len(existing) - len(messages)
    return {"inserted": inserted, "updated": updated, "deleted": deleted, "bytes": nbytes}


def messages_get(conv_id: str) -> list[dict[str, Any]]:
    rows = db.connect().execute("SELECT body FROM messages WHERE conv_id=? ORDER BY seq", (conv_id,)).fetchall()
    out: list[dict[str, Any]] = []
    for (body,) in rows:
        try:
            out.append(json.loads(body))
        except ValueError:
            continue
    return out


def conv_delete(conv_id: str) -> bool:
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        cur = conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    return cur.rowcount > 0


def conv_ids_by_parent(project_id: str) -> dict[str, list[str]]:
    rows = db.connect().execute(
        "SELECT parent_conv_id, id FROM conversations WHERE project_id=? AND parent_conv_id<>''", (project_id,)
    ).fetchall()
    out: dict[str, list[str]] = {}
    for parent, cid in rows:
        out.setdefault(str(parent), []).append(str(cid))
    return out


def conv_max_sort_order(project_id: str, folder_id: str) -> float:
    row = db.connect().execute(
        "SELECT max(sort_order) FROM conversations WHERE project_id=? AND folder_id=?", (project_id, folder_id)
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else -1.0


# --------------------------------------------------------------------------- search


def _fts_query(query: str) -> str:
    terms = [t.replace('"', '""') for t in query.split() if t.strip()]
    return " ".join(f'"{t}"' for t in terms)


def search_messages(project_id: str, query: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Body search over every message in a project via FTS5 (bm25 ranked)."""
    q = _fts_query(query)
    if not q:
        return []
    rows = db.connect().execute(
        "SELECT c.id AS conv_id, c.title, c.folder_id, m.seq, "
        "snippet(message_fts, 0, '', '', '…', 12) AS preview "
        "FROM message_fts JOIN messages m ON m.id = message_fts.rowid "
        "JOIN conversations c ON c.id = m.conv_id "
        "WHERE message_fts MATCH ? AND c.project_id=? ORDER BY bm25(message_fts) LIMIT ?",
        (q, project_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def has_rows() -> bool:
    return db.connect().execute("SELECT 1 FROM conversations LIMIT 1").fetchone() is not None
