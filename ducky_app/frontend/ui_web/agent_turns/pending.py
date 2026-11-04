"""Sends that can't start now: run admission for a send, messages held for a chat with no
model, sends into a busy chat (queued, offered as a steer) and delivering them after the turn."""

from __future__ import annotations

import threading
import uuid
from functools import wraps
from typing import Any

from frontend.settings import PanelSettings
from frontend.ui_web.agent_turns.push import (
    PushFn,
    _in_bridge_process,
    _resolve_push,
    notify_chats_changed,
)
from frontend.ui_web.agent_turns.state import (
    SESSION_JOIN_TIMEOUT,
    _AdmissionCancelled,
    _admission_context,
    _admission_lock,
    _check_admission,
    _pending_agent_messages,
    _pending_lock,
    _pending_person_sends,
    _pending_steered,
    _release_admission,
    _reserve_admission,
    is_agent_running,
    wait_for_idle,
)
from frontend.ui_web.chats.saves import save_conversation


def _with_admission(fn):
    @wraps(fn)
    def admitted(conv_id, user_text, mode, model, **kwargs):
        if not kwargs.get("_local") and _in_bridge_process():
            kwargs.pop("_mem_admitted", None)
            return fn(conv_id, user_text, mode, model, **kwargs)
        from frontend.ui_web import admission

        # A slot from the memory wait line (admission.py): this call already holds it.
        slot = {"held": bool(kwargs.pop("_mem_admitted", False)), "started": False}
        try:
            return _admit(fn, conv_id, user_text, mode, model, kwargs, slot,
                          lambda: admitted(conv_id, user_text, mode, model, _mem_admitted=True, **kwargs))
        finally:
            if slot["held"] and not slot["started"]:
                admission.release(conv_id)
    return admitted


def _admit(fn, conv_id, user_text, mode, model, kwargs, slot, start_later) -> str:
    queue = kwargs.get("queue_if_busy", False) and not kwargs.get("force", False)
    from backend.agent.a2a_broker import automatic_work_blocked, resume_by_user
    from frontend.ui_web import admission

    def hold() -> str:
        if kwargs.get("attachments"):  # the text-only agent queue would drop them
            return _queue_person_send(conv_id, user_text, mode, kwargs)
        _queue_pending(conv_id, user_text)
        return "queued"

    if queue and automatic_work_blocked(conv_id, queued=True):
        return hold()
    if is_agent_running(conv_id):
        # Only Stop (cancel_agent, by_user=True) ends a turn; a send into one waits for it.
        return hold() if queue else _queue_person_send(conv_id, user_text, mode, kwargs)
    if not slot["held"]:
        # A person's own send (no parent, no starter) never waits for memory; agent work may.
        person = not queue and not kwargs.get("parent") and kwargs.get("started_by") is None
        if not admission.may_start(conv_id, "person" if person else "agent", start=start_later,
                                   push=_resolve_push(kwargs.get("push"))):
            return "queued"
        slot["held"] = admission.holds(conv_id)
    token = _reserve_admission(conv_id)
    if token is None:
        return hold() if queue else _queue_person_send(conv_id, user_text, mode, kwargs)
    previous = getattr(_admission_context, "current", None)
    _admission_context.current = (conv_id, token)
    try:
        with _admission_lock:
            _check_admission(conv_id, token)
            if queue and automatic_work_blocked(conv_id, queued=True):
                return hold()
            if not queue:
                resume_by_user(conv_id)
        return fn(conv_id, user_text, mode, model, **kwargs)
    except _AdmissionCancelled:
        return ""
    finally:
        _admission_context.current = previous
        # A started turn keeps its memory slot until agent_stopped (_make_broker_tap).
        slot["started"] = bool(token["transferred"])
        if not token["transferred"]:
            _release_admission(conv_id, token)


# Messages sent to a chat that had no model and none to take (no usable Default Model,
# no gateway fallback model): kept until the person picks a model, then run in order.
HELD_FOR_MODEL = "held_for_model"
BUSY_QUEUED = ("That chat is still working on its own turn, so your message waits and runs right "
               "after it. Don't send it again.")
_held_messages: dict[str, list[dict[str, Any]]] = {}
_held_lock = threading.Lock()
_HELD_MAX = 10


