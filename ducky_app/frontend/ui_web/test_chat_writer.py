"""The chat-writer: one thread, batched saves, read-your-writes, skips and WAL upkeep."""

from __future__ import annotations

import threading
import time

import pytest

from backend.store import db
from backend.store.repos import chats as repo
from frontend.settings import PanelSettings
from frontend.ui_web import chat_writer, project_chats
from frontend.ui_web.chat_writer import ChatWriter
from frontend.ui_web.project_chats import (
    create_conversation,
    flush_chat_saves,
    load_conversation,
    load_conversation_meta,
    save_conversation,
)


@pytest.fixture(autouse=True)
def _flushed():
    yield
    flush_chat_saves()


def _msgs(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}", "text": f"m{i}", "ts": i} for i in range(n)]


def _chat(root: str, title: str = "chat"):
    conv = create_conversation(PanelSettings.load(), "", title=title, project_root=root)
    return load_conversation(conv.id, root)


def _stored_count(conv_id: str) -> int:
    """Read through a second connection (a fresh thread's), as another process would."""
    out: list[int] = []

    def read() -> None:
        row = db.connect().execute("SELECT message_count FROM conversations WHERE id=?", (conv_id,)).fetchone()
        out.append(int(row[0]) if row else -1)

    reader = threading.Thread(target=read)
    reader.start()
    reader.join()
    return out[0]


def test_a_save_with_wait_is_visible_to_another_connection_at_once(tmp_path) -> None:
    root = str(tmp_path)
    conv = _chat(root)
    conv.messages = _msgs(3)
    save_conversation(conv, root, wait=True)
    assert _stored_count(conv.id) == 3


def test_a_new_chat_is_written_before_create_returns(tmp_path) -> None:
    conv = create_conversation(PanelSettings.load(), "", title="new", project_root=str(tmp_path))
    assert _stored_count(conv.id) == 0  # the row is there (-1 would mean missing)


def test_flush_writes_what_waits(tmp_path) -> None:
    """What the HTTP server calls before answering /__panel_rpc and /__panel_run."""
    root = str(tmp_path)
    conv = _chat(root)
    conv.messages = _msgs(4)
    save_conversation(conv, root)
    assert flush_chat_saves() is True
    assert _stored_count(conv.id) == 4
    assert flush_chat_saves() is True  # nothing waits: returns at once


def test_a_load_right_after_a_save_returns_the_saved_state(tmp_path) -> None:
    root = str(tmp_path)
    conv = _chat(root)
    conv.messages = _msgs(6)
    save_conversation(conv, root)
    loaded = load_conversation(conv.id, root)
    assert loaded is not None and [m["content"] for m in loaded.messages] == [f"m{i}" for i in range(6)]
    meta = load_conversation_meta(conv.id, root)
    assert meta is not None and meta.messages == [] and meta.title == conv.title


