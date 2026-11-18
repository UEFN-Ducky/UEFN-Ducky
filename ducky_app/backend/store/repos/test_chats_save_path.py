"""The save path: a save prepared before the write transaction (``prepare_save`` /
``prepare_run_message``) writes the same rows and does no encoding or sealing inside it;
a chat's skill snapshot text is read once per hash."""

from __future__ import annotations

import contextlib
import json
import sys
import time
from collections import OrderedDict

from backend.store import account_rows, db
from backend.store.repos import chats as repo

PROJECT = "proj_save_path"


def _doc(conv_id: str, title: str = "") -> dict:
    return {"id": conv_id, "title": title or conv_id, "created": 1.0, "updated": 1.0, "skill_snapshot": "idx",
            "pinned": True}


def _msgs(n: int) -> list[dict]:
    out = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"m{i} " + ("goblin spawner " * 900 if i % 7 == 3 else "")  # some rows carry a part
        out.append({"role": role, "content": text, "text": text, "ts": float(i), "run_id": f"r{i}"})
    return out


def _rows(conv_id: str) -> list[tuple]:
    return db.connect().execute(
        "SELECT seq, hash, role, ts, run_id, fmt FROM messages WHERE conv_id=? ORDER BY seq", (conv_id,)
    ).fetchall()


def _in_batch(fn):
    """Run *fn* inside an outer write transaction, as the chat writer's batch does."""
    with db.write_txn(db.connect()):
        return fn()


def _watch(monkeypatch) -> list[str]:
    """Names of the encode/seal functions that ran while a write transaction was open."""
    depth = [0]
    hits: list[str] = []
    real_txn = db.write_txn

    @contextlib.contextmanager
    def write_txn(conn, *a, **kw):
        depth[0] += 1
        try:
            with real_txn(conn, *a, **kw) as out:
                yield out
        finally:
            depth[0] -= 1

    monkeypatch.setattr(db, "write_txn", write_txn)

    def watch(owner, name):
        fn = getattr(owner, name)

        def wrapped(*a, **kw):
            if depth[0]:
                hits.append(name)
            return fn(*a, **kw)

        monkeypatch.setattr(owner, name, wrapped)

    for name in ("_dumps", "_hash", "_prep_row", "_seal_part", "_sealed_as"):
        watch(repo, name)
    for name in ("seal", "seal_blob", "blind_text"):
        watch(account_rows, name)
    return hits


def test_no_encoding_or_sealing_runs_inside_the_transaction(monkeypatch) -> None:
    repo.conv_save(PROJECT, _doc("watched"), messages=_msgs(10))
    msgs = _msgs(14)
    hits = _watch(monkeypatch)

    prepared = repo.prepare_save(_doc("watched", "renamed"), msgs, dirty_from=10)
    rev = repo.conv_msg_rev("watched")
    out = _in_batch(lambda: repo.conv_save(
        PROJECT, _doc("watched", "renamed"), messages=msgs, dirty_from=10, expect_rev=rev, prepared=prepared
    ))
    run = {"role": "assistant", "content": "x" * 20_000, "text": "working", "ts": 2.0, "run_id": "live"}
    ready = repo.prepare_run_message(run)
    _in_batch(lambda: repo.upsert_run_message("watched", "live", run, updated=3.0, prepared=ready))
    meta_only = repo.prepare_save(_doc("watched", "again"))
    _in_batch(lambda: repo.conv_save(PROJECT, _doc("watched", "again"), messages=None, prepared=meta_only))

    assert hits == []
    assert out["path"] == "delta" and out["inserted"] == 4
    assert repo.conv_get("watched", with_messages=False)["title"] == "again"
    assert repo.messages_get("watched")[-1]["text"] == "working"
    _in_batch(lambda: repo.conv_save(PROJECT, _doc("watched", "control"), messages=None))
    assert "_dumps" in hits  # the watch does see an unprepared save


