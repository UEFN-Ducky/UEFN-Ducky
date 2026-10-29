"""Delta saves: a Conversation knows the lowest message index changed since its last save."""

from __future__ import annotations

import re
from pathlib import Path

import frontend.chat_store as chat_store
from frontend.chat_store import Conversation


def test_reassigning_messages_marks_everything_dirty() -> None:
    conv = Conversation(id="c")
    conv._dirty_from = None
    conv.messages = [{"role": "user", "content": "hi"}]
    assert conv._dirty_from == 0
    assert conv.messages == [{"role": "user", "content": "hi"}]


def test_append_message_marks_new_index(monkeypatch) -> None:
    monkeypatch.setattr(chat_store, "save_conversation", lambda conv: None)
    conv = Conversation(id="c", messages=[{"role": "user", "content": str(i)} for i in range(3)])
    conv._dirty_from = None
    chat_store.append_message(conv, {"role": "assistant", "content": "x"})
    assert conv._dirty_from == 3


def test_mark_dirty_keeps_lowest() -> None:
    conv = Conversation(id="c")
    conv._dirty_from = None
    conv.mark_dirty(5)
    conv.mark_dirty(9)
    assert conv._dirty_from == 5
    conv.mark_dirty(2)
    assert conv._dirty_from == 2


def test_round_trip_leaves_no_trace() -> None:
    conv = Conversation(id="c", messages=[{"role": "user", "content": "hi"}])
    conv._msg_rev = 7
    conv.mark_dirty(0)
    d = conv.to_dict()
    assert "_dirty_from" not in d and "_msg_rev" not in d and "_messages" not in d
    back = Conversation.from_dict(d)
    assert back._msg_rev is None
    assert back.messages == conv.messages
    assert back.to_dict() == d


def test_swap_and_take_keep_save_state() -> None:
    conv = Conversation(id="c", messages=[{"n": 0}, {"n": 1}])
    conv._dirty_from = None
    conv.swap_messages([{"n": 0}, {"n": 2}], 1)
    assert conv._dirty_from == 1 and conv.messages[1] == {"n": 2}
    conv.swap_messages([{"n": 0}, {"n": 2}], None)
    assert conv._dirty_from == 1
    fresh = Conversation(id="c", messages=[{"n": 9}])
    fresh._dirty_from, fresh._msg_rev = None, 42
    conv.take_messages(fresh)
    assert (conv._dirty_from, conv._msg_rev, conv.messages) == (None, 42, [{"n": 9}])
    assert conv.messages is not fresh.messages


# A list mutation on a Conversation's transcript that skips mark_dirty is saved late
# (turn end). Each allowed line here is followed by mark_dirty / swap_messages.
_MUTATION = re.compile(
    r"\.messages\.(append|insert|pop|extend|remove|clear)\(|del [\w.]*messages\[|\.messages\[[^\]]*\]\s*=[^=]|\.messages\s*\+="
)
_ALLOWED = {
    ("chat_store.py", "conv.messages.append(message)"),
    ("ui_web/project_chats.py", "conv.messages.append(message)"),
}


def test_mutation_sites_are_marked() -> None:
    root = Path(chat_store.__file__).resolve().parent
    found = set()
    for path in root.rglob("*.py"):
        if path.name.startswith("test_") or path.name == "conftest.py":
            continue
        rel = path.relative_to(root).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            if _MUTATION.search(line) and not line.lstrip().startswith("#"):
                found.add((rel, line.strip()))
    assert found - _ALLOWED == set(), "mark_dirty the new message mutation, then allow it here"


# --------------------------------------------------------------- save_conversation (row store)


def _big_chat(root: str, n: int, tag: str = "m"):
    from frontend.settings import PanelSettings
    from frontend.ui_web import project_chats as pc

    conv = pc.create_conversation(PanelSettings.load(), "", title="big", project_root=root)
    conv = pc.load_conversation(conv.id, root)
    conv.messages = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{tag}{i}", "ts": i,
         **({} if i % 2 == 0 else {"blocks": [{"type": "tool_call", "id": f"t{i}", "name": "workspace_read_file",
                                               "arguments": {"path": f"Content/f{i % 40}.verse"}, "status": "success",
                                               "result": {"data": "x" * 400}}]})}
        for i in range(n)
    ]
    pc.save_conversation(conv, root, wait=True)
    return pc.load_conversation(conv.id, root)


