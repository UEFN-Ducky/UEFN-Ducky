"""panel_chats.messages: tool results are capped in a page and fetched whole on demand."""

from __future__ import annotations

import frontend.ui_web.panel_api as _pa
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
    api = PanelApiChatsMixin()
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: None)
    assert api.load_tool_result("c", 5) == {"ok": False, "error": "Chat not found"}
    monkeypatch.setattr(_pa, "load_conversation", lambda cid: object())
    monkeypatch.setattr(_pa, "_messages_to_ui", lambda conv, **kw: [{"id": 5, "tool": {"result": "all of it"}}])
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
