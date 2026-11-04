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
    assert ui_rpc.respond(rid, {"ok": True, "answers": {"choice": {"text": "yes"}}}) is True  # slot survived
    ui_rpc.cancel(rid)
    ui_rpc.cancel(rid2)


def test_ui_rpc_has_pending_for_conv():
    from frontend.ui_web import ui_rpc

    rid, _ = ui_rpc.submit("ask_user", {"conv_id": "member-1"})
    assert ui_rpc.has_pending_for_conv("member-1") is True
    assert ui_rpc.has_pending_for_conv("other") is False
    ui_rpc.respond(rid, {"ok": True, "answers": {"choice": {"text": "yes"}}})
    ui_rpc.wait(rid, 0.0)
    assert ui_rpc.has_pending_for_conv("member-1") is False


if __name__ == "__main__":
    test_normalize_questions()
    test_normalize_rejects_empty()
    test_normalize_rejects_duplicate_id()
    test_normalize_rejects_bad_option()
    test_ask_user_never_times_out()
    test_ui_rpc_active_poll_survives_sweep()
    test_ui_rpc_has_pending_for_conv()
    print("ok")


def test_a_members_question_also_shows_in_its_leads_chat(monkeypatch):
    # Oct 15 2026 owner: everything goes through the lead. A team member's question card sat
    # in a member chat nobody had open; it now also shows in the lead's chat, with the
    # member's name on it (the panel already shows a member's card in the chats named in
    # group_ids), and the alert opens the lead's chat.
    import json
    from types import SimpleNamespace

    from backend.tools.panel import panel_ui, permission_prompt

    sent: list[dict] = []
    monkeypatch.setattr(panel_ui, "_resolve_ask_user_conv_id", lambda: "member")
    monkeypatch.setattr("frontend.ui_web.group_orchestrator.lookup_member_hub", lambda cid: (["group-core"], {}))
    monkeypatch.setattr(permission_prompt, "starter_root", lambda cid: "lead" if cid == "member" else "")
    monkeypatch.setattr("frontend.ui_web.chats.conversations.load_conversation",
                        lambda cid, **_k: SimpleNamespace(ducky_name="Verse Coder", title="Verse Coder"))
    monkeypatch.setattr(panel_ui, "panel_rpc", lambda method, payload, timeout=0: sent.append(payload) or {"ok": True})
    out = json.loads(panel_ui.ducky_ask_user([{"id": "q", "prompt": "Which date format?"}]))
    assert out["ok"] is True
    assert sent[0]["conv_id"] == "member" and sent[0]["lead_conv_id"] == "lead"
    assert sent[0]["group_ids"] == ["group-core", "lead"]
    assert sent[0]["author"] == {"name": "Verse Coder", "member_conv_id": "member"}

    monkeypatch.setattr(panel_ui, "_resolve_ask_user_conv_id", lambda: "lead")
    monkeypatch.setattr("frontend.ui_web.group_orchestrator.lookup_member_hub", lambda cid: None)
    panel_ui.ducky_ask_user([{"id": "q", "prompt": "Ship it?"}])
    assert "lead_conv_id" not in sent[1] and "group_ids" not in sent[1]
