"""Large message fields live once in ``message_parts``; chats read back unchanged."""

from __future__ import annotations

import json
import sqlite3
import time

from backend.store import account_rows, db
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
    refs = json.loads(account_rows.open_text(first, account_rows.current()))["x"]
    assert len({r[-1] for r in refs}) == 2 and len(refs) == 3


def _saved_before_the_upgrade(monkeypatch, chats: dict[str, list[dict]]) -> None:
    """Write *chats* into a database at schema 14 (every body inline), then reopen at head."""
    real_files = db.migration_files
    old_files = [(n, p) for n, p in real_files() if n <= 14]
    db.reset_for_tests()
    monkeypatch.setattr(db, "migration_files", lambda: old_files)
    conn = db.connect()
    assert db.user_version(conn) == 14
    with db.write_txn(conn):
        for conv_id, messages in chats.items():
            conn.execute("INSERT INTO conversations(id, project_id) VALUES (?, ?)", (conv_id, PROJECT))
            for seq, message in enumerate(messages):
                body = _json(message)
                conn.execute(
                    "INSERT INTO messages(conv_id, seq, hash, role, text, body) VALUES (?, ?, ?, ?, ?, ?)",
                    (conv_id, seq, repo._hash(body), message["role"], repo.message_search_text(message), body),
                )
    db.reset_for_tests()
    monkeypatch.setattr(db, "migration_files", real_files)


def test_rows_saved_before_the_upgrade_still_load(monkeypatch) -> None:
    old = [_user("before"), _assistant(_tool_text("old"))]
    _saved_before_the_upgrade(monkeypatch, {"legacy": old})

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


# --------------------------------------------------------------------------- saves touch only what changed


def _count_message_dumps(monkeypatch) -> list[dict]:
    dumped: list[dict] = []
    real = repo._dumps

    def spy(value):
        if isinstance(value, dict) and "role" in value:
            dumped.append(value)
        return real(value)

    monkeypatch.setattr(repo, "_dumps", spy)
    return dumped


def _transcript(n: int) -> list[dict]:
    return [_user(f"question {i}") if i % 2 == 0 else _assistant(_tool_text(f"r{i}")) for i in range(n)]


def test_a_save_writes_only_the_messages_that_changed(monkeypatch) -> None:
    messages = _transcript(30)
    rev = repo.conv_save(PROJECT, _doc("c6"), messages=messages)["msg_rev"]
    # The runner changes messages in place: a new tool call on one, a longer result on another.
    messages[5]["blocks"].append({"type": "tool_call", "name": "run_terminal", "status": "running", "arguments": {}})
    messages[7]["blocks"][0]["result"]["text"] += "\nmore output"
    stats = repo.conv_save(PROJECT, _doc("c6"), messages=messages)
    assert stats["path"] == "full" and (stats["inserted"], stats["updated"], stats["deleted"]) == (0, 2, 0)
    assert _json(repo.messages_get("c6")) == _json(messages)
    # A delta serialises only from the first changed message on.
    messages[25]["text"] = "edited"
    dumped = _count_message_dumps(monkeypatch)
    stats = repo.conv_save(PROJECT, _doc("c6"), messages=messages, dirty_from=25, expect_rev=stats["msg_rev"])
    assert stats["path"] == "delta" and stats["updated"] == 1 and len(dumped) == 5
    assert rev < stats["msg_rev"]


def test_an_unchanged_copy_from_the_panel_writes_nothing() -> None:
    messages = _transcript(20)
    rev = repo.conv_save(PROJECT, _doc("c7"), messages=messages)["msg_rev"]
    fresh = json.loads(_json(messages))  # the panel sends its own copy of the same chat
    stats = repo.conv_save(PROJECT, _doc("c7"), messages=fresh)
    assert (stats["inserted"], stats["updated"], stats["deleted"]) == (0, 0, 0)
    assert stats["msg_rev"] == rev


def test_a_row_another_process_rewrote_is_checked_and_saved_again() -> None:
    messages = _transcript(6)
    repo.conv_save(PROJECT, _doc("c8"), messages=messages)
    conn = db.connect()
    with db.write_txn(conn):
        conn.execute("UPDATE messages SET hash='elsewhere', body='{}', fmt=0 WHERE conv_id='c8' AND seq=3")
    stats = repo.conv_save(PROJECT, _doc("c8"), messages=messages)
    assert stats["updated"] == 1
    assert _json(repo.messages_get("c8")) == _json(messages)


def test_a_load_is_the_callers_own_and_sees_other_writers() -> None:
    messages = _transcript(12)
    repo.conv_save(PROJECT, _doc("c10"), messages=messages)
    first = repo.messages_get("c10")
    assert _json(first) == _json(messages)
    first[3]["text"] = "edited by the caller"  # a loaded copy is the caller's own
    assert _json(repo.messages_get("c10")) == _json(messages)
    conn = db.connect()
    with db.write_txn(conn):  # another process rewrote one row
        conn.execute("UPDATE messages SET hash='elsewhere', body=?, fmt=0 WHERE conv_id='c10' AND seq=4",
                     (account_rows.seal(_json(_user("theirs")), account_rows.current()),))
    assert repo.messages_get("c10")[4]["text"] == "theirs"


