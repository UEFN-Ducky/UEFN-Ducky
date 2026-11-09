"""Read-only, cursor-based chat display; never builds a partial saved Conversation."""
from __future__ import annotations

from contextlib import closing
from types import SimpleNamespace
from typing import Any

from frontend.settings import PanelSettings
from frontend.ui_web.chat_rows import ROW_ID_STEP, _messages_to_ui
from frontend.ui_web.chats import conversations, saves, store


def load_display_rows(conv_id: str, *, before_id: int | None, limit: int, live: bool,
                      display_attachments: bool = True) -> list[dict[str, Any]]:
    """One SQL snapshot, preceded by pending local writes. On a failed flush,
    preserve the existing queued-copy fallback instead of returning stale data."""
    if not store._use_db() or not saves.flush_chat_saves(conv_id):
        conv = conversations.load_conversation(conv_id)
        return [] if conv is None else _messages_to_ui(
            conv, before_id=before_id, limit=limit, live=live, display_attachments=display_attachments)
    pages = []
    count = 0
    root = PanelSettings.load().uefn_project_root
    conv_dir = store.get_conversations_dir(root)
    before_seq = None if before_id is None else int(before_id) // ROW_ID_STEP
    store._repo()  # the one-time legacy import
    from backend.store.repos import chat_display

    with closing(chat_display.display_messages(conv_id, before_seq=before_seq)) as messages:
        for seq, last, message in messages:
            rows = _messages_to_ui(
                SimpleNamespace(id=conv_id, messages=[message]), before_id=before_id, limit=limit - count,
                live=live, message_offset=seq, last_message_index=last, display_attachments=display_attachments,
                project_root=root, conversations_dir=conv_dir)
            pages.append(rows)
            count += len(rows)
            if count >= limit:
                break
    return [row for page in reversed(pages) for row in page][-limit:]