def test_closed_chats_leave_no_message_list_alive(tmp_path) -> None:
    import gc

    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    for j in range(20):
        conv = _big_chat(root, 2000, tag=f"leak{j}-")
        pc.upsert_in_flight_assistant(conv, {"role": "assistant", "content": "live"}, run_id="live", project_root=root)
        pc.save_conversation(conv, root, wait=True, full=True)  # turn end
        conv = pc.load_conversation(conv.id, root)
        pc.save_conversation(conv, root, wait=True)
    del conv  # every chat closed
    gc.collect()
    alive = sum(
        1 for o in gc.get_objects()
        if type(o) is list and len(o) >= 2000 and isinstance(o[0], dict)
        and str(o[0].get("content", "")).startswith("leak")
    )
    print(f"{alive} of 20 message lists alive after close")
    assert alive == 0


def _record_saves(monkeypatch) -> list[dict]:
    import time

    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    calls: list[dict] = []
    real_save, real_rows = repo.conv_save, pc._save_conversation_rows

    def conv_save(project_id, doc, *, messages, **kw):
        result = real_save(project_id, doc, messages=messages, **kw)
        calls.append({**kw, "path": result.get("path"), "msg_rev": result.get("msg_rev"),
                      "theirs": result.get("theirs")})
        return result

    def rows(conv, project_root, origin=None):
        started = time.perf_counter()
        try:
            return real_rows(conv, project_root, origin)
        finally:
            calls[-1]["ms"] = (time.perf_counter() - started) * 1000

    monkeypatch.setattr(repo, "conv_save", conv_save)
    monkeypatch.setattr(pc, "_save_conversation_rows", rows)
    return calls


def test_appends_to_a_5000_message_chat_save_by_delta(tmp_path, monkeypatch) -> None:
    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 5000)
    assert conv._dirty_from is None and conv._msg_rev == repo.conv_msg_rev(conv.id)
    calls = _record_saves(monkeypatch)
    for k in range(40):
        conv.messages.append({"role": "assistant", "content": f"reply {k}", "ts": 5000 + k})
        conv.mark_dirty(len(conv.messages) - 1)
        pc.save_conversation(conv, root, touch_updated=False, wait=True)
    assert len(calls) == 40
    assert all(c["path"] == "delta" and c["dirty_from"] >= 4998 for c in calls), calls[:3]
    ms = sorted(c["ms"] for c in calls)
    print(f"delta save on 5,000 msgs: p50 {ms[20]:.1f} ms, p95 {ms[37]:.1f} ms")
    assert ms[37] < 5.0
    assert conv._msg_rev == repo.conv_msg_rev(conv.id)
    assert repo.messages_get(conv.id) == conv.messages


def test_reply_streamed_in_place_is_saved_without_marking(tmp_path, monkeypatch) -> None:
    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 50)
    calls = _record_saves(monkeypatch)
    conv.messages[-1]["content"] = "streamed further"  # the in-flight reply, after the last user
    pc.save_conversation(conv, root, wait=True)
    assert calls[-1]["path"] == "delta"
    assert repo.messages_get(conv.id)[-1]["content"] == "streamed further"


def test_unmarked_old_edit_waits_for_the_turn_end_full_save(tmp_path, monkeypatch) -> None:
    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 50)
    calls = _record_saves(monkeypatch)
    conv.messages[10]["content"] = "edited, never marked"
    pc.save_conversation(conv, root, wait=True)
    assert calls[-1]["path"] == "delta"
    assert repo.messages_get(conv.id)[10]["content"] == "m10"  # skipped: below the delta
    pc.save_conversation(conv, root, wait=True, full=True)
    assert calls[-1]["dirty_from"] == 0
    assert repo.messages_get(conv.id)[10]["content"] == "edited, never marked"


