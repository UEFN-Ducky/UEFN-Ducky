"""Sealing work for :mod:`backend.store.repos.chats`, which re-exports the public names.

- :func:`seal_stored_messages`: seal the rows and payloads an account saved in plaintext
  (before migration 0024).
- :func:`prepare_save` / :func:`prepare_run_message`: encode and seal a save before the
  write lock, for a caller (the chat writer) that writes inside a transaction it holds.

The row helpers are looked up on ``chats`` when called, so a test that patches
``chats._write_parts``, ``chats._compact_stored`` or ``chats._dumps`` patches this code too.
"""

from __future__ import annotations

import functools
import time
from typing import Any

from backend.store import account_rows, db


def prepare_save(
    doc: dict[str, Any], messages: list[Any] | None = None, *, dirty_from: int | None = None,
    dirty_rows: list[int] | None = None, base_hashes: list[str] | None = None,
) -> dict[str, Any]:
    """What ``conv_save`` encodes and seals before its write lock (title, state, snapshot
    hash; with *messages*, each row's JSON, hash, parts, seal and search text), for a
    caller that calls conv_save inside a write transaction of its own (the chat writer's
    batch): pass it back as ``prepared=``. Whatever changed since is done again there."""
    from backend.store.repos import chats as c

    account = account_rows.current()
    key = account_rows.ready(account)
    conn = db.connect()
    out: dict[str, Any] = {"account": account, "meta": prepare_meta(conn, doc, account)}
    if messages is not None:
        rows, tail = c._dirty_span(messages, dirty_from, dirty_rows)
        pre, prepped, sealed = c._encode_rows(conn, str(doc["id"]), messages, rows, tail, account, key, base_hashes)
        out.update(messages=list(messages), pre=pre, prepped=prepped, sealed=sealed)
    return out


def prepare_meta(conn, doc: dict[str, Any], account: str) -> dict[str, Any]:
    """The chat row's sealed title and state against what is stored now (``seen``), and
    the snapshot hash; the transaction seals again only if ``seen`` moved."""
    from backend.store.repos import chats as c

    state = {k: v for k, v in doc.items() if k not in c._STATE_EXCLUDED}
    title, state_text = str(doc.get("title") or ""), c._dumps(state)
    snapshot = str(doc.get("skill_snapshot") or "")
    seen = conn.execute("SELECT title, state FROM conversations WHERE id=?", (doc["id"],)).fetchone()
    seen = ("", "") if seen is None else (str(seen[0] or ""), str(seen[1] or ""))
    return {
        "title": title, "state": state, "state_text": state_text, "snapshot": snapshot,
        "snap_hash": c._hash(snapshot) if snapshot else "", "seen": seen,
        "sealed": (c._sealed_as(seen[0], title, account), c._sealed_as(seen[1], state_text, account)),
    }


def meta_still(meta: dict[str, Any], doc: dict[str, Any]) -> bool:
    """*meta* was prepared from a doc with this title, state and snapshot (no encoding)."""
    from backend.store.repos import chats as c

    return (
        meta["title"] == str(doc.get("title") or "")
        and meta["snapshot"] == str(doc.get("skill_snapshot") or "")
        and meta["state"] == {k: v for k, v in doc.items() if k not in c._STATE_EXCLUDED}
    )


def still_prepared(
    prepared: dict[str, Any], messages: list[Any]
) -> tuple[dict[int, tuple[str, str]], dict[int, tuple]]:
    """*prepared*'s rows whose message is still the same object; a row changed since is
    encoded again in the transaction."""
    was = prepared["messages"]

    def same(seq: int) -> bool:
        return seq < len(messages) and seq < len(was) and messages[seq] is was[seq]

    return (
        {s: v for s, v in prepared["pre"].items() if same(s)},
        {s: v for s, v in prepared["prepped"].items() if same(s)},
    )


def prepare_run_message(message: dict[str, Any]) -> tuple:
    """The row work ``upsert_run_message`` does before its write lock; pass it back as
    ``prepared=`` (see :func:`prepare_save`)."""
    from backend.store.repos import chats as c

    account = account_rows.current()
    key = account_rows.ready(account)
    body = c._dumps(message)
    h = c._hash(body)
    prep = c._prep_row(message, body, h, account, key, functools.partial(account_rows.part_digest, key=key))
    return message, account, h, prep, c._seal_new_parts(db.connect(), prep[3], account)


def seal_stored_messages(account: str | None = None, *, batch: int = 40, pause_s: float = 0.05) -> dict[str, int]:
    """Seal the account's message rows still in plaintext (saved before 0024, or by an older
    build), a few rows per transaction so chats keep saving meanwhile. A large row from
    before message parts gets the compact form on the way. Then its payloads from before.

    Resumable: a sealed row is never looked at again. A row is replaced only when it still
    holds what was read (a save meanwhile wrote it sealed); its hash stays the same.
    """
    from backend.store.repos import chats as c

    account = account or account_rows.current()
    key = account_rows.ready(account)
    digest_of = functools.partial(account_rows.part_digest, key=key)
    conn = db.connect()
    sealed = compacted = scanned = cursor = 0
    while True:
        rows = conn.execute(
            "SELECT m.id, m.body, m.fmt, m.text FROM messages m JOIN conversations c ON c.id = m.conv_id "
            "WHERE c.account_id=? AND m.id>? AND substr(m.body, 1, 5)<>? ORDER BY m.id LIMIT ?",
            (account, cursor, c._SEALED, batch),
        ).fetchall()
        if not rows:
            break
        todo: list[tuple[int, str, int, str, str, dict[str, str]]] = []
        for row_id, body, fmt, text in rows:
            scanned += 1
            stored, new_fmt, parts = str(body), int(fmt), {}
            if new_fmt == c.FMT_PLAIN and len(stored) >= c.PART_MIN_CHARS:
                done = c._compact_stored(stored, digest_of)
                if done is not None:
                    (stored, parts), new_fmt = done, c.FMT_PARTS
            blind = account_rows.blind_text(str(text or ""), key)
            todo.append((int(row_id), str(body), new_fmt, account_rows.seal(stored, account), blind, parts))
        with db.write_txn(conn):
            for row_id, old, new_fmt, body, blind, parts in todo:
                now = conn.execute("SELECT body FROM messages WHERE id=?", (row_id,)).fetchone()
                if now is None or str(now[0]) != old:
                    continue  # saved again since it was read; that save sealed it
                if parts:
                    c._write_parts(conn, row_id, parts, account)
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
    from backend.store.repos import chats as c

    sealed = 0
    cursor = ""
    while True:
        rows = conn.execute(
            "SELECT p.hash, p.data FROM message_parts p WHERE p.hash>? AND substr(p.data, 1, 4)<>? AND EXISTS ("
            "SELECT 1 FROM message_part_refs r JOIN messages m ON m.id = r.message_id "
            "JOIN conversations c ON c.id = m.conv_id WHERE r.hash = p.hash AND c.account_id=?) "
            "ORDER BY p.hash LIMIT ?",
            (cursor, c._SEALED_BLOB, account, batch),
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
