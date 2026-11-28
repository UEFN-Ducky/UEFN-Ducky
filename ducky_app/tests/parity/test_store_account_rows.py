"""Rows from before accounts, claimed and sealed by either backend, and the chat view's page."""

from __future__ import annotations

import contextlib
import hashlib
import itertools
import json
import re
import zlib

from backend.store import account_rows, db
from backend.store.repos import chat_display, chats, duckies, ducky_mirrors

from parity_harness import plain
from test_store_chats import _doc, _sql

_RESULT = {"ok": True, "text": "pelican notes é\n" * 1500}
_USER = {"role": "user", "text": "about the pelican", "ts": 1.0}
_INLINE = {"role": "assistant", "content": "big harbour", "blocks": [{"name": "read", "result": _RESULT}]}
_TABLES = ("message_part_refs", "message_parts", "messages", "conversations", "folders", "duckies", "duckies_history", "ducky_mirror_chats")


def _reset() -> None:
    for table in _TABLES:
        _sql(f"DELETE FROM {table}")
    _sql("DELETE FROM meta WHERE key LIKE 'rows_%'")


def _old_rows(owner: str = "") -> None:
    """Plaintext chats, folders and duckies as a build from before accounts left them."""
    part = json.dumps(_RESULT, ensure_ascii=False)
    digest = hashlib.sha256(part.encode()).hexdigest()
    envelope = {"m": {"role": "assistant", "content": "read harbour", "blocks": [{"name": "read", "result": None}]},
                "x": [["blocks", 0, "result", digest]]}
    for conv_id, project in (("old-1", "p1"), ("old-2", "p2")):
        _sql("INSERT INTO conversations(id, project_id, folder_id, title, updated, state, account_id) VALUES (?, ?, 'f-old', ?, 5, ?, ?)",
             conv_id, project, f"Old pelican {conv_id}", json.dumps({"ducky_name": "Harbour ducky"}), owner)
    for conv_id, seq, role, message, fmt, searched in (("old-1", 0, "user", _USER, 0, _USER), ("old-1", 1, "assistant", envelope, 1, _INLINE),
                                                      ("old-2", 0, "assistant", _INLINE, 0, _INLINE)):
        body = json.dumps(message, ensure_ascii=False)
        _sql("INSERT INTO messages(conv_id, seq, hash, role, text, body, fmt) VALUES (?, ?, ?, ?, ?, ?, ?)",
             conv_id, seq, chats._hash(body), role, chats.message_search_text(searched), body, fmt)
        if fmt:
            _sql("INSERT INTO message_parts(hash, data, size, created) VALUES (?, ?, ?, 0)", digest, zlib.compress(part.encode()), len(part))
            _sql("INSERT INTO message_part_refs(message_id, hash) SELECT id, ? FROM messages WHERE conv_id=? AND seq=?", digest, conv_id, seq)
    for project, folder_id, name in (("p1", "archive", "Archive"), ("p1", "f-old", "Pelican folder"), ("p2", "archive", "")):
        _sql("INSERT INTO folders(account_id, project_id, id, name) VALUES (?, ?, ?, ?)", owner, project, folder_id, name)
    for ducky_id, kind, data in (("custom-old", "custom", {"name": "Pelican builder"}), ("material-artist", "bundled", {"name": "Harbour artist"})):
        _sql("INSERT INTO duckies(id, ducky_id, kind, data, created, updated, account_id) VALUES (?, ?, ?, ?, 1, 1, ?)",
             ducky_id, ducky_id, kind, json.dumps(data), owner)
    _sql("INSERT INTO duckies_history(id, kind, data, archived, deleted, saved, account_id) VALUES ('custom-old', 'custom', ?, 0, 0, 0, ?)",
         json.dumps({"name": "Pelican builder v0"}), owner)


