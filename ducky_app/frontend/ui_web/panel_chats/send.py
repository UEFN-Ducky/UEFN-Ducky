"""Send and stop: the composer's send (its model pick applied first), held messages,
continue, edit + resend, restart from a message, Stop, steering and which chats run."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


def _sync_composer_selection(conv_id: str, model: str, coding_agent: str = "") -> None:
    """Apply the composer's model + backend to the chat before a turn starts.

    The picker's own save can lose to a still-running turn that saves its stale
    copy of the chat, so the send is authoritative. Updates this conversation
    only — never global settings or a Ducky profile's favorite_models.
    """
    from backend.agent.coding_agents.base import normalize_coding_agent
    from backend.agent.model_pricing import resolve_provider_for_model

    turn_model = (model or "").strip()
    if turn_model.lower() == "default":
        turn_model = ""
    agent = normalize_coding_agent(coding_agent) if (coding_agent or "").strip() else ""
    if not turn_model and not agent:
        return
    conv = _pa.load_conversation(conv_id)
    if conv is None:
        return
    changed = False
    current = normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky")
    if agent and agent != current:
        conv.coding_agent = current = agent
        if agent != "ducky":
            conv.provider = ""
        changed = True
    if turn_model and (conv.model or "").strip() != turn_model:
        conv.model = turn_model
        changed = True
    if turn_model and current == "ducky":
        resolved = resolve_provider_for_model(turn_model, conv.provider or "")
        if resolved and resolved != (conv.provider or "").strip().lower():
            conv.provider = resolved
            changed = True
    if changed:
        _pa.save_conversation(conv)


def send_message(
    self,
    conv_id: str,
    text: str,
    mode: str,
    model: str,
    file_path: str = "",
    attachments: list[dict[str, Any]] | None = None,
    coding_agent: str = "",
) -> dict[str, Any]:
    from frontend.ui_web.group_orchestrator import (
        is_group_conversation,
        run_group_turn,
    )

    if file_path:
        before = _pa.load_conversation(conv_id)
        had_path = bool(before and before.file_path)
        _pa.ensure_conversation_file_path(conv_id, file_path)
        if not had_path:
            after = _pa.load_conversation(conv_id)
            if after and after.file_path:
                _pa.notify_chats_changed(
                    after.id, after.title, after.folder_id, push=self._push, open_tab=False
                )
    _sync_composer_selection(conv_id, model, coding_agent)
    conv = _pa.load_conversation(conv_id)
    # Subagents retired — every non-group chat is composable (group members included).
    if conv is not None and is_group_conversation(conv):
        run_id = run_group_turn(
            conv_id,
            text,
            mode=mode,
            model=model,
            push=self._push,
            attachments=attachments or [],
        )
        return {"run_id": run_id}
    run_id = _pa.run_message(conv_id, text, mode, model, push=self._push, attachments=attachments or [])
    from frontend.ui_web.agent_turns.pending import HELD_FOR_MODEL

    if run_id == HELD_FOR_MODEL:
        return {"run_id": "", "held_for_model": "1"}
    if run_id == "queued":  # the chat runs: the send waits as a steer / next turn, never a Stop
        return {"ok": True, "queued": True, "note": "Sent — the agent takes it at its next step"}
    return {"run_id": run_id}


def run_held_messages(self, conv_id: str, model: str, coding_agent: str = "") -> dict[str, Any]:
    """The composer picked a model for a chat whose messages waited for one: save the
    pick the way a send does, then run them in order."""
    from frontend.ui_web.agent_turns.pending import release_held_for_model

    if not (model or "").strip() or (model or "").strip().lower() == "default":
        return {"ok": False, "error": "Pick a model first."}
    _sync_composer_selection(conv_id, model, coding_agent)
    return {"ok": True, "ran": release_held_for_model(conv_id)}


def held_for_model(self, conv_id: str) -> dict[str, Any]:
    """How many messages this chat keeps until a model is picked."""
    from frontend.ui_web.agent_turns.pending import held_for_model

    return {"ok": True, "held": held_for_model(conv_id)}


def continue_interrupted(
    self,
    conv_id: str,
    mode: str,
    model: str,
    file_path: str = "",
    coding_agent: str = "",
) -> dict[str, str]:
    """Resume the last interrupted turn without appending a user message."""
    from frontend.ui_web.group_orchestrator import is_group_conversation

    if file_path:
        _pa.ensure_conversation_file_path(conv_id, file_path)
    _sync_composer_selection(conv_id, model, coding_agent)
    conv = _pa.load_conversation(conv_id)
    if conv is None or (conv is not None and is_group_conversation(conv)):
        return {"run_id": ""}
    run_id = _pa.run_message(conv_id, "", mode, model, push=self._push, resume=True)
    return {"run_id": run_id}


def resend_last_user_message(
    self,
    conv_id: str,
    text: str,
    mode: str,
    model: str,
    file_path: str = "",
    attachments: list[dict[str, Any]] | None = None,
    coding_agent: str = "",
) -> dict[str, str]:
    """Cursor-style edit + resend: rewind to the last user turn and rerun.

    Only the most recent user message is editable, so we never need to map a
    synthetic UI row id back to a stored message — we just drop the last
    ``role == "user"`` entry (and everything after it) and let the normal
    send path append the edited message and start a fresh run.
    """
    conv = _pa.load_conversation(conv_id)
    if not conv:
        return {"run_id": ""}
    # Cursor-style edit mid-answer: cancel the live run, then rewind + rerun.
    if conv_id in _pa.list_running_agents():
        _pa.cancel_agent(conv_id)
        _pa.wait_for_idle(conv_id, 2.0)
        if conv_id in _pa.list_running_agents():
            return {"run_id": ""}
    last_user = next(
        (i for i in range(len(conv.messages) - 1, -1, -1) if conv.messages[i].get("role") == "user"),
        None,
    )
    if last_user is not None:
        conv.messages = conv.messages[:last_user]
        _pa.save_conversation(conv)
    return self.send_message(conv_id, text, mode, model, file_path, attachments, coding_agent)


def rewind_to_message(self, conv_id: str, row_id: int, text: str = "") -> dict[str, Any]:
    """Restart from here: erase this user message and everything after it, as if never sent.

    ``row_id`` is the message's UI row id (stored index * ROW_ID_STEP); ``text`` must still
    match it, so a chat changed meanwhile is refused instead of cut in the wrong place. A
    coding agent's resumed session still holds the erased turns: it is dropped, and the
    next turn starts a new one from the kept messages. The composer takes the text back.
    """
    from frontend.ui_web.chat_rows import ROW_ID_STEP

    conv = _pa.load_conversation(conv_id)
    if not conv:
        return {"ok": False, "error": "Chat not found."}
    if conv_id in _pa.list_running_agents():
        _pa.cancel_agent(conv_id)
        _pa.wait_for_idle(conv_id, 2.0)
        if conv_id in _pa.list_running_agents():
            return {"ok": False, "error": "The agent is still stopping. Try again in a moment."}
        conv = _pa.load_conversation(conv_id) or conv
    try:
        index = int(row_id) // ROW_ID_STEP
    except (TypeError, ValueError):
        return {"ok": False, "error": "Unknown message."}
    msg = conv.messages[index] if 0 <= index < len(conv.messages) else None
    stored = str((msg or {}).get("text", (msg or {}).get("content", "")) or "") if isinstance(msg, dict) else ""
    if not isinstance(msg, dict) or msg.get("role") != "user" or stored.strip() != (text or "").strip():
        return {"ok": False, "error": "This chat changed. Reload it and try again."}
    conv.messages = conv.messages[:index]
    if str(getattr(conv, "upstream_session_id", "") or "").strip():
        from backend.agent.coding_agents.session_refresh import mark_rewound

        mark_rewound(conv)
    try:
        from backend.agent.prompt_cache import invalidate_conv_cache

        invalidate_conv_cache(conv)
    except Exception:
        pass
    _pa.save_conversation(conv)
    return {"ok": True, "kept": index}


def cancel_agent(self, conv_id: str = "") -> bool:
    from frontend.ui_web.group_orchestrator import cancel_group_run, list_group_running_ids

    if conv_id:
        cancel_group_run(conv_id)
    else:
        for gid in list_group_running_ids():
            cancel_group_run(gid)
    _pa.cancel_agent(conv_id or None)
    return True


def steer_message(self, conv_id: str, steer_id: str, text: str) -> dict[str, Any]:
    """Send now on a queued message: the running turn takes it at its next tool call
    (frontend/ui_web/steer.py). ok False: no turn runs, send it as a normal message."""
    from frontend.ui_web import steer

    return {"ok": steer.post(conv_id, steer_id, text)}


def list_running_agents(self) -> list[str]:
    from frontend.ui_web.group_orchestrator import list_group_running_ids

    ids = list(_pa.list_running_agents())
    for gid in list_group_running_ids():
        if gid not in ids:
            ids.append(gid)
    return ids


def wait_for_agent_idle(self, conv_id: str, timeout: float = 2.0) -> bool:
    return _pa.wait_for_idle(conv_id, timeout)
