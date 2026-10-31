"""A rename tells every window, @ mention list and search at once."""

from __future__ import annotations

from types import SimpleNamespace

import frontend.ui_web.panel_api as _pa
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin


def test_rename_pushes_chats_changed_without_opening_a_tab(monkeypatch):
    conv = SimpleNamespace(id="c1", title="Old", folder_id="f1")
    pushed: list[tuple] = []
    monkeypatch.setattr(_pa, "load_conversation", lambda _cid: conv)
    monkeypatch.setattr(_pa, "save_conversation", lambda _conv: None)
    monkeypatch.setattr(_pa, "notify_chats_changed", lambda *a, **k: pushed.append((a, k)))

    PanelApiChatsMixin().rename_conversation("c1", "  Limo driver  ")

    assert conv.title == "Limo driver"
    assert pushed == [(("c1", "Limo driver", "f1"), {"open_tab": False})]
