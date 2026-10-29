"""``conversations`` / ``messages`` / ``folders`` / ``snapshots`` (ADR 0003, phase 2).

The wire shape is unchanged: every function speaks the ``Conversation.to_dict()``
document. Columns hold what lists and lookups need; ``state`` holds the rest as
JSON; messages are one row each so an append or an in-flight checkpoint writes
one row instead of the whole transcript.

A message field of ``PART_MIN_CHARS`` or more (a tool result, tool arguments, a
long reply) is stored once in ``message_parts``, compressed, and the row keeps
a reference to it (``fmt`` 1). Reads put the fields back, so callers always see
the message exactly as it was saved; rows written before that keep ``fmt`` 0.

Every chat and folder belongs to the signed-in account (migration 0024,
:mod:`backend.store.account_rows`): these functions see only its rows, and its
titles, state, message bodies, payloads and folder names are sealed for it. A
message's search text is kept as keyed word tokens, so search needs no plaintext.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
import sys
import threading
import time
import zlib
from collections import OrderedDict
from typing import Any, Callable

from backend.store import account_rows, db

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
_SEALED = "enc1:"
_SEALED_BLOB = b"UDE1"


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


def _owned(conn, conv_id: str, account: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM conversations WHERE id=? AND account_id=?", (conv_id, account)
    ).fetchone() is not None


# --------------------------------------------------------------------------- folders


def _folder(row, account: str) -> dict[str, Any]:
    out = dict(row)
    out["name"] = account_rows.open_cached(str(out.get("name") or ""), account)
    return out


def folders_get(project_id: str) -> list[dict[str, Any]]:
    account = account_rows.current()
    rows = db.connect().execute(
        "SELECT id, name, parent_id, sort_order, group_hub_id FROM folders WHERE account_id=? AND project_id=? "
        "ORDER BY parent_id, sort_order, id",
        (account, project_id),
    ).fetchall()
    return [_folder(r, account) for r in rows]


def folders_list_all_projects() -> list[tuple[str, list[dict[str, Any]]]]:
    account = account_rows.current()
    rows = db.connect().execute(
        "SELECT project_id, id, name, parent_id, sort_order, group_hub_id FROM folders WHERE account_id=? "
        "ORDER BY project_id, parent_id, sort_order, id",
        (account,),
    ).fetchall()
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(str(r["project_id"]), []).append(_folder(r, account))
    return list(by.items())


def folder_project_id(folder_id: str) -> str | None:
    """The project a folder is stored under (the Archive folder is in every project: None)."""
    if not folder_id or folder_id == "archive":
        return None
    row = db.connect().execute(
        "SELECT project_id FROM folders WHERE account_id=? AND id=? LIMIT 1", (account_rows.current(), folder_id)
    ).fetchone()
    return str(row[0]) if row else None


def folders_exist(project_id: str) -> bool:
    return db.connect().execute(
        "SELECT 1 FROM folders WHERE account_id=? AND project_id=? LIMIT 1", (account_rows.current(), project_id)
    ).fetchone() is not None


def folders_replace(project_id: str, folders: list[dict[str, Any]]) -> None:
    account = account_rows.current()
    account_rows.ready(account)
    rows = [
        (
            account,
            project_id,
            str(f.get("id", "")),
            account_rows.seal(str(f.get("name", "")), account),
            str(f.get("parent_id", "") or ""),
            float(f.get("sort_order", 0.0) or 0.0),
            str(f.get("group_hub_id", "") or ""),
        )
        for f in folders
        if f.get("id")
    ]
    conn = db.connect()
    with db.write_txn(conn):
        # A writer holding a folder list read before a delete must not put the folder back.
        gone = {
            str(r[0])[len(_DELETED_FOLDER_KEY):]
            for r in conn.execute(
                "SELECT key FROM workspace_state WHERE key >= ? AND key < ?",
                (_DELETED_FOLDER_KEY, _DELETED_FOLDER_KEY[:-1] + ";"),
            )
        }
        conn.execute("DELETE FROM folders WHERE account_id=? AND project_id=?", (account, project_id))
        conn.executemany(
            "INSERT INTO folders(account_id, project_id, id, name, parent_id, sort_order, group_hub_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [row for row in rows if row[2] not in gone],
        )


def move_to_project(target: str, folders: list[tuple[str, str, str]], convs: list[tuple[str, str]]) -> None:
    """Move folders ``(source project, folder id, new parent)`` and chats ``(chat id, folder)``
    of the signed-in account onto the ``target`` project, in one transaction."""
    account = account_rows.current()
    conn = db.connect()
    with db.write_txn(conn):
        for source, folder_id, parent_id in folders:
            conn.execute(
                "UPDATE folders SET project_id=?, parent_id=? WHERE account_id=? AND project_id=? AND id=?",
                (target, parent_id, account, source, folder_id),
            )
        for conv_id, folder_id in convs:
            conn.execute(
                "UPDATE conversations SET project_id=?, folder_id=? WHERE account_id=? AND id=?",
                (target, folder_id, account, conv_id),
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


def _split_parts(
    message: dict[str, Any], digest_of: Callable[[str], str]
) -> tuple[dict[str, Any], list[list[Any]], dict[str, str]]:
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
        digest = digest_of(text)
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


def _compact(message: Any, body: str, digest_of: Callable[[str], str]) -> tuple[str, int, dict[str, str]]:
    """(stored body, fmt, {part hash: part JSON}) for a message whose JSON is *body*.

    The row hash stays the hash of the whole message JSON whichever way the
    row is stored, so a later save compares rows of either kind the same way.
    """
    if not isinstance(message, dict) or len(body) < PART_MIN_CHARS:
        return body, FMT_PLAIN, {}
    skeleton, refs, parts = _split_parts(message, digest_of)
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


def _seal_part(text: str, account: str) -> tuple[bytes, int]:
    raw = text.encode("utf-8")
    return account_rows.seal_blob(zlib.compress(raw, 6), account), len(raw)


def _seal_new_parts(conn, parts: dict[str, str], account: str) -> dict[str, tuple[bytes, int]]:
    """Before the write lock: (sealed blob, size) for the *parts* not stored yet, so the
    transaction only inserts them. One stored meanwhile is skipped there; one this missed
    is sealed there."""
    if not parts:
        return {}
    have = {
        str(r[0])
        for r in conn.execute(f"SELECT hash FROM message_parts WHERE hash IN ({','.join('?' * len(parts))})", tuple(parts))
    }
    return {digest: _seal_part(text, account) for digest, text in parts.items() if digest not in have}


def _write_parts(
    conn, message_id: int, parts: dict[str, str], account: str, sealed: dict[str, tuple[bytes, int]] | None = None
) -> None:
    """Point the row at exactly *parts*: store new ones (sealed, from *sealed* when it has
    them), drop refs it no longer uses."""
    old = {str(r[0]) for r in conn.execute("SELECT hash FROM message_part_refs WHERE message_id=?", (message_id,))}
    for digest, text in parts.items():
        if digest in old:
            continue
        if conn.execute("SELECT 1 FROM message_parts WHERE hash=?", (digest,)).fetchone() is None:
            blob = (sealed or {}).get(digest) or _seal_part(text, account)
            conn.execute(
                "INSERT INTO message_parts(hash, data, size, created) VALUES (?, ?, ?, ?)",
                (digest, *blob, time.time()),
            )
        conn.execute("INSERT INTO message_part_refs(message_id, hash) VALUES (?, ?)", (message_id, digest))
    stale = old - parts.keys()
    if stale:
        conn.executemany(
            "DELETE FROM message_part_refs WHERE message_id=? AND hash=?", [(message_id, d) for d in stale]
        )


def _part_reader(packed: dict[str, bytes], account: str) -> Callable[[str], str | None]:
    inflated: dict[str, str] = {}

    def part_text(digest: str) -> str | None:
        text = inflated.get(digest)
        if text is None and digest in packed:
            try:
                raw = account_rows.open_blob(packed[digest], account)
                text = inflated[digest] = zlib.decompress(raw).decode("utf-8")
            except (zlib.error, UnicodeDecodeError):
                return None
        return text

    return part_text


def _conv_parts(conn, conv_id: str, account: str) -> Callable[[str], str | None]:
    """Part JSON by hash for one chat, read in one query and opened on first use."""
    packed = {
        str(h): bytes(data)
        for h, data in conn.execute(
            "SELECT hash, data FROM message_parts WHERE hash IN (SELECT r.hash FROM message_part_refs r "
            "JOIN messages m ON m.id = r.message_id WHERE m.conv_id=?)",
            (conv_id,),
        )
    }
    return _part_reader(packed, account)


def _message_parts(conn, message_id: int, account: str) -> Callable[[str], str | None]:
    packed = {
        str(h): bytes(data)
        for h, data in conn.execute(
            "SELECT p.hash, p.data FROM message_parts p JOIN message_part_refs r ON r.hash = p.hash "
            "WHERE r.message_id=?",
            (message_id,),
        )
    }
    return _part_reader(packed, account)


def _interned(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    return {sys.intern(k): v for k, v in pairs}


def _message(body: str, fmt: int, account: str, parts: Callable[[], Callable[[str], str | None]]) -> Any:
    """One stored row as the message it was saved as; ``ValueError`` when it isn't one.
    Keys are interned: every row shares one copy of "role", "content", ..."""
    text = account_rows.open_text(body, account)
    if fmt == FMT_PARTS:
        return _decode_parts(json.loads(text, object_pairs_hook=_interned), parts())
    return json.loads(text, object_pairs_hook=_interned)


# --------------------------------------------------------------------------- older rows


def _compact_stored(body: str, digest_of: Callable[[str], str]) -> tuple[str, dict[str, str]] | None:
    """The compact form of a stored ``fmt`` 0 body, only if it reads back as the same JSON."""
    try:
        message = json.loads(body)
    except ValueError:
        return None
    stored, fmt, parts = _compact(message, body, digest_of)
    if fmt != FMT_PARTS:
        return None
    try:
        again = _decode_parts(json.loads(stored), parts.get)
    except ValueError:
        return None
    if _dumps(again) != _dumps(message):
        return None
    return stored, parts


def seal_stored_messages(account: str | None = None, *, batch: int = 40, pause_s: float = 0.05) -> dict[str, int]:
    """Seal the account's message rows still in plaintext (saved before 0024, or by an older
    build), a few rows per transaction so chats keep saving meanwhile. A large row from
    before message parts gets the compact form on the way. Then its payloads from before.

    Resumable: a sealed row is never looked at again. A row is replaced only when it still
    holds what was read (a save meanwhile wrote it sealed); its hash stays the same.
    """
    account = account or account_rows.current()
    key = account_rows.ready(account)
    digest_of = functools.partial(account_rows.part_digest, key=key)
    conn = db.connect()
    sealed = compacted = scanned = cursor = 0
    while True:
        rows = conn.execute(
            "SELECT m.id, m.body, m.fmt, m.text FROM messages m JOIN conversations c ON c.id = m.conv_id "
            "WHERE c.account_id=? AND m.id>? AND substr(m.body, 1, 5)<>? ORDER BY m.id LIMIT ?",
            (account, cursor, _SEALED, batch),
        ).fetchall()
        if not rows:
            break
        todo: list[tuple[int, str, int, str, str, dict[str, str]]] = []
        for row_id, body, fmt, text in rows:
            scanned += 1
            stored, new_fmt, parts = str(body), int(fmt), {}
            if new_fmt == FMT_PLAIN and len(stored) >= PART_MIN_CHARS:
                done = _compact_stored(stored, digest_of)
                if done is not None:
                    (stored, parts), new_fmt = done, FMT_PARTS
            blind = account_rows.blind_text(str(text or ""), key)
            todo.append((int(row_id), str(body), new_fmt, account_rows.seal(stored, account), blind, parts))
        with db.write_txn(conn):
            for row_id, old, new_fmt, body, blind, parts in todo:
                now = conn.execute("SELECT body FROM messages WHERE id=?", (row_id,)).fetchone()
                if now is None or str(now[0]) != old:
                    continue  # saved again since it was read; that save sealed it
                if parts:
                    _write_parts(conn, row_id, parts, account)
                    compacted += 1
                conn.execute(
                    "UPDATE messages SET body=?, fmt=?, text=?, account_id=? WHERE id=?",
                    (body, new_fmt, blind, account, row_id),
                )
                sealed += 1
        cursor = int(rows[-1][0])
        if pause_s:
            time.sleep(pause_s)
    if sealed:
        # The index still holds the old rows' words in segments it marked deleted; one
        # merge leaves only the tokens.
        with db.write_txn(conn):
            conn.execute("INSERT INTO message_fts(message_fts) VALUES('optimize')")
    return {"sealed": sealed, "compacted": compacted, "scanned": scanned, "parts": _seal_stored_parts(conn, account, batch)}


def _seal_stored_parts(conn, account: str, batch: int) -> int:
    """Seal in place the plaintext payloads the account's messages use (saved before 0024)."""
    sealed = 0
    cursor = ""
    while True:
        rows = conn.execute(
            "SELECT p.hash, p.data FROM message_parts p WHERE p.hash>? AND substr(p.data, 1, 4)<>? AND EXISTS ("
            "SELECT 1 FROM message_part_refs r JOIN messages m ON m.id = r.message_id "
            "JOIN conversations c ON c.id = m.conv_id WHERE r.hash = p.hash AND c.account_id=?) "
            "ORDER BY p.hash LIMIT ?",
            (cursor, _SEALED_BLOB, account, batch),
        ).fetchall()
        if not rows:
            return sealed
        todo = [(str(h), bytes(data), account_rows.seal_blob(bytes(data), account)) for h, data in rows]
        with db.write_txn(conn):
            for digest, old, new in todo:
                sealed += conn.execute(
                    "UPDATE message_parts SET data=? WHERE hash=? AND data=?", (new, digest, old)
                ).rowcount
        cursor = todo[-1][0]


