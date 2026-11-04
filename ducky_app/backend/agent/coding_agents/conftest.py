from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _meta_reads_follow_faked_chats(monkeypatch):
    """Plans read chats through ``load_conversation_meta`` (no messages). Many tests fake
    the chats by replacing ``chats.conversations.load_conversation``; the metadata read follows
    such a fake so those tests keep describing the same chats."""
    from frontend.ui_web.chats import conversations as pc

    real_meta, real_load = pc.load_conversation_meta, pc.load_conversation

    def meta(conv_id, project_root=None):
        if pc.load_conversation is not real_load:
            return pc.load_conversation(conv_id, project_root=project_root)
        return real_meta(conv_id, project_root)

    monkeypatch.setattr(pc, "load_conversation_meta", meta)
