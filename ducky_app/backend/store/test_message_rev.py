"""``conversations.msg_rev`` moves on every message write, by any writer (migration 0026)."""

from __future__ import annotations

import sqlite3
import threading
import time

from backend.store import db
from backend.store.repos import chats as repo

PROJECT = "proj_rev"


def _doc(conv_id: str) -> dict:
    return {"id": conv_id, "title": conv_id, "created": 1.0, "updated": 1.0, "skill_snapshot": "idx"}


def _msg(role: str, text: str, **extra) -> dict:
    return {"role": role, "content": text, "text": text, "ts": time.time(), **extra}


def _rev(conv_id: str) -> int:
    return int(db.connect().execute("SELECT msg_rev FROM conversations WHERE id=?", (conv_id,)).fetchone()[0])


def _other_connection() -> sqlite3.Connection:
    path = db.connect().execute("PRAGMA database_list").fetchone()[2]
    return sqlite3.connect(path, isolation_level=None)


def test_migration_keeps_rows_and_starts_rev_at_zero() -> None:
    conn = db.connect()
    repo.conv_save(PROJECT, _doc("old"), messages=[_msg("user", "hi"), _msg("assistant", "hello")])
    # Leave a 0025-shaped database: no column, no triggers.
    for trigger in ("messages_rev_ai", "messages_rev_ad", "messages_rev_au"):
        conn.execute(f"DROP TRIGGER {trigger}")
    conn.execute("ALTER TABLE conversations DROP COLUMN msg_rev")
    conn.execute("PRAGMA user_version=25")
    db.reset_for_tests()

    assert db.user_version(db.connect()) == db.head_version()
    assert [m["text"] for m in repo.messages_get("old")] == ["hi", "hello"]
    assert _rev("old") == 0


def test_two_inserts_and_an_update_from_another_connection_make_rev_three() -> None:
    repo.conv_save(PROJECT, _doc("c1"), messages=None)
    assert _rev("c1") == 0
    other = _other_connection()
    try:
        for seq in (0, 1):
            other.execute(
                "INSERT INTO messages(conv_id, seq, hash, body) VALUES ('c1', ?, ?, '{}')", (seq, f"h{seq}")
            )
        other.execute("UPDATE messages SET hash='h9' WHERE conv_id='c1' AND seq=1")
        assert _rev("c1") == 3
        other.execute("DELETE FROM messages WHERE conv_id='c1' AND seq=0")
        assert _rev("c1") == 4
    finally:
        other.close()


def test_saves_bump_only_their_own_chat() -> None:
    repo.conv_save(PROJECT, _doc("a"), messages=[_msg("user", "one")])
    repo.conv_save(PROJECT, _doc("b"), messages=[_msg("user", "two")])
    before = _rev("a"), _rev("b")
    repo.conv_save(PROJECT, _doc("a"), messages=[_msg("user", "one"), _msg("assistant", "more")])
    assert _rev("a") > before[0] and _rev("b") == before[1]


def _long_chat(conv_id: str, n: int = 5000) -> tuple[list[dict], int]:
    messages = [_msg("user" if i % 2 == 0 else "assistant", f"message {i}") for i in range(n)]
    out = repo.conv_save(PROJECT, _doc(conv_id), messages=messages)
    assert out["path"] == "full" and out["msg_rev"] == _rev(conv_id) == n
    return messages, out["msg_rev"]


def _counting_dumps(monkeypatch) -> list[int]:
    calls = [0]
    real = repo._dumps

    def dumps(value):
        calls[0] += 1
        return real(value)

    monkeypatch.setattr(repo, "_dumps", dumps)
    return calls


def test_delta_append_on_a_long_chat_serialises_one_row(monkeypatch) -> None:
    messages, rev = _long_chat("long")
    messages.append(_msg("user", "one more"))
    calls = _counting_dumps(monkeypatch)
    started = time.perf_counter()
    out = repo.conv_save(PROJECT, _doc("long"), messages=messages, dirty_from=5000, expect_rev=rev)
    took = time.perf_counter() - started
    assert out["path"] == "delta" and out["hashes_from"] == 5000 and len(out["hashes"]) == 1
    assert (out["inserted"], out["updated"], out["deleted"]) == (1, 0, 0)
    assert out["msg_rev"] == rev + 1 == _rev("long")
    assert calls[0] == 2  # the message row and the conversation state; no transcript walk
    assert took < 0.05, f"delta append took {took * 1000:.1f} ms"
    assert [m["text"] for m in repo.messages_get("long")[-2:]] == ["message 4999", "one more"]