# --------------------------------------------------------------------------- opened rows
#
# Opening a row costs a DPAPI call plus its parts, so a load of a long chat cost
# seconds. This keeps each row's decoded message (its shell) under the row hash it was
# stored with, so a row another writer changed (a new hash) is opened afresh, never
# served stale. A load returns new dicts and lists around the shell's strings: every
# copy of a chat shares one set of strings, and no caller can change the shell. A save
# adds the rows it synced from the saver's own messages (as main's ``_remember_rows``
# did), so a load right after it decodes nothing and shares the saver's strings. It
# holds the last ``_OPENED_MAX_CHATS`` chats loaded or saved and at most
# ``_OPENED_MAX_BYTES`` of shells as Python allocates them (see :func:`_shell`).

_OPENED_MAX_CHATS = 8
_OPENED_MAX_BYTES = 32 * 1024 * 1024
_NODE_BYTES = 64  # an object header plus its slot in the parent
_KEY_BYTES = 48  # a dict entry (hash, key, value) plus spare table room; keys are shared
_opened_lock = threading.Lock()
# conv_id -> {seq: (row hash, shell, size)}, least recently loaded chat first.
_opened: OrderedDict[str, dict[int, tuple[str, Any, int]]] = OrderedDict()
_opened_size = 0


def _copy_containers(value: Any) -> Any:
    """New dicts and lists around the same (immutable) strings and numbers."""
    kind = type(value)
    if kind is dict:
        return {k: _copy_containers(v) for k, v in value.items()}
    if kind is list:
        return [_copy_containers(v) for v in value]
    return value


