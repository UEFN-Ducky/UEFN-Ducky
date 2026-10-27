"""Skill hints for a user turn: the few sections that best match what the person asked.

Added to the turn's live context (Ducky's own agent) or prompt (coding agents),
never to the system prompt, so the cached prompt prefix stays byte-identical. A
hint is only given when the top match is clearly relevant; chit-chat ("continue",
"did you finish?") gets none.
"""

from __future__ import annotations

import logging
import re
from typing import Any

_log = logging.getLogger("uefn_ducky.search")
_SENTENCE = re.compile(r"(?<=[.?!])\s+")

MAX_CHARS = 400
MIN_TEXT = 8
QUERY_CHARS = 600
# A hit counts when its meaning score is high, or when keywords and meaning agree.
# Calibrated on backend/search/eval (agent questions vs everyday chat lines).
MEANING_ALONE = 0.52
MEANING_WITH_KEYWORD = 0.40
KEYWORD_RANK = 5
# Oct 26 (scripts/search_eval.py hints): chat lines hinted 0.75 -> 0.25; requests hinted
# 1.00 -> 0.96, the losses being typo-only keyword matches like the noise itself.


def _has_model() -> bool:
    try:
        from backend.search.embedder import get_embedder

        return get_embedder() is not None
    except Exception:  # noqa: BLE001
        return False


def _relevant(row: dict[str, Any]) -> bool:
    match = row.get("match") or {}
    meaning = match.get("meaning")
    kw = match.get("keyword_rank")
    if meaning is None:
        # Not even among the 50 closest by meaning: with the model loaded that is a stray
        # word (Oct 26: "make aplna to fix best prtiace" hinted tripo-api at keyword rank 1).
        return kw is not None and kw <= 3 and not _has_model()
    return meaning >= MEANING_ALONE or (kw is not None and kw <= KEYWORD_RANK and meaning >= MEANING_WITH_KEYWORD)


def _label(row: dict[str, Any]) -> str:
    pack, sub, section = row.get("pack"), row.get("subskill"), row.get("section")
    if row.get("kind") == "pack":
        return str(pack)
    name = f"{pack}/{sub}"
    return f"{name} > {section}" if section else name


def context_for_conversation(conv: Any) -> Any:
    """The search context of a chat's own skill selection (packs and sub-skills it may use)."""
    from backend.search.query import Context, chat_context

    try:
        from backend.skills.store import resolve_conversation_selection
        from frontend.settings import PanelSettings

        sel = resolve_conversation_selection(conv, PanelSettings.load())
        ctx = chat_context()
        return Context(
            disabled_packs=frozenset(sel.disabled_packs) | ctx.disabled_packs,
            enabled_subskills={k: frozenset(v) for k, v in (sel.enabled_subskills or {}).items()} or ctx.enabled_subskills,
            plugin_enabled=ctx.plugin_enabled,
        )
    except Exception:  # noqa: BLE001
        return chat_context()


def _focus(text: str) -> str:
    """The sentence the request is about: the first with three or more content words.

    Later sentences are often about the answer ("reply in three lines", "don't change any
    files", "name the section you read"); searched with the rest they pulled the hint
    toward UEFN Ducky's own how-to sections (Oct 16 live test).
    """
    from backend.store.repos.search import title_words

    for sentence in _SENTENCE.split(text):
        if len(title_words(sentence).split()) >= 3:
            return sentence.strip()
    return text


_TEAMWORK = re.compile(r"\b(teams?|group|lead|leads|leader|swarm|together|parallel|same time|delegate|split)\b", re.I)


def _duckies_named(text: str) -> str:
    """A request about working together names ducky roles as duckies: "a team of 2 Verse
    Coders" is a team of duckies, not Verse's game teams. Oct 21 2026: that hint sent a lead
    to verse/sys_teams. A request with no teamwork in it ("as a level designer…") is left be."""
    if not _TEAMWORK.search(text):
        return text
    try:
        from frontend.agent_profiles import list_agent_profiles_available

        names = {str(p.get("name") or "").strip() for p in list_agent_profiles_available()} - {""}
    except Exception:  # noqa: BLE001
        return text
    if not names:
        return text
    roles = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.sub(rf"\b(?:{roles})s?\b", lambda m: f"ducky {m.group(0)}", text, flags=re.I)


def _sent_by_ducky(text: str) -> bool:
    """A plan step or nudge Ducky sends ("[Ducky] Your task…"), or a workflow step's prompt.

    Oct 18 2026: every plan step got "read before acting" while its own task text said not
    to search skills, and agents opened skill sections step after step."""
    if text.startswith("[Ducky]"):
        return True
    try:
        from backend.automations.runner import _LIVE

        return _LIVE.get() is not None
    except Exception:  # noqa: BLE001
        return False


def skill_hint(user_text: str, *, conv: Any = None, limit: int = 3) -> str:
    """One short block naming the best sections for ``user_text``, or '' when none is clear.
    Only for the person's own requests: never for a message Ducky sends."""
    text = " ".join((user_text or "").split())
    if len(text) < MIN_TEXT or _sent_by_ducky(text):
        return ""
    text = _duckies_named(text)
    try:
        from backend.search import indexer, query

        indexer.maybe_refresh()
        ctx = context_for_conversation(conv) if conv is not None else query.chat_context()
        focus = _focus(text)
        rows = [r for r in query.search(focus[:QUERY_CHARS], limit=limit, context=ctx, refresh=False) if _relevant(r)]
        if not rows and focus != text:
            rows = [r for r in query.search(text[:QUERY_CHARS], limit=limit, context=ctx, refresh=False) if _relevant(r)]
    except Exception:  # noqa: BLE001 — a hint is optional
        _log.debug("search: skill hint failed", exc_info=True)
        return ""
    if not rows:
        return ""
    parts: list[str] = []
    used = 0
    for row in rows:
        piece = f"{_label(row)} — {row.get('read', '')}"
        if used + len(piece) > MAX_CHARS and parts:
            break
        parts.append(piece)
        used += len(piece)
    return "Skill sections that may help with this request (read one only if you need it): " + "; ".join(parts)