def test_100_saves_of_10_chats_take_at_most_10_transactions(tmp_path) -> None:
    root = str(tmp_path)
    chats = [_chat(root, f"c{i}") for i in range(10)]
    flush_chat_saves()
    writer = project_chats._writer()
    before = writer.transactions
    t0 = time.monotonic()
    for i in range(100):
        conv = chats[i % 10]
        conv.messages = _msgs(i // 10 + 1)
        save_conversation(conv, root)
    elapsed = time.monotonic() - t0
    flush_chat_saves()
    assert writer.transactions - before <= max(10, int(elapsed / chat_writer.INTERVAL_S) + 2)
    for conv in chats:
        assert _stored_count(conv.id) == 10  # the last save of each chat won


def test_saves_of_one_chat_merge_latest_wins() -> None:
    written: list[tuple[str, int]] = []
    writer = ChatWriter(lambda cid, item: written.append((cid, item)), connect=db.connect, interval=0.3)
    try:
        for n in range(1, 11):
            writer.submit("a", n)
        assert writer.peek("a") == 10
        assert writer.flush() is True
        assert written == [("a", 10)]
        assert writer.peek("a") is None
    finally:
        writer.close()


def test_exit_flush_writes_everything() -> None:
    written: list[str] = []

    def write(cid: str, item: int) -> None:
        time.sleep(0.01)
        written.append(cid)

    writer = ChatWriter(write, connect=db.connect, interval=5.0)
    futures = [writer.submit(f"c{i}", i) for i in range(5)]
    writer.close()
    assert sorted(written) == [f"c{i}" for i in range(5)]
    assert all(f.done() and f.exception() is None for f in futures)
    writer.submit("late", 1).result(timeout=1)  # after exit a save writes in the caller
    assert written[-1] == "late"


def test_a_failed_save_fails_only_its_own_future() -> None:
    def write(cid: str, item: int) -> None:
        if cid == "bad":
            raise ValueError("nope")

    writer = ChatWriter(write, connect=db.connect)
    try:
        good, bad = writer.submit("good", 1), writer.submit("bad", 1)
        writer.flush()
        assert good.exception() is None
        assert isinstance(bad.exception(), ValueError)
    finally:
        writer.close()


# --------------------------------------------------------------- p6-noreload


def _count_message_reads(monkeypatch) -> list[int]:
    """Transcript decodes: every message read (conv_get with messages, messages_get) goes
    through repo._messages once."""
    reads: list[int] = []
    real_messages = repo._messages
    monkeypatch.setattr(repo, "_messages", lambda *a, **k: reads.append(1) or real_messages(*a, **k))
    return reads


def test_a_metadata_save_reads_no_messages(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _chat(root)
    conv.messages = _msgs(40)
    save_conversation(conv, root, wait=True)
    meta = load_conversation_meta(conv.id, root)
    reads = _count_message_reads(monkeypatch)
    meta.title = "renamed"
    save_conversation(meta, root, wait=True)
    assert reads == []
    assert meta.messages == []  # kept as it was, not reloaded
    assert _stored_count(conv.id) == 40


def test_a_stale_copy_reloads_once_after_another_writer_added_rows(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _chat(root)
    conv.messages = _msgs(1)  # just the user's question
    save_conversation(conv, root, wait=True)
    other = load_conversation(conv.id, root)
    other.messages.append({"role": "assistant", "content": "answer", "text": "answer", "ts": 2})
    save_conversation(other, root, wait=True)
    reads = _count_message_reads(monkeypatch)
    save_conversation(conv, root, wait=True)
    assert len(reads) == 1
    assert [m["content"] for m in conv.messages] == ["m0", "answer"]
    assert _stored_count(conv.id) == 2


# ------------------------------------------------------------------ p6-skip


def test_identical_saves_write_once_and_a_title_change_writes(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _chat(root)
    flush_chat_saves()
    writes: list[str] = []
    real_save = repo.conv_save
    monkeypatch.setattr(repo, "conv_save", lambda p, doc, **k: writes.append(doc["id"]) or real_save(p, doc, **k))
    meta = load_conversation_meta(conv.id, root)
    for _ in range(10):
        save_conversation(meta, root, wait=True)
    assert len(writes) <= 1
    meta.title = "a new title"
    save_conversation(meta, root, wait=True)
    assert len(writes) <= 2 and writes[-1] == conv.id
    assert load_conversation_meta(conv.id, root).title == "a new title"


def test_the_key_ignores_the_timestamp_and_the_transcript() -> None:
    doc = {"id": "x", "title": "t", "updated": 1.0, "messages": [{"role": "user"}]}
    same = {**doc, "updated": 2.0, "messages": []}
    assert chat_writer.doc_key("p", doc) == chat_writer.doc_key("p", same)
    assert chat_writer.doc_key("p", doc) != chat_writer.doc_key("p", {**doc, "title": "u"})
    assert chat_writer.doc_key("p", doc) != chat_writer.doc_key("q", doc)


def test_a_message_save_is_never_skipped() -> None:
    writer = ChatWriter(lambda cid, item: None, connect=db.connect)
    writer.remember("x", "k")
    assert writer.unchanged("x", "k", None) is True
    assert writer.unchanged("x", "k", []) is False
    writer.forget("x")
    assert writer.unchanged("x", "k", None) is False


# ----------------------------------------------------- roster lanes (206e154d10)


def _member(mid: str, lane=None, at: float = 0.0) -> dict:
    return {"member_conv_id": mid, "write_allowed": lane, "lane_set_by": "lead" if at else "", "lane_set_at": at}


def _hub(root: str, members: list[dict]):
    hub = _chat(root, "group")
    hub.is_group = True
    hub.group_members = members
    save_conversation(hub, root, wait=True)
    return load_conversation_meta(hub.id, root)


def _lanes(hub_id: str, root: str) -> dict:
    stored = load_conversation_meta(hub_id, root)
    return {r["member_conv_id"]: r["write_allowed"] for r in stored.group_members}


def test_a_hub_saved_twice_with_different_lanes_keeps_both(tmp_path) -> None:
    root = str(tmp_path)
    hub = _hub(root, [_member("A"), _member("B")])
    first, second = load_conversation_meta(hub.id, root), load_conversation_meta(hub.id, root)
    first.group_members = [_member("A", ["a/**"], 10.0), _member("B")]
    save_conversation(first, root, wait=True)
    second.group_members = [_member("A"), _member("B", ["b/**"], 11.0)]
    save_conversation(second, root, wait=True)
    assert _lanes(hub.id, root) == {"A": ["a/**"], "B": ["b/**"]}


def test_an_empty_lane_survives_an_older_copy_a_save_and_a_reload(tmp_path) -> None:
    root = str(tmp_path)
    hub = _hub(root, [_member("A"), _member("B")])
    stale = load_conversation_meta(hub.id, root)
    fresh = load_conversation_meta(hub.id, root)
    fresh.group_members = [_member("A"), _member("B", [], 10.0)]
    save_conversation(fresh, root, wait=True)
    stale.group_members = stale.group_members + [_member("C")]  # invite C from the old copy
    save_conversation(stale, root, wait=True)
    flush_chat_saves()
    assert _lanes(hub.id, root) == {"A": None, "B": [], "C": None}


def test_a_merged_writer_batch_keeps_the_newest_roster(tmp_path) -> None:
    root = str(tmp_path)
    hub = _hub(root, [_member("A"), _member("B")])
    stale = load_conversation_meta(hub.id, root)
    fresh = load_conversation_meta(hub.id, root)
    fresh.group_members = [_member("A", ["a/**"], 10.0), _member("B")]
    save_conversation(fresh, root)  # no placement change: both saves are queued
    stale.group_members = stale.group_members + [_member("C")]
    save_conversation(stale, root)
    assert _lanes(hub.id, root) == {"A": ["a/**"], "B": None, "C": None}  # the queued copy
    flush_chat_saves()
    assert _lanes(hub.id, root) == {"A": ["a/**"], "B": None, "C": None}


# ------------------------------------------- host-owned chats (8a0312c333)


def _owned_by_host(monkeypatch, *conv_ids: str) -> None:
    from frontend.agent_host import turns

    monkeypatch.setattr(turns, "owns", lambda cid: object() if cid in conv_ids else None)


def test_a_host_owned_chat_saves_metadata_but_never_messages(tmp_path, monkeypatch, caplog) -> None:
    root = str(tmp_path)
    conv = _chat(root)
    conv.messages = _msgs(2)
    save_conversation(conv, root, wait=True)
    _owned_by_host(monkeypatch, conv.id)
    project_chats._host_refused.discard(conv.id)
    conv.title = "renamed while running"
    conv.messages = _msgs(5)
    with caplog.at_level("WARNING"):
        save_conversation(conv, root, wait=True)
        conv.messages = _msgs(6)
        save_conversation(conv, root, wait=True)
    assert _stored_count(conv.id) == 2  # the transcript stays the host's
    assert load_conversation_meta(conv.id, root).title == "renamed while running"
    assert [m["content"] for m in load_conversation(conv.id, root).messages] == ["m0", "m1"]
    assert sum("agent host" in r.getMessage() for r in caplog.records) == 1  # logged once per chat


def test_a_chat_not_owned_by_a_host_saves_messages_as_before(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _chat(root)
    _owned_by_host(monkeypatch, "someone-else")
    conv.messages = _msgs(3)
    save_conversation(conv, root, wait=True)
    assert _stored_count(conv.id) == 3


# ------------------------------------------------------------------- p6-wal


def test_a_big_batch_runs_a_passive_checkpoint() -> None:
    writer = ChatWriter(lambda cid, item: {"bytes": 2000 * chat_writer.PAGE_BYTES}, connect=db.connect)
    try:
        writer.submit("big", 1)
        writer.flush()
        assert writer.checkpoints["PASSIVE"] == 1
    finally:
        writer.close()


def test_a_small_batch_runs_none() -> None:
    writer = ChatWriter(lambda cid, item: {"bytes": 10 * chat_writer.PAGE_BYTES}, connect=db.connect)
    try:
        writer.submit("small", 1)
        writer.flush()
        assert writer.checkpoints["PASSIVE"] == 0
    finally:
        writer.close()


def test_30_idle_seconds_truncate_the_wal_once() -> None:
    now = [100.0]
    writer = ChatWriter(lambda cid, item: None, connect=db.connect, clock=lambda: now[0])
    try:
        assert writer.idle_checkpoint() is False  # nothing written yet
        writer.submit("a", 1)
        writer.flush()
        now[0] += 10
        assert writer.idle_checkpoint() is False
        now[0] += 21
        assert writer.idle_checkpoint() is True
        assert writer.checkpoints["TRUNCATE"] == 1
        assert writer.idle_checkpoint() is False  # once per quiet spell
        conn = db.connect()
        assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) == db.BUSY_TIMEOUT_MS
    finally:
        writer.close()


def test_the_writer_thread_is_named() -> None:
    writer = ChatWriter(lambda cid, item: None, connect=db.connect)
    try:
        writer.submit("a", 1)
        assert any(t.name == "chat-writer" for t in threading.enumerate())
    finally:
        writer.close()


# ------------------------------------------------------------------ checkpoints


def _turn(root: str):
    conv = _chat(root, "turn")
    conv.messages = [{"role": "user", "content": "go", "ts": 1}]
    save_conversation(conv, root, wait=True)
    return conv


def _stored_reply(conv_id: str) -> str:
    return repo.messages_get(conv_id)[-1]["content"]


def test_a_burst_of_200_checkpoints_is_at_most_2_writes(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _turn(root)
    calls: list[str] = []
    real = repo.upsert_run_message
    monkeypatch.setattr(repo, "upsert_run_message", lambda cid, rid, m, **kw: calls.append(m["content"]) or real(cid, rid, m, **kw))
    for i in range(200):
        project_chats.upsert_in_flight_assistant(conv, {"role": "assistant", "content": f"step {i}"}, run_id="r1", project_root=root)
    flush_chat_saves()
    print(f"burst 200 -> {len(calls)} write(s)")
    assert len(calls) <= 2 and calls[-1] == "step 199"
    assert _stored_reply(conv.id) == "step 199"
    assert conv._msg_rev == repo.conv_msg_rev(conv.id)  # the next save goes by delta


def test_a_checkpoint_queued_before_a_save_never_overwrites_it(tmp_path) -> None:
    root = str(tmp_path)
    conv = _turn(root)
    project_chats.upsert_in_flight_assistant(conv, {"role": "assistant", "content": "working"}, run_id="r1", project_root=root)
    msgs = list(conv.messages)
    msgs[-1] = {**msgs[-1], "content": "done"}
    conv.swap_messages(msgs, len(msgs) - 1)
    save_conversation(conv, root, wait=True)  # the turn end
    flush_chat_saves()
    assert _stored_reply(conv.id) == "done"


def test_the_queue_writes_in_submit_order() -> None:
    order: list[tuple[str, int]] = []
    writer = ChatWriter(
        lambda cid, item: order.append(("save", item)),
        write_checkpoint=lambda cid, item: order.append(("cp", item)),
        connect=db.connect,
        interval=5.0,
    )
    try:
        writer.submit("a", 1)
        writer.submit_checkpoint("a", "r", 2)
        writer.submit("a", 3)  # newer than the checkpoint: written after it
        writer.flush()
        assert order == [("cp", 2), ("save", 3)]
        order.clear()
        writer.submit("a", 4)
        writer.submit_checkpoint("a", "r", 5)  # newer than the save: written after it
        assert writer.flush("a") is True
        assert order == [("save", 4), ("cp", 5)]
    finally:
        writer.close()


def test_close_writes_pending_checkpoints() -> None:
    written: list[int] = []
    writer = ChatWriter(lambda cid, item: None, write_checkpoint=lambda cid, item: written.append(item),
                        connect=db.connect, interval=5.0)
    writer.submit_checkpoint("a", "r", 7)
    writer.close()
    assert written == [7]


def test_checkpoint_enqueue_is_cheap_while_the_writer_is_blocked(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _turn(root)
    started, release = threading.Event(), threading.Event()
    real = repo.upsert_run_message

    def blocked(*a, **kw):
        started.set()
        release.wait(10)
        return real(*a, **kw)

    monkeypatch.setattr(repo, "upsert_run_message", blocked)
    try:
        project_chats.upsert_in_flight_assistant(conv, {"role": "assistant", "content": "first"}, run_id="r1", project_root=root)
        project_chats._writer().flush(conv.id, timeout=0.0)  # write it now, so the writer blocks
        assert started.wait(5)
        ms: list[float] = []
        for i in range(200):
            t0 = time.perf_counter()
            project_chats.upsert_in_flight_assistant(conv, {"role": "assistant", "content": f"step {i}"}, run_id="r1", project_root=root)
            ms.append((time.perf_counter() - t0) * 1000)
    finally:
        release.set()
    flush_chat_saves()
    ms.sort()
    print(f"enqueue p50 {ms[100]:.3f} ms, p95 {ms[189]:.3f} ms")
    assert ms[189] < 1.0
    assert _stored_reply(conv.id) == "step 199"
