"""Large message fields live once in ``message_parts``; chats read back unchanged."""

from __future__ import annotations

import json
import sqlite3
import time

from backend.store import db
from backend.store.repos import chats as repo

PROJECT = "proj_parts"


def _doc(conv_id: str) -> dict:
    return {"id": conv_id, "title": conv_id, "created": 1.0, "updated": 1.0, "skill_snapshot": "idx"}


def _tool_text(seed: str, size: int = 40_000) -> str:
    line = f"{seed}: var Count : int = 0 # spawner wiring for goblin_{seed}\n"
    return (line * (size // len(line) + 1))[:size]


def _assistant(*results: str, **extra) -> dict:
    blocks = [
        {"type": "tool_call", "id": f"call_{i}", "name": "workspace_read_file", "status": "success",
         "arguments": {"relative_path": f"Content/Verse/f{i}.verse"},
         "result": {"ok": True, "text": text, "hint": ""}}
        for i, text in enumerate(results)
    ]
    return {"role": "assistant", "content": "read them", "text": "read them", "ts": time.time(), "blocks": blocks, **extra}


def _user(text: str) -> dict:
    return {"role": "user", "content": text, "text": text, "ts": time.time()}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _count(sql: str, *args) -> int:
    return int(db.connect().execute(sql, args).fetchone()[0])


def test_large_tool_result_is_stored_once_compressed_and_reads_back_identical() -> None:
    messages = [_user("read the spawner"), _assistant(_tool_text("a"), _tool_text("b"))]
    repo.conv_save(PROJECT, _doc("c1"), messages=messages)

    assert _json(repo.messages_get("c1")) == _json(messages)
    conn = db.connect()
    fmt, body = conn.execute("SELECT fmt, body FROM messages WHERE conv_id='c1' AND seq=1").fetchone()
    assert fmt == repo.FMT_PARTS
    assert len(body) < repo.PART_MIN_CHARS  # the results are no longer inside the row
    assert _count("SELECT count(*) FROM message_parts") == 2
    stored = _count("SELECT sum(length(data)) FROM message_parts")
    assert stored < 40_000  # two 40 KB results, compressed
    # The user row is small and stays a plain JSON row.
    assert conn.execute("SELECT fmt FROM messages WHERE conv_id='c1' AND seq=0").fetchone()[0] == repo.FMT_PLAIN


def test_a_copied_chat_and_a_repeated_read_share_the_stored_result() -> None:
    same = _tool_text("shared")
    repo.conv_save(PROJECT, _doc("orig"), messages=[_user("q"), _assistant(same), _assistant(same)])
    repo.conv_save(PROJECT, _doc("copy"), messages=[_user("q"), _assistant(same)])
    assert _count("SELECT count(*) FROM message_parts") == 1
    assert repo.messages_get("copy")[1]["blocks"][0]["result"]["text"] == same


def test_checkpoints_store_only_the_new_result_and_drop_what_no_message_uses() -> None:
    first, second = _tool_text("first"), _tool_text("second")
    live = _assistant(first, incomplete=True)
    repo.conv_save(PROJECT, _doc("c2"), messages=[_user("go"), live])
    created = db.connect().execute("SELECT hash, created FROM message_parts").fetchall()
    live = _assistant(first, second, incomplete=True)
    repo.conv_save(PROJECT, _doc("c2"), messages=[_user("go"), live])
    after = dict(db.connect().execute("SELECT hash, created FROM message_parts").fetchall())
    assert len(after) == 2 and all(after[h] == ts for h, ts in created)  # the first result was not rewritten

    # The turn is retried with a small reply: its results go, nothing else uses them.
    repo.conv_save(PROJECT, _doc("c2"), messages=[_user("go"), _user("short")])
    assert _count("SELECT count(*) FROM message_parts") == 0
    assert _count("SELECT count(*) FROM message_part_refs") == 0
    assert db.connect().execute("SELECT fmt FROM messages WHERE conv_id='c2' AND seq=1").fetchone()[0] == repo.FMT_PLAIN


def test_deleting_a_chat_removes_its_parts_but_keeps_shared_ones() -> None:
    shared, own = _tool_text("shared"), _tool_text("own")
    repo.conv_save(PROJECT, _doc("keep"), messages=[_assistant(shared)])
    repo.conv_save(PROJECT, _doc("gone"), messages=[_assistant(shared, own), _assistant(own)])
    assert _count("SELECT count(*) FROM message_parts") == 2
    assert repo.conv_delete("gone")
    assert _count("SELECT count(*) FROM message_parts") == 1
    assert repo.messages_get("keep")[0]["blocks"][0]["result"]["text"] == shared


def test_search_still_finds_text_from_a_stored_result() -> None:
    repo.conv_save(PROJECT, _doc("c3"), messages=[_user("look"), _assistant(_tool_text("needle"))])
    hits = repo.search_messages(PROJECT, "goblin_needle")
    assert [(h["conv_id"], h["seq"]) for h in hits] == [("c3", 1)]


def test_large_top_level_fields_and_odd_shapes_round_trip() -> None:
    big = _tool_text("reply", 20_000)
    messages = [
        {"role": "assistant", "content": big, "text": big, "ts": 1.0, "attachments": [{"kind": "file", "text": big}]},
        {"role": "tool", "tool": {"name": "x", "result": big, "fileEdit": {"before": big, "after": big + "!"}}},
        {"role": "assistant", "blocks": [big, {"type": "text", "text": "small"}, None, 7]},
        {"role": "user", 3: big, "text": "int key"},
    ]
    repo.conv_save(PROJECT, _doc("c4"), messages=messages)
    assert _json(repo.messages_get("c4")) == _json(json.loads(_json(messages)))
    # content and text hold the same reply: one stored copy
    first = db.connect().execute("SELECT body FROM messages WHERE conv_id='c4' AND seq=0").fetchone()[0]
    refs = json.loads(first)["x"]
    assert len({r[-1] for r in refs}) == 2 and len(refs) == 3


def test_rows_saved_before_the_upgrade_still_load(tmp_path, monkeypatch) -> None:
    """A database at schema 14 (every body inline) opens, migrates and reads the same."""
    real_files = db.migration_files
    old_files = [(n, p) for n, p in real_files() if n <= 14]
    db.reset_for_tests()
    monkeypatch.setattr(db, "migration_files", lambda: old_files)
    conn = db.connect()
    assert db.user_version(conn) == 14
    old = [_user("before"), _assistant(_tool_text("old"))]
    with db.write_txn(conn):
        conn.execute("INSERT INTO conversations(id, project_id) VALUES ('legacy', ?)", (PROJECT,))
        for seq, message in enumerate(old):
            body = _json(message)
            conn.execute(
                "INSERT INTO messages(conv_id, seq, hash, role, text, body) VALUES ('legacy', ?, ?, ?, ?, ?)",
                (seq, repo._hash(body), message["role"], repo.message_search_text(message), body),
            )
    db.reset_for_tests()
    monkeypatch.setattr(db, "migration_files", real_files)

    conn = db.connect()
    assert db.user_version(conn) == db.head_version()
    assert conn.execute("SELECT fmt FROM messages WHERE conv_id='legacy' AND seq=1").fetchone()[0] == repo.FMT_PLAIN
    assert _json(repo.messages_get("legacy")) == _json(old)
    # An unchanged save leaves the old rows alone; a new turn is stored compact next to them.
    new = _assistant(_tool_text("new"))
    stats = repo.conv_save(PROJECT, _doc("legacy"), messages=[*old, new])
    assert (stats["inserted"], stats["updated"]) == (1, 0)
    assert [r[0] for r in conn.execute("SELECT fmt FROM messages WHERE conv_id='legacy' ORDER BY seq")] == [0, 0, 1]
    assert _json(repo.messages_get("legacy")) == _json([*old, new])
    assert repo.search_messages(PROJECT, "goblin_old")[0]["seq"] == 1


def test_fts_entry_is_kept_when_only_the_body_changes() -> None:
    repo.conv_save(PROJECT, _doc("c5"), messages=[_user("unique wording here")])
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("UPDATE messages SET body=body WHERE conv_id='c5'")
    assert [h["seq"] for h in repo.search_messages(PROJECT, "unique wording")] == [0]
    raw = sqlite3.connect(str(db.db_path()))
    try:
        assert raw.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        raw.execute("INSERT INTO message_fts(message_fts) VALUES('integrity-check')")
    finally:
        raw.close()
