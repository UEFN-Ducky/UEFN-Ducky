"""Chat saves: cost stays flat as a chat grows, and a waiting save never sits out the batch timer."""

from __future__ import annotations

import time

import pytest

from backend.store.repos import chats as repo
from frontend.settings import PanelSettings
from frontend.ui_web import project_chats as pc
from frontend.ui_web.session_files import session_stats_from_messages


@pytest.fixture(autouse=True)
def _flushed():
    yield
    pc.flush_chat_saves()


def _transcript(n: int) -> list[dict]:
    out: list[dict] = []
    for i in range(n):
        if i % 2 == 0:
            out.append({"role": "user", "content": f"step {i}", "text": f"step {i}", "ts": i})
            continue
        blocks = [{"type": "tool_call", "id": f"t{i}_{j}", "name": "workspace_write_file" if j == 0 else "workspace_read_file",
                   "arguments": {"path": f"Content/f{i % 40}.verse"}, "status": "success", "result": {"data": "x" * 600}}
                  for j in range(4)]
        out.append({"role": "assistant", "content": "Done.", "blocks": blocks, "run_id": f"r{i}", "ts": i})
    return out


def _chat(root: str, n: int):
    conv = pc.create_conversation(PanelSettings.load(), "", title="big", project_root=root)
    conv = pc.load_conversation(conv.id, root)
    conv.messages = _transcript(n)
    pc.save_conversation(conv, root, wait=True)
    return pc.load_conversation(conv.id, root)


def _p95(values: list[float]) -> float:
    return sorted(values)[int(len(values) * 0.95) - 1]


def test_append_save_on_a_5000_message_chat_stays_cheap(tmp_path) -> None:
    root = str(tmp_path)
    conv = _chat(root, 5000)
    pc._save_conversation_rows(conv, root)  # warm: this process has seen the rows once
    ms: list[float] = []
    for k in range(40):
        conv.messages.append({"role": "assistant", "content": f"reply {k}", "ts": k, "blocks": [
            {"type": "tool_call", "id": f"n{k}", "name": "workspace_edit_file", "arguments": {"path": f"Content/new{k}.verse"},
             "status": "success", "result": {}}]})
        started = time.perf_counter()
        pc._save_conversation_rows(conv, root)
        ms.append((time.perf_counter() - started) * 1000)
    print(f"conv_save append on 5,000 msgs: p50 {sorted(ms)[20]:.1f} ms, p95 {_p95(ms):.1f} ms")
    assert _p95(ms) < 30.0  # HEAD p95 16.9 ms here; a guard against an O(n) step coming back
    # The counters no longer rescan the chat: rows seen before cost a dict lookup.
    hashes = _cached_hashes(conv.id)
    assert len(hashes) == len(conv.messages)
    started = time.perf_counter()
    pc._stats_by_hash(conv.id, conv.messages, hashes)
    assert (time.perf_counter() - started) * 1000 < 5.0
    stored = repo.conv_get(conv.id, with_messages=False)
    expected = session_stats_from_messages(conv.messages)
    assert (stored["tool_call_count"], stored["file_count"]) == (expected["tool_call_count"], expected["file_count"])


def _cached_hashes(conv_id: str) -> list[str]:
    rows = repo._saved_rows(conv_id)
    return [rows[s][0] for s in sorted(rows)]


def _db_hashes(conv_id: str) -> list[str]:
    from backend.store import db

    return [str(r[0]) for r in db.connect().execute("SELECT hash FROM messages WHERE conv_id=? ORDER BY seq", (conv_id,))]


def _written_elsewhere(conv, seq: int, message: dict) -> None:
    """Another process (an agent host) rewrites one row: this process's cache stays as it was."""
    with repo._saved_lock:
        mine = repo._saved[conv.id]
    other = list(conv.messages)
    other[seq] = message
    doc = conv.to_dict()
    repo.conv_save(repo.conv_project_id(conv.id), doc, messages=other)
    with repo._saved_lock:
        repo._saved[conv.id] = mine


def test_a_row_another_writer_changed_is_compared_again(tmp_path, monkeypatch) -> None:
    root = str(tmp_path)
    conv = _chat(root, 5000)
    pc._save_conversation_rows(conv, root)
    theirs = {"role": "assistant", "content": "the host's redo", "blocks": [], "ts": 7}
    _written_elsewhere(conv, 7, theirs)
    serialised: list = []
    real = repo._shell
    monkeypatch.setattr(repo, "_shell", lambda m: serialised.append(m) or real(m))
    # This window got the host's row, and adds one: the changed row's hash no longer
    # matches the cache, so that row is compared again and the host's row survives.
    conv.messages[7] = dict(theirs)
    conv.messages.append({"role": "user", "content": "next", "ts": 5000})
    pc._save_conversation_rows(conv, root)
    assert [m.get("content") for m in serialised] == ["the host's redo", "next"]
    assert repo.messages_get(conv.id)[7] == theirs
    assert _cached_hashes(conv.id) == _db_hashes(conv.id)
    # A row this window never saw is never vouched for: its own copy of row 3, equal to
    # what it remembers, is written back over the host's (as a save always did), and
    # the cache matches the database afterwards.
    _written_elsewhere(conv, 3, {"role": "assistant", "content": "host again", "blocks": [], "ts": 3})
    pc._save_conversation_rows(conv, root)
    assert repo.messages_get(conv.id) == conv.messages
    assert _cached_hashes(conv.id) == _db_hashes(conv.id)


def test_counts_follow_rows_another_writer_changed(tmp_path) -> None:
    root = str(tmp_path)
    conv = _chat(root, 20)
    pc._save_conversation_rows(conv, root)
    # Another writer (an agent host) rewrites a row directly; this process's copy loads it.
    fresh = pc.load_conversation(conv.id, root)
    fresh.messages[1] = {"role": "assistant", "content": "redo", "blocks": [], "ts": 1}
    repo.conv_save(repo.conv_project_id(conv.id), fresh.to_dict(), messages=fresh.messages)
    again = pc.load_conversation(conv.id, root)
    pc._save_conversation_rows(again, root)
    stored = repo.conv_get(conv.id, with_messages=False)
    expected = session_stats_from_messages(again.messages)
    assert (stored["tool_call_count"], stored["file_count"]) == (expected["tool_call_count"], expected["file_count"])


def test_a_waiting_save_does_not_sit_out_the_batch_timer(tmp_path) -> None:
    root = str(tmp_path)
    conv = _chat(root, 200)
    ms: list[float] = []
    for k in range(20):
        conv.title = f"renamed {k}"  # a rename waits until it is written (other processes read it)
        started = time.perf_counter()
        pc.save_conversation(conv, root)
        ms.append((time.perf_counter() - started) * 1000)
    print(f"save_conversation rename (wait=True) caller: p95 {_p95(ms):.1f} ms")
    assert _p95(ms) < 50.0  # was p50 124 ms: the 100 ms writer interval plus the commit
    assert repo.conv_get(conv.id, with_messages=False)["title"] == "renamed 19"