def test_prepared_saves_write_the_rows_the_old_path_writes() -> None:
    msgs = _msgs(40)
    repo.conv_save(PROJECT, _doc("old"), messages=msgs)
    prepared = repo.prepare_save(_doc("new"), msgs)
    _in_batch(lambda: repo.conv_save(PROJECT, _doc("new"), messages=msgs, prepared=prepared))
    assert _rows("new") == _rows("old")
    assert json.dumps(repo.messages_get("new")) == json.dumps(msgs)
    # Then a delta save of both: an edited row below the tail and two new rows.
    edited = [*msgs[:5], {**msgs[5], "text": "edited"}, *msgs[6:], *_msgs(42)[40:]]
    for conv_id in ("old", "new"):
        rev = repo.conv_msg_rev(conv_id)
        kw = {"messages": edited, "dirty_from": 40, "dirty_rows": [5], "expect_rev": rev}
        if conv_id == "old":
            repo.conv_save(PROJECT, _doc(conv_id), **kw)
        else:
            ready = repo.prepare_save(_doc(conv_id), edited, dirty_from=40, dirty_rows=[5])
            _in_batch(lambda: repo.conv_save(PROJECT, _doc(conv_id), prepared=ready, **kw))
    assert _rows("new") == _rows("old")
    assert json.dumps(repo.messages_get("new")) == json.dumps(edited)


def test_a_row_changed_after_it_was_prepared_is_written_as_it_is_now() -> None:
    msgs = _msgs(6)
    prepared = repo.prepare_save(_doc("moved"), msgs)
    msgs[4] = {**msgs[4], "text": "newer"}  # a new object: the prepared row is stale
    repo.conv_save(PROJECT, _doc("moved"), messages=msgs, prepared=prepared)
    assert repo.messages_get("moved")[4]["text"] == "newer"
    run = {"role": "assistant", "content": "a", "text": "a", "ts": time.time(), "run_id": "r"}
    stale = repo.prepare_run_message(run)
    newer = {**run, "text": "b"}
    repo.upsert_run_message("moved", "r", newer, updated=2.0, prepared=stale)
    assert repo.messages_get("moved")[-1]["text"] == "b"


def _empty_snapshot_cache(monkeypatch) -> None:
    monkeypatch.setattr(repo, "_snap_text", OrderedDict())
    monkeypatch.setattr(repo, "_snap_bytes", 0)


def test_the_sidebar_list_reads_each_snapshot_once(monkeypatch) -> None:
    for i in range(200):
        repo.conv_save(PROJECT, {**_doc(f"side{i}"), "skill_snapshot": f"skill index {i % 3}"}, messages=None)
    _empty_snapshot_cache(monkeypatch)
    conn = db.connect()
    reads: list[str] = []
    conn.set_trace_callback(lambda sql: reads.append(sql) if "FROM snapshots" in sql else None)
    try:
        listed = repo.conv_list(PROJECT)
        again = repo.conv_get("side7")
    finally:
        conn.set_trace_callback(None)
    assert len(reads) == 3
    assert sorted({d["skill_snapshot"] for d in listed}) == [f"skill index {i}" for i in range(3)]
    assert again["skill_snapshot"] == "skill index 1"


def test_the_cache_stays_under_its_byte_limit(monkeypatch) -> None:
    _empty_snapshot_cache(monkeypatch)
    monkeypatch.setattr(repo, "_SNAP_MAX_BYTES", 100_000)
    for i in range(10):
        repo.conv_save(PROJECT, {**_doc(f"big{i}"), "skill_snapshot": f"{i}" + "s" * 30_000}, messages=None)
        assert repo.conv_get(f"big{i}")["skill_snapshot"].startswith(f"{i}s")
    assert 0 < repo._snap_bytes <= 100_000
    assert repo._snap_bytes == sum(sys.getsizeof(t) for t in repo._snap_text.values())
    assert len(repo._snap_text) == 3  # three 30 KB texts fit, the older ones were dropped
    repo.conv_save(PROJECT, {**_doc("huge"), "skill_snapshot": "h" * 200_000}, messages=None)
    assert len(repo.conv_get("huge")["skill_snapshot"]) == 200_000  # too big to keep, still read
    assert not any(t.startswith("h") for t in repo._snap_text.values())


def test_a_title_changed_by_another_writer_is_sealed_again() -> None:
    repo.conv_save(PROJECT, _doc("titled", "one"), messages=None)
    prepared = repo.prepare_save(_doc("titled", "two"))
    repo.conv_save(PROJECT, _doc("titled", "other"), messages=None)  # lands in between
    repo.conv_save(PROJECT, _doc("titled", "two"), messages=None, prepared=prepared)
    assert repo.conv_get("titled", with_messages=False)["title"] == "two"
