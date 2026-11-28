"""Chat messages: saved by one backend, loaded and saved again by the other.

Most tests do each save twice, on chat ``r`` through Rust and on chat ``p`` through
Python, and want the same answer and the same rows. Tests run signed out and signed in.
"""

from __future__ import annotations

import copy

from backend.store.repos import chats

from .parity_harness import plain
from .test_store_chats import _doc, _sql

_USER = {"role": "user", "text": "hello world", "ts": 1.0}
_REPLY = {"role": "assistant", "content": "  hi there é \n", "ts": 2.0, "run_id": "r1"}
_NUMBERS = {
    "role": "user", "text": "numbers", "ts": 2.5,
    "vals": [1e-05, 1e16, 0.1, 3, -2.5e-07, 1.0, 0.0001, 123456789012, -0.0],
    "nested": {"a": [True, None, {"k": "\x1f\t\"quoted\" \\ é 😀"}]},
}
# Two fields past 8 KB of JSON: each is stored once as a payload.
_BIG = {
    "role": "assistant", "content": "reply é", "ts": 3.0, "run_id": "r2",
    "blocks": [
        {"type": "tool", "name": "read_file", "arguments": {"path": "a.txt", "n": None, "ok": True, "it's": 1.5},
         "result": {"text": "X" * 9000 + " needle zebra"}},
        "loose",
        {"name": "t2", "arguments": "plain words", "result": {"hint": "hinted at"}},
    ],
    "long": "é" * 9000,
}
_ALL = [_USER, _REPLY, _NUMBERS, _BIG]


def _save(rust_store, conv_id: str, messages, **how) -> dict:
    doc = _doc(conv_id)
    if conv_id.startswith("r"):
        return rust_store.store("chats", "conv_save", project_id="p1", doc=doc, messages=messages, **how)
    return plain(chats.conv_save("p1", doc, messages=copy.deepcopy(messages), **how))


def _rows(conv_id: str) -> list:
    return _sql("SELECT seq, hash, role, ts, run_id, text, fmt FROM messages WHERE conv_id=? ORDER BY seq", conv_id)


def _payloads(conv_id: str) -> list:
    return _sql(
        "SELECT m.seq, r.hash FROM message_part_refs r JOIN messages m ON m.id = r.message_id "
        "WHERE m.conv_id=? ORDER BY m.seq, r.hash",
        conv_id,
    )


def _same_chats(rust_store) -> None:
    """Chats ``r`` and ``p`` hold the same rows, and each backend reads both alike."""
    assert _rows("r") == _rows("p") and _payloads("r") == _payloads("p")
    assert chats.messages_get("r") == chats.messages_get("p")
    for conv_id in ("r", "p"):
        assert rust_store.store("chats", "messages_get", conv_id=conv_id) == plain(chats.messages_get(conv_id))
        assert rust_store.store("chats", "messages_get_rev", conv_id=conv_id) == plain(
            chats.messages_get_rev(conv_id, with_hashes=True)
        )


def _both(rust_store, messages, **how) -> dict:
    ours, theirs = _save(rust_store, "r", messages, **how), _save(rust_store, "p", messages, **how)
    assert ours == theirs
    _same_chats(rust_store)
    return ours


