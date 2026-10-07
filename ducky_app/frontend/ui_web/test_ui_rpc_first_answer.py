"""An ask sent to every window: the first answer wins, a late one can't overwrite it."""

from __future__ import annotations

from frontend.ui_web import ui_rpc


def test_first_answer_wins() -> None:
    request_id, _event = ui_rpc.submit("ask_user", {"questions": []})
    try:
        assert ui_rpc.respond(request_id, {"ok": True, "answers": {"agent_permission": {"selected": ["all"]}}}) is True
        assert ui_rpc.respond(request_id, {"error": "answered in another window"}) is False
        assert ui_rpc.wait(request_id, 0.1) == {"ok": True, "answers": {"agent_permission": {"selected": ["all"]}}}
    finally:
        ui_rpc.cancel(request_id)
