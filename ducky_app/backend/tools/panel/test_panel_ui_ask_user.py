"""Normalize questions for ducky_ask_user."""

from __future__ import annotations

from backend.tools.panel.panel_ui import _normalize_ask_user_questions


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


def test_ask_user_never_times_out():
    """Asks suspend the agent until answered — no layer may cap the wait."""
    import math

    from backend.agent.tools import tool_timeout
    from backend.tools.panel.panel_ui import _MAX_ASK_USER_WAIT_S

    assert math.isinf(_MAX_ASK_USER_WAIT_S)
    assert math.isinf(tool_timeout("ducky_ask_user"))


def test_ui_rpc_active_poll_survives_sweep():
    """A slot being actively polled must never be swept, however old."""
    from frontend.ui_web import ui_rpc

    rid, _ = ui_rpc.submit("ask_user", {})
    # Age the slot far past the TTL, then poll it (refreshes keep-alive).
    ui_rpc._pending[rid].created -= ui_rpc._MAX_TTL_S * 2
    assert ui_rpc.wait(rid, 0.0) is None  # still pending, not "unknown"
    rid2, _ = ui_rpc.submit("noop", {})  # triggers the sweep
    assert ui_rpc.respond(rid, {"ok": True}) is True  # slot survived
    ui_rpc.cancel(rid)
    ui_rpc.cancel(rid2)


if __name__ == "__main__":
    test_normalize_questions()
    test_normalize_rejects_empty()
    test_normalize_rejects_duplicate_id()
    test_normalize_rejects_bad_option()
    test_ask_user_never_times_out()
    test_ui_rpc_active_poll_survives_sweep()
    print("ok")