def _take_chat_model(conv: Any, settings: PanelSettings, push: PushFn) -> tuple[str, str, str]:
    """Give a Ducky chat with no model the Default Model, else a gateway's fallback model.

    Returns (model, coding_agent, error); with neither, model is "" and error says why."""
    from frontend.favorite_models import chat_model_choice

    choice, error = chat_model_choice(settings)
    if choice is None:
        return "", "ducky", error
    conv.coding_agent, conv.model = choice.coding_agent, choice.model
    conv.provider = choice.provider or ("" if choice.coding_agent != "ducky" else conv.provider)
    try:
        save_conversation(conv)
    except Exception:
        pass
    notify_chats_changed(conv.id, conv.title, conv.folder_id, push=push, open_tab=False)
    push({"type": "model_adopted", "conv_id": conv.id, "model": choice.model,
          "coding_agent": choice.coding_agent, "provider": choice.provider})
    return choice.model, choice.coding_agent, ""


def _provider_label(provider: str) -> str:
    """A gateway's name as Settings → LLMs shows it (OpenAI, not openai)."""
    pid = (provider or "").strip().lower()
    try:
        from backend.uefn_plugins.plugin_host.ui_registry import get_contributions

        for row in get_contributions().get("llm_providers") or []:
            if str(row.get("id") or "").strip().lower() == pid:
                return str(row.get("label") or "").strip() or provider
    except Exception:
        pass
    return provider or "This model's AI service"


def _hold_for_model(conv: Any, text: str, mode: str, attachments: list[dict[str, Any]] | None,
                    parent: str, started_by: str | None, why: str, push: PushFn) -> str:
    """Keep a message for a chat with no model, and ask the person to pick one."""
    item = {"text": text, "mode": mode or "agent", "attachments": list(attachments or []),
            "parent": parent or "", "started_by": started_by}
    with _held_lock:
        held = _held_messages.setdefault(conv.id, [])
        if not any(h["text"] == text for h in held):
            held.append(item)
        del held[:-_HELD_MAX]
        count = len(held)
    push({"type": "model_needed", "conv_id": conv.id, "held": count, "text": why})
    try:
        from frontend.notifications import alert

        who = str(getattr(conv, "title", "") or "").strip() or "A ducky"
        alert(who, "A message is waiting for you to pick a model.", level="ask", kind="chat",
              target_id=conv.id, detail="Open the chat and pick a model; the message runs then.")
    except Exception:
        pass
    return HELD_FOR_MODEL


def held_for_model(conv_id: str) -> int:
    with _held_lock:
        return len(_held_messages.get(conv_id) or [])


def release_held_for_model(conv_id: str) -> int:
    """Run the messages a chat kept while it had no model, oldest first. Returns how many."""
    with _held_lock:
        held = _held_messages.pop(conv_id, [])
    if not held:
        return 0

    def _worker() -> None:
        from frontend.ui_web.agent_turns.routing import run_message

        for item in held:
            run_message(conv_id, item["text"], item["mode"], "", attachments=item["attachments"] or None,
                        parent=item["parent"], started_by=item["started_by"], queue_if_busy=True)

    threading.Thread(target=_worker, daemon=True, name=f"held-for-model-{conv_id[:8]}").start()
    return len(held)


def _queue_person_send(conv_id: str, text: str, mode: str, kwargs: dict[str, Any]) -> str:
    """A person's send while the chat runs: queued for the next turn, never a Stop.

    Text also goes to the steer inbox so the running turn can take it at its next Ducky
    tool call; a steer the turn took is dropped from the queue when the turn ends
    (``_drop_steered_pending``). The old path cancelled the turn and waited 2 s, which
    killed commands mid-run or dropped the message with "Agent already running".
    """
    body = str(text or "")
    attachments = kwargs.get("attachments") or None
    if attachments:
        with _pending_lock:
            _pending_person_sends.setdefault(conv_id, []).append(
                {"text": body, "mode": mode or "agent", "attachments": attachments})
        from backend.agent.a2a_broker import _retry_queue_later
        _retry_queue_later(conv_id)
        return "queued"
    if not body.strip():  # a resume or an empty send: the running turn already covers it
        return ""
    _queue_pending(conv_id, body)
    from frontend.ui_web import steer

    sid = "send-" + uuid.uuid4().hex
    if steer.post(conv_id, sid, body):
        with _pending_lock:
            _pending_steered.setdefault(conv_id, {})[sid] = body
    return "queued"