def test_a_merge_of_listed_rows_encodes_them_before_the_lock(monkeypatch) -> None:
    import contextlib

    messages, rev = _long_chat("listed")
    other = [dict(m) for m in messages]
    other[100] = _msg("assistant", "another writer")
    repo.conv_save(PROJECT, _doc("listed"), messages=other, dirty_from=100, expect_rev=rev)  # makes rev stale
    messages[11] = _msg("assistant", "edited 11")
    messages.append(_msg("user", "tail"))
    mine = {id(m) for m in messages}
    encoded = [0]
    real_dumps = repo._dumps

    def dumps(value):
        encoded[0] += id(value) in mine
        return real_dumps(value)

    held: list[float] = []
    real_txn = db.write_txn

    @contextlib.contextmanager
    def timed(conn):
        with real_txn(conn) as c:
            started = time.perf_counter()
            try:
                yield c
            finally:
                held.append(time.perf_counter() - started)

    monkeypatch.setattr(repo, "_dumps", dumps)
    monkeypatch.setattr(db, "write_txn", timed)
    out = repo.conv_save(PROJECT, _doc("listed"), messages=messages, dirty_from=5000, dirty_rows=[11],
                         expect_rev=rev)
    monkeypatch.setattr(repo, "_dumps", real_dumps)
    monkeypatch.setattr(db, "write_txn", real_txn)
    assert out["path"] == "merge" and out["hashes_from"] == 5000 and len(out["hashes"]) == 1
    assert (out["inserted"], out["updated"], out["deleted"]) == (1, 1, 0)
    assert list(out["dirty_hashes"]) == [11]
    assert encoded[0] == 2, encoded[0]  # row 11 and the tail, nothing between
    print(f"merge lock hold: {held[0] * 1000:.2f} ms")
    assert held[0] < 0.003, f"merge held the write lock {held[0] * 1000:.1f} ms"
    stored = [m["text"] for m in repo.messages_get("listed")]
    assert stored[11] == "edited 11" and stored[100] == "another writer" and stored[-1] == "tail"


def test_stale_shorter_copy_merges_and_never_truncates() -> None:
    messages, rev = _long_chat("shared", 4)
    other = repo.upsert_run_message("shared", "r9", _msg("assistant", "from a host", run_id="r9"), updated=2.0)
    assert other["seq"] == 4
    # This copy never saw the host's row: it edits seq 3, claims only that changed, and is shorter.
    messages[3] = _msg("assistant", "edited")
    out = repo.conv_save(PROJECT, _doc("shared"), messages=messages, dirty_from=3, expect_rev=rev)
    assert out["path"] == "merge" and out["deleted"] == 0 and out["msg_rev"] == _rev("shared")
    assert [m["text"] for m in repo.messages_get("shared")][3:] == ["edited", "from a host"]
    assert repo.conv_message_summary("shared")[0] == 5
    # An explicit reassign (no dirty_from) is still the whole transcript.
    assert repo.conv_save(PROJECT, _doc("shared"), messages=messages, expect_rev=rev)["path"] == "full"
    assert len(repo.messages_get("shared")) == 4


