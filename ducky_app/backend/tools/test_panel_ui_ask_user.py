"""Normalize questions for ducky_ask_user."""

from __future__ import annotations

from backend.tools.panel_ui import _normalize_ask_user_questions


def test_normalize_questions():
    out = _normalize_ask_user_questions(
        [
            {
                "id": "owner",
                "prompt": "Who owns the tables?",
                "options": [
                    {"id": "plugin", "label": "Plugin", "description": "Takes ownership"},
                    {"id": "core", "label": "Core"},
                ],
            },
            {
                "id": "notes",
                "prompt": "Anything else?",
                "allow_free_text": True,
                "required": False,
            },
        ]
    )
    assert isinstance(out, list)
    assert len(out) == 2
    assert out[0]["id"] == "owner"
    assert out[0]["options"][0]["description"] == "Takes ownership"
    assert out[0]["allow_free_text"] is True
    assert out[0]["required"] is True
    assert out[1]["options"] == []
    assert out[1]["required"] is False


def test_normalize_rejects_empty():
    out = _normalize_ask_user_questions([])
    assert isinstance(out, dict) and out.get("error")


def test_normalize_rejects_duplicate_id():
    out = _normalize_ask_user_questions(
        [
            {"id": "a", "prompt": "One"},
            {"id": "a", "prompt": "Two"},
        ]
    )
    assert isinstance(out, dict) and "duplicate" in str(out.get("error"))


def test_normalize_rejects_bad_option():
    out = _normalize_ask_user_questions(
        [{"id": "a", "prompt": "Q", "options": [{"id": "x"}]}]
    )
    assert isinstance(out, dict) and out.get("error")


if __name__ == "__main__":
    test_normalize_questions()
    test_normalize_rejects_empty()
    test_normalize_rejects_duplicate_id()
    test_normalize_rejects_bad_option()
    print("ok")