def _shell(value: Any) -> tuple[Any, int] | None:
    """In one walk: ``_copy_containers(value)`` and an upper bound on what it allocates
    (``_NODE_BYTES`` per string, number, list and dict, plus string chars, 4 bytes each
    past ASCII, 8 bytes per list item and ``_KEY_BYTES`` per dict key). None when it holds
    anything else (a tuple, an object), which would not read back as itself."""
    size = 0

    def copy(v: Any) -> Any:
        nonlocal size
        kind = type(v)
        if kind is str:
            size += _NODE_BYTES + (len(v) if v.isascii() else 4 * len(v))
            return v
        if v is None or kind in (bool, int, float):
            size += _NODE_BYTES
            return v
        if kind is list:
            size += _NODE_BYTES + 8 * len(v)
            return [copy(x) for x in v]
        if kind is dict:
            size += _NODE_BYTES + _KEY_BYTES * len(v)
            out = {}
            for k, x in v.items():
                if type(k) is not str:
                    raise TypeError
                out[k] = copy(x)
            return out
        raise TypeError

    try:
        return copy(value), size
    except TypeError:
        return None


def _opened_touch(conv_id: str) -> None:
    """A load of *conv_id*: it becomes the most recently loaded chat."""
    with _opened_lock:
        if conv_id in _opened:
            _opened.move_to_end(conv_id)


def _opened_get(conv_id: str, seq: int, h: str) -> Any | None:
    """The cached shell itself: callers hand out only ``_copy_containers`` of it."""
    with _opened_lock:
        hit = _opened.get(conv_id, {}).get(seq)
        return None if hit is None or hit[0] != h else hit[1]


def _opened_drop_chat(conv_id: str) -> None:
    global _opened_size
    _opened_size -= sum(entry[2] for entry in _opened.pop(conv_id).values())


def _opened_put(conv_id: str, seq: int, h: str, message: Any, *, add: bool = True) -> None:
    """Cache a copy of *message* (the caller keeps its own); *add* False only refreshes."""
    with _opened_lock:
        hit = _opened.get(conv_id, {}).get(seq)
        if (hit is None and not add) or (hit is not None and hit[0] == h):
            return  # nothing to refresh, or already this row
    _opened_store(conv_id, seq, h, _shell(message), add=add)


def _opened_store(conv_id: str, seq: int, h: str, shell: tuple[Any, int] | None, *, add: bool = True) -> None:
    """Keep *shell* (from :func:`_shell`) as the row; None drops what the cache had there."""
    global _opened_size
    shell, size = (None, 0) if shell is None or shell[1] > _OPENED_MAX_BYTES else shell
    with _opened_lock:
        rows = _opened.get(conv_id)
        if rows is None:
            if not add:
                return
            rows = _opened[conv_id] = {}
            while len(_opened) > _OPENED_MAX_CHATS:
                _opened_drop_chat(next(iter(_opened)))
        elif not add and seq not in rows:
            return
        old = rows.pop(seq, None)
        if old is not None:
            _opened_size -= old[2]
        if shell is None:
            return  # not cacheable: a stale entry must not stay
        _opened_size += size
        rows[seq] = (h, shell, size)
        # Over the byte cap: rows go from the least recently loaded chat first (from this
        # one last, so a chat bigger than the cap keeps what fits).
        while _opened_size > _OPENED_MAX_BYTES:
            oldest = next(iter(_opened))
            if not _opened[oldest]:
                del _opened[oldest]
                continue
            _, (_, _, dropped) = _opened[oldest].popitem()
            _opened_size -= dropped