def _drop_steered_pending(conv_id: str) -> None:
    """Queued person sends the turn already took as a steer don't run again. Call before steer.end_run."""
    with _pending_lock:
        offered = _pending_steered.pop(conv_id, {})
    if not offered:
        return
    from frontend.ui_web import steer

    # ponytail: reads steer's inbox directly; a steer.pending_ids() helper if steer grows more callers.
    with steer._lock:
        waiting = {m["id"] for m in steer._inbox.get(conv_id) or []}
    with _pending_lock:
        box = _pending_agent_messages.get(conv_id) or []
        for sid, body in offered.items():
            if sid not in waiting and body in box:
                box.remove(body)
        if not box:
            _pending_agent_messages.pop(conv_id, None)

def _queue_pending(conv_id: str, text: str) -> None:
    with _pending_lock:
        _pending_agent_messages.setdefault(conv_id, []).append(text)
    from backend.agent.a2a_broker import _retry_queue_later
    _retry_queue_later(conv_id)


def take_pending_agent_messages(conv_id: str) -> list[str]:
    """Hand this chat's held messages to the agent mailbox's next delivery.

    A busy team's mailbox starts the next turn the moment one ends, so these held
    messages waited for an idle moment that never came (Oct 11 2026: a message to a
    team lead sat undelivered through three of its turns).
    """
    with _pending_lock:
        return _pending_agent_messages.pop(conv_id, [])


def _deliver_pending_agent_messages(conv_id: str) -> None:
    from backend.agent.a2a_broker import automatic_work_blocked
    if automatic_work_blocked(conv_id, queued=True):
        return
    with _pending_lock:
        if not _pending_agent_messages.get(conv_id) and not _pending_person_sends.get(conv_id):
            return

    def _worker() -> None:
        from frontend.ui_web.agent_turns.routing import run_message

        if not wait_for_idle(conv_id, SESSION_JOIN_TIMEOUT):
            return
        if automatic_work_blocked(conv_id, queued=True):
            return
        with _pending_lock:
            texts = _pending_agent_messages.pop(conv_id, [])
            sends = _pending_person_sends.pop(conv_id, [])
        if texts:
            run_message(conv_id, "\n\n".join(texts), "agent", "", queue_if_busy=True)
        for item in sends:  # queue_if_busy: each waits behind the turn before it
            run_message(conv_id, item["text"], item["mode"], "", attachments=item["attachments"],
                        queue_if_busy=True)

    threading.Thread(target=_worker, daemon=True, name=f"a2a-pending-{conv_id[:8]}").start()


def _make_broker_tap(push: PushFn, conv_id: str) -> PushFn:
    """Feed turn-lifecycle events to the A2A broker (inbox drain + owed-reply notices)."""

    seen: set[str] = set()
    terminal_lock = threading.Lock()

    def tapped(event: dict[str, Any]) -> None:
        push(event)
        if event.get("conv_id") != conv_id or event.get("type") != "agent_stopped":
            return
        rid = str(event.get("run_id") or "")
        with terminal_lock:
            if rid in seen:
                return
            seen.add(rid)
        from frontend.ui_web import admission

        admission.release(conv_id)  # the turn's memory slot (taken in _admit)
        # Messages no tool call picked up: the composer's queue sends them as the next turn.
        from frontend.ui_web import steer

        _drop_steered_pending(conv_id)
        steer.end_run(conv_id)
        try:
            from backend.agent.a2a_broker import on_agent_stopped, automatic_work_blocked, stats

            on_agent_stopped(
                conv_id,
                str(event.get("reason") or "done"),
                detail=str(event.get("detail") or event.get("text") or ""),
                run_id=rid,
            )
            if stats().get("held", {}).get(conv_id):
                push({"type": "error", "conv_id": conv_id, "run_id": rid,
                      "text": "Agent work is held for manual recovery because prior execution is uncertain. It will not be replayed automatically."})
            if automatic_work_blocked(conv_id, queued=True):
                return
        except Exception:
            return
        _deliver_pending_agent_messages(conv_id)

    return tapped