def _lost_update(conv_id: str, stale_dirty_from) -> None:
    """Copy 2 rewrites row 11; stale copy 1 then appends and saves: row 11 must survive."""
    one = [_msg("user" if i % 2 == 0 else "assistant", f"m{i}") for i in range(20)]
    rev = repo.conv_save(PROJECT, _doc(conv_id), messages=one)["msg_rev"]
    two = repo.messages_get(conv_id)
    two[11] = _msg("assistant", "rewritten by copy 2")
    rev2 = repo.conv_save(PROJECT, _doc(conv_id), messages=two, dirty_from=11, expect_rev=rev)["msg_rev"]
    assert rev2 > rev
    one.append(_msg("user", "appended by copy 1"))
    out = repo.conv_save(PROJECT, _doc(conv_id), messages=one, dirty_from=stale_dirty_from(one), expect_rev=rev)
    assert out["path"] == "merge" and out["msg_rev"] > rev2
    stored = [m["text"] for m in repo.messages_get(conv_id)]
    assert stored[11] == "rewritten by copy 2" and stored[-1] == "appended by copy 1" and len(stored) == 21


def test_a_stale_copy_never_writes_rows_below_its_change() -> None:
    _lost_update("lost1", lambda m: len(m) - 1)  # the append itself


def test_a_stale_turn_end_save_keeps_the_other_writers_row() -> None:
    # Turn end saves from the turn's first message (after the last user row it started on).
    _lost_update("lost2", lambda m: 15)


def test_a_stale_copy_far_past_the_stored_end_leaves_no_gap() -> None:
    msgs = [_msg("user", str(i)) for i in range(5)]
    rev = repo.conv_save(PROJECT, _doc("gap"), messages=msgs)["msg_rev"]
    repo.conv_save(PROJECT, _doc("gap"), messages=msgs[:3])  # another writer truncated it
    out = repo.conv_save(PROJECT, _doc("gap"), messages=msgs + [_msg("user", "5")], dirty_from=5, expect_rev=rev)
    assert out["path"] == "merge" and out["hashes_from"] == 3
    assert [m["text"] for m in repo.messages_get("gap")] == ["0", "1", "2", "3", "4", "5"]


def test_messages_since_returns_the_tail_and_the_rev(monkeypatch) -> None:
    messages, rev = _long_chat("since")
    opens = _counting_opens(monkeypatch)
    tail, got_rev, count = repo.messages_since("since", 4990)
    assert tail == messages[4990:] and got_rev == rev and count == 5000
    assert opens[0] == 0
    assert repo.messages_since("since", 6000) == ([], rev, 5000)
    assert repo.messages_since("missing", 0) == ([], None, 0)


def test_delta_from_zero_matches_the_full_path() -> None:
    first = [_msg("user", "a"), _msg("assistant", "b"), _msg("user", "c")]
    rev = repo.conv_save(PROJECT, _doc("d"), messages=first)["msg_rev"]
    repo.conv_save(PROJECT, _doc("f"), messages=first)
    second = [_msg("user", "a"), _msg("assistant", "B")]
    delta = repo.conv_save(PROJECT, _doc("d"), messages=second, dirty_from=0, expect_rev=rev)
    full = repo.conv_save(PROJECT, _doc("f"), messages=second)
    assert delta["path"] == "delta" and full["path"] == "full"
    for key in ("inserted", "updated", "deleted", "hashes"):
        assert delta[key] == full[key], key
    assert repo.messages_get("d") == repo.messages_get("f") == second


def test_delta_truncates_and_load_returns_the_rev() -> None:
    msgs = [_msg("user", str(i)) for i in range(5)]
    repo.conv_save(PROJECT, _doc("t"), messages=msgs)
    loaded, rev = repo.messages_get_rev("t")
    assert loaded == msgs and rev == repo.conv_msg_rev("t") == 5
    out = repo.conv_save(PROJECT, _doc("t"), messages=msgs[:2], dirty_from=9, expect_rev=rev)
    assert out["path"] == "delta" and out["deleted"] == 3 and out["hashes"] == []
    assert repo.messages_get("t") == msgs[:2]
    assert repo.conv_save(PROJECT, _doc("t"), messages=None)["path"] == "meta"
    assert repo.messages_get_rev("missing") == ([], None)


def test_full_path_on_a_long_chat_stays_cheap() -> None:
    messages, _ = _long_chat("whole")
    messages.append(_msg("user", "one more"))
    started = time.perf_counter()
    out = repo.conv_save(PROJECT, _doc("whole"), messages=messages)
    took = time.perf_counter() - started
    print(f"full save on 5,001 msgs: {took * 1000:.1f} ms")
    assert out["path"] == "full" and out["inserted"] == 1 and len(out["hashes"]) == 5001
    assert took < 0.25, f"full save took {took * 1000:.1f} ms"  # ~40 ms idle; slack for a busy PC