def _opened_saved(conv_id: str, messages: list[Any], start: int, stats: dict[str, Any]) -> None:
    """After a save commits: keep the rows it synced (``hashes`` from *start*, the listed
    ``dirty_hashes``) from the saver's messages, except another writer's (``theirs``)."""
    theirs = set(stats.get("theirs") or ())
    synced = [*(stats.get("dirty_hashes") or {}).items(), *enumerate(stats.get("hashes") or (), start)]
    _opened_touch(conv_id)
    # Rows already cached under their hash are not walked again. Older chats' rows go
    # first when over the cap, this chat's last, so *room* is what this chat may hold: a row
    # past it would be dropped at once, so it is never copied (a chat over the cap re-walked
    # its uncached rows on every save).
    with _opened_lock:
        cached = _opened.get(conv_id, {})
        room = _OPENED_MAX_BYTES - sum(entry[2] for entry in cached.values())
        todo: list[tuple[int, str, bool]] = []
        for seq, h in synced:
            hit = cached.get(seq)
            if seq in theirs or seq >= len(messages) or (hit is not None and hit[0] == h):
                continue
            room += 0 if hit is None else hit[2]
            todo.append((seq, h, hit is not None))
    for seq, h, stale in todo:
        shell = _shell(messages[seq]) if room > 0 else None
        if shell is not None:
            room -= shell[1]
            if room < 0:
                shell = None
        if shell is not None or stale:
            _opened_store(conv_id, seq, h, shell)  # None drops the stale row


def _opened_forget(conv_id: str) -> None:
    with _opened_lock:
        if conv_id in _opened:
            _opened_drop_chat(conv_id)


# --------------------------------------------------------------------------- conversations


def _row_to_doc(conn, row, account: str, *, with_messages: bool) -> dict[str, Any]:
    doc: dict[str, Any] = {}
    try:
        doc.update(json.loads(account_rows.open_cached(row["state"] or "{}", account)))
    except ValueError:
        pass
    for col in _COLUMNS:
        doc[col] = row[col]
    doc["title"] = account_rows.open_cached(str(row["title"] or ""), account)
    doc["is_group"] = bool(row["is_group"])
    doc["id"] = row["id"]
    doc["skill_snapshot"] = _snapshot_text(conn, row["skill_snapshot_hash"])
    doc["messages"] = _messages(conn, row["id"], account) if with_messages else []
    return doc


def conv_get(conv_id: str, *, project_id: str | None = None, with_messages: bool = True) -> dict[str, Any] | None:
    account = account_rows.current()
    conn = db.connect()
    if project_id is None:
        row = conn.execute("SELECT * FROM conversations WHERE id=? AND account_id=?", (conv_id, account)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM conversations WHERE id=? AND project_id=? AND account_id=?", (conv_id, project_id, account)
        ).fetchone()
    if row is None:
        return None
    return _row_to_doc(conn, row, account, with_messages=with_messages)


def conv_project_id(conv_id: str) -> str | None:
    row = db.connect().execute(
        "SELECT project_id FROM conversations WHERE id=? AND account_id=?", (conv_id, account_rows.current())
    ).fetchone()
    return None if row is None else str(row[0])


def conv_account(conv_id: str) -> str | None:
    """Whose chat this is (any account), or None when there is no such chat."""
    row = db.connect().execute("SELECT account_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return None if row is None else str(row[0])


def conv_list(project_id: str, *, with_messages: bool = False) -> list[dict[str, Any]]:
    account = account_rows.current()
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM conversations WHERE account_id=? AND project_id=? ORDER BY sort_order, updated DESC, id",
        (account, project_id),
    ).fetchall()
    return [_row_to_doc(conn, r, account, with_messages=with_messages) for r in rows]


def conv_list_all_projects(*, with_messages: bool = False) -> list[tuple[str, dict[str, Any]]]:
    account = account_rows.current()
    conn = db.connect()
    rows = conn.execute("SELECT * FROM conversations WHERE account_id=? ORDER BY project_id, id", (account,)).fetchall()
    return [(str(r["project_id"]), _row_to_doc(conn, r, account, with_messages=with_messages)) for r in rows]


def conv_count(project_id: str) -> int:
    """Chats of every account in one project: AppData upkeep keeps a project's folder
    (attachments) while any account still has a chat in it."""
    return int(db.connect().execute("SELECT count(*) FROM conversations WHERE project_id=?", (project_id,)).fetchone()[0])


def conv_if_settings_newer(conv_id: str, edited: float) -> dict[str, Any] | None:
    """The stored chat (no messages) when its ducky settings changed after *edited*, else None."""
    account = account_rows.current()
    row = db.connect().execute(
        "SELECT state FROM conversations WHERE id=? AND account_id=?", (conv_id, account)
    ).fetchone()
    if row is None:
        return None
    try:
        state = json.loads(account_rows.open_cached(row[0] or "{}", account))
        newer = float(state.get("settings_edited") or 0.0) > float(edited or 0.0)
    except (ValueError, TypeError, AttributeError):
        return None
    return conv_get(conv_id, with_messages=False) if newer else None


def conv_set_counts(conv_id: str, *, tool_call_count: int, file_count: int) -> None:
    """The sidebar's two counters, after a save that synced messages."""
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute(
            "UPDATE conversations SET tool_call_count=?, file_count=? WHERE id=? AND account_id=?",
            (int(tool_call_count), int(file_count), conv_id, account_rows.current()),
        )


def conv_message_summary(conv_id: str) -> tuple[int, bool]:
    """(message count, any assistant row) without loading bodies."""
    row = db.connect().execute(
        "SELECT count(m.id), coalesce(max(m.role='assistant'), 0) FROM messages m "
        "JOIN conversations c ON c.id = m.conv_id WHERE m.conv_id=? AND c.account_id=?",
        (conv_id, account_rows.current()),
    ).fetchone()
    return int(row[0]), bool(row[1])


