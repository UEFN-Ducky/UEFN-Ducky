"""chat_rows owns the row conversion; panel_api re-exports it for mixins."""

import time
from types import SimpleNamespace

from frontend.ui_web import chat_rows, panel_api


def test_panel_api_reexports_chat_rows() -> None:
    assert panel_api._messages_to_ui is chat_rows._messages_to_ui
    assert panel_api._tool_result_text is chat_rows._tool_result_text


def _big_conv(n: int = 3000) -> SimpleNamespace:
    messages = []
    for k in range(n):
        if k % 2 == 0:
            messages.append({"role": "user", "text": f"u{k}"})
        else:  # one message, several rows: a tool call (2 rows) and a reply
            messages.append(
                {
                    "role": "assistant",
                    "content": f"a{k}",
                    "blocks": [
                        {"type": "tool_call", "id": f"t{k}", "name": "x", "arguments": {}, "status": "success",
                         "result": {"data": "ok"}, "file_edits": []},
                    ],
                }
            )
    return SimpleNamespace(id="big", messages=messages)


def test_pages_match_full_load_ids() -> None:
    conv = _big_conv()
    full = chat_rows._messages_to_ui(conv, project_root="")
    ids = [r["id"] for r in full]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert full[1]["id"] == 1000 and full[2]["id"] == 1001 and full[3]["id"] == 1002

    started = time.monotonic()
    page = chat_rows._messages_to_ui(conv, project_root="", limit=200)
    assert time.monotonic() - started < 0.3
    assert page == full[-200:]

    started = time.monotonic()
    older = chat_rows._messages_to_ui(conv, project_root="", before_id=page[0]["id"], limit=200)
    assert time.monotonic() - started < 0.3
    assert older == full[-400:-200]

    # before_id mid-message: only that message's earlier rows, then the ones before it.
    mid = full[-2]["id"]
    assert chat_rows._messages_to_ui(conv, project_root="", before_id=mid, limit=3) == full[-5:-2]
    # before_id without a limit: everything before it.
    assert chat_rows._messages_to_ui(conv, project_root="", before_id=page[0]["id"]) == full[:-200]


def test_first_page_of_a_900_message_chat_skips_old_edit_diffs(monkeypatch) -> None:
    """Old edits rebuild their diff from disk (file read + difflib): the first page of an
    867-message chat must only pay for the messages it shows, not all of them."""
    from frontend.ui_web.verse_editor import agent_sync

    diffs: list[str] = []
    monkeypatch.setattr(agent_sync, "build_file_edit_meta", lambda name, _a, _r: diffs.append(name))
    conv = _big_conv(900)
    for m in conv.messages:
        for block in m.get("blocks", ()):
            del block["file_edits"]  # older turns stored no diff

    page = chat_rows._messages_to_ui(conv, project_root="", limit=300)
    assert len(page) == 300 and page[-1]["id"] // 1000 == 899
    assert 0 < len(diffs) <= 80  # 300 rows ≈ 75 user+assistant pairs, not all 450 edits
