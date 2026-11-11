"""panel_chats.messages: tool results are capped in a page and fetched whole on demand."""

from __future__ import annotations

import frontend.ui_web.panel_api  # noqa: F401 - initialize the mixin import cycle
from frontend.ui_web.panel_api_chats import PanelApiChatsMixin
from frontend.ui_web.panel_chats import messages


def test_a_long_tool_result_is_cut_with_its_size():
    big = "x" * (messages.TOOL_RESULT_CAP + 5)
    rows = [{"tool": {"result": big}}, {"tool": {"result": "short"}}, {"text": "hi"}]
    messages._cap_tool_results(rows)
    assert rows[0]["tool"]["resultTruncated"] and rows[0]["tool"]["resultBytes"] == len(big)
    assert len(rows[0]["tool"]["result"]) == messages.TOOL_RESULT_CAP
    assert rows[1] == {"tool": {"result": "short"}} and rows[2] == {"text": "hi"}


def test_a_missing_chat_or_row_has_no_tool_result(monkeypatch):
    from frontend.ui_web.chats import conversations, display
    api = PanelApiChatsMixin()
    monkeypatch.setattr(conversations, "load_conversation_meta", lambda cid: None)
    assert api.load_tool_result("c", 5) == {"ok": False, "error": "Chat not found"}
    monkeypatch.setattr(conversations, "load_conversation_meta", lambda cid: object())
    monkeypatch.setattr(display, "load_display_rows", lambda conv, **kw: [{"id": 5, "tool": {"result": "all of it"}}])
    assert api.load_tool_result("c", 5) == {"ok": True, "result": "all of it"}
    assert api.load_tool_result("c", 6)["ok"] is False


def test_the_context_meter_never_raises(monkeypatch):
    from frontend.ui_web import context_tokens

    def boom(*a, **kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(context_tokens, "compute_context_usage", boom)
    monkeypatch.setattr(context_tokens, "context_limit_for_model", lambda m: 1000)
    got = PanelApiChatsMixin().get_context_usage("c", "m")
    assert got["context_limit"] == 1000 and got["error"] == "nope" and got["used_tokens"] == 0


def _edit(size: int, path: str = "a.verse") -> dict:
    return {"path": path, "before": "b" * size, "after": "a" * size, "linesAdded": 3, "linesRemoved": 1, "kind": "write"}


def test_a_big_file_edit_travels_without_its_texts():
    """300 rows of one chat were 4.4 MB, 4.0 MB of it file texts no card shows until opened."""
    small, big = _edit(10), _edit(messages.FILE_EDIT_INLINE_CHARS)
    shared = _edit(messages.FILE_EDIT_INLINE_CHARS, "b.verse")
    rows = [
        {"tool": {"name": "t", "fileEdit": big}},
        {"tool": {"name": "t", "fileEdit": small}},
        {"tool": {"name": "t", "fileEdits": [shared, small], "fileEdit": shared}},
        {"tool": {"name": "t", "result": "r"}},
        {"text": "hi"},
    ]
    messages._trim_file_edits(rows)
    cut = rows[0]["tool"]["fileEdit"]
    assert cut == {"path": "a.verse", "before": "", "after": "", "linesAdded": 3, "linesRemoved": 1, "kind": "write",
                   "contentOmitted": True}
    assert big["before"]  # the stored edit itself is never changed
    assert rows[1]["tool"]["fileEdit"] is small and "contentOmitted" not in small
    many = rows[2]["tool"]["fileEdits"]
    assert many[0]["contentOmitted"] and many[0]["path"] == "b.verse" and many[1] is small
    assert rows[2]["tool"]["fileEdit"] is many[0]  # one copy under both names
    assert rows[3] == {"tool": {"name": "t", "result": "r"}} and rows[4] == {"text": "hi"}


def test_a_cards_file_edits_are_read_whole_on_demand(monkeypatch):
    from frontend.ui_web.chats import conversations, display

    api = PanelApiChatsMixin()
    monkeypatch.setattr(api, "list_running_agents", lambda: ["live"], raising=False)
    monkeypatch.setattr(conversations, "load_conversation_meta", lambda cid: None)
    assert api.load_tool_file_edits("c", 5) == {"ok": False, "error": "Chat not found"}
    monkeypatch.setattr(conversations, "load_conversation_meta", lambda cid: object())
    asked: list[dict] = []
    whole = _edit(50_000)

    def rows_for(conv, **kw):
        asked.append({"conv": conv, **kw})
        return [{"id": 5, "tool": {"fileEdits": [whole], "fileEdit": whole}}]

    monkeypatch.setattr(display, "load_display_rows", rows_for)
    assert api.load_tool_file_edits("c", 5) == {"ok": True, "fileEdits": [whole]}
    assert asked[-1] == {"conv": "c", "before_id": 6, "limit": 1, "live": False}
    api.load_tool_file_edits("live", 5)
    assert asked[-1]["live"] is True  # numbered as the page of a running chat was
    assert api.load_tool_file_edits("c", 6)["ok"] is False
    monkeypatch.setattr(display, "load_display_rows", lambda conv, **kw: [{"id": 5, "tool": {"fileEdit": whole}}])
    assert api.load_tool_file_edits("c", 5) == {"ok": True, "fileEdits": [whole]}
    monkeypatch.setattr(display, "load_display_rows", lambda conv, **kw: [{"id": 5, "tool": {"result": "x"}}])
    assert api.load_tool_file_edits("c", 5) == {"ok": False, "error": "No file edit in that row"}