def test_saves_keep_at_most_eight_chats_and_memory_stays_bounded(monkeypatch) -> None:
    import tracemalloc

    repo._opened.clear()
    repo._opened_size = 0
    chats = {f"m{i}": [_msg("user", f"chat {i} " + "x" * 200) for _ in range(196)] for i in range(50)}
    big, _ = _long_chat("big")
    tracemalloc.start()
    try:
        base = tracemalloc.take_snapshot()
        for _ in range(4):
            for conv_id, messages in chats.items():
                messages.append(_msg("assistant", "y" * 200))
                repo.conv_save(PROJECT, _doc(conv_id), messages=messages)
        assert len(repo._opened) <= 8 and "big" not in repo._opened  # the last chats saved
        opens = _counting_opens(monkeypatch)
        assert repo.messages_get("big") == big and repo.messages_get("big") == big
        assert opens[0] == 5000  # the second load opened nothing
        grown = sum(s.size_diff for s in tracemalloc.take_snapshot().compare_to(base, "filename")
                    if s.traceback[0].filename.endswith("chats.py"))
    finally:
        tracemalloc.stop()
    assert grown < 20 * 1024 * 1024, f"chats.py kept {grown / 1e6:.1f} MB"


def test_a_load_right_after_a_save_decodes_no_row(monkeypatch) -> None:
    import tracemalloc

    repo._opened.clear()
    repo._opened_size = 0
    messages, _ = _long_chat("fresh")
    opens = _counting_opens(monkeypatch)
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        loaded = repo.messages_get("fresh")
        grown = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    print(f"load after save: {grown / 1e6:.1f} MB")
    assert loaded == messages and opens[0] == 0
    assert loaded[7]["text"] is messages[7]["text"] and loaded[7] is not messages[7]  # the saver's strings
    assert grown <= 12 * 1024 * 1024, f"load grew {grown / 1e6:.1f} MB"


def test_a_full_load_cache_allocates_no_more_than_its_cap() -> None:
    import gc
    import tracemalloc

    repo._opened.clear()
    repo._opened_size = 0
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for c in range(8):
            for seq in range(9000):
                # Fresh strings each row: the cache owns all it holds (the worst case).
                repo._opened_put(f"full{c}", seq, f"h{seq}", {
                    "role": "assistant", "ts": seq + 0.5, "run_id": f"run{c}",
                    "content": f"{c} {seq} " + "x" * 300, "text": f"{c} {seq} " + "é" * 100,
                    "tool_calls": [{"name": "read", "args": {"path": f"a/{seq}.py", "n": seq}}],
                })
        gc.collect()
        held = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    counted = repo._opened_size
    rows = sum(len(r) for r in repo._opened.values())
    repo._opened.clear()
    repo._opened_size = 0
    print(f"full cache: {rows} rows, {counted / 1e6:.1f} MB counted, {held / 1e6:.1f} MB allocated")
    assert rows < 8 * 9000  # past its cap: rows were dropped
    assert counted <= repo._OPENED_MAX_BYTES
    assert held <= 32 * 1024 * 1024, f"the cache holds {held / 1e6:.1f} MB"


def _counting_opens(monkeypatch) -> list[int]:
    calls = [0]
    real = repo._message

    def message(*args):
        calls[0] += 1
        return real(*args)

    monkeypatch.setattr(repo, "_message", message)
    return calls


