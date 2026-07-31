"""Name a fresh ducky after the job it was asked to do (first message only).

Instant keyword role first so the sidebar renames immediately, then an optional
cheap-model refinement in the background while the agent already streams.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from frontend.chat_store import Conversation

log = logging.getLogger("uefn.chat_title")

PushFn = Callable[[dict[str, Any]], None]

_SYSTEM = (
    "You name a specialist AI teammate after the job the user is asking for. "
    "Reply with ONLY a 2-4 word job title in Title Case — no quotes, no punctuation, "
    "no explanation, no sentence. "
    "Examples: Level Designer, UI Programmer, NPC VFX Artist, Verse Gameplay Engineer, "
    "Material Artist, Animation Engineer."
)

_FALLBACK_ROLE = "General Helper"

# Checked in order — the first keyword hit wins, so put the narrow roles first.
_ROLE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("VFX Artist", ("vfx", "niagara", "particle", "emitter")),
    ("NPC AI Designer", ("npc", "enemy", "enemies", "behavior tree", " ai ", "spawner")),
    ("Material Artist", ("material", "shader", "texture")),
    ("Animation Engineer", ("animation", "animate", "rig", "retarget", "skeleton", "sequencer")),
    ("UI Programmer", ("ui ", "hud", "widget", "umg", "menu", "button", "screen")),
    ("Verse Programmer", ("verse", "script", "compile", "device", "code")),
    ("Level Designer", ("level", "blockout", "layout", "map", "terrain", "landscape", "island")),
    ("3D Modeler", ("blender", "mesh", "model", "sculpt", "uv ", "lod")),
    ("Audio Designer", ("audio", "sound", "music", "sfx")),
    ("Debug Engineer", ("test", "debug", "error", "crash", "broken", "fix ")),
)

_MAX_TITLE_WORDS = 4
_MAX_TITLE_CHARS = 40


def role_from_keywords(text: str) -> str:
    """Best-guess role for the first message without calling a model."""
    haystack = f" {(text or '').lower()} "
    for role, keywords in _ROLE_KEYWORDS:
        if any(word in haystack for word in keywords):
            return role
    return _FALLBACK_ROLE


def sanitize_role_title(raw: str) -> str:
    """Strip a model reply down to a bare Title Case job title, or '' when unusable."""
    text = (raw or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0]
    text = text.strip().strip("`*_")
    text = text.strip("\"'“”‘’")
    text = re.sub(r"[.,;:!?]+$", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    words = text.split(" ")[:_MAX_TITLE_WORDS]
    text = " ".join(words)[:_MAX_TITLE_CHARS].strip()
    if not text:
        return ""
    # Only re-case all-lowercase replies — title() would mangle "NPC VFX Artist".
    if text.islower():
        text = text.title()
    return text


def generate_role_title(text: str, model: str = "") -> str:
    """Ask a cheap API model for the role title. Returns '' when unavailable or on failure."""
    prompt = (text or "").strip()
    if not prompt:
        return ""
    try:
        import asyncio

        from backend.agent.batch_backends import supports_batch_complete
        from frontend.ui_web.plugin_llm import _complete_text, _resolve_api_model

        provider_name, model_id = _resolve_api_model(model=(model or "").strip())
        if not supports_batch_complete(provider_name):
            return ""
        reply = asyncio.run(
            _complete_text(
                provider_name=provider_name,
                model=model_id,
                system=_SYSTEM,
                user=f"Name the specialist for this request:\n\n{prompt[:2000]}",
                usage_agent="chat_title",
            )
        )
        return sanitize_role_title(reply)
    except Exception as exc:
        log.debug("chat title generation failed: %r", exc)
        return ""


def _notify(push: PushFn | None) -> None:
    # Deferred: agent_modes imports project_chats, which this module also touches.
    from frontend.ui_web.agent_modes import notify_chats_changed

    # No conv_id — a bare reload renames the row and tab. Passing one would make
    # the panel treat it as a brand-new chat and open a tab for it.
    notify_chats_changed(push=push)


def _refine(
    conv: Conversation,
    text: str,
    provisional: str,
    model: str,
    project_root: str | None,
    push: PushFn | None,
) -> None:
    from frontend.ui_web.project_chats import retitle_if_unchanged

    refined = generate_role_title(text, model)
    if not refined or refined == provisional:
        return
    if not retitle_if_unchanged(conv.id, provisional, refined, project_root):
        # Renamed by hand while the model was thinking — leave it alone.
        return
    # The caller keeps saving this same object while the turn streams, so it has to
    # carry the new title too or the next save_conversation puts the old one back.
    if (conv.title or "").strip() == provisional:
        conv.title = refined
    _notify(push)


def start_auto_title(
    conv: Conversation,
    first_user_message: str,
    *,
    push: PushFn | None = None,
    project_root: str | None = None,
) -> str:
    """Rename a still-unnamed chat after the role its first message asks for.

    Mutates ``conv`` in place. Returns the applied title, or '' when the name is kept.
    """
    from frontend.settings import PanelSettings
    from frontend.ui_web.project_chats import auto_title

    text = (first_user_message or "").strip()
    if not text:
        return ""
    settings = PanelSettings.load()
    if not getattr(settings, "chat_auto_title", True):
        return ""
    provisional = role_from_keywords(text)
    if not auto_title(conv, provisional, project_root):
        return ""
    _notify(push)

    model = str(getattr(settings, "chat_title_model", "") or "").strip()
    if model:
        threading.Thread(
            target=_refine,
            args=(conv, text, provisional, model, project_root, push),
            daemon=True,
            name="ducky-chat-title",
        ).start()
    return provisional
