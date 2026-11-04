"""Turn errors: the crash record, the row a crashed turn leaves, one automatic resume after
a service hiccup, and carrying on with Claude Code when the Ducky AI plan limit is hit."""

from __future__ import annotations

import threading
import time
from typing import Any

from frontend.settings import PanelSettings
from frontend.ui_web.agent_turns.push import PushFn
from frontend.ui_web.project_chats import load_conversation, save_conversation


def _log_agent_crash(
    conv,
    *,
    provider: str,
    model: str,
    error: str,
    partial: dict[str, Any] | None = None,
    elapsed_s: float,
    first_token_s: float | None,
    thinking: str = "",
    answer: str = "",
) -> None:
    """Persist a full-transcript crash record + a short entry in the error panel.

    ``thinking``/``answer`` fall back to the partial message's fields, so the
    Agent-loop path can just pass the partial while the Ask path passes its own
    accumulated buffers.
    """
    if partial:
        thinking = thinking or str(partial.get("thinking") or "")
        answer = answer or str(partial.get("content") or "")
    try:
        from frontend.agent_crash_log import record_crash

        record_crash(
            conv_id=getattr(conv, "id", "") or "",
            provider=provider,
            model=model,
            error=error,
            thinking=thinking,
            answer=answer,
            elapsed_s=elapsed_s,
            first_token_s=first_token_s,
        )
    except Exception:
        pass
    try:
        from frontend import usage_stats
        from frontend.error_log import record_error

        ft = f"{first_token_s:.1f}s" if first_token_s is not None else "n/a"
        # A plan / rate limit is not a crash: chat.turn already counts it as outcome:limit.
        record_error(
            "agent",
            f"turn crashed after {elapsed_s:.1f}s (first token {ft}): {error}",
            count=usage_stats._turn_outcome("error", error) != "limit",
        )
    except Exception:
        pass


AUTO_RESUME_DELAY_S = 60.0
_auto_resumed_at: dict[str, float] = {}


def _crash_row(
    partial: dict[str, Any] | None,
    error: str,
    *,
    tools_run: int,
    written: set[str] | list[str],
    auto_resume: bool,
) -> dict[str, Any]:
    """The incomplete assistant row a crashed turn leaves: its partial reply, and after
    any tool ran, one line saying what ran, the files written and what happens next."""
    row = dict(partial) if partial else {"role": "assistant", "content": "", "ts": time.time()}
    row["incomplete"] = True
    row["error"] = error
    if tools_run:
        files = sorted(written)
        shown = ", ".join(f"`{f}`" for f in files[:8]) + (f" and {len(files) - 8} more" if len(files) > 8 else "")
        line = f"**Interrupted** ({error}) after {tools_run} tool call{'s' if tools_run != 1 else ''}"
        line += f"; files written: {shown}." if files else "; no files written."
        line += " Resuming in 60 s." if auto_resume else " Press Resume to continue."
        prior = str(row.get("content") or "").rstrip()
        row["content"] = f"{prior}\n\n{line}" if prior else line
    return row


def _is_transient(error: str) -> bool:
    try:
        from backend.agent.providers import _TRANSIENT_RE

        return bool(_TRANSIENT_RE.search(error or ""))
    except Exception:
        return False


def _schedule_auto_resume(conv_id: str, error: str, mode: str) -> bool:
    """One automatic Continue, 60 s after a turn died on a service hiccup. Skipped when the
    chat moved on meanwhile (the person sent, resumed or stopped)."""
    # ponytail: once per chat per 10 min, so a resumed turn that dies again waits for Resume.
    now = time.time()
    if now - _auto_resumed_at.get(conv_id, 0.0) < 600:
        return False
    _auto_resumed_at[conv_id] = now

    def fire() -> None:
        from frontend.ui_web.agent_turns.routing import run_message

        conv = load_conversation(conv_id)
        last = (getattr(conv, "messages", None) or [None])[-1] if conv else None
        if not (isinstance(last, dict) and last.get("incomplete") and last.get("error") == error):
            return
        try:
            run_message(conv_id, "", mode, "", resume=True)
        except Exception:
            pass

    t = threading.Timer(AUTO_RESUME_DELAY_S, fire)
    t.daemon = True
    t.start()
    return True


QUOTA_FALLBACK_AGENT = "claude_code"
QUOTA_FALLBACK_MODEL = "claude-opus-5-5"


def _continue_on_claude_code(conv_id: str, mode: str, push: PushFn) -> bool:
    """Ducky AI plan limit hit: carry on with this chat on Claude Code, Opus 5.5 only
    (the person's choice, Nov 2026). Claude Code missing or signed out: say so and stop."""
    from backend.agent.coding_agents.base import get_adapter
    from frontend.ui_web.agent_turns.routing import run_message

    try:
        adapter = get_adapter(QUOTA_FALLBACK_AGENT)
        info = adapter.detect(PanelSettings.load()) if adapter is not None else None
        ready = bool(info and info.enabled and info.available)
    except Exception:
        ready = False
    if not ready:
        push({"type": "error", "conv_id": conv_id,
              "text": "Ducky AI limit reached, and Claude Code is not installed or signed in, "
                      "so this turn stops here. Sign in to Claude Code to keep working."})
        return False
    conv = load_conversation(conv_id)
    if conv is None:
        return False
    conv.coding_agent = QUOTA_FALLBACK_AGENT
    conv.model = QUOTA_FALLBACK_MODEL
    conv.provider = ""
    save_conversation(conv)
    push({"type": "status", "conv_id": conv_id,
          "text": "Ducky AI limit reached, continuing on Claude Code Opus 5.5"})
    # queue_if_busy: this crashed turn may still be closing its session.
    run_message(conv_id, "", mode, QUOTA_FALLBACK_MODEL, resume=True, queue_if_busy=True)
    return True
