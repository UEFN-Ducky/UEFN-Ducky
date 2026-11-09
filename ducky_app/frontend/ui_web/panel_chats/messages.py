"""A chat's messages and context: load a page of UI rows (tool results capped), one tool
row's whole result, the context meter, resetting or restoring context, and session files."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa

# A tool card's preview; past it the card asks load_tool_result for the rest.
TOOL_RESULT_CAP = 8_000


def _cap_tool_results(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        tool = row.get("tool")
        result = tool.get("result") if isinstance(tool, dict) else None
        if isinstance(result, str) and len(result) > TOOL_RESULT_CAP:
            row["tool"] = {**tool, "result": result[:TOOL_RESULT_CAP], "resultTruncated": True,
                           "resultBytes": len(result.encode("utf-8"))}


# The stand-in for a message a page doesn't show: _messages_to_ui walks from the end and
# stops once it has enough rows, so it is never converted (a page that reaches it widens).
_NOT_LOADED: dict[str, Any] = {"role": "user", "text": ""}


# How long a chat load waits for the writer to land this chat's in-flight row. Past it the
# load reads the last written snapshot: the running turn's stream shows the rest. Without the
# cap load_conversation's flush waited up to 30 s behind a busy writer and the panel gave up
# at 45 s (CHAT_LOAD_TIMEOUT_MS) on short chats while a team ran.
CHECKPOINT_FLUSH_WAIT_S = 0.5


def _writer_settled(conv_id: str) -> bool:
    """True when no checkpoint of this chat waits for the writer (after a short flush)."""
    from frontend.ui_web.chats import saves as chat_saves

    writer = chat_saves._WRITER
    if writer is None or not writer.checkpoint_waits(conv_id):
        return True
    return bool(writer.flush(conv_id, CHECKPOINT_FLUSH_WAIT_S)) or not writer.checkpoint_waits(conv_id)


def _page_rows(conv_id: str, before_id: Any, limit: int, live: bool,
               display_attachments: bool = True) -> list[dict[str, Any]] | None:
    """The ``limit`` rows before ``before_id`` (the newest without it), opening only the
    messages they come from. ``load_conversation`` opened every message of the chat, one
    DPAPI call each: a 735-message chat out of the opened-row cache took 0.45 s for its
    first page on an idle PC (Nov 1 2026), and the cache keeps 8 chats. None: use the
    whole load (file store, or a save of this chat still queued for the writer)."""
    from frontend.ui_web.chat_rows import ROW_ID_STEP
    from frontend.ui_web.chats import conversations as chat_convs, saves as chat_saves, store as chat_store

    # A checkpoint still waiting is not a reason for the whole load: that flushes for 30 s.
    if not chat_store._use_db() or chat_saves._queued(conv_id) is not None:
        return None
    conv = chat_convs.load_conversation_meta(conv_id)
    if conv is None:
        return []
    repo = chat_store._repo()
    stop = None if before_id is None else int(before_id) // ROW_ID_STEP + 1
    want = max(16, limit // 4)  # assistant turns give many rows each
    while True:
        _, count = repo.messages_range(conv_id, 0, 0)
        end = count if stop is None else min(stop, count)
        start = max(0, end - want)
        got, count = repo.messages_range(conv_id, start, end)
        messages: list[Any] = [_NOT_LOADED] * count
        for seq, message in got:
            if seq < count:
                messages[seq] = message
        conv.messages = messages
        rows = _pa._messages_to_ui(conv, live=live, before_id=None if before_id is None else int(before_id),
                                   limit=limit, display_attachments=display_attachments)
        if start == 0 or (rows and rows[0]["id"] >= start * ROW_ID_STEP):
            return rows
        want *= 4


def load_messages(
    self, conv_id: str, before_id: int | None = None, limit: int | None = None, full_results: bool = False
) -> list[dict[str, Any]]:
    """The one message-load path: conversation as flat UI rows.

    `_messages_to_ui` is the single source of truth for how a stored
    conversation becomes rows; row ids are message index * ROW_ID_STEP + row index,
    so a page (``limit`` newest rows, or the ``limit`` rows before
    ``before_id``) converts only the messages it shows, newest first.
    Without arguments the whole conversation comes back, as before.

    A tool result over ``TOOL_RESULT_CAP`` chars is cut, with ``resultTruncated``
    and ``resultBytes`` on the row (``load_tool_result`` has it whole): 40 rows over
    32 KB were 22% of a 7 MB chat. ``full_results`` keeps them whole (account mirror).
    """
    started = _pa.time.monotonic()
    try:
        live = conv_id in self.list_running_agents()
        settled = _writer_settled(conv_id)
        # ponytail: a whole load with the writer busy reads every message as one huge page.
        page = int(limit) if limit is not None else (None if settled else 10**9)
        rows = None if page is None else _page_rows(conv_id, before_id, page, live,
                                                    display_attachments=not full_results)
        if rows is None:
            conv = _pa.load_conversation(conv_id)
            if not conv:
                return []
            rows = _pa._messages_to_ui(
                conv,
                live=live,
                before_id=None if before_id is None else int(before_id),
                limit=None if limit is None else int(limit),
                display_attachments=not full_results,
            )
    except Exception as exc:
        # A chat that can't be read must leave a trace, not just a spinner.
        _pa.record_error("chat", f"Chat {str(conv_id)[:8]} couldn't be loaded: {type(exc).__name__}: {exc}")
        raise
    if not full_results:
        _cap_tool_results(rows)
    took = _pa.time.monotonic() - started
    if took > 5.0:
        _pa.record_activity("chat", f"Chat {str(conv_id)[:8]} took {took:.1f}s to load ({len(rows)} rows)")
    return rows


def load_tool_result(self, conv_id: str, row_id: int) -> dict[str, Any]:
    """The whole result of one tool row that ``load_messages`` cut."""
    from frontend.ui_web.chats.display import load_display_rows
    from frontend.ui_web.chats.conversations import load_conversation_meta

    if load_conversation_meta(conv_id) is None:
        return {"ok": False, "error": "Chat not found"}
    rows = load_display_rows(conv_id, before_id=int(row_id) + 1, limit=1, live=False)
    tool = rows[-1].get("tool") if rows and rows[-1].get("id") == int(row_id) else None
    if not isinstance(tool, dict):
        return {"ok": False, "error": "No tool result in that row"}
    return {"ok": True, "result": str(tool.get("result") or "")}


def get_context_usage(
    self,
    conv_id: str,
    model: str,
    mode: str = "agent",
    draft: str = "",
    include_content: bool = False,
) -> dict[str, Any]:
    from frontend.ui_web.context_tokens import compute_context_usage

    try:
        return compute_context_usage(
            conv_id, model, mode=mode, draft_text=draft, include_content=include_content
        )
    except Exception as exc:

        _pa.logging.getLogger(__name__).exception("get_context_usage failed for %s: %s", conv_id, exc)
        from frontend.ui_web.context_tokens import context_limit_for_model

        limit = context_limit_for_model(model) or 0
        return {
            "used_tokens": 0,
            "context_limit": limit,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "breakdown": [],
            "omitted": [],
            "error": str(exc),
        }


def reset_context(self, conv_id: str, segments: list[str], mode: str = "agent", model: str = "") -> dict[str, Any]:
    from frontend.ui_web.context_control import reset_context

    return reset_context(conv_id, segments, model=model, mode=mode)


def restore_context(self, conv_id: str, segments: list[str], mode: str = "agent", model: str = "") -> dict[str, Any]:
    from frontend.ui_web.context_control import restore_context

    return restore_context(conv_id, segments, model=model, mode=mode)


def get_session_files(self, conv_id: str) -> list[dict[str, Any]]:
    from frontend.ui_web.session_files import compute_session_files

    return compute_session_files(conv_id)