# Delta-save contract (migration 0026). Writers say what changed; the database guards
# against other writers.
#
#   conv_save(project_id, doc, *, messages, dirty_from: int | None = None,
#             expect_rev: int | None = None, dirty_rows: list[int] | None = None) -> dict
#
# - Rows are serialised, hashed, compacted and sealed before the write lock; the
#   transaction only reads the stored hashes, applies the rules below and writes.
# - ``dirty_rows``: seqs below ``dirty_from`` this copy changed. On "delta" and "merge"
#   they are written too (by hash, with ``base_hashes``), and nothing else below the
#   tail is read; their stored hashes come back as ``dirty_hashes`` ({seq: hash}). The
#   full path ignores them (it compares every row).
#
# - ``conversations.msg_rev`` is bumped by SQLite triggers on every INSERT/UPDATE/DELETE
#   on ``messages`` (any connection, any process, raw SQL), never by Python.
# - Delta path: only when ``messages is not None and dirty_from is not None and
#   expect_rev == stored msg_rev``. Writes ``messages[dirty_from:]`` (by hash, as the
#   full path does) and deletes rows with ``seq >= len(messages)``; rows below
#   ``dirty_from`` are not read or compared. ``dirty_from`` is clamped to
#   ``0..len(messages)``.
# - Merge path: ``dirty_from > 0`` but a stale ``expect_rev`` (another writer touched the
#   chat): writes only ``messages[dirty_from:]`` (starting no later than the stored end)
#   and never deletes, so another writer's older rows and newer tail survive.
# - Full path: ``dirty_from`` None or 0 with a stale rev, or a new chat: an explicit
#   reassign. Every message is hashed and compared with its stored row; rows past the
#   end are deleted.
# - ``messages_since(conv_id, seq) -> (messages[seq:], msg_rev, count)`` refreshes a
#   copy's tail without loading the whole chat.
# - Meta path: ``messages is None``; the transcript and msg_rev are untouched.
# - Returns the existing stats (inserted, updated, deleted, bytes, skipped when the chat
#   is another account's or deleted) plus:
#     ``msg_rev``     the chat's rev after the write; pass it as the next ``expect_rev``
#     ``path``        "delta" | "merge" | "full" | "meta"
#     ``hashes``      row hash per seq for ``seq >= hashes_from`` (absent on "meta")
#     ``hashes_from`` 0 on "full", the first seq written on "delta" / "merge"
#     ``theirs``      seqs kept as another writer's (see ``base_hashes``)
# - ``base_hashes`` (optional, on every path): each row's hash when this copy last read
#   or saved it. A row whose hash is still its base (this copy didn't change it) but
#   whose stored hash differs is another writer's newer edit and is never written over;
#   a row both changed goes to this copy. Keep the base from ``messages_get_rev`` /
#   ``messages_since(..., with_hashes=True)``, and after a save take ``hashes`` as the new
#   base except at the ``theirs`` seqs (this copy still holds the old content there).
# - Loading: ``messages_get_rev(conv_id) -> (messages, msg_rev)`` reads both in one
#   snapshot, so a freshly loaded chat saves by delta at once; ``conv_msg_rev(conv_id)``
#   reads the rev alone. ``messages_get`` keeps returning the list.
# - Writers that cannot track changes pass neither keyword and always take the full
#   path, so the guard is never weaker than today.
#
#   conv_save(pid, doc, messages=m, dirty_from=4998, expect_rev=812)
#     -> {..., "msg_rev": 814, "path": "delta", "hashes_from": 4998}


def conv_save(
    project_id: str,
    doc: dict[str, Any],
    *,
    messages: list[dict[str, Any]] | None,
    dirty_from: int | None = None,
    expect_rev: int | None = None,
    base_hashes: list[str] | None = None,
    dirty_rows: list[int] | None = None,
) -> dict[str, Any]:
    """Upsert the row; when *messages* is given, sync rows by content hash.

    Returns counts: rows inserted/updated/deleted for messages, plus ``msg_rev`` and
    ``path`` (the contract above). Passing ``messages=None`` leaves the transcript
    untouched (metadata-only save). A chat of another account is never written
    (``skipped``).
    """
    account = account_rows.current()
    key = account_rows.ready(account)
    conn = db.connect()
    state = {k: v for k, v in doc.items() if k not in _STATE_EXCLUDED}
    values = {col: doc.get(col) for col in _COLUMNS}
    values["is_group"] = 1 if doc.get("is_group") else 0
    stats: dict[str, Any] = {"inserted": 0, "updated": 0, "deleted": 0, "bytes": 0}
    rows: list[int] = []
    pre: dict[int, tuple[str, str]] = {}
    prepped: dict[int, tuple] = {}
    if messages is not None:
        n = len(messages)
        if dirty_from is None:
            tail = 0
        else:
            tail = max(0, min(int(dirty_from), n))
            rows = sorted({int(s) for s in dirty_rows or () if 0 <= int(s) < tail})
        pre, prepped = _encode_rows(conn, str(doc["id"]), messages, rows, tail, account, key, base_hashes)
    with db.write_txn(conn):
        owner = conn.execute(
            "SELECT account_id, title, state, msg_rev FROM conversations WHERE id=?", (doc["id"],)
        ).fetchone()
        if owner is not None and str(owner[0]) != account:
            _log.warning("skipped saving chat %s: it belongs to another account", doc["id"])
            return {**stats, "skipped": 1}
        if _was_deleted(conn, str(doc["id"])):
            # A turn, team message or other window still holding the chat saved it after
            # it was deleted: never bring it back.
            _log.info("skipped saving deleted chat %s", doc["id"])
            return {**stats, "skipped": 1}
        stored = ("", "") if owner is None else (str(owner[1] or ""), str(owner[2] or ""))
        title = _sealed_as(stored[0], str(values["title"] or ""), account)
        sealed_state = _sealed_as(stored[1], _dumps(state), account)
        snap_hash = _snapshot_put(conn, "skill_index", str(doc.get("skill_snapshot") or ""))
        conn.execute(
            "INSERT INTO conversations(id, account_id, project_id, folder_id, title, created, updated, sort_order, "
            "provider, model, coding_agent, profile_id, file_path, parent_conv_id, leader_conv_id, is_group, "
            "tool_call_count, file_count, message_count, skill_snapshot_hash, state) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "coalesce((SELECT message_count FROM conversations WHERE id=?), 0), ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET project_id=excluded.project_id, folder_id=excluded.folder_id, "
            "title=excluded.title, created=excluded.created, updated=excluded.updated, sort_order=excluded.sort_order, "
            "provider=excluded.provider, model=excluded.model, coding_agent=excluded.coding_agent, "
            "profile_id=excluded.profile_id, file_path=excluded.file_path, parent_conv_id=excluded.parent_conv_id, "
            "leader_conv_id=excluded.leader_conv_id, is_group=excluded.is_group, tool_call_count=excluded.tool_call_count, "
            "file_count=excluded.file_count, skill_snapshot_hash=excluded.skill_snapshot_hash, state=excluded.state",
            (
                doc["id"],
                account,
                project_id,
                str(values["folder_id"] or ""),
                title,
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
                sealed_state,
            ),
        )
        stats["path"] = "meta"
        if messages is not None:
            # The rev was read in this write transaction: no other writer can slip in.
            current = owner is not None and expect_rev is not None and int(owner[3]) == expect_rev
            if dirty_from is not None and current:
                path, start = "delta", max(0, min(int(dirty_from), len(messages)))
            elif dirty_from and owner is not None:
                # A stale copy: rows below dirty_from are the store's (another writer may have
                # changed them), and it never truncates. Starting no later than the stored end
                # leaves no gap.
                count = _row_count(conn, doc["id"])
                path, start = "merge", max(0, min(int(dirty_from), len(messages), count))
            else:
                path, start = "full", 0  # an explicit reassign: this copy is the transcript
            listed = [s for s in rows if s < start] if path != "full" else []
            stats.update(_sync_messages(
                conn, doc["id"], messages, start, account, key, truncate=path != "merge", base=base_hashes,
                rows=listed, pre=pre, prepped=prepped,
            ))
            stats.update(path=path, hashes_from=start)
            count = len(messages) if path != "merge" else _row_count(conn, doc["id"])
            conn.execute("UPDATE conversations SET message_count=? WHERE id=?", (count, doc["id"]))
        stats["msg_rev"] = int(
            conn.execute("SELECT msg_rev FROM conversations WHERE id=?", (doc["id"],)).fetchone()[0]
        )
    if messages is not None:
        _opened_saved(str(doc["id"]), messages, stats["hashes_from"], stats)
    return stats