def test_a_new_chat_saves_the_same_rows(rust_store, account) -> None:
    stats = _both(rust_store, _ALL)
    assert (stats["path"], stats["inserted"], stats["updated"], stats["deleted"], stats["hashes_from"]) == ("full", 4, 0, 0, 0)
    assert len(stats["hashes"]) == 4 and stats["theirs"] == [] and stats["dirty_hashes"] == {}
    assert chats.messages_get("r") == _ALL  # exactly as saved, big fields put back
    assert [row[-1] for row in _rows("r")] == [0, 0, 0, 1] and len(_payloads("r")) == 2
    assert _sql("SELECT count(*) FROM message_parts") == [(2,)]  # one account: the payloads are shared
    for conv_id in ("r", "p"):
        bodies = [b for (b,) in _sql("SELECT body FROM messages WHERE conv_id=?", conv_id)]
        assert all(b.startswith("enc1:") for b in bodies)
        assert all(bytes(d).startswith(b"UDE1") for (d,) in _sql("SELECT data FROM message_parts"))
        got = rust_store.store("chats", "conv_get", conv_id=conv_id, with_messages=True)
        assert got == plain(chats.conv_get(conv_id)) and got["messages"] == plain(_ALL)
    assert rust_store.store("chats", "conv_list", project_id="p1", with_messages=True) == plain(
        chats.conv_list("p1", with_messages=True)
    )
    assert rust_store.store("chats", "conv_list_all_projects", with_messages=True) == plain(
        chats.conv_list_all_projects(with_messages=True)
    )


def test_saving_the_other_backends_rows_again_writes_nothing(rust_store, account) -> None:
    _both(rust_store, _ALL)
    # Each chat goes to the other backend: every hash must already match.
    ours = rust_store.store("chats", "conv_save", project_id="p1", doc=_doc("p"), messages=_ALL)
    theirs = plain(chats.conv_save("p1", _doc("r"), messages=copy.deepcopy(_ALL)))
    assert ours == theirs and (ours["inserted"], ours["updated"], ours["deleted"], ours["bytes"]) == (0, 0, 0, 0)
    # And what Rust loads from Python's chat saves back unchanged.
    loaded = rust_store.store("chats", "messages_get", conv_id="p")
    again = rust_store.store("chats", "conv_save", project_id="p1", doc=_doc("p"), messages=loaded)
    assert (again["inserted"], again["updated"]) == (0, 0)
    _same_chats(rust_store)


def test_a_5000_message_chat_loads_the_same(rust_store, account) -> None:
    long = [
        {"role": "assistant" if i % 2 else "user", "content": f"message {i} é " + "word " * (i % 40), "ts": float(i),
         "blocks": [{"name": "read_file", "arguments": {"n": i}, "result": {"text": "Z" * 9000 if i % 500 == 0 else "ok"}}]}
        for i in range(5000)
    ]
    stats = _both(rust_store, long)  # saved by each, read by both, row for row
    assert stats["inserted"] == 5000 and len(_payloads("r")) == 10
    assert rust_store.store("chats", "messages_get", conv_id="p") == plain(long) and chats.messages_get("r") == long
    tail = rust_store.store("chats", "messages_range", conv_id="p", start=4990, stop=None)
    assert tail == plain(chats.messages_range("p", 4990, None)) and [m for _, m in tail[0]] == plain(long[4990:])


def test_pages_tails_and_the_last_reply(rust_store, account) -> None:
    _both(rust_store, _ALL + [{"role": "assistant", "content": "   "}, {"role": "assistant", "content": 7}])
    for conv_id in ("r", "p", "missing"):
        for seq in (-2, 0, 2, 5, 99):
            assert rust_store.store("chats", "messages_since", conv_id=conv_id, seq=seq) == plain(
                chats.messages_since(conv_id, seq, with_hashes=True)
            )
        for start, stop in ((0, None), (1, 3), (-5, 2), (3, 4), (4, 99), (2, 2)):
            assert rust_store.store("chats", "messages_range", conv_id=conv_id, start=start, stop=stop) == plain(
                chats.messages_range(conv_id, start, stop)
            )
        assert rust_store.store("chats", "last_reply", conv_id=conv_id) == plain(chats.last_reply(conv_id))
        assert rust_store.store("chats", "conv_message_summary", conv_id=conv_id) == plain(chats.conv_message_summary(conv_id))
    assert rust_store.store("chats", "last_reply", conv_id="r") == ["reply é", 4]
    assert rust_store.store("chats", "messages_get_rev", conv_id="missing") == [[], None, []]