def test_a_reload_of_a_long_chat_opens_no_row(monkeypatch) -> None:
    messages, _ = _long_chat("reload")
    repo._opened.clear()
    repo._opened_size = 0
    opens = _counting_opens(monkeypatch)
    assert repo.messages_get("reload") == messages and opens[0] == 5000  # cold: every row opened once
    started = time.perf_counter()
    again, rev = repo.messages_get_rev("reload")
    took = time.perf_counter() - started
    print(f"warm reload of 5,000 msgs: {took * 1000:.1f} ms")
    assert again == messages and rev == _rev("reload") and opens[0] == 5000
    assert took < 0.1, f"warm reload took {took * 1000:.1f} ms"
    # A saved row is remembered too: no open for what this process just wrote.
    messages[10] = _msg("assistant", "rewritten here")
    repo.conv_save(PROJECT, _doc("reload"), messages=messages)
    assert repo.messages_get("reload")[10]["text"] == "rewritten here" and opens[0] == 5000
    # Another connection's row has a new hash: it is opened afresh, never served stale.
    other = _other_connection()
    try:
        body = repo.account_rows.seal(repo._dumps(_msg("user", "theirs")), repo.account_rows.current())
        other.execute("UPDATE messages SET hash='elsewhere', body=?, fmt=0 WHERE conv_id='reload' AND seq=3", (body,))
    finally:
        other.close()
    assert repo.messages_get("reload")[3]["text"] == "theirs" and opens[0] == 5001
    assert repo.conv_delete("reload") and "reload" not in repo._opened


def _opened_total() -> int:
    return sum(entry[2] for rows in repo._opened.values() for entry in rows.values())


def test_opened_rows_stay_under_the_cap(monkeypatch) -> None:
    monkeypatch.setattr(repo, "_OPENED_MAX_BYTES", 200_000)
    for i in range(20):
        repo.conv_save(PROJECT, _doc(f"cap{i}"), messages=[_msg("user", f"{i} " + "z" * 1000) for _ in range(50)])
        repo.messages_get(f"cap{i}")
    assert repo._opened_size == _opened_total() <= 200_000
    assert repo.messages_get("cap19")[0]["text"].startswith("19 ")


def test_the_load_cache_keeps_eight_chats_under_its_cap(monkeypatch) -> None:
    import tracemalloc

    repo._opened.clear()
    repo._opened_size = 0
    sizes = {**{f"w{i}": 200 for i in range(60)}, "w5k": 5000, "w20k": 20000}
    chats = {cid: [_msg("user" if k % 2 == 0 else "assistant", f"{cid} {k} " + "x" * 100) for k in range(n)]
             for cid, n in sizes.items()}
    for cid, messages in chats.items():
        repo.conv_save(PROJECT, _doc(cid), messages=messages)
    tracemalloc.start()
    try:
        base = tracemalloc.take_snapshot()
        for cid, messages in chats.items():
            assert repo.messages_get(cid) == messages
        opens = _counting_opens(monkeypatch)
        assert repo.messages_get("w5k") == chats["w5k"] and opens[0] == 0  # recently loaded: no row opened
        grown = sum(s.size_diff for s in tracemalloc.take_snapshot().compare_to(base, "filename")
                    if s.traceback[0].filename.endswith("chats.py"))
    finally:
        tracemalloc.stop()
    print(f"load cache: {len(repo._opened)} chats, {repo._opened_size / 1e6:.1f} MB counted, "
          f"chats.py kept {grown / 1e6:.1f} MB")
    assert len(repo._opened) <= 8 and list(repo._opened)[-2:] == ["w20k", "w5k"]
    assert repo._opened_size == _opened_total() <= repo._OPENED_MAX_BYTES
    assert grown < 25 * 1024 * 1024, f"chats.py kept {grown / 1e6:.1f} MB"