def _sealed_as(stored: str, text: str, account: str) -> str:
    """*text* sealed, reusing the stored value when it already holds *text*: opened
    values are memoised, so an unchanged title or state costs no DPAPI call."""
    # ponytail: a state over open_cached's 256 KB item cap is opened every save; cache it if that shows up.
    try:
        if account_rows.is_sealed(stored) and account_rows.open_cached(stored, account) == text:
            return stored
    except Exception:  # noqa: BLE001 — a value that won't open is sealed afresh
        pass
    return account_rows.seal(text, account)


def _row_count(conn, conv_id: str) -> int:
    return int(conn.execute("SELECT coalesce(max(seq), -1) + 1 FROM messages WHERE conv_id=?", (conv_id,)).fetchone()[0])


def _stored_rows(conn, conv_id: str, rows: list[int], start: int) -> dict[int, tuple[str, int]]:
    """(hash, id) by seq for the listed *rows* and every row from *start* on."""
    where = f"seq>=? OR seq IN ({','.join('?' * len(rows))})" if rows else "seq>=?"
    return {
        int(r[0]): (str(r[1]), int(r[2]))
        for r in conn.execute(f"SELECT seq, hash, id FROM messages WHERE conv_id=? AND ({where})", (conv_id, start, *rows))
    }


def _encode_rows(
    conn, conv_id: str, messages: list[Any], rows: list[int], tail: int, account: str, key: bytes,
    base: list[str] | None,
) -> tuple[dict[int, tuple[str, str]], dict[int, tuple]]:
    """Before the write lock: (body, hash) for the listed *rows* and ``messages[tail:]``, and
    the full row write (see :func:`_prep_row`) for those whose stored hash differs now.
    The transaction re-reads the hashes; a row it needs that isn't here is encoded there."""
    pre: dict[int, tuple[str, str]] = {}
    for seq in (*rows, *range(tail, len(messages))):
        body = _dumps(messages[seq])
        pre[seq] = (body, _hash(body))
    stored = _stored_rows(conn, conv_id, rows, tail) if pre else {}
    digest_of = functools.partial(account_rows.part_digest, key=key)
    prepped: dict[int, tuple] = {}
    for seq, (body, h) in pre.items():
        row = stored.get(seq)
        if row is not None and (row[0] == h or (base is not None and seq < len(base) and base[seq] == h)):
            continue  # unchanged, or another writer's (theirs): not written
        prepped[seq] = _prep_row(messages[seq], body, h, account, key, digest_of)
    return pre, prepped


def _sync_messages(
    conn, conv_id: str, messages: list[dict[str, Any]], start: int, account: str, key: bytes, *,
    truncate: bool = True, base: list[str] | None = None, rows: list[int] | None = None,
    pre: dict[int, tuple[str, str]] | None = None, prepped: dict[int, tuple] | None = None,
) -> dict[str, Any]:
    """Write the listed *rows* (all below *start*) and ``messages[start:]`` where the stored
    row's hash differs and (*truncate*) drop rows past the end; other rows below *start* are
    not read. With *base* (each row's hash when this copy last read it), a row the copy
    didn't change (its hash is the base) that another writer did is left as theirs
    (``theirs`` lists those seqs). ``hashes`` is each seq's stored row hash from *start* on
    (callers key caches on it), ``dirty_hashes`` the same for the listed rows. *pre* /
    *prepped* are rows :func:`_encode_rows` encoded before the lock."""
    rows, pre, prepped = rows or [], pre or {}, prepped or {}
    existing = _stored_rows(conn, conv_id, rows, start)
    digest_of = functools.partial(account_rows.part_digest, key=key)
    hashes: list[str] = []
    dirty_hashes: dict[int, str] = {}
    theirs: list[int] = []
    inserted = updated = deleted = nbytes = 0
    for seq in (*rows, *range(start, len(messages))):
        enc = pre.get(seq)
        if enc is None:
            body = _dumps(messages[seq])
            enc = (body, _hash(body))
        body, h = enc
        row = existing.get(seq)
        known = base[seq] if base is not None and seq < len(base) else None
        got = h
        if row is not None and row[0] != h and known and h == known:
            # Unchanged here, changed by another writer since this copy read it: theirs is newer.
            got = row[0]
            theirs.append(seq)
        elif row is None or row[0] != h:
            nbytes += len(body)
            prep = prepped.get(seq) or _prep_row(messages[seq], body, h, account, key, digest_of)
            _write_row(conn, conv_id, seq, row, prep, account, refresh=False)  # conv_save keeps it after commit
            if row is not None:
                updated += 1
            else:
                inserted += 1
        if seq < start:
            dirty_hashes[seq] = got
        else:
            hashes.append(got)
    if truncate and any(seq >= len(messages) for seq in existing):
        deleted = conn.execute("DELETE FROM messages WHERE conv_id=? AND seq>=?", (conv_id, len(messages))).rowcount
    return {"inserted": inserted, "updated": updated, "deleted": deleted, "bytes": nbytes, "hashes": hashes,
            "dirty_hashes": dirty_hashes, "theirs": theirs}


