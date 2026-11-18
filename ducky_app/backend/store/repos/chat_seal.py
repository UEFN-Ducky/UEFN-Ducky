"""Seal the message rows and payloads an account saved in plaintext (before migration 0024).

Split out of :mod:`backend.store.repos.chats`, which re-exports
:func:`seal_stored_messages`. The row helpers are looked up on that module when
called, so a test that patches ``chats._write_parts`` or ``chats._compact_stored``
patches this code too.
"""

from __future__ import annotations

import functools
import time

from backend.store import account_rows, db


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