def test_loads_share_the_cached_strings_and_never_the_shell(monkeypatch) -> None:
    import tracemalloc

    messages = [_msg("user" if i % 2 == 0 else "assistant", f"{i} " + "w" * 1000, tags=["a", [i]])
                for i in range(5000)]
    repo.conv_save(PROJECT, _doc("shells"), messages=messages)
    repo._opened.clear()
    repo._opened_size = 0
    opens = _counting_opens(monkeypatch)
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        one = repo.messages_get("shells")
        first = tracemalloc.get_traced_memory()[0] - before
        opened = opens[0]
        before = tracemalloc.get_traced_memory()[0]
        two, _ = repo.messages_get_rev("shells")
        second = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    print(f"load allocations: cold {first / 1e6:.1f} MB, warm {second / 1e6:.1f} MB")
    assert one == two == messages and opened == 5000 and opens[0] == 5000  # the warm load opened 0
    assert second < first * 0.25, (first, second)
    assert one[7]["text"] is two[7]["text"] and one[7] is not two[7]  # shared strings, own containers
    one[7]["tags"][1].append("mutated")
    one[8]["text"] = "changed"
    three = repo.messages_get("shells")
    assert two[7]["tags"] == three[7]["tags"] == ["a", [7]] and three[8]["text"] == messages[8]["text"]
    # Another writer's row (a new hash) is decoded fresh.
    other = _other_connection()
    try:
        body = repo.account_rows.seal(repo._dumps(_msg("user", "fresh")), repo.account_rows.current())
        other.execute("UPDATE messages SET hash='new', body=?, fmt=0 WHERE conv_id='shells' AND seq=9", (body,))
    finally:
        other.close()
    assert repo.messages_get("shells")[9]["text"] == "fresh" and opens[0] == 5001


def test_a_row_only_another_writer_changed_is_kept_and_a_row_both_changed_goes_to_the_copy() -> None:
    msgs = [_msg("user", f"m{i}") for i in range(6)]
    repo.conv_save(PROJECT, _doc("tri"), messages=msgs)
    mine, rev, base = repo.messages_get_rev("tri", with_hashes=True)
    theirs = [dict(m) for m in mine]
    theirs[2], theirs[4] = _msg("user", "theirs 2"), _msg("user", "theirs 4")
    repo.conv_save(PROJECT, _doc("tri"), messages=theirs)
    mine[4] = _msg("user", "mine 4")  # both changed 4; only they changed 2
    for path_args in ({"dirty_from": 1, "expect_rev": rev}, {}):  # a merge, then a stale full save
        out = repo.conv_save(PROJECT, _doc("tri"), messages=mine, base_hashes=base, **path_args)
        stored = [m["text"] for m in repo.messages_get("tri")]
        assert stored[2] == "theirs 2" and stored[4] == "mine 4", (out["path"], stored)
        assert 2 in out["theirs"]
    assert out["path"] == "full"


