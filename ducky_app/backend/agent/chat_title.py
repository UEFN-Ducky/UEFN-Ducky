"""The working agent names a new Ducky as its first tool call."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from frontend.chat_store import Conversation

PushFn = Callable[[dict[str, Any]], None]

_MAX_TITLE_WORDS = 4
_MAX_TITLE_CHARS = 40


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


def start_auto_title(
    conv: Conversation,
    first_user_message: str,
    *,
    push: PushFn | None = None,
    project_root: str | None = None,
) -> str:
    """Compatibility hook: the working agent now names itself in its first turn."""
    return ""


_NAMED_MAX = 512
# Chats seen with a real title. A named chat is never unnamed by a tool call, so the check
# that ran on every call (a full chat load, twice per ducky_call_tool) answers from here.
_named: dict[str, None] = {}


def _remember_named(conv_id: str) -> None:
    _named[conv_id] = None
    while len(_named) > _NAMED_MAX:
        _named.pop(next(iter(_named)), None)


def forget_name(conv_id: str) -> None:
    """The chat's title went back to a placeholder: look it up again."""
    _named.pop(conv_id, None)


def _chat_exists_titled(conv_id: str) -> tuple[bool, str]:
    """(exists, title) without reading the chat's messages."""
    from frontend.ui_web.chats.conversations import is_placeholder_title, load_conversation_meta

    conv = load_conversation_meta(conv_id)
    if conv is None:
        return False, ""
    title = str(conv.title or "")
    if is_placeholder_title(title):
        forget_name(conv_id)
    else:
        _remember_named(conv_id)
    return True, title


def self_naming_instruction(title: str, conv_id: str = "") -> str:
    """Ask the working model to choose its name, preserving names chosen by humans."""
    from backend.agent.run_context import current_mode

    if current_mode() == "ask":
        return ""
    if conv_id in _named:
        return ""
    from frontend.settings import PanelSettings
    from frontend.ui_web.chats.conversations import is_placeholder_title

    if conv_id:
        exists, found = _chat_exists_titled(conv_id)
        if exists:
            title = found
    if not PanelSettings.load().chat_auto_title or not is_placeholder_title(title):
        return ""
    return (
        "\n## Name this Ducky first\n"
        "Your FIRST tool call must be ducky_rename_self(title=...). Choose a concise "
        "2–4 word name describing the task in the user's first message. You choose "
        "the name yourself as part of this response; no other model names you. "
        "Send it in the same turn as your first real calls (it runs first), never as "
        "a turn of its own.\n"
    )


def require_self_name(name: str, conv_id: str) -> None:
    """Refuse task tools until the working agent has named its placeholder chat."""
    from backend.agent.run_context import current_mode

    if current_mode() == "ask":
        return
    if not conv_id or name == "ducky_rename_self" or conv_id in _named:
        return
    exists, title = _chat_exists_titled(conv_id)
    if exists and self_naming_instruction(title):
        raise ValueError("Call ducky_rename_self first with a 2–4 word task name, then retry this tool.")
