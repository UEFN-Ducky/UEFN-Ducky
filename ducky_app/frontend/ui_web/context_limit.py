"""Context limit: an agent past its token limit starts a fresh session between turns.

The limit comes from, first to last: a ``Context: 200k`` line in the plan step it works, the
chat's own ``context_limit_tokens``, the ``context_limit_tokens`` setting, then 60% of the
model's context window (600k when the window is unknown).

Over the limit with work left, the chat writes a short handoff, its session is reset (history,
plan, lane, seat and approvals stay), and its next turn starts with the handoff and its task.
Never mid-turn: the team keeper checks only members whose turn has ended. The keeper also
starts a member fresh when it hands it a new sub plan, and when the agent asked for it
(``ducky_fresh_session``, its own handoff kept here until then).
"""

from __future__ import annotations

import re
import time
from typing import Any

DEFAULT_SHARE = 0.6
FALLBACK_TOKENS = 600_000
HANDOFF_SHARE = 0.03  # the handoff's budget, as a share of the limit
_IDLE_WAIT_S = 600.0
_TABLE = "workspace_state"
_STEP_RE = re.compile(r"(?im)^[\s>*_`-]*context\s*:\s*([\d.,]+)\s*([km])?\b")


def parse_tokens(value: Any) -> int:
    """``200k`` → 200000, ``1.5m`` → 1500000, ``200,000`` → 200000; 0 when unreadable."""
    m = re.fullmatch(r"\s*([\d.,]+)\s*([km])?\s*", str(value or ""), flags=re.I)
    if not m:
        return 0
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return max(0, int(n * {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)))


def step_limit(step: Any) -> int:
    """The ``Context: 200k`` line of a plan step (its title or notes); 0 when it has none."""
    if isinstance(step, dict):
        step = f"{step.get('content') or ''}\n{step.get('body_markdown') or ''}"
    m = _STEP_RE.search(str(step or ""))
    return parse_tokens(m.group(1) + (m.group(2) or "")) if m else 0


def default_limit(conv: Any) -> int:
    """The settings default, else 60% of the chat's model window, else 600k."""
    from frontend.settings import PanelSettings

    try:
        set_to = int(PanelSettings.load().context_limit_tokens or 0)
    except Exception:
        set_to = 0
    if set_to > 0:
        return set_to
    window = 0
    try:
        from frontend.ui_web.context_tokens import _coding_agent_context_limit, context_limit_for_model

        model = str(getattr(conv, "model", "") or "")
        agent = str(getattr(conv, "coding_agent", "") or "ducky")
        stats = getattr(conv, "coding_agent_stats", None)
        if isinstance(stats, dict):
            window = int(stats.get("context_limit") or 0)
        if not window and model:
            window = (context_limit_for_model(model, str(getattr(conv, "provider", "") or "")) if agent == "ducky"
                      else _coding_agent_context_limit(agent, model)) or 0
    except Exception:
        window = 0
    return int(window * DEFAULT_SHARE) if window > 0 else FALLBACK_TOKENS


def resolve_limit(conv: Any, step: Any = None) -> int:
    """Plan step line > the chat's own limit > settings / model default."""
    return step_limit(step) or int(getattr(conv, "context_limit_tokens", 0) or 0) or default_limit(conv)


def used_tokens(conv: Any) -> int:
    """The chat's context size after its last turn (the sidebar's own count)."""
    from frontend.ui_web.panel_api_chats import PanelApiChatsMixin

    return PanelApiChatsMixin._sidebar_context_tokens(conv)