def test_concurrent_rewriter_and_stale_appender_lose_nothing() -> None:
    import random
    import threading

    repo.conv_save(PROJECT, _doc("race"), messages=[_msg("user", f"m{i}") for i in range(4001)])
    errors: list[BaseException] = []
    rewrites: dict[int, str] = {}
    appends: list[str] = []

    def copy() -> tuple[list[dict], int, list[str]]:
        msgs, rev, base = repo.messages_get_rev("race", with_hashes=True)
        return msgs, rev, base

    def saved(msgs: list[dict], rev: int, base: list[str], start: int, out: dict) -> tuple[int, list[str]]:
        new = base[:start] + list(out["hashes"])
        for seq in out["theirs"]:
            new[seq] = base[seq]  # this copy still holds the old content there
        return (out["msg_rev"] if out["path"] in ("delta", "full") else rev), new

    def rewriter() -> None:
        try:
            msgs, rev, base = copy()
            rnd = random.Random(7)
            for k in range(100):
                seq = rnd.randint(1, 4000)
                msgs[seq] = _msg("assistant", f"rewrite {k} of {seq}")
                rewrites[seq] = msgs[seq]["text"]
                out = repo.conv_save(PROJECT, _doc("race"), messages=msgs, dirty_from=seq, expect_rev=rev,
                                     base_hashes=base)
                rev, base = saved(msgs, rev, base, out["hashes_from"], out)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            db.close_thread_connections()

    def appender() -> None:
        try:
            msgs, rev, base = copy()  # never reloaded: stale for every rewrite
            for k in range(100):
                msgs.append(_msg("user", f"append {k}"))
                appends.append(msgs[-1]["text"])
                out = repo.conv_save(PROJECT, _doc("race"), messages=msgs, dirty_from=1, expect_rev=rev,
                                     base_hashes=base)  # a whole-turn save over the rewriter's rows
                rev, base = saved(msgs, rev, base, out["hashes_from"], out)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            db.close_thread_connections()

    threads = [threading.Thread(target=rewriter), threading.Thread(target=appender)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert not errors, errors
    stored = [m["text"] for m in repo.messages_get("race")]
    lost = [seq for seq, text in rewrites.items() if stored[seq] != text]
    assert lost == [], f"{len(lost)} of {len(rewrites)} rewrites lost"
    assert stored[4001:] == appends
    repo.conv_save(PROJECT, _doc("run"), messages=[_msg("user", "go")])
    start = _rev("run")
    repo.upsert_run_message("run", "r1", _msg("assistant", "working", run_id="r1"), updated=2.0)
    appended = _rev("run")
    assert appended > start
    repo.upsert_run_message("run", "r1", _msg("assistant", "done", run_id="r1"), updated=3.0)
    assert _rev("run") > appended


def test_checkpoint_prepares_its_row_outside_the_write_lock(monkeypatch) -> None:
    repo.conv_save(PROJECT, _doc("prep"), messages=[_msg("user", "go")])
    repo.messages_get("prep")  # the in-flight row gets cached once written
    under_lock: list[str] = []

    def watch(name, fn):
        def wrapped(*a, **kw):
            if db._write_lock._is_owned():
                under_lock.append(name)
            return fn(*a, **kw)
        monkeypatch.setattr(repo, name, wrapped)

    for name in ("_prep_row", "_seal_part", "_shell", "_copy_containers", "_dumps", "_hash"):
        watch(name, getattr(repo, name))
    for i in range(3):
        big = f"{i}" + "x" * 20_000  # a part: compressed and sealed
        repo.upsert_run_message("prep", "r1", _msg("assistant", big, run_id="r1"), updated=2.0 + i)
    assert under_lock == []
    assert repo.messages_get("prep")[-1]["text"].startswith("2")


def test_checkpoint_lock_hold_p95_under_5ms(monkeypatch) -> None:
    held: list[float] = []

    class TimedLock:
        def __init__(self) -> None:
            self._lock = threading.RLock()
            self._t0 = 0.0

        def __enter__(self):
            self._lock.acquire()
            self._t0 = time.perf_counter()
            return self

        def __exit__(self, *exc):
            held.append(time.perf_counter() - self._t0)
            self._lock.release()

        def _is_owned(self) -> bool:
            return self._lock._is_owned()

    repo.conv_save(PROJECT, _doc("p95"), messages=[_msg("user", "go")])
    monkeypatch.setattr(db, "_write_lock", TimedLock())
    for i in range(200):
        text = f"{i}:" + "y" * 50_000
        repo.upsert_run_message("p95", "r1", _msg("assistant", text, run_id="r1"), updated=2.0 + i)
    held.sort()
    p95 = held[int(len(held) * 0.95)] * 1000
    assert p95 < 5.0, f"lock-held p95 {p95:.2f} ms"
    assert repo.messages_get("p95")[-1]["text"].startswith("199:")


def test_fill_on_save_costs_a_20k_chat_little_cpu(monkeypatch) -> None:
    # ~1.8 KB rows: the chat is over the cache cap, so most rows can never be kept.
    messages = [_msg("user" if k % 2 == 0 else "assistant", f"{k} " + "x" * 1500) for k in range(20_000)]
    repo.conv_save(PROJECT, _doc("cpu20k"), messages=messages)

    def saves() -> float:
        took = 0.0
        for i in range(20):
            messages.append(_msg("assistant", f"new {i}"))
            started = time.thread_time()
            repo.conv_save(PROJECT, _doc("cpu20k"), messages=messages)
            took += time.thread_time() - started
        return took

    repo._opened.clear()
    repo._opened_size = 0
    with monkeypatch.context() as m:
        m.setattr(repo, "_opened_saved", lambda *a: None)
        off = saves()
    on = saves()
    print(f"20 saves of 20k: {on:.2f} s CPU vs {off:.2f} s with fill off ({on / off:.2f}x)")
    assert repo._opened_size == _opened_total() <= repo._OPENED_MAX_BYTES
    assert on <= 1.2 * off, f"fill-on-save: {on:.2f} s vs {off:.2f} s"