def test_upsert_checkpoints_count_the_prefix_once(tmp_path, monkeypatch) -> None:
    import time

    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc
    from frontend.ui_web import session_files

    root = str(tmp_path)
    conv = _big_chat(root, 5000)
    conv.messages.append({"role": "user", "content": "go", "ts": 5000})
    conv.mark_dirty(len(conv.messages) - 1)
    pc.save_conversation(conv, root, wait=True)
    counted: list[int] = []
    real_stats = session_files.message_stats
    monkeypatch.setattr(session_files, "message_stats", lambda m: counted.append(1) or real_stats(m))
    sent: list[tuple[dict, dict]] = []
    real_upsert = repo.upsert_run_message

    def today(msgs: list) -> dict:  # _stats_for, without touching the counter
        count, paths = 0, set()
        for m in msgs:
            c, p = real_stats(m)
            count, paths = count + c, paths | p
        return {"tool_call_count": count, "file_count": len(paths)}

    def upsert(conv_id, run_id, message, *, updated, stats=None):
        msgs = list(conv.messages)
        idx = pc._run_row_index(msgs, run_id)
        if idx >= 0:
            msgs[idx] = message
        else:
            msgs.append(message)
        sent.append((stats, today(msgs)))
        return real_upsert(conv_id, run_id, message, updated=updated, stats=stats)

    monkeypatch.setattr(repo, "upsert_run_message", upsert)
    ms: list[float] = []
    real_cp = pc._checkpoint_stats

    def timed(*a):
        started = time.perf_counter()
        try:
            return real_cp(*a)
        finally:
            ms.append((time.perf_counter() - started) * 1000)

    monkeypatch.setattr(pc, "_checkpoint_stats", timed)
    blocks: list[dict] = []
    for k in range(50):
        blocks.append({"type": "tool_call", "id": f"live{k}", "name": "workspace_write_file", "status": "success",
                       "arguments": {"path": f"Content/f{k % 45}.verse"}, "result": {}})
        before = len(counted)
        pc.upsert_in_flight_assistant(conv, {"role": "assistant", "content": f"step {k}", "blocks": list(blocks)},
                                      run_id="live", project_root=root)
        pc.flush_chat_saves(conv.id)  # the checkpoint is written (and counted) on the writer
        if k:
            assert len(counted) - before == 1  # only the in-flight row; the prefix once, at k == 0
    assert len(counted) < 5000 + 50 + 5
    assert all(got == want for got, want in sent), next((g, w) for g, w in sent if g != w)
    ms.sort()
    print(f"checkpoint stats on 5,000 msgs: p50 {ms[25]:.2f} ms, p95 {ms[47]:.2f} ms")
    assert ms[47] < 3.0


def test_another_writer_turns_the_next_save_into_a_merge(tmp_path, monkeypatch) -> None:
    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 20)
    other = pc.load_conversation(conv.id, root)
    other.messages.append({"role": "user", "content": "from elsewhere", "ts": 99})
    other.mark_dirty(len(other.messages) - 1)
    pc.save_conversation(other, root, wait=True)
    calls = _record_saves(monkeypatch)
    conv.messages[-1]["content"] = "mine"
    pc.save_conversation(conv, root, wait=True)
    # Its rev is stale: only its own change is written, and the other writer's row survives.
    assert calls[-1]["path"] == "merge"
    stored = repo.messages_get(conv.id)
    assert stored[19]["content"] == "mine" and stored[20]["content"] == "from elsewhere"
    assert conv._msg_rev != repo.conv_msg_rev(conv.id)  # it is not the store's copy


def test_checkpoint_after_a_foreign_write_refreshes_only_the_tail(tmp_path, monkeypatch) -> None:
    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 5000)  # ends with an assistant at 4999 after the user at 4998
    pid = repo.conv_project_id(conv.id)
    # Another writer rewrites an old row and adds a steer after the last user message.
    other = pc.load_conversation(conv.id, root)
    other.messages[7] = {"role": "assistant", "content": "the host's redo", "ts": 7}
    other.messages.append({"role": "assistant", "content": "steer reply", "ts": 5000})
    repo.conv_save(pid, other.to_dict(), messages=other.messages)
    loads: list[str] = []
    monkeypatch.setattr(pc, "load_conversation", lambda cid, root=None: loads.append(cid))
    since: list[int] = []
    real_since = repo.messages_since
    monkeypatch.setattr(repo, "messages_since", lambda cid, seq, **kw: since.append(seq) or real_since(cid, seq, **kw))
    # The writer finds the store moved; the next checkpoint takes their tail first.
    for _ in range(2):
        pc.upsert_in_flight_assistant(conv, {"role": "assistant", "content": "live"}, run_id="live", project_root=root)
        pc.flush_chat_saves(conv.id)
    assert loads == []  # no whole-chat reload
    assert since and min(since) >= 4998  # only the tail was read
    stored = repo.messages_get(conv.id)
    assert stored[7]["content"] == "the host's redo"  # the foreign row survives
    assert [m["content"] for m in conv.messages[4998:]] == [m["content"] for m in stored[4998:]]
    assert conv._msg_rev == repo.conv_msg_rev(conv.id)
    # The turn end saves by delta from the copy's last user message.
    calls = _record_saves(monkeypatch)
    pc.save_conversation(conv, root, wait=True)
    assert calls[-1]["path"] == "delta" and calls[-1]["dirty_from"] >= 4998
    assert repo.messages_get(conv.id)[7]["content"] == "the host's redo"