def test_values_a_copy_cannot_track_are_serialised_every_time() -> None:
    class Tag:
        def __init__(self) -> None:
            self.name = "a"

        def __str__(self) -> str:
            return self.name

    tag = Tag()
    messages = [{"role": "user", "content": "q", "tag": tag}]
    repo.conv_save(PROJECT, _doc("c9"), messages=messages)
    tag.name = "b"  # changes in place, where == on the object cannot see it
    stats = repo.conv_save(PROJECT, _doc("c9"), messages=messages)
    assert stats["updated"] == 1
    assert repo.messages_get("c9")[0]["tag"] == "b"


# --------------------------------------------------------------------------- compacting older rows


def _old_chats() -> dict[str, list[dict]]:
    shared = _tool_text("shared")
    return {
        "old1": [_user("q"), _assistant(_tool_text("one"), shared), _user("small"), _assistant(_tool_text("two"))],
        "old2": [_assistant(shared), _assistant(_tool_text("three"))],
    }


def _fmts() -> dict[tuple[str, int], int]:
    rows = db.connect().execute("SELECT conv_id, seq, fmt FROM messages").fetchall()
    return {(c, s): f for c, s, f in rows}


def test_older_rows_are_sealed_compacted_and_read_back_identical(monkeypatch) -> None:
    chats = _old_chats()
    _saved_before_the_upgrade(monkeypatch, chats)
    conn = db.connect()
    hashes = conn.execute("SELECT id, hash FROM messages ORDER BY id").fetchall()

    result = repo.seal_stored_messages(batch=2, pause_s=0)

    assert (result["sealed"], result["compacted"]) == (6, 4)
    big = {("old1", 1), ("old1", 3), ("old2", 0), ("old2", 1)}
    assert {k for k, f in _fmts().items() if f == repo.FMT_PARTS} == big
    assert _count("SELECT count(*) FROM message_parts") == 4  # the shared read is stored once
    assert conn.execute("SELECT id, hash FROM messages ORDER BY id").fetchall() == hashes
    # Bodies, payloads and search text hold none of the words any more.
    for body, text in conn.execute("SELECT body, text FROM messages"):
        assert body.startswith("enc1:") and "goblin" not in body + text
    assert all(bytes(d).startswith(b"UDE1") for (d,) in conn.execute("SELECT data FROM message_parts"))
    for conv_id, messages in chats.items():
        assert _json(repo.messages_get(conv_id)) == _json(messages)
    assert repo.search_messages(PROJECT, "goblin_three")[0]["conv_id"] == "old2"
    conn.execute("INSERT INTO message_fts(message_fts) VALUES('integrity-check')")
    # Done: the next boot has nothing left to look at.
    assert repo.seal_stored_messages(batch=2, pause_s=0) == {"sealed": 0, "compacted": 0, "scanned": 0, "parts": 0}


def test_compaction_picks_up_where_it_stopped(monkeypatch) -> None:
    chats = _old_chats()
    _saved_before_the_upgrade(monkeypatch, chats)
    real = repo._write_parts
    calls: list[int] = []

    def fail_in_third_batch(conn, message_id, parts, account):
        calls.append(message_id)
        if len(calls) == 3:
            raise RuntimeError("app closed")
        return real(conn, message_id, parts, account)

    monkeypatch.setattr(repo, "_write_parts", fail_in_third_batch)
    try:
        repo.seal_stored_messages(batch=2, pause_s=0)
    except RuntimeError:
        pass
    # The first batches stayed done; the interrupted one rolled back whole.
    assert sorted(f for f in _fmts().values()) == [0, 0, 0, 0, 1, 1]
    for conv_id, messages in chats.items():
        assert _json(repo.messages_get(conv_id)) == _json(messages)

    monkeypatch.setattr(repo, "_write_parts", real)
    assert repo.seal_stored_messages(batch=2, pause_s=0)["compacted"] == 2
    assert sorted(f for f in _fmts().values()) == [0, 0, 1, 1, 1, 1]
    for conv_id, messages in chats.items():
        assert _json(repo.messages_get(conv_id)) == _json(messages)


def test_compaction_leaves_a_row_that_was_saved_meanwhile(monkeypatch) -> None:
    chats = _old_chats()
    _saved_before_the_upgrade(monkeypatch, chats)
    edited = [*chats["old2"]]
    edited[1] = _assistant(_tool_text("edited"))
    real = repo._compact_stored

    def save_while_reading(body, digest_of):
        out = real(body, digest_of)
        if "goblin_three" in body:
            repo.conv_save(PROJECT, _doc("old2"), messages=edited)  # the chat goes on meanwhile
        return out

    monkeypatch.setattr(repo, "_compact_stored", save_while_reading)
    repo.seal_stored_messages(batch=10, pause_s=0)
    assert _json(repo.messages_get("old2")) == _json(edited)
    assert _json(repo.messages_get("old1")) == _json(chats["old1"])
