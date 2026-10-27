"""A team chat whose coding-agent session got too big starts its next turn on a fresh one.

Codex and the other resumable CLIs re-read their whole thread on every turn. A team lead
that resumed one thread all afternoon read over five million tokens per turn, so each
turn took many minutes and the team waited on it. When a team chat's last turn read more
than ``FRESH_SESSION_TOKENS``, its next turn starts a new session instead, carrying a
handoff Ducky builds from what it already keeps: the chat's own last replies and its plan.
Every message stays in the chat; only the agent's resumed thread is dropped.
"""

from __future__ import annotations

import time
from typing import Any

FRESH_SESSION_TOKENS = 2_000_000
# Below this a cold resume costs little; compacting would lose more than it saves.
COLD_COMPACT_TOKENS = 200_000
_LAST_REPLIES = 5
_REPLY_CHARS = 1200


def _team_chat(conv_id: str) -> bool:
    """A lead or member of an agent group (nested groups included)."""
    try:
        from backend.agent.a2a_broker import _groups_by_member

        containing = _groups_by_member()
    except Exception:
        return False
    return bool(containing) and conv_id in containing


def _turn_tokens(conv: Any) -> int:
    stats = getattr(conv, "coding_agent_stats", None)
    if not isinstance(stats, dict):
        return 0
    try:
        return int(stats.get("context_tokens") or 0)
    except (TypeError, ValueError):
        return 0


def needs_fresh_session(conv: Any) -> bool:
    if not str(getattr(conv, "upstream_session_id", "") or "").strip():
        return False
    if _turn_tokens(conv) < FRESH_SESSION_TOKENS:
        return False
    return _team_chat(str(getattr(conv, "id", "") or ""))


def team_cache_env(conv_id: str) -> dict[str, str]:
    """A team chat waits on its teammates longer than Claude's 5-minute prompt cache, so every
    wake re-sent its whole prompt uncached. A 1 h cache write costs 2x instead of 1.25x and pays
    for itself on the first hit after a gap over 5 minutes. A value the user set wins."""
    import os

    if "CLAUDE_CODE_PROMPT_CACHE_TTL" in os.environ or not _team_chat(conv_id):
        return {}
    return {"CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}


def needs_cold_compact(conv: Any, now: float | None = None) -> bool:
    """The resumed session is big and its prompt cache has expired: the next turn would re-send
    all of it uncached, so the agent compacts it first (Claude Code honors DUCKY_COMPACT_FIRST)."""
    if not str(getattr(conv, "upstream_session_id", "") or "").strip():
        return False
    stats = getattr(conv, "coding_agent_stats", None)
    if not isinstance(stats, dict):
        return False
    try:
        expires = float(stats.get("cache_expires_at") or 0)
    except (TypeError, ValueError):
        return False
    return 0 < expires < (time.time() if now is None else now) and _turn_tokens(conv) >= COLD_COMPACT_TOKENS


def _last_replies(conv: Any) -> list[str]:
    rows: list[str] = []
    for message in reversed(list(getattr(conv, "messages", None) or [])):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        text = str(message.get("text") or message.get("content") or "").strip()
        if not text:
            continue
        stamp = message.get("ts")
        when = time.strftime("%H:%M", time.localtime(float(stamp))) if stamp else ""
        clipped = text if len(text) <= _REPLY_CHARS else text[:_REPLY_CHARS] + " …"
        rows.append(f"- {when} {clipped}".rstrip())
        if len(rows) >= _LAST_REPLIES:
            break
    return list(reversed(rows))


def _plan_outline(conv_id: str) -> list[str]:
    try:
        from backend.agent.coding_agents.plans import load_plan, outline_numbers

        plan = load_plan(conv_id)
    except Exception:
        return []
    if not isinstance(plan, dict):
        return []
    rows = outline_numbers(plan.get("nodes"))
    if not rows:
        return []
    done = sum(1 for _label, node in rows if node.get("status") == "completed")
    open_rows = [f"- {label} {node.get('status', '')}: {str(node.get('content', ''))[:140]}"
                 for label, node in rows if node.get("status") not in ("completed", "cancelled")]
    return [f"{done} of {len(rows)} steps done. Open steps:", *open_rows]


def fresh_session_handoff(conv: Any) -> str:
    """The handoff to put before this turn's message when it starts a fresh session ('' = keep)."""
    if not needs_fresh_session(conv):
        return ""
    tokens = _turn_tokens(conv)
    parts = [
        "## Fresh session",
        f"Your previous session got too big (your last turn re-read {tokens / 1_000_000:.1f}M tokens), "
        "so Ducky started this turn on a fresh one. Every message is still in this chat; nothing was lost.",
    ]
    replies = _last_replies(conv)
    if replies:
        parts += ["", "Where you were (your own last replies, newest last):", *replies]
    outline = _plan_outline(str(getattr(conv, "id", "") or ""))
    if outline:
        parts += ["", "Your plan:", *outline]
    parts += [
        "",
        "Re-read only what you need (ducky_get_plan, project_memory_list, ducky_read_chat) and carry on.",
        "",
    ]
    return "\n".join(parts)


def start_fresh(conv: Any) -> None:
    """Drop the resumed thread; the new session reports its own size."""
    conv.upstream_session_id = ""
    conv.coding_agent_stats = None


# Restart from here: the chat's messages were cut back, so its resumed thread still holds
# turns the person erased. It starts a new session that is given the kept messages instead.
_REWOUND_TABLE = "workspace_state"


def _rewound_key(conv_id: str) -> str:
    return f"rewound_session:{conv_id}"


def mark_rewound(conv: Any) -> None:
    """Drop the resumed thread; the next turn replays the kept messages (``take_rewound``)."""
    start_fresh(conv)
    from backend.store.repos import kv

    kv.set_doc(_REWOUND_TABLE, _rewound_key(str(conv.id)), {"at": time.time()})


def take_rewound(conv_id: str) -> bool:
    """True once after ``mark_rewound``: this turn's prompt carries the kept history."""
    try:
        from backend.store.repos import kv

        if kv.get_doc(_REWOUND_TABLE, _rewound_key(conv_id)) is None:
            return False
        kv.delete_doc(_REWOUND_TABLE, _rewound_key(conv_id))
        return True
    except Exception:
        return False