def test_a_delta_save_writes_only_what_changed(rust_store, account) -> None:
    first = _both(rust_store, _ALL)
    more = _ALL + [{"role": "user", "text": "one more", "ts": 9.0}]
    stats = _both(rust_store, more, dirty_from=4, expect_rev=first["msg_rev"])
    assert (stats["path"], stats["inserted"], stats["updated"], stats["hashes_from"]) == ("delta", 1, 0, 4)
    # A listed row below the tail is written too; the rows between are not read.
    edited = copy.deepcopy(more)
    edited[1]["content"] = "edited reply"
    edited[2]["text"] = "changed but not listed: left as stored"
    stats = _both(rust_store, edited, dirty_from=5, expect_rev=stats["msg_rev"], dirty_rows=[1, 1, 7, -1])
    assert (stats["path"], stats["updated"], list(stats["dirty_hashes"]), stats["hashes"]) == ("delta", 1, ["1"], [])
    assert chats.messages_get("r")[1]["content"] == "edited reply" and chats.messages_get("r")[2]["text"] == "numbers"
    # Shorter than what is stored: the rows past the end go, with the payloads only they used.
    stats = _both(rust_store, edited[:3], dirty_from=3, expect_rev=stats["msg_rev"])
    assert (stats["path"], stats["deleted"]) == ("delta", 2)
    assert _sql("SELECT count(*) FROM message_parts") == [(0,)] and _payloads("r") == []
    # dirty_from past the end is clamped.
    stats = _both(rust_store, edited[:3], dirty_from=50, expect_rev=stats["msg_rev"])
    assert (stats["path"], stats["hashes_from"], stats["inserted"]) == ("delta", 3, 0)


def test_a_stale_copy_merges_and_never_truncates(rust_store, account) -> None:
    first = _both(rust_store, _ALL)
    _both(rust_store, _ALL + [{"role": "user", "text": "another writer's row"}], dirty_from=4, expect_rev=first["msg_rev"])
    # This copy still holds 4 rows and the old rev: its tail is written, nothing is dropped.
    mine = copy.deepcopy(_ALL)
    mine[3] = {"role": "assistant", "content": "my newer reply", "run_id": "r2"}
    stats = _both(rust_store, mine, dirty_from=3, expect_rev=first["msg_rev"])
    assert (stats["path"], stats["updated"], stats["deleted"], stats["hashes_from"]) == ("merge", 1, 0, 3)
    assert [m.get("text") or m.get("content") for m in chats.messages_get("r")][3:] == ["my newer reply", "another writer's row"]
    assert _sql("SELECT message_count FROM conversations WHERE id='r'") == [(5,)]
    # dirty_from past the stored end starts at the stored end: no gap.
    stats = _both(rust_store, mine + [{"role": "user", "text": "x"}] * 4, dirty_from=7, expect_rev=-1)
    assert (stats["path"], stats["hashes_from"], stats["inserted"]) == ("merge", 5, 3)
    # dirty_from 0 with a stale rev is a reassign: the full path.
    stats = _both(rust_store, _ALL[:2], dirty_from=0, expect_rev=-1)
    assert (stats["path"], stats["deleted"]) == ("full", 6)


def test_another_writers_newer_row_is_kept_as_theirs(rust_store, account) -> None:
    first = _both(rust_store, _ALL)
    theirs = copy.deepcopy(_ALL)
    theirs[0]["text"] = "edited by another writer"
    theirs[1]["content"] = "also theirs"
    _both(rust_store, theirs)
    # This copy changed only row 1 since it read the chat (base = the first hashes).
    mine = copy.deepcopy(_ALL)
    mine[1]["content"] = "mine"
    stats = _both(rust_store, mine, base_hashes=first["hashes"])
    assert (stats["path"], stats["theirs"], stats["updated"]) == ("full", [0], 1)
    got = chats.messages_get("r")
    assert (got[0]["text"], got[1]["content"]) == ("edited by another writer", "mine")
    # The same on the delta path with listed rows: a copy still at its base keeps both
    # stored rows (row 1 is now "mine" in the store, and this copy never changed it).
    stats = _both(rust_store, _ALL, dirty_from=2, expect_rev=stats["msg_rev"], dirty_rows=[0, 1], base_hashes=first["hashes"])
    assert (stats["path"], stats["theirs"], stats["updated"], sorted(stats["dirty_hashes"])) == ("delta", [0, 1], 0, ["0", "1"])
    got = chats.messages_get("r")
    assert (got[0]["text"], got[1]["content"]) == ("edited by another writer", "mine")
    # A row both changed goes to this copy.
    mine[0]["text"] = "mine too"
    stats = _both(rust_store, mine, dirty_from=2, expect_rev=stats["msg_rev"], dirty_rows=[0], base_hashes=first["hashes"])
    assert (stats["theirs"], stats["updated"]) == ([], 1) and chats.messages_get("r")[0]["text"] == "mine too"