def _owners() -> dict:
    """Who owns each row now (a parked copy without the row number in its owner)."""
    tables = (("conversations", "id"), ("folders", "project_id, id"), ("duckies", "ducky_id"), ("duckies_history", "id"))
    out = {table: sorted((re.sub(r"\d+$", "", row[0]) if row[0].startswith("_parked:") else row[0], *row[1:])
                         for row in _sql(f"SELECT account_id, {cols} FROM {table}")) for table, cols in tables}
    return {**out, "claimed_by": _sql("SELECT value FROM meta WHERE key='rows_claimed_by'")}


def test_old_rows_go_to_the_first_account_that_opens_them(rust_store, account: str) -> None:
    def python(function: str, **args):
        return getattr(account_rows, function)(*args.values())

    def rust(function: str, **args):
        return rust_store.store("account_rows", function, **args)

    def claimed(call, named: str = "") -> list:
        _reset()
        # The account already has this folder and this ducky: the old copies are kept aside.
        chats.folders_replace("p1", [{"id": "archive", "name": "Mine"}])
        duckies.save("material-artist", "bundled", {"name": "My edit"})
        if named:
            _sql("INSERT INTO meta(key, value, updated) VALUES ('rows_claimed_by', ?, 0)", named)
        _old_rows()
        ids = [sorted(found) for found in call("owned_ids", account=account)]
        return [call("claimer"), call("claim", account=account), call("claim", account=account), call("claimer"), ids,
                [sorted(found) for found in call("owned_ids", account=account)], _owners()]

    theirs = claimed(python)
    assert plain(claimed(rust)) == plain(theirs)
    assert theirs[:4] == ["", 6, 0, account] and theirs[5] == [["old-1", "old-2"], ["archive", "f-old"]]
    owners = theirs[6]
    assert ("_parked:", "p1", "archive") in owners["folders"] and ("_parked:", "material-artist") in owners["duckies"]
    assert not any(row[0] == "" for rows in list(owners.values())[:4] for row in rows)

    # A restored backup names the account its rows went to: they go there again.
    theirs = claimed(python, named="someone")
    assert plain(claimed(rust, named="someone")) == plain(theirs)
    assert theirs[:4] == ["someone", 8, 0, "someone"] and theirs[5] == [[], ["archive"]]


def _reads(account: str) -> list:
    return plain([
        [chats.conv_get(conv_id, with_messages=True) for conv_id in ("old-1", "old-2")],
        [chats.folders_get(project) for project in ("p1", "p2")],
        [duckies.get(ducky_id) for ducky_id in ("custom-old", "material-artist")],
        duckies.history("custom-old"),
        ducky_mirrors.rows(account, "d1", "m1"),
    ])


def _plain_left() -> list:
    """What is still stored unsealed, by table."""
    texts = {f"{table}.{col}": [value for (value,) in _sql(f"SELECT {col} FROM {table}") if value and not value.startswith("enc1:")]
             for table, cols in (*account_rows.SEALED_COLUMNS, ("messages", ("body",))) for col in cols}
    blobs = [bytes(data)[:4] for (data,) in _sql("SELECT data FROM message_parts") if not bytes(data).startswith(b"UDE1")]
    return [name for name, left in {**texts, "message_parts.data": blobs}.items() if left]