def test_another_writers_rewrite_of_an_old_row_survives_full_saves(tmp_path, monkeypatch) -> None:
    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 50)
    assert conv._base_hashes is not None and len(conv._base_hashes) == 50
    other = pc.load_conversation(conv.id, root)
    other.messages[11] = {"role": "assistant", "content": "rewritten elsewhere", "ts": 11}
    repo.conv_save(repo.conv_project_id(conv.id), other.to_dict(), messages=other.messages)
    calls = _record_saves(monkeypatch)
    for k in range(2):  # the second save builds on the base the first left
        conv.messages.append({"role": "user", "content": f"more {k}", "ts": 50 + k})
        conv.mark_dirty(len(conv.messages) - 1)
        pc.save_conversation(conv, root, wait=True, full=True)
        assert calls[-1]["base_hashes"] is not None and calls[-1]["theirs"] == [11], calls[-1]["path"]
        assert repo.messages_get(conv.id)[11]["content"] == "rewritten elsewhere"
    # This copy edits row 11 itself: its edit wins.
    conv.messages[11] = {"role": "assistant", "content": "mine now", "ts": 11}
    conv.mark_dirty(11)
    pc.save_conversation(conv, root, wait=True)
    assert calls[-1]["theirs"] == []
    assert repo.messages_get(conv.id)[11]["content"] == "mine now"
    assert repo.messages_get(conv.id) == conv.messages


def test_old_row_edit_saves_that_row_and_the_tail(tmp_path, monkeypatch) -> None:
    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 5000)
    calls = _record_saves(monkeypatch)
    conv.messages[11] = {"role": "assistant", "content": "edited", "ts": 11}
    conv.mark_dirty(11)
    conv.messages.append({"role": "user", "content": "next", "ts": 5000})
    conv.mark_dirty(len(conv.messages) - 1)
    assert (conv._dirty_rows, conv._dirty_from) == ({11}, 5000)
    mine = {id(m) for m in conv.messages}
    encoded = [0]
    real_dumps = repo._dumps
    monkeypatch.setattr(repo, "_dumps", lambda v: encoded.__setitem__(0, encoded[0] + (id(v) in mine)) or real_dumps(v))
    pc.save_conversation(conv, root, wait=True)
    assert calls[-1]["path"] == "delta" and calls[-1]["dirty_rows"] == [11] and calls[-1]["dirty_from"] == 5000
    assert encoded[0] == 2, encoded[0]  # row 11 and the new row, nothing between
    assert (conv._dirty_rows, conv._dirty_from) == (set(), None)
    assert repo.messages_get(conv.id) == conv.messages
    # The written row's hash is the copy's base now: saving again leaves it alone.
    pc.save_conversation(conv, root, wait=True, full=True)
    assert calls[-1]["theirs"] == [] and repo.messages_get(conv.id)[11]["content"] == "edited"


def test_replaced_queued_save_merges_its_rows(tmp_path, monkeypatch) -> None:
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 50)
    queued = pc._from_queue(conv, with_messages=True)
    queued._dirty_rows, queued._dirty_from = {11}, 49
    monkeypatch.setattr(pc, "_queued", lambda cid: queued)
    calls = _record_saves(monkeypatch)
    conv.messages[40] = {"role": "assistant", "content": "edited", "ts": 40}
    conv.mark_dirty(40)
    pc.save_conversation(conv, root, wait=True)
    assert calls[-1]["dirty_rows"] == [11, 40] and calls[-1]["dirty_from"] == 48


def test_reassign_sends_a_whole_save(tmp_path, monkeypatch) -> None:
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 50)
    calls = _record_saves(monkeypatch)
    conv.mark_dirty(11)
    conv.messages = list(conv.messages)
    pc.save_conversation(conv, root, wait=True)
    assert calls[-1]["dirty_from"] == 0 and calls[-1]["dirty_rows"] == []


def test_reassign_clears_the_base_and_to_dict_never_has_it() -> None:
    conv = Conversation(id="c", messages=[{"n": 0}])
    conv._base_hashes = ["h0"]
    assert "_base_hashes" not in conv.to_dict()
    conv.swap_messages([{"n": 1}], 0)
    assert conv._base_hashes == ["h0"]
    conv.messages = [{"n": 2}]
    assert conv._base_hashes is None


def test_append_message_refreshes_the_tail_not_the_chat(tmp_path, monkeypatch) -> None:
    from backend.store.repos import chats as repo
    from frontend.ui_web import project_chats as pc

    root = str(tmp_path)
    conv = _big_chat(root, 2000)
    other = pc.load_conversation(conv.id, root)
    other.messages.append({"role": "assistant", "content": "late reply", "ts": 2000})
    other.mark_dirty(len(other.messages) - 1)
    pc.save_conversation(other, root, wait=True)
    loads: list[str] = []
    monkeypatch.setattr(pc, "load_conversation", lambda cid, root=None: loads.append(cid))
    pc.append_message(conv, {"role": "user", "content": "next", "ts": 2001}, root)
    assert loads == []
    stored = [m["content"] for m in repo.messages_get(conv.id)[-3:]]
    assert stored == ["m1999", "late reply", "next"]
    assert [m["content"] for m in conv.messages[-3:]] == stored