def test_a_save_without_messages_leaves_the_transcript(rust_store, account) -> None:
    first = _both(rust_store, _ALL)
    for conv_id in ("r", "p"):
        doc = _doc(conv_id, title="Renamed", extra={"now": 1}, skill_snapshot="another index", is_group=1, tool_call_count="7")
        stats = rust_store.store("chats", "conv_save", project_id="p9", doc=doc) if conv_id == "r" else plain(
            chats.conv_save("p9", doc, messages=None)
        )
        assert stats == {"inserted": 0, "updated": 0, "deleted": 0, "bytes": 0, "path": "meta", "msg_rev": first["msg_rev"]}
        got = rust_store.store("chats", "conv_get", conv_id=conv_id, with_messages=True)
        assert got == plain(chats.conv_get(conv_id))
        assert (got["title"], got["extra"], got["skill_snapshot"], got["is_group"], got["tool_call_count"]) == (
            "Renamed", {"now": 1}, "another index", True, 7,
        )
        assert chats.conv_project_id(conv_id) == "p9" and len(got["messages"]) == 4
    _same_chats(rust_store)
    # An unchanged title and state keep their stored value: nothing is sealed again.
    before = _sql("SELECT id, title, state FROM conversations ORDER BY id")
    rust_store.store("chats", "conv_save", project_id="p9", doc=_doc("p", title="Renamed", extra={"now": 1}))
    chats.conv_save("p9", _doc("r", title="Renamed", extra={"now": 1}), messages=None)
    assert _sql("SELECT id, title, state FROM conversations ORDER BY id") == before
    assert _sql("SELECT kind, text FROM snapshots ORDER BY text") == [
        ("skill_index", "another index"), ("skill_index", "the skills index"),
    ]


def test_another_accounts_chat_and_a_deleted_one_are_never_written(rust_store, account) -> None:
    _both(rust_store, _ALL)
    other = "_local" if account != "_local" else "a_0000000000000000"
    _sql("UPDATE conversations SET account_id=? WHERE id IN ('r', 'p')", other)
    skipped = {"inserted": 0, "updated": 0, "deleted": 0, "bytes": 0, "skipped": 1}
    assert _save(rust_store, "r", _ALL[:1]) == _save(rust_store, "p", _ALL[:1]) == skipped
    assert rust_store.store("chats", "upsert_run_message", conv_id="r", run_id="x", message=_REPLY, updated=1.0) == {"skipped": 1}
    assert rust_store.store("chats", "messages_get", conv_id="r") == []
    assert rust_store.store("chats", "last_reply", conv_id="r") == ["", 0]
    assert _sql("SELECT count(*) FROM messages") == [(8,)]
    _sql("UPDATE conversations SET account_id=? WHERE id IN ('r', 'p')", account)
    assert rust_store.store("chats", "conv_delete", conv_id="r") is True and chats.conv_delete("p") is True
    assert _save(rust_store, "r", _ALL) == _save(rust_store, "p", _ALL) == skipped
    assert rust_store.store("chats", "upsert_run_message", conv_id="r", run_id="x", message=_REPLY, updated=1.0) == {"skipped": 1}
    assert _sql("SELECT count(*) FROM conversations") == [(0,)]


