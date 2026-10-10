"""``conversations`` / ``messages`` / ``folders`` / ``snapshots`` (ADR 0003, phase 2).

The wire shape is unchanged: every function speaks the ``Conversation.to_dict()``
document. Columns hold what lists and lookups need; ``state`` holds the rest as
JSON; messages are one row each so an append or an in-flight checkpoint writes
one row instead of the whole transcript.

A message field of ``PART_MIN_CHARS`` or more (a tool result, tool arguments, a
long reply) is stored once in ``message_parts``, compressed, and the row keeps
a reference to it (``fmt`` 1). Reads put the fields back, so callers always see
the message exactly as it was saved; rows written before that keep ``fmt`` 0.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import zlib
from collections import OrderedDict
from typing import Any, Callable

from backend.store import db

_log = logging.getLogger("uefn_ducky.store")

# Message rows: the body is the message JSON, or an envelope whose large fields
# live in message_parts.
FMT_PLAIN = 0
FMT_PARTS = 1
PART_MIN_CHARS = 8 * 1024

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


def folders_list_all_projects() -> list[tuple[str, list[dict[str, Any]]]]:
    rows = db.connect().execute(
        "SELECT project_id, id, name, parent_id, sort_order, group_hub_id FROM folders "
        "ORDER BY project_id, parent_id, sort_order, id"
    ).fetchall()
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(str(r["project_id"]), []).append(dict(r))
    return list(by.items())


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


# --------------------------------------------------------------------------- message parts


def _split_parts(message: dict[str, Any]) -> tuple[dict[str, Any], list[list[Any]], dict[str, str]]:
    """The message with its large fields set to None, where they were, and the fields.

    Large means ``PART_MIN_CHARS`` or more of JSON: a top-level field, or one
    field of a block (``blocks[i].result``), so a checkpoint that adds a tool
    call stores only the new result and the earlier ones are shared.
    """
    refs: list[list[Any]] = []
    parts: dict[str, str] = {}

    def take(path: list[Any], value: Any) -> bool:
        if value is None or isinstance(value, (bool, int, float)):
            return False
        # JSON turns every key into a string; only a field reached by string keys can be put back.
        if not all(isinstance(step, str) for step in path[::2]):
            return False
        text = _dumps(value)
        if len(text) < PART_MIN_CHARS:
            return False
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        parts[digest] = text
        refs.append([*path, digest])
        return True

    skeleton: dict[str, Any] = {}
    for key, value in message.items():
        if key == "blocks" and isinstance(value, list):
            blocks: list[Any] = []
            for i, block in enumerate(value):
                if isinstance(block, dict):
                    blocks.append({bk: (None if take(["blocks", i, bk], bv) else bv) for bk, bv in block.items()})
                else:
                    blocks.append(None if take(["blocks", i], block) else block)
            skeleton[key] = blocks
        else:
            skeleton[key] = None if take([key], value) else value
    return skeleton, refs, parts


def _compact(message: Any, body: str) -> tuple[str, int, dict[str, str]]:
    """(stored body, fmt, {part hash: part JSON}) for a message whose JSON is *body*.

    The row hash stays the hash of the whole message JSON whichever way the
    row is stored, so a later save compares rows of either kind the same way.
    """
    if not isinstance(message, dict) or len(body) < PART_MIN_CHARS:
        return body, FMT_PLAIN, {}
    skeleton, refs, parts = _split_parts(message)
    if not refs:
        return body, FMT_PLAIN, {}
    return _dumps({"m": skeleton, "x": refs}), FMT_PARTS, parts


def _decode_parts(envelope: Any, part_text: Callable[[str], str | None]) -> dict[str, Any]:
    """Put the stored fields back into a ``fmt`` 1 body (see :func:`_split_parts`)."""
    if not isinstance(envelope, dict) or not isinstance(envelope.get("m"), dict):
        raise ValueError("not a message envelope")
    message = envelope["m"]
    try:
        for ref in envelope.get("x") or []:
            *path, digest = ref
            target: Any = message
            for step in path[:-1]:
                target = target[step]
            text = part_text(str(digest))
            if text is None:
                # Never expected (a part goes only with its last ref); keep the rest of the message.
                _log.warning("message part %s is missing; the field reads as empty", digest)
                target[path[-1]] = None
                continue
            target[path[-1]] = json.loads(text)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"bad message envelope: {exc}") from exc
    return message


def _write_parts(conn, message_id: int, parts: dict[str, str]) -> None:
    """Point the row at exactly *parts*: store new ones, drop refs it no longer uses."""
    old = {str(r[0]) for r in conn.execute("SELECT hash FROM message_part_refs WHERE message_id=?", (message_id,))}
    for digest, text in parts.items():
        if digest in old:
            continue
        if conn.execute("SELECT 1 FROM message_parts WHERE hash=?", (digest,)).fetchone() is None:
            raw = text.encode("utf-8")
            conn.execute(
                "INSERT INTO message_parts(hash, data, size, created) VALUES (?, ?, ?, ?)",
                (digest, zlib.compress(raw, 6), len(raw), time.time()),
            )
        conn.execute("INSERT INTO message_part_refs(message_id, hash) VALUES (?, ?)", (message_id, digest))
    stale = old - parts.keys()
    if stale:
        conn.executemany(
            "DELETE FROM message_part_refs WHERE message_id=? AND hash=?", [(message_id, d) for d in stale]
        )


def _conv_parts(conn, conv_id: str) -> Callable[[str], str | None]:
    """Part JSON by hash for one chat, read in one query and inflated on first use."""
    packed = {
        str(h): bytes(data)
        for h, data in conn.execute(
            "SELECT hash, data FROM message_parts WHERE hash IN (SELECT r.hash FROM message_part_refs r "
            "JOIN messages m ON m.id = r.message_id WHERE m.conv_id=?)",
            (conv_id,),
        )
    }
    inflated: dict[str, str] = {}

    def part_text(digest: str) -> str | None:
        text = inflated.get(digest)
        if text is None and digest in packed:
            try:
                text = inflated[digest] = zlib.decompress(packed[digest]).decode("utf-8")
            except (zlib.error, UnicodeDecodeError):
                return None
        return text

    return part_text


# --------------------------------------------------------------------------- saved rows
#
# Finding the one or two messages a save changed by serialising and hashing all
# of them made every in-flight checkpoint cost the whole transcript. For the
# chats saved most recently this remembers, per seq, the row hash written and a
# copy of the message's dicts and lists (sharing its strings and numbers, which
# cannot change in place). A message equal to its copy, whose row still has that
# hash in the database, is unchanged and is not serialised again. Copies keep
# the strings alive, so only a few chats and a bounded amount are kept.

_SAVED_MAX_CHATS = 8
_SAVED_MAX_CHARS = 32 * 1024 * 1024
_SHARED_TYPES = (str, int, float, bool, type(None))
_saved_lock = threading.Lock()
_saved: OrderedDict[str, tuple[dict[int, tuple[str, Any, int]], int]] = OrderedDict()


class _NotShareable(Exception):
    pass


def _copy_containers(value: Any) -> Any:
    kind = type(value)
    if kind is dict:
        return {k: _copy_containers(v) for k, v in value.items()}
    if kind is list:
        return [_copy_containers(v) for v in value]
    if kind in _SHARED_TYPES:
        return value
    raise _NotShareable


def _shell(message: Any) -> Any | None:
    """A copy to compare the next save against, or None when it could not be told
    apart reliably (a value of another type might change in place)."""
    try:
        return _copy_containers(message)
    except (_NotShareable, RecursionError, RuntimeError):
        return None


def _unchanged(shell: Any, message: Any) -> bool:
    try:
        return bool(shell == message)
    except Exception:
        return False


def _saved_rows(conv_id: str) -> dict[int, tuple[str, Any, int]]:
    with _saved_lock:
        entry = _saved.get(conv_id)
        return entry[0] if entry is not None else {}


def _remember_rows(conv_id: str, rows: dict[int, tuple[str, Any, int]], chars: int) -> None:
    with _saved_lock:
        _saved.pop(conv_id, None)
        _saved[conv_id] = (rows, chars)
        total = sum(c for _, c in _saved.values())
        while len(_saved) > 1 and (len(_saved) > _SAVED_MAX_CHATS or total > _SAVED_MAX_CHARS):
            _, (_, dropped) = _saved.popitem(last=False)
            total -= dropped


def _forget_rows(conv_id: str) -> None:
    with _saved_lock:
        _saved.pop(conv_id, None)


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
    remember: tuple[dict[int, tuple[str, Any, int]], int] | None = None
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
            counts, rows, chars = _sync_messages(conn, doc["id"], messages)
            stats.update(counts)
            remember = (rows, chars)
            conn.execute("UPDATE conversations SET message_count=? WHERE id=?", (len(messages), doc["id"]))
    if remember is not None:
        _remember_rows(doc["id"], *remember)  # only once the rows are committed
    return stats


def _sync_messages(
    conn, conv_id: str, messages: list[dict[str, Any]]
) -> tuple[dict[str, int], dict[int, tuple[str, Any, int]], int]:
    """Write the messages whose row differs; returns counts and what to remember."""
    existing = {
        int(r[0]): (str(r[1]), int(r[2]))
        for r in conn.execute("SELECT seq, hash, id FROM messages WHERE conv_id=?", (conv_id,))
    }
    known = _saved_rows(conv_id)
    remembered: dict[int, tuple[str, Any, int]] = {}
    chars = 0
    inserted = updated = deleted = nbytes = 0
    for seq, message in enumerate(messages):
        row = existing.get(seq)
        last = known.get(seq)
        if last is not None and row is not None and last[0] == row[0] and _unchanged(last[1], message):
            remembered[seq] = last
            chars += last[2]
            continue
        # Serialise the copy, not the live message, so the hash describes exactly
        # what is remembered even if another thread is still adding to it.
        shell = _shell(message)
        source = message if shell is None else shell
        body = _dumps(source)
        h = _hash(body)
        if shell is not None:
            remembered[seq] = (h, shell, len(body))
            chars += len(body)
        if row is not None and row[0] == h:
            continue
        nbytes += len(body)
        body, fmt, parts = _compact(source, body)
        params = (
            h,
            str(source.get("role") or "") if isinstance(source, dict) else "",
            float(source.get("ts") or 0.0) if isinstance(source, dict) else 0.0,
            str(source.get("run_id") or "") if isinstance(source, dict) else "",
            message_search_text(source) if isinstance(source, dict) else "",
            body,
            fmt,
        )
        if row is not None:
            message_id = row[1]
            conn.execute(
                "UPDATE messages SET hash=?, role=?, ts=?, run_id=?, text=?, body=?, fmt=? WHERE id=?",
                (*params, message_id),
            )
            updated += 1
        else:
            cur = conn.execute(
                "INSERT INTO messages(conv_id, seq, hash, role, ts, run_id, text, body, fmt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conv_id, seq, *params),
            )
            message_id = int(cur.lastrowid)
            inserted += 1
        if parts or row is not None:
            _write_parts(conn, message_id, parts)
    if len(existing) > len(messages):
        conn.execute("DELETE FROM messages WHERE conv_id=? AND seq>=?", (conv_id, len(messages)))
        deleted = len(existing) - len(messages)
    counts = {"inserted": inserted, "updated": updated, "deleted": deleted, "bytes": nbytes}
    return counts, remembered, chars


def messages_get(conv_id: str) -> list[dict[str, Any]]:
    conn = db.connect()
    rows = conn.execute("SELECT body, fmt FROM messages WHERE conv_id=? ORDER BY seq", (conv_id,)).fetchall()
    part_text: Callable[[str], str | None] | None = None
    out: list[dict[str, Any]] = []
    for body, fmt in rows:
        try:
            if fmt == FMT_PARTS:
                if part_text is None:
                    part_text = _conv_parts(conn, conv_id)
                out.append(_decode_parts(json.loads(body), part_text))
            else:
                out.append(json.loads(body))
        except ValueError:
            continue
    return out


def conv_delete(conv_id: str) -> bool:
    _forget_rows(conv_id)
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
    if not terms:
        return ""
    and_q = " ".join(f'"{t}"' for t in terms)
    if len(terms) > 1:
        joined = "_".join(terms)
        return f'({and_q}) OR "{joined}"'
    return and_q


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