def test_plaintext_from_before_is_sealed_the_same(rust_store, account: str) -> None:
    def python_seal(table: str, parked: bool) -> int:
        return account_rows._seal_table(db.connect(), table, dict(account_rows.SEALED_COLUMNS)[table], account, 2, parked=parked)

    def rust_seal(table: str, parked: bool) -> int:
        return rust_store.store("account_rows", "seal_table", table=table, parked=parked, batch=2)

    def left() -> bool:
        theirs = account_rows._plaintext_left(db.connect(), account, claimer_pass=True)
        assert rust_store.store("account_rows", "plaintext_left", account=account, claimer_pass=True) is theirs
        return theirs

    def sealed(seal, messages) -> list:
        _reset()
        _old_rows(account)
        _sql("INSERT INTO folders(account_id, project_id, id, name) VALUES ('_parked:7', 'p1', 'archive', 'Parked pelican')")
        _sql("INSERT INTO duckies(id, ducky_id, kind, data, created, updated, account_id) VALUES ('parked', 'custom-old', 'custom', '{\"name\": \"parked\"}', 1, 1, '_parked:9')")
        ducky_mirrors.note_chat(account, ducky="d1", conv_id="m1", title="sealed already", updated=1.0)
        ducky_mirrors.put_rows(account, ducky="d1", conv_id="m1", rows=[{"role": "user", "content": "x"}], of=1.0)
        _sql("UPDATE ducky_mirror_chats SET title='plain pelican title', rows='[1, 2]'")
        before = _reads(account)
        assert left() is True and len(_plain_left()) == 9
        counts = [[table, seal(table, False), seal(table, True) if table in ("folders", "duckies") else 0] for table, _ in account_rows.SEALED_COLUMNS]
        assert left() is True and _plain_left() == ["messages.body", "message_parts.data"]
        counts.append(messages())
        assert left() is False and _plain_left() == [] and _reads(account) == before
        assert [hit["conv_id"] for hit in chats.search_messages("p1", "pelican")] == ["old-1", "old-1"]  # both of its messages
        for conv_id in ("old-1", "old-2"):
            assert rust_store.store("chats", "messages_get", conv_id=conv_id) == plain(chats.messages_get(conv_id))
        owners = {owner for (owner,) in _sql("SELECT account_id FROM messages")}
        assert owners == {account} and _sql("SELECT count(*) FROM message_parts") == [(2,)]
        # A second pass finds nothing.
        return [counts, [seal(table, False) for table, _ in account_rows.SEALED_COLUMNS], messages()]

    theirs = sealed(python_seal, lambda: chats.seal_stored_messages(account, batch=2, pause_s=0))
    ours = sealed(rust_seal, lambda: rust_store.store("account_rows", "seal_stored_messages", batch=2))
    assert ours == theirs
    assert theirs[0] == [["conversations", 2, 0], ["folders", 2, 1], ["duckies", 2, 1], ["duckies_history", 1, 0], ["ducky_mirror_chats", 1, 0],
                         {"sealed": 3, "compacted": 1, "scanned": 3, "parts": 1}]
    assert theirs[1:] == [[0, 0, 0, 0, 0], {"sealed": 0, "compacted": 0, "scanned": 0, "parts": 0}]
    status, reply = rust_store.request("/__rs/store/account_rows/seal_table", {"table": "messages; DROP TABLE meta", "batch": 2})
    assert status == 200 and reply == {"ok": False, "error": "messages; DROP TABLE meta holds nothing to seal"}


def _full(message):
    """A chat view message with every field it would open on demand."""
    if isinstance(message, dict):
        return {key: _full(message[key]) for key in list(message)}
    return [_full(item) for item in message] if isinstance(message, list) else message


def test_the_chat_view_page_is_the_same(rust_store, account: str) -> None:
    messages = [{"role": "user", "text": f"message {index}", "ts": float(index)} for index in range(40)]
    messages[37] = {"role": "assistant", "content": "big", "ts": 37.0, "extra": "y" * 9000, "blocks": [
        {"name": "read", "arguments": {"path": "a é"}, "result": _RESULT}, {"name": "small", "result": {"ok": True}}]}
    chats.conv_save("p1", _doc("c1"), messages=messages)
    for before, limit in ((None, 32), (None, 5), (38, 3), (2, 32), (0, 1), (500, 4), (-1, 5)):
        with contextlib.closing(chat_display.display_messages("c1", before_seq=before)) as page:
            theirs = [[seq, last, _full(message)] for seq, last, message in itertools.islice(page, limit)]
        assert rust_store.store("chats", "display_messages", conv_id="c1", before_seq=before, limit=limit) == plain(theirs)
    assert theirs == [] and rust_store.store("chats", "display_messages", conv_id="none", limit=5) == []
    newest = rust_store.store("chats", "display_messages", conv_id="c1", before_seq=37, limit=1)
    assert newest == [[37, 39, messages[37]]]
