"""Message me: a workflow posts a message to you in a chat. From a chat run it lands in
that chat; a scheduled or manual run posts to the "Workflow reports" chat (made once,
found again by its name), which shows on this PC and in the phone panel.

With "Also if the run fails" on, a run that fails before reaching the node still
messages you: which step stopped it and why (a daily 8 AM check never goes quiet)."""

from __future__ import annotations

import logging
import time
from typing import Any

REPORTS_CHAT = "Workflow reports"
_log = logging.getLogger("automations")


def _reports_chat(title: str):
    from frontend.settings import PanelSettings
    from frontend.ui_web.chats.conversations import create_conversation, list_conversations, load_conversation

    for conv in list_conversations():
        if (conv.title or "").strip() == title and not getattr(conv, "is_group", False):
            return load_conversation(conv.id) or conv, False
    return create_conversation(PanelSettings.load(), "", title=title), True


def _announce(conv: Any, title: str, text: str) -> None:
    """A row in the header's activity list, and an alert: the message itself shows in the
    Windows notification only; the account and the phone get "Sent you a message."."""
    try:
        from frontend.ui_web.agent_turns.push import push_ui_event

        push_ui_event({"type": "background_job", "id": f"message:{conv.id}", "source": "message",
                       "title": title, "detail": "Sent you a message", "phase": "done", "conv_id": conv.id})
    except Exception:
        pass
    try:
        from frontend.notifications import alert

        alert(title, "Sent you a message.", level="info", kind="chat", target_id=conv.id, detail=text)
    except Exception:
        _log.debug("Message me alert failed", exc_info=True)


def post(text: str, cfg: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Post ``text`` (headed with the workflow's name) and say where it went."""
    name = str(payload.get("workflow_name") or "").strip()
    said = text
    if name and cfg.get("heading", True) is not False:
        text = f"**{name}**\n\n{text}"
    from frontend.ui_web.agent_turns.push import notify_chats_changed
    from frontend.ui_web.chats.conversations import load_conversation
    from frontend.ui_web.chats.saves import append_message

    caller = str(payload.get("caller_conv_id") or "").strip()
    conv = load_conversation(caller) if caller else None
    created = False
    if conv is None:
        conv, created = _reports_chat(str(cfg.get("chat") or "").strip() or REPORTS_CHAT)
    append_message(conv, {"role": "assistant", "content": text, "text": text, "ts": time.time()})
    try:
        notify_chats_changed(conv.id, conv.title, open_tab=False)
    except Exception:
        pass
    # From a chat's run the message lands in that chat, which is already in front of you.
    if conv.id != caller:
        _announce(conv, name or str(conv.title or REPORTS_CHAT), said)
    # Not "conv_id": the run's own chat id lives under that name.
    return {"message_chat_id": conv.id, "message_chat": conv.title, "new_chat": created}


def message_me(cfg: dict[str, Any], inputs: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    text = str(inputs.get("message") or "").strip()
    if not text:
        raise ValueError("Message me has nothing to say: wire a text into Message or type one in its details.")
    sent = post(text, cfg, payload)
    return {"ok": True, "outputs": {"sent": True, "chat_id": sent["message_chat_id"]}, "result": sent}


def on_failure(
    nodes: dict[str, dict[str, Any]],
    ran: dict[str, Any],
    order: list[dict[str, Any]],
    error: str,
    payload: dict[str, Any],
) -> None:
    """The run failed: a Message me node set to report failures that never ran says so."""
    for nid, node in nodes.items():
        cfg = node.get("config") if isinstance(node.get("config"), dict) else {}
        if node.get("type") != "notify.message" or not cfg.get("on_fail") or nid in ran:
            continue
        failed = next((step for step in reversed(order) if isinstance(step, dict) and step.get("ok") is False), {})
        where = str(failed.get("label") or failed.get("type") or "a step")
        text = f"❌ The run stopped at **{where}**: {error or failed.get('error') or 'it failed'}"
        try:
            post(text, cfg, payload)
        except Exception:
            _log.exception("could not post the workflow failure message")
        return