def test_a_run_writes_its_reply_as_one_row(rust_store, account) -> None:
    _both(rust_store, [_USER])

    def upsert(run_id: str, message: dict, updated: float, stats: dict | None = None) -> dict:
        ours = rust_store.store("chats", "upsert_run_message", conv_id="r", run_id=run_id, message=message, updated=updated, stats=stats)
        theirs = chats.upsert_run_message("p", run_id, copy.deepcopy(message), updated=updated, stats=stats)
        assert ours == theirs
        _same_chats(rust_store)
        assert _sql("SELECT updated, message_count, tool_call_count, file_count FROM conversations WHERE id='r'") == _sql(
            "SELECT updated, message_count, tool_call_count, file_count FROM conversations WHERE id='p'"
        )
        return ours

    assert upsert("run1", {"role": "assistant", "content": "thinking", "run_id": "run1"}, 30.0) == {"seq": 1, "count": 2}
    # The same run again replaces its row, payloads and all.
    assert upsert("run1", {**_BIG, "run_id": "run1"}, 31.0, {"tool_call_count": 3, "file_count": 2}) == {"seq": 1, "count": 2}
    assert len(_payloads("r")) == 2
    assert upsert("run1", {**_BIG, "run_id": "run1", "long": "short now"}, 32.0) == {"seq": 1, "count": 2}
    assert len(_payloads("r")) == 1
    assert upsert("run1", {**_BIG, "run_id": "run1", "long": "short now"}, 33.0, {}) == {"seq": 1, "count": 2}  # unchanged
    # Another run, and no run at all, append.
    assert upsert("run2", {"role": "assistant", "content": "second", "run_id": "run2"}, 34.0) == {"seq": 2, "count": 3}
    assert upsert("", {"role": "assistant", "content": "no run"}, 35.0) == {"seq": 3, "count": 4}
    assert _sql("SELECT tool_call_count, file_count, updated FROM conversations WHERE id='r'") == [(3, 2, 35.0)]
    # After a new user row the old run's reply is not replaced.
    more = chats.messages_get("r") + [{"role": "user", "text": "next question"}]
    _both(rust_store, more, dirty_from=4, expect_rev=chats.conv_msg_rev("r"))
    assert upsert("run1", {"role": "assistant", "content": "a new turn", "run_id": "run1"}, 36.0) == {"seq": 5, "count": 6}
    assert rust_store.store("chats", "upsert_run_message", conv_id="missing", run_id="x", message=_REPLY, updated=1.0) == {"skipped": 1}


def test_search_finds_the_same_messages(rust_store, account) -> None:
    _both(rust_store, _ALL)
    chats.conv_save("p2", _doc("elsewhere", title="Other project"), messages=[{"role": "user", "text": "hello needle elsewhere"}])

    def found(project: str, query: str, **how) -> list:
        ours = rust_store.store("chats", "search_messages", project_id=project, query=query, **how)
        theirs = plain(chats.search_messages(project, query, **how))
        order = lambda hit: (hit["conv_id"], hit["seq"])  # noqa: E731 — equal ranks come in any order
        assert sorted(ours, key=order) == sorted(theirs, key=order), query
        return sorted(ours, key=order)

    assert [(h["conv_id"], h["seq"]) for h in found("p1", "hello")] == [("p", 0), ("r", 0)]
    assert [(h["seq"], h["title"]) for h in found("p1", "NEEDLE zebra")] == [(3, "Chat é"), (3, "Chat é")]
    assert found("p1", "needle")[0]["preview"].startswith("…")
    assert [(h["conv_id"], h["preview"]) for h in found("p2", "needle")] == [("elsewhere", "hello needle elsewhere")]
    for query in ("read_file", "a.txt", "hinted", "plain words", "é", "hi there", "world hello", "nothing here", "", '"', "numbers"):
        found("p1", query)
    assert len(found("p1", "hello", limit=1)) == 1
    assert found("p1", "nothing here") == [] and found("p1", "") == []