def set_chat_limit(conv_id: str, value: Any, project_root: str | None = None) -> int:
    """Store a chat's own limit (``200k``, ``200000``; 0/empty clears it). Returns the tokens."""
    from frontend.ui_web.project_chats import load_conversation, save_conversation

    tokens = parse_tokens(value)
    conv = load_conversation(conv_id, project_root=project_root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")
    conv.context_limit_tokens = tokens
    save_conversation(conv, project_root)
    return tokens


def over_limit(conv_id: str, step: Any = None, project_root: str | None = None) -> tuple[int, int] | None:
    """``(used, limit)`` when the chat's context is past its limit, else None."""
    from frontend.ui_web.project_chats import load_conversation

    try:
        conv = load_conversation(conv_id, project_root=project_root)
        if conv is None or getattr(conv, "is_group", False):
            return None
        used, limit = used_tokens(conv), resolve_limit(conv, step)
    except Exception:
        return None
    return (used, limit) if limit > 0 and used - _fresh_base(conv_id, used) > limit else None


def _base_key(conv_id: str) -> str:
    return f"fresh_base:{conv_id}"


def _fresh_base(conv_id: str, used: int) -> int:
    """What a fresh session of this chat costs before any work: its first size after the
    reset (system prompt, tools, the handoff). Only growth past that counts toward the
    limit; a chat whose prompt alone is over its limit (a Ducky AI turn is ~166k against
    77k) handed off after every turn, forever (Oct 29 2026). 0 before any fresh start."""
    try:
        from backend.store.repos import kv

        doc = kv.get_doc(_TABLE, _base_key(conv_id))
        if not isinstance(doc, dict):
            return 0
        if doc.get("base") is None:
            kv.set_doc(_TABLE, _base_key(conv_id), {"base": int(used), "at": time.time()})
            return int(used)
        return int(doc.get("base") or 0)
    except Exception:
        return 0


def _measure_base_next(conv_id: str) -> None:
    """After a reset: the next check records the fresh session's size as its base."""
    try:
        from backend.store.repos import kv

        kv.set_doc(_TABLE, _base_key(conv_id), {"base": None, "at": time.time()})
    except Exception:
        pass


def _fresh_key(conv_id: str) -> str:
    return f"fresh_session:{conv_id}"


def request_fresh(conv_id: str, handoff: str) -> None:
    """The agent asked for a fresh session after this turn, with its own handoff."""
    from backend.store.repos import kv

    kv.set_doc(_TABLE, _fresh_key(conv_id), {"handoff": str(handoff), "at": time.time()})


def requested_handoff(conv_id: str) -> str | None:
    """The handoff a chat asked to start fresh with, or None."""
    try:
        from backend.store.repos import kv

        doc = kv.get_doc(_TABLE, _fresh_key(conv_id))
    except Exception:
        return None
    return str(doc.get("handoff") or "") if isinstance(doc, dict) else None


def clear_request(conv_id: str) -> None:
    try:
        from backend.store.repos import kv

        kv.delete_doc(_TABLE, _fresh_key(conv_id))
    except Exception:
        pass


def handoff_prompt(limit: int) -> str:
    from backend.tools.panel.ducky_panel import _SUBAGENT_HANDOFF_PROMPT

    budget = max(1_000, int(limit * HANDOFF_SHARE))
    return (f"{_SUBAGENT_HANDOFF_PROMPT} Your context is full: this chat starts a fresh session next. "
            f"Keep the handoff under {budget:,} tokens (about {budget * 3 // 4:,} words).")


def _k(tokens: int) -> str:
    return f"{max(0, tokens) // 1000}k"


def start_fresh(conv_id: str, next_text: str, *, used: int = 0, limit: int = 0, handoff: str | None = None,
                project_root: str | None = None) -> bool:
    """Handoff, session reset, then the next turn with the handoff + ``next_text``.

    ``handoff`` None: the chat writes one first. A given text (its own, from
    ``ducky_fresh_session``) is used as is; "" (a new sub plan) starts with ``next_text`` alone.
    Runs between turns. A failed handoff (or reset) keeps the old session and still starts
    ``next_text``. Returns True when the chat started fresh."""
    from backend.tools.panel.ducky_panel import build_recycle_spawn_message
    from frontend.ui_web import agent_modes
    from frontend.ui_web.context_control import reset_context
    from frontend.ui_web.project_chats import append_message, load_conversation

    conv = load_conversation(conv_id, project_root=project_root)
    model = str(getattr(conv, "model", "") or "") if conv is not None else ""
    if conv is not None:
        try:
            used = used or used_tokens(conv)
            limit = limit or resolve_limit(conv)
        except Exception:
            pass
    limit = limit or FALLBACK_TOKENS

    def go_on(text: str) -> None:
        agent_modes.run_message(conv_id, text, "agent", model, queue_if_busy=True)

    if handoff is None:
        out = agent_modes.run_message_and_wait(conv_id, handoff_prompt(limit), "agent", model,
                                               timeout_sec=_IDLE_WAIT_S, cancel_on_timeout=False)
        handoff = str(out.get("assistant_text") or "").strip()
        if out.get("status") != "done" or not handoff:
            go_on(next_text)
            return False
    # A handoff's reply lands before its run is marked idle, and the reset refuses a running chat.
    idle_by = time.monotonic() + _IDLE_WAIT_S
    while agent_modes.is_agent_running(conv_id) and time.monotonic() < idle_by:
        time.sleep(0.5)
    try:
        reset_context(conv_id, ["session"], project_root=project_root)
    except Exception:
        go_on(next_text)
        return False
    _measure_base_next(conv_id)
    cap = max(4_000, int(limit * HANDOFF_SHARE) * 4)  # chars: about 4 per token
    text = (build_recycle_spawn_message(handoff[:cap], next_text, max_chars=cap + len(next_text) + 1_000)
            if handoff.strip() else next_text)
    fresh = load_conversation(conv_id, project_root=project_root)
    if fresh is not None:
        line = f"Started fresh at {_k(used)} → {_k(len(text) // 4)} tokens"
        append_message(fresh, {"role": "assistant", "content": line, "ts": time.time()}, project_root=project_root)
    agent_modes.run_message(conv_id, text, "agent", model)
    return True
