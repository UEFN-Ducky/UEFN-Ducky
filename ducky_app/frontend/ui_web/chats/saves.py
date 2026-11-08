"""Chat saves: the chat-writer queue, delta row saves, run-row checkpoints and the
guards that keep a stub or an older copy from overwriting a stored transcript."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import Counter, OrderedDict
from concurrent.futures import Future
from copy import copy as _shallow
from operator import is_
from typing import Any

from frontend.atomic_json import write_json_atomic
from frontend.chat_store import Conversation
from frontend.ui_web.chats import conversations as _convs
from frontend.ui_web.chats import lanes as _lanes
from frontend.ui_web.chats import store as _store

_writer_guard = threading.Lock()
_host_refused: set[str] = set()  # chats whose refused message save was logged once
_WRITER: Any = None  # the chat-writer (row store only), made on the first save


def _writer():
    global _WRITER
    with _writer_guard:
        if _WRITER is None:
            from backend.store import db
            from frontend.ui_web.chat_writer import ChatWriter

            _WRITER = ChatWriter(
                _write_queued, write_checkpoint=_write_run_row, connect=db.connect, root=db.app_root
            )
        return _WRITER


def flush_chat_saves(conv_id: str | None = None, timeout: float = 30.0) -> bool:
    """Write queued chat saves now (one chat's or all); True once written. Cheap when
    nothing waits. Called before a direct row write and before /__panel_rpc answers."""
    writer = _WRITER
    return True if writer is None else writer.flush(conv_id, timeout)


def _forget_saved(conv_id: str | None = None) -> None:
    if _WRITER is not None:
        _WRITER.forget(conv_id)


def _queued(conv_id: str) -> Conversation | None:
    """This chat's copy still waiting for the writer, if any."""
    writer = _WRITER
    item = writer.peek(conv_id) if writer is not None else None
    return item[0] if item else None


def _write_queued(conv_id: str, item: tuple[Any, ...]) -> dict[str, Any]:
    """*item* is ``(snapshot, project_root, origin)``: origin is the caller's object, told
    the chat's ``msg_rev`` after the write so its next save goes by delta."""
    conv, project_root, origin = (*item, None)[:3]
    if conv._dirty_from == 0:
        with _row_stats_lock:  # turn end: a checkpoint written just before refilled it
            _PREFIX_STATS.pop(conv_id, None)
    try:
        return _save_conversation_rows(conv, project_root, origin)
    except Exception:
        if origin is not None:
            origin.mark_all_dirty()  # this snapshot's changes never landed: the next save is whole
        raise


def _host_owned(conv_id: str) -> bool:
    """An agent host runs this chat's turn now: the host writes its messages, the app
    only its metadata, so the two never race on the same rows."""
    try:
        from frontend.agent_host.turns import owns
    except Exception:
        return False
    return owns(conv_id) is not None


def _refuse_host_messages(conv_id: str) -> None:
    if conv_id not in _host_refused:
        _host_refused.add(conv_id)
        logging.getLogger(__name__).warning(
            "chat %s runs in an agent host: saved its metadata, not its messages", conv_id
        )


def _is_assistant(message: Any) -> bool:
    return isinstance(message, dict) and message.get("role") == "assistant"


def _keeps_stored_rows(incoming: list[Any], stored: list[Any]) -> bool:
    """The stub guard: an empty copy, or one that lost the stored assistant rows, never
    replaces a transcript."""
    if not incoming:
        return True
    return not any(_is_assistant(m) for m in incoming) and any(_is_assistant(m) for m in stored)


def _from_queue(conv: Conversation, *, with_messages: bool) -> Conversation:
    """A reader's own copy of a queued save, with the transcript the writer will keep."""
    out = Conversation.from_dict({**conv.to_dict(), "messages": []})
    if not with_messages:
        return out
    mine = list(conv.messages or [])
    if not mine or not any(_is_assistant(m) for m in mine):
        stored = _store._repo().messages_get(conv.id)  # a stub save never blanks the stored rows
        if stored and (not mine or any(_is_assistant(m) for m in stored)):
            out.messages = stored  # rev unknown: its next save is whole
            return out
    # The queued copy's transcript with its pending changes and base rev, so a save of
    # this copy (it replaces the queued one) still goes by delta.
    out.swap_messages(mine, None)
    out._dirty_from, out._msg_rev, out._base_hashes = conv._dirty_from, conv._msg_rev, conv._base_hashes
    out._dirty_rows = set(conv._dirty_rows)
    return out


def _stats_for(messages: list[Any]) -> dict[str, int]:
    from frontend.ui_web.session_files import session_stats_from_messages

    return session_stats_from_messages(messages)


# conv id -> (msg_rev after the save, row hash per seq, {row hash: (tool calls, file paths)},
# running totals (tool calls, {path: rows naming it}), built by the first delta save).
# A save scanned every message of the chat for these counts (55% of a 5,000-message
# save); now only rows it has not seen. Keyed on the row hash, so rows another writer (an
# agent host) changed are rescanned. A delta save reuses the hashes below its start only
# when the rev says nobody wrote since.
_ROW_STATS: OrderedDict[str, tuple[int | None, list[str], dict[str, tuple[int, frozenset[str]]], tuple[int, Counter[str]] | None]] = OrderedDict()
_ROW_STATS_CHATS = 32
_row_stats_lock = threading.Lock()


def _delta_stats(
    conv_id: str,
    messages: list[Any],
    entry: tuple[Any, ...],
    hashes: list[str],
    hashes_from: int,
    rev_after: int | None,
    dirty_hashes: dict[int, str],
) -> dict[str, int]:
    """The counts after a delta save, in O(rows it wrote): take the replaced rows out of
    the running totals and add the new ones."""
    from frontend.ui_web.session_files import message_stats

    _, stored, rows, totals = entry
    if totals is None:  # the first delta since a whole save
        totals = sum(rows[h][0] for h in stored), Counter(p for h in stored for p in rows[h][1])
    count, path_rows = totals
    listed = {seq: h for seq, h in dirty_hashes.items() if seq < hashes_from}
    for old in [*(stored[seq] for seq in listed), *stored[hashes_from:]]:
        count -= rows[old][0]
        for path in rows[old][1]:
            path_rows[path] -= 1
            if not path_rows[path]:
                del path_rows[path]
    del stored[hashes_from:]
    stored.extend(hashes)
    for seq, h in [*listed.items(), *enumerate(hashes, hashes_from)]:
        stored[seq] = h
        # ponytail: replaced hashes stay in rows until the turn-end full save rebuilds it.
        stat = rows[h] = rows.get(h) or message_stats(messages[seq])
        count += stat[0]
        path_rows.update(stat[1])
    with _row_stats_lock:
        _ROW_STATS[conv_id] = (rev_after, stored, rows, (count, path_rows))
        while len(_ROW_STATS) > _ROW_STATS_CHATS:
            _ROW_STATS.popitem(last=False)
    return {"tool_call_count": count, "file_count": len(path_rows)}


def _stats_by_hash(
    conv_id: str,
    messages: list[Any],
    hashes: list[str],
    hashes_from: int = 0,
    rev_before: int | None = None,
    rev_after: int | None = None,
    dirty_hashes: dict[int, str] | None = None,
) -> dict[str, int] | None:
    """The chat's counts; None when a delta save can't tell (keep the stored counts, the
    turn-end full save recounts). *dirty_hashes* are rows below *hashes_from* the save
    wrote too ({seq: hash})."""
    from frontend.ui_web.session_files import message_stats

    with _row_stats_lock:
        entry = _ROW_STATS.pop(conv_id, None)
    known = entry[2] if entry else {}
    if hashes_from:
        if entry is None or entry[0] is None or entry[0] != rev_before or len(entry[1]) < hashes_from:
            return None
        if hashes_from + len(hashes) == len(messages):
            return _delta_stats(conv_id, messages, entry, hashes, hashes_from, rev_after, dirty_hashes or {})
        return _stats_for(messages)
    if len(hashes) != len(messages):
        return _stats_for(messages)
    rows: dict[str, tuple[int, frozenset[str]]] = {}
    count = 0
    paths: set[str] = set()
    for message, h in zip(messages, hashes):
        stat = rows.get(h) or known.get(h)
        if stat is None:
            stat = message_stats(message)
        rows[h] = stat
        count += stat[0]
        paths |= stat[1]
    with _row_stats_lock:
        _ROW_STATS[conv_id] = (rev_after, list(hashes), rows, None)
        while len(_ROW_STATS) > _ROW_STATS_CHATS:
            _ROW_STATS.popitem(last=False)
    return {"tool_call_count": count, "file_count": len(paths)}


def _reload_if_stale(conv: Conversation) -> None:
    """A copy without the assistant rows another writer stored is given them, so its later
    saves build on them. A metadata stub (no messages) is never reloaded."""
    incoming = conv.messages or []
    if not incoming or any(_is_assistant(m) for m in incoming):
        return
    count, has_assistant = _store._repo().conv_message_summary(conv.id)
    if count and has_assistant and count != len(incoming):
        msgs, rev, hashes = _store._repo().messages_get_rev(conv.id, with_hashes=True)
        conv.swap_messages(msgs, None)
        conv._dirty_from, conv._msg_rev, conv._base_hashes = None, rev, hashes
        conv._dirty_rows = set()


def _delta_start(messages: list[Any], dirty_from: int | None) -> int:
    """Where a save's tail starts: *dirty_from*, or the in-flight tail (from the last
    user message, else the last message) if lower, so a reply streamed in place needs no
    marking. Marked rows below it go as ``dirty_rows``."""
    i = len(messages) - 1
    while i >= 0 and not (isinstance(messages[i], dict) and messages[i].get("role") == "user"):
        i -= 1
    tail = i if i >= 0 else max(len(messages) - 1, 0)
    return tail if dirty_from is None else min(dirty_from, tail)


def save_conversation(
    conv: Conversation,
    project_root: str | None = None,
    *,
    touch_updated: bool = True,
    wait: bool = False,
    full: bool = False,
) -> Future | None:
    """Row store: the save is queued for the chat-writer thread and its future returned.
    ``wait=True`` (and any save that creates, moves, renames or re-settings a chat) returns
    once it is written, so other processes see it at once.

    Only messages from ``conv``'s lowest changed index (or its in-flight tail) are written.
    ``full=True`` re-checks every message: the turn-end safety net for an old message
    edited in place without :meth:`Conversation.mark_dirty`."""
    with _store._conversation_lock(conv.id):
        if full:
            conv.mark_all_dirty()
            with _row_stats_lock:  # turn end: the checkpoint prefix holds the chat's rows
                _PREFIX_STATS.pop(conv.id, None)
        # A self-rename is made through the bridge while this runner owns an older
        # conversation object. Never save its placeholder over the chosen name.
        if _convs.is_placeholder_title(conv.title):
            stored = _convs.load_conversation_meta(conv.id, project_root)
            if stored is not None and not _convs.is_placeholder_title(stored.title):
                conv.title = stored.title
        if touch_updated:
            conv.updated = time.time()
        if _store._use_db():
            wait = wait or _convs._placement_changed(conv)
            queued = _queued(conv.id)
            if queued is None:
                newer = _store._repo().conv_if_settings_newer(conv.id, conv.settings_edited)
            else:
                newer_edit = float(queued.settings_edited or 0.0) > float(conv.settings_edited or 0.0)
                newer = queued.to_dict() if newer_edit else None
            _convs._keep_newer_settings(conv, newer)
            held = getattr(conv, _convs._HELD_ATTR, None)
            if held or getattr(conv, "is_group", False):
                stored_doc = (
                    queued.to_dict() if queued is not None else _store._repo().conv_get(conv.id, with_messages=False)
                )
                if held:
                    _convs._keep_newer_placement(conv, stored_doc)
                _lanes._keep_stored_lanes(conv, stored_doc)
            if queued is None:
                _reload_if_stale(conv)
            snapshot = _shallow(conv)
            # ponytail: the whole list is copied (pointers only, ~20 us at 5,000) so loads
            # can read the queued copy; copy just the tail if that ever shows in a profile.
            snapshot.swap_messages(list(conv.messages or []), None)
            snapshot._dirty_from = _delta_start(snapshot.messages, conv._dirty_from)
            rows = set(conv._dirty_rows)
            if queued is not None:
                # Latest wins in the queue: what the replaced copy changed is written too.
                snapshot._dirty_from = min(snapshot._dirty_from, queued._dirty_from or 0)
                rows |= queued._dirty_rows
                if queued._msg_rev != snapshot._msg_rev:
                    snapshot._msg_rev = None  # two bases: write it whole
            snapshot._dirty_rows = {r for r in rows if r < snapshot._dirty_from}
            if queued is not None and queued.messages and _keeps_stored_rows(snapshot.messages, queued.messages):
                # The queued copy is what the store will hold: a stub replacing it in the
                # queue (latest wins) must not drop its transcript.
                snapshot.messages = list(queued.messages)
            if snapshot.messages and _host_owned(conv.id):
                _refuse_host_messages(conv.id)
                snapshot.messages = []  # a metadata save; loads read the host's rows
            writer = _writer()
            conv._dirty_from, conv._dirty_rows = None, set()  # the snapshot carries these changes now
            fut = writer.submit(conv.id, (snapshot, project_root, conv))
            _convs._held(conv)
            if wait:
                # Write it now: waiting out the batch timer cost the caller 100 ms.
                writer.flush(conv.id, timeout=60.0)
                fut.result(timeout=60.0)
            return fut
        path = _convs.conversation_path(conv.id, project_root)
        home = _convs._find_conversation_meta(conv.id, project_root)
        if home is not None:
            path = home
        path.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        incoming_msgs = getattr(conv, "messages", None) or []
        # list_conversations() strips messages. Saving that stub must not blank history.
        # A sidebar/open stub that kept only user prompts must not wipe a checkpointed turn.
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    _convs._keep_newer_settings(conv, existing)
                    _convs._keep_newer_placement(conv, existing)
                    _lanes._keep_stored_lanes(conv, existing)
                disk_msgs = existing.get("messages") if isinstance(existing, dict) else None
                if isinstance(disk_msgs, list) and disk_msgs:
                    if not incoming_msgs:
                        conv.messages = disk_msgs
                    elif _disk_has_assistant_incoming_does_not(incoming_msgs, disk_msgs):
                        conv.messages = disk_msgs
            except (json.JSONDecodeError, OSError):
                pass
        data = conv.to_dict()
        write_json_atomic(path, data)
        _convs._held(conv)
        try:
            from frontend.perf_trace import trace

            payload = json.dumps(data, ensure_ascii=False, default=str)
            trace(
                "conv_save",
                conv.id,
                (time.perf_counter() - t0) * 1000.0,
                bytes=len(payload),
                message_count=len(getattr(conv, "messages", []) or []),
            )
        except Exception:
            pass


def _take_base(conv: Conversation, origin: Conversation, result: dict[str, Any], count: int) -> None:
    """After a message save the stored hashes are the copy's new base, except at the
    ``theirs`` seqs: the copy still holds the old content there, so its base stays old
    and later saves keep skipping them. The snapshot shares its base list with *origin*
    (a reload gives origin a new list), so it is updated in place."""
    hashes = list(result.get("hashes") or [])
    start = int(result.get("hashes_from") or 0)
    theirs = set(result.get("theirs") or ())
    base = conv._base_hashes
    if base is None:
        if origin._base_hashes is not None:
            return  # origin was reloaded meanwhile: its base is newer
        base = origin._base_hashes = []
    end = start + len(hashes)
    if len(base) < end:
        base.extend([""] * (end - len(base)))
    for seq, h in [*enumerate(hashes, start), *(result.get("dirty_hashes") or {}).items()]:
        if seq not in theirs and seq < len(base):
            base[seq] = h
    if result.get("path") != "merge":
        del base[count:]  # rows past the end were deleted


def _save_conversation_rows(
    conv: Conversation, project_root: str | None, origin: Conversation | None = None
) -> dict[str, Any]:
    """ADR 0003: one row per message. A metadata stub (no messages) or a stub that
    lost the assistant row never blanks a stored transcript — same guard as the
    file store, without reading the transcript back. Runs on the chat-writer thread
    with a snapshot, so ``conv.messages`` is never reloaded here.

    Messages go by delta from ``conv._dirty_from`` (plus the ``conv._dirty_rows`` below
    it) when the chat's ``msg_rev`` is still
    the one this copy is based on, else whole (the repo decides). *origin*, the object
    the snapshot was taken from, learns the new rev."""
    t0 = time.perf_counter()
    repo = _store._repo()
    project_id = repo.conv_project_id(conv.id) or _store._project_id(project_root)
    incoming = list(getattr(conv, "messages", None) or [])
    messages: list[dict[str, Any]] | None = incoming
    stored_count = -1  # read only when a stub may lose rows: counting them is O(n)
    if not incoming:
        messages = None  # nothing to sync (a stub, or a new chat): a metadata save
    elif not any(isinstance(m, dict) and m.get("role") == "assistant" for m in incoming):
        stored_count, stored_has_assistant = repo.conv_message_summary(conv.id)
        if stored_count and stored_has_assistant:
            messages = None
    if messages is not None and _host_owned(conv.id):  # its turn moved to a host meanwhile
        _refuse_host_messages(conv.id)
        messages = None
    doc = conv.to_dict()
    if messages is None:  # a message save counts after the write (_stats_by_hash)
        current = repo.conv_get(conv.id, with_messages=False)
        if current is not None:
            doc["tool_call_count"] = current.get("tool_call_count", doc["tool_call_count"])
            doc["file_count"] = current.get("file_count", doc["file_count"])
    from frontend.ui_web.chat_writer import doc_key

    writer = _WRITER
    key = doc_key(project_id, doc)
    if writer is not None and writer.unchanged(conv.id, key, messages):
        return {"unchanged": 1}
    base = conv._msg_rev
    expect = base
    step = getattr(origin, "_rev_step", None)
    if step is not None and base is not None and step[0] == base:
        # Taken while this copy's previous save was being written: build on that write.
        expect = step[1]
    if messages is None:
        result = repo.conv_save(project_id, doc, messages=None)
    else:
        result = repo.conv_save(
            project_id, doc, messages=messages, dirty_from=conv._dirty_from, expect_rev=expect,
            base_hashes=conv._base_hashes, dirty_rows=sorted(conv._dirty_rows),
        )
    if result.get("skipped"):
        _forget_saved(conv.id)
        return result  # the chat was deleted meanwhile; it stays deleted
    rev = result.get("msg_rev")
    if origin is not None and messages is not None:
        _take_base(conv, origin, result, len(messages))
    if origin is not None and result.get("path") in ("delta", "full"):
        # Only a message save makes the store equal this copy; a metadata save never does.
        # Nor does "merge": the store may keep another writer's rows past this copy's end,
        # which a delta from this rev would delete. Its next save merges again (O(changed))
        # until a tail refresh (_refresh_tail) gives it the stored rev.
        origin._rev_step = (base, rev)
        if origin._msg_rev == base:  # not reloaded meanwhile
            origin._msg_rev = rev
    stats = None
    if messages is not None:
        stats = _stats_by_hash(
            conv.id,
            messages,
            list(result.pop("hashes", None) or []),
            int(result.get("hashes_from") or 0),
            expect,
            rev,
            result.get("dirty_hashes"),
        )
    if stats is not None:
        conv.tool_call_count, conv.file_count = stats["tool_call_count"], stats["file_count"]
        if (doc["tool_call_count"], doc["file_count"]) != (conv.tool_call_count, conv.file_count):
            repo.conv_set_counts(conv.id, **stats)
            doc["tool_call_count"], doc["file_count"] = conv.tool_call_count, conv.file_count
            key = doc_key(project_id, doc)
    if writer is not None:
        writer.remember(conv.id, key)
    try:
        from frontend.perf_trace import trace

        if messages is None and stored_count < 0:
            stored_count = repo.conv_message_summary(conv.id)[0]
        trace(
            "conv_save",
            conv.id,
            (time.perf_counter() - t0) * 1000.0,
            bytes=int(result.get("bytes", 0)),
            message_count=len(messages) if messages is not None else stored_count,
            rows=int(result.get("inserted", 0)) + int(result.get("updated", 0)),
        )
    except Exception:
        pass
    return result


def _refresh_tail(conv: Conversation, project_root: str | None) -> None:
    """Another writer moved the chat: take the stored rows from this copy's last user
    message on and keep the rows before it (a stale copy never writes below its own
    changes: the merge path). The whole chat is loaded only when rows were deleted."""
    msgs = list(conv.messages or [])
    start = _delta_start(msgs, None) if msgs else 0
    rows, rev, count, hashes = _store._repo().messages_since(conv.id, start, with_hashes=True)
    if rev is None:
        return  # gone, or another account's
    if count < start:
        fresh = _convs.load_conversation(conv.id, project_root)
        if fresh is not None:
            conv.take_messages(fresh)
        return
    base = list(conv._base_hashes or [])[:start]
    conv.swap_messages(msgs[:start] + rows, None)
    conv._msg_rev = rev
    conv._base_hashes = base + [""] * (start - len(base)) + hashes


def append_message(conv: Conversation, message: dict[str, Any], project_root: str | None = None) -> None:
    with _store._conversation_lock(conv.id):
        if _store._use_db() and conv._msg_rev is not None:
            flush_chat_saves(conv.id)  # a queued copy lands first, so the rev below is current
            if _store._repo().conv_msg_rev(conv.id) != conv._msg_rev:
                _refresh_tail(conv, project_root)
        else:
            fresh = _convs.load_conversation(conv.id, project_root)
            if fresh is not None:
                conv.take_messages(fresh)
                conv.updated = fresh.updated
        conv.messages.append(message)
        conv.mark_dirty(len(conv.messages) - 1)
        # A new row (a prompt, a steer, a reply): other readers (search, other processes)
        # look at the store right away, so it is written before this returns.
        save_conversation(conv, project_root, touch_updated=True, wait=True)


KILLED_TURN_ERROR = "Closed while the agent was still going."


def _disk_has_assistant_incoming_does_not(incoming: list[Any], disk: list[Any]) -> bool:
    def has_asst(msgs: list[Any]) -> bool:
        return any(isinstance(m, dict) and m.get("role") == "assistant" for m in msgs)

    return has_asst(disk) and not has_asst(incoming)


def upsert_in_flight_assistant(
    conv: Conversation,
    message: dict[str, Any],
    *,
    run_id: str,
    project_root: str | None = None,
) -> None:
    """Replace the last assistant for ``run_id``, or append if this run has none.

    Mid-turn checkpoints and the final emit share one row so a hard kill cannot
    leave a stack of partial assistants, and a finished turn does not duplicate.
    """
    rid = (run_id or "").strip()
    payload = dict(message)
    if rid:
        payload["run_id"] = rid
    if _store._use_db():
        with _store._conversation_lock(conv.id):
            _upsert_run_row(conv, payload, rid, project_root)
        return
    with _store._conversation_lock(conv.id):
        fresh = _convs.load_conversation(conv.id, project_root)
        if fresh is not None:
            conv.take_messages(fresh)
            conv.updated = fresh.updated
        msgs = list(conv.messages or [])
        idx = _run_row_index(msgs, rid)
        if idx >= 0:
            msgs[idx] = payload
        else:
            idx = len(msgs)
            msgs.append(payload)
        conv.swap_messages(msgs, idx)
        save_conversation(conv, project_root, touch_updated=True)


def _run_row_index(msgs: list[Any], rid: str) -> int:
    """Index of the run's assistant after the last user message, or -1."""
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if not isinstance(m, dict):
            continue
        if m.get("role") == "user":
            break
        if rid and m.get("role") == "assistant" and str(m.get("run_id") or "") == rid:
            return i
    return -1


# conv id -> (rows before the in-flight row, their tool calls, their file paths). A
# checkpoint (~every 0.8 s per running turn) counted every message of the chat; now it
# counts the in-flight row and reuses the rest.
_PREFIX_STATS: OrderedDict[str, tuple[list[Any], int, frozenset[str]]] = OrderedDict()


def _checkpoint_stats(conv_id: str, msgs: list[Any], idx: int) -> dict[str, int]:
    """``_stats_for(msgs)``, with the rows before *idx* counted once per prefix.

    ponytail: the prefix is matched by length and row identity (one C-level pass), so a
    row edited in place below the in-flight row keeps its old counts until the next
    message save recounts them (_stats_by_hash)."""
    from frontend.ui_web.session_files import message_stats

    prefix = msgs[:idx]
    with _row_stats_lock:
        hit = _PREFIX_STATS.pop(conv_id, None)
    if hit is not None and len(hit[0]) == len(prefix) and all(map(is_, hit[0], prefix)):
        _, count, paths = hit
    else:
        count, found = 0, set()
        for message in prefix:
            c, p = message_stats(message)
            count += c
            found |= p
        paths = frozenset(found)
    with _row_stats_lock:
        _PREFIX_STATS[conv_id] = (prefix, count, paths)
        while len(_PREFIX_STATS) > _ROW_STATS_CHATS:
            _PREFIX_STATS.popitem(last=False)
    tail = set(paths)
    for message in msgs[idx:]:
        c, p = message_stats(message)
        count += c
        tail |= p
    return {"tool_call_count": count, "file_count": len(tail)}


def _upsert_run_row(conv: Conversation, payload: dict[str, Any], rid: str, project_root: str | None) -> None:
    """Row store: queue only this turn's row on the chat writer (latest wins per run) and
    patch ``conv.messages`` the same way. The caller only enqueues: the write, its stats
    and the rev read run on the writer thread (_write_run_row). When the stored chat no
    longer matched this copy (someone else added a row) the next checkpoint reloads its
    tail first, so a later full save of this copy never writes over their rows."""
    if getattr(conv, "_stale_tail", False):
        conv._stale_tail = False
        flush_chat_saves(conv.id)  # rare: a queued copy lands first, then read their tail
        _refresh_tail(conv, project_root)
    msgs = list(conv.messages or [])
    idx = _run_row_index(msgs, rid)
    if idx >= 0:
        msgs[idx] = payload
    else:
        idx = len(msgs)
        msgs.append(payload)
    now = time.time()
    conv.updated = now
    conv.swap_messages(msgs, None)  # the queued row is this change; a later save is newer
    _writer().submit_checkpoint(conv.id, rid, (payload, msgs, idx, len(msgs), now, conv))


def _write_run_row(conv_id: str, item: tuple[Any, ...]) -> dict[str, Any]:
    """Chat-writer thread: write a queued checkpoint, then tell its chat object the new
    rev so the next save (the turn end's) goes by delta."""
    payload, msgs, idx, n, now, origin = item
    try:
        result = _store._repo().upsert_run_message(
            conv_id, str(payload.get("run_id") or ""), payload, updated=now,
            stats=_checkpoint_stats(conv_id, msgs, idx),
        )
    except Exception:
        origin.mark_all_dirty()  # the row never landed: the next save is whole
        raise
    if result.get("skipped") or origin.messages is not msgs:
        return result  # gone, or a newer checkpoint or a reload replaced this copy
    if result.get("seq") == idx and result.get("count") == n == len(msgs):
        # The row is stored; only the rev moved. Rev and row count come from one
        # snapshot: equal counts mean no other writer added or removed rows.
        _, rev, count, hashes = _store._repo().messages_since(conv_id, idx, with_hashes=True)
        if rev is not None and count == n:
            origin._rev_step = (origin._msg_rev, rev)  # a save queued since builds on it
            origin._msg_rev = rev
            if hashes and origin._base_hashes is not None:
                # A new list: a queued snapshot may still share the old one.
                base = origin._base_hashes[:idx]
                origin._base_hashes = base + [""] * (idx - len(base)) + hashes
            return result
    origin._stale_tail = True  # someone else added or removed rows: their tail wins
    return result


def heal_usage_orphaned_turn(
    conv: Conversation,
    *,
    project_root: str | None = None,
    sent_before: float | None = None,
) -> bool:
    """Usage was saved mid-run but the assistant row never landed (embedded Ducky/Ollama).

    *sent_before*: only a prompt sent before then can be an orphan (a turn sent since
    Ducky started may still be running)."""
    last: dict[str, Any] | None = None
    for m in reversed(conv.messages or []):
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            last = m
            break
    if last is None or last.get("role") != "user":
        return False
    usage = getattr(conv, "token_usage", None)
    calls = usage.get("calls") if isinstance(usage, dict) else None
    if not isinstance(calls, list) or not calls:
        return False
    user_ts = float(last.get("ts") or 0)
    if sent_before is not None and user_ts >= sent_before:
        return False
    if not any(isinstance(c, dict) and float(c.get("ts") or 0) > user_ts for c in calls):
        return False
    upsert_in_flight_assistant(
        conv,
        {
            "role": "assistant",
            "content": "",
            "text": "",
            "ts": time.time(),
            "incomplete": True,
            "error": KILLED_TURN_ERROR,
        },
        run_id=f"heal-{conv.id}",
        project_root=project_root,
    )
    return True


def heal_usage_orphaned_turns_for_project(
    project_root: str | None = None, *, sent_before: float | None = None
) -> list[str]:
    """Boot: persist a killed-turn stub when token_usage outran the transcript.

    Returns the ids of the chats it healed."""
    healed: list[str] = []
    for stub in _convs.list_conversations(project_root=project_root):
        usage = getattr(stub, "token_usage", None)
        calls = usage.get("calls") if isinstance(usage, dict) else None
        if not isinstance(calls, list) or not calls:
            continue
        conv = _convs.load_conversation(stub.id, project_root)
        if conv is None:
            continue
        if heal_usage_orphaned_turn(conv, project_root=project_root, sent_before=sent_before):
            healed.append(conv.id)
    return healed


def heal_killed_coding_turn(
    conv_id: str,
    run_id: str,
    *,
    project_root: str | None = None,
) -> bool:
    """If a dead run left only the user prompt, persist an interrupted assistant row."""
    cid = (conv_id or "").strip()
    rid = (run_id or "").strip()
    if not cid or not rid:
        return False
    conv = _convs.load_conversation(cid, project_root)
    if conv is None:
        return False
    last: dict[str, Any] | None = None
    for m in reversed(conv.messages or []):
        if isinstance(m, dict) and m.get("role") in ("user", "assistant"):
            last = m
            break
    if last is None:
        return False
    if last.get("role") == "assistant":
        if str(last.get("run_id") or "") != rid or not last.get("incomplete"):
            return False
        if str(last.get("error") or "").strip():
            return False
        last = dict(last)
        last["error"] = KILLED_TURN_ERROR
        upsert_in_flight_assistant(conv, last, run_id=rid, project_root=project_root)
        return True
    if last.get("role") != "user":
        return False
    upsert_in_flight_assistant(
        conv,
        {
            "role": "assistant",
            "content": "",
            "text": "",
            "ts": time.time(),
            "coding_agent": (getattr(conv, "coding_agent", "") or "").strip(),
            "run_id": rid,
            "incomplete": True,
            "error": KILLED_TURN_ERROR,
        },
        run_id=rid,
        project_root=project_root,
    )
    return True