def _prep_row(
    source: Any, body: str, h: str, account: str, key: bytes, digest_of: Callable[[str], str]
) -> tuple[str, Any, tuple, dict[str, str]]:
    """Everything a row write needs that reads no database (big fields as parts, the
    seal, search text), so it runs before the write lock: (body, source, params, parts)."""
    stored, fmt, parts = _compact(source, body, digest_of)
    params = (
        h,
        str(source.get("role") or "") if isinstance(source, dict) else "",
        float(source.get("ts") or 0.0) if isinstance(source, dict) else 0.0,
        str(source.get("run_id") or "") if isinstance(source, dict) else "",
        account_rows.blind_text(message_search_text(source), key) if isinstance(source, dict) else "",
        account_rows.seal(stored, account),
        fmt,
    )
    return body, source, params, parts


def _write_row(
    conn, conv_id: str, seq: int, row: tuple[str, int] | None, prep: tuple, account: str, *,
    sealed: dict[str, tuple[bytes, int]] | None = None, refresh: bool = True,
) -> None:
    """Write one message row from :func:`_prep_row`; *row* is the existing (hash, id) at
    *seq*, or None to insert. *refresh* False: the caller refreshes the opened cache after
    the lock (the copy walks the whole message)."""
    _body, source, params, parts = prep
    # Refresh only (loads add); a stale entry after a rollback only misses: the hash differs.
    if refresh:
        _opened_put(conv_id, seq, params[0], source, add=False)
    if row is not None:
        message_id = row[1]
        conn.execute(
            "UPDATE messages SET hash=?, role=?, ts=?, run_id=?, text=?, body=?, fmt=? WHERE id=?",
            (*params, message_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO messages(conv_id, seq, hash, role, ts, run_id, text, body, fmt, account_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conv_id, seq, *params, account),
        )
        message_id = int(cur.lastrowid)
    if parts or row is not None:
        _write_parts(conn, message_id, parts, account, sealed)


def upsert_run_message(
    conv_id: str, run_id: str, message: dict[str, Any], *, updated: float, stats: dict[str, int] | None = None
) -> dict[str, Any]:
    """Write a turn's in-flight assistant as ONE row: the run's last assistant after the
    last user row is replaced, else the message is appended. Checkpoints used to load
    and re-save the whole chat on every tool, a cost that grew with the chat.

    Returns ``{"seq", "count"}`` (``skipped`` when the chat is gone or not this
    account's). *stats* sets ``tool_call_count`` / ``file_count`` when given.
    """
    account = account_rows.current()
    key = account_rows.ready(account)
    conn = db.connect()
    digest_of = functools.partial(account_rows.part_digest, key=key)
    body = _dumps(message)
    h = _hash(body)
    # Everything but SQL runs before the lock: encode, hash, seal, compress parts. Round 5
    # held _write_lock ~110 ms per checkpoint, mostly waiting on the GIL mid-Python.
    prep = _prep_row(message, body, h, account, key, digest_of)
    sealed = _seal_new_parts(conn, prep[3], account)
    wrote = False
    with db.write_txn(conn):
        owner = conn.execute("SELECT account_id FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if owner is None or str(owner[0]) != account or _was_deleted(conn, conv_id):
            return {"skipped": 1}
        last_user = conn.execute(
            "SELECT coalesce(max(seq), -1) FROM messages WHERE conv_id=? AND role='user'", (conv_id,)
        ).fetchone()[0]
        hit = conn.execute(
            "SELECT seq, hash, id FROM messages WHERE conv_id=? AND seq>? AND role='assistant' AND run_id=? "
            "ORDER BY seq DESC LIMIT 1",
            (conv_id, int(last_user), run_id),
        ).fetchone() if run_id else None
        count = int(conn.execute("SELECT count(*) FROM messages WHERE conv_id=?", (conv_id,)).fetchone()[0])
        if hit is not None:
            seq, row = int(hit[0]), (str(hit[1]), int(hit[2]))
        else:
            seq, row = count, None
            count += 1
        if row is None or row[0] != h:
            _write_row(conn, conv_id, seq, row, prep, account, sealed=sealed, refresh=False)
            wrote = True
        conn.execute("UPDATE conversations SET updated=?, message_count=? WHERE id=?", (float(updated), count, conv_id))
        if stats:
            conn.execute(
                "UPDATE conversations SET tool_call_count=?, file_count=? WHERE id=?",
                (int(stats.get("tool_call_count") or 0), int(stats.get("file_count") or 0), conv_id),
            )
    if wrote:
        _opened_put(conv_id, seq, h, message, add=False)
    return {"seq": seq, "count": count}


def _messages(
    conn, conv_id: str, account: str, start: int = 0, hashes: list[str] | None = None
) -> list[dict[str, Any]]:
    """The transcript from seq *start*; *hashes* gets each message's row hash (a copy's
    base for ``conv_save(base_hashes=...)``). A row whose hash matches an opened copy is
    read from it."""
    if hashes is None:
        hashes = []
    rows = conn.execute(
        "SELECT seq, hash, body, fmt FROM messages WHERE conv_id=? AND seq>=? ORDER BY seq", (conv_id, start)
    ).fetchall()
    _opened_touch(conv_id)
    reader: list[Callable[[str], str | None]] = []

    def parts() -> Callable[[str], str | None]:
        if not reader:
            reader.append(_conv_parts(conn, conv_id, account))
        return reader[0]

    out: list[dict[str, Any]] = []
    for seq, h, body, fmt in rows:
        shell = _opened_get(conv_id, int(seq), str(h))
        if shell is not None:
            out.append(_copy_containers(shell))
            hashes.append(str(h))
            continue
        try:
            message = _message(str(body), int(fmt), account, parts)
        except ValueError:
            continue
        out.append(message)
        hashes.append(str(h))
        _opened_put(conv_id, int(seq), str(h), message)
    return out


def messages_get(conv_id: str) -> list[dict[str, Any]]:
    account = account_rows.current()
    conn = db.connect()
    if not _owned(conn, conv_id, account):
        return []
    return _messages(conn, conv_id, account)


def conv_msg_rev(conv_id: str) -> int | None:
    """The chat's message revision (migration 0026), or None when it isn't this account's."""
    row = db.connect().execute(
        "SELECT msg_rev FROM conversations WHERE id=? AND account_id=?", (conv_id, account_rows.current())
    ).fetchone()
    return None if row is None else int(row[0])


def messages_get_rev(conv_id: str, *, with_hashes: bool = False) -> tuple:
    """The transcript and its ``msg_rev`` from one read snapshot, so the caller can save
    by delta at once (``expect_rev``); *with_hashes* adds each row's hash (the base)."""
    account = account_rows.current()
    conn = db.connect()
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN")
    try:
        rev = conv_msg_rev(conv_id)
        hashes: list[str] = []
        msgs = [] if rev is None else _messages(conn, conv_id, account, 0, hashes)
        return (msgs, rev, hashes) if with_hashes else (msgs, rev)
    finally:
        if own:
            conn.execute("COMMIT")


def messages_since(conv_id: str, seq: int, *, with_hashes: bool = False) -> tuple:
    """The messages from *seq* on, the chat's ``msg_rev`` and its row count, from one read
    snapshot: a copy refreshes its tail without loading the whole chat. ``([], None, 0)``
    when the chat isn't this account's. *with_hashes* adds the rows' hashes (the base)."""
    account = account_rows.current()
    conn = db.connect()
    own = not conn.in_transaction
    if own:
        conn.execute("BEGIN")
    try:
        rev = conv_msg_rev(conv_id)
        if rev is None:
            return ([], None, 0, []) if with_hashes else ([], None, 0)
        hashes: list[str] = []
        msgs = _messages(conn, conv_id, account, max(0, int(seq)), hashes)
        count = _row_count(conn, conv_id)
        return (msgs, rev, count, hashes) if with_hashes else (msgs, rev, count)
    finally:
        if own:
            conn.execute("COMMIT")


# Deleted chat and folder ids, kept in workspace_state (no new table: an older app must
# still open this database) so a late save from any process can't re-create them.
_DELETED_KEY = "deleted_chat:"
_DELETED_FOLDER_KEY = "deleted_folder:"
_DELETED_KEEP_S = 30 * 86400


def _was_deleted(conn, conv_id: str) -> bool:
    return conn.execute("SELECT 1 FROM workspace_state WHERE key=?", (_DELETED_KEY + conv_id,)).fetchone() is not None


def _mark_deleted(conn, prefix: str, ids: list[str], now: float) -> None:
    conn.executemany(
        "INSERT INTO workspace_state(key, value, updated) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
        [(prefix + i, json.dumps(now), now) for i in ids if i],
    )
    # No turn outlives a month: older marks only take space.
    conn.execute(
        "DELETE FROM workspace_state WHERE key >= ? AND key < ? AND updated < ?",
        (prefix, prefix[:-1] + ";", now - _DELETED_KEEP_S),
    )


def conv_delete(conv_id: str) -> bool:
    """Delete one of the signed-in account's chats (another account's is left alone)."""
    account = account_rows.current()
    _opened_forget(conv_id)
    conn = db.connect()
    with db.write_txn(conn):
        if not _owned(conn, conv_id, account):
            return False
        conn.execute("DELETE FROM messages WHERE conv_id=?", (conv_id,))
        cur = conn.execute("DELETE FROM conversations WHERE id=? AND account_id=?", (conv_id, account))
        _mark_deleted(conn, _DELETED_KEY, [conv_id], time.time())
    return cur.rowcount > 0


def folders_mark_deleted(folder_ids: list[str]) -> None:
    """Folders the person deleted: never written back by a stale folder list."""
    ids = [str(i) for i in folder_ids if str(i or "").strip()]
    if not ids:
        return
    conn = db.connect()
    with db.write_txn(conn):
        _mark_deleted(conn, _DELETED_FOLDER_KEY, ids, time.time())


def conv_ids_by_parent(project_id: str) -> dict[str, list[str]]:
    rows = db.connect().execute(
        "SELECT parent_conv_id, id FROM conversations WHERE account_id=? AND project_id=? AND parent_conv_id<>''",
        (account_rows.current(), project_id),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for parent, cid in rows:
        out.setdefault(str(parent), []).append(str(cid))
    return out


def conv_max_sort_order(project_id: str, folder_id: str) -> float:
    row = db.connect().execute(
        "SELECT max(sort_order) FROM conversations WHERE account_id=? AND project_id=? AND folder_id=?",
        (account_rows.current(), project_id, folder_id),
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


_PREVIEW_WORDS = 12


def _preview(text: str, query: str) -> str:
    """About a dozen words around the first one the query matched."""
    wanted = set(account_rows.words(query))
    chunks = text.split()
    at = next((i for i, chunk in enumerate(chunks) if wanted & set(account_rows.words(chunk))), 0)
    start = max(0, at - _PREVIEW_WORDS // 2 + 1)
    shown = chunks[start:start + _PREVIEW_WORDS]
    return ("…" if start else "") + " ".join(shown) + ("…" if start + _PREVIEW_WORDS < len(chunks) else "")


def search_messages(project_id: str, query: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """Body search over every message of the account in a project via FTS5 (bm25 ranked).

    Rows hold keyed word tokens; the plain query still finds rows an older build saved
    until they are sealed. Previews are made from the opened message."""
    account = account_rows.current()
    blind = account_rows.blind_query(query, account_rows.chat_key(account))
    plain = _fts_query(query)
    if not blind and not plain:
        return []
    q = f"({blind}) OR ({plain})" if blind and plain else (blind or plain)
    conn = db.connect()
    rows = conn.execute(
        "SELECT c.id AS conv_id, c.title, c.folder_id, m.seq, m.id AS message_id, m.body, m.fmt "
        "FROM message_fts JOIN messages m ON m.id = message_fts.rowid "
        "JOIN conversations c ON c.id = m.conv_id "
        "WHERE message_fts MATCH ? AND c.account_id=? AND c.project_id=? ORDER BY bm25(message_fts) LIMIT ?",
        (q, account, project_id, limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            message = _message(str(r["body"]), int(r["fmt"]), account, lambda: _message_parts(conn, r["message_id"], account))
            preview = _preview(message_search_text(message) if isinstance(message, dict) else "", query)
        except ValueError:
            preview = ""
        out.append({
            "conv_id": r["conv_id"],
            "title": account_rows.open_cached(str(r["title"] or ""), account),
            "folder_id": r["folder_id"],
            "seq": r["seq"],
            "preview": preview,
        })
    return out


def has_rows() -> bool:
    return db.connect().execute("SELECT 1 FROM conversations LIMIT 1").fetchone() is not None
