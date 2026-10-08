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


def self_naming_instruction(title: str, conv_id: str = "") -> str:
    """Ask the working model to choose its name, preserving names chosen by humans."""
    from frontend.settings import PanelSettings
    from frontend.ui_web.project_chats import is_placeholder_title, load_conversation

    if conv_id:
        conv = load_conversation(conv_id)
        if conv is not None:
            title = conv.title
    if not PanelSettings.load().chat_auto_title or not is_placeholder_title(title):
        return ""
    return (
        "\n## Name this Ducky first\n"
        "Your FIRST tool call must be ducky_rename_self(title=...). Choose a concise "
        "2–4 word name describing the task in the user's first message. You choose "
        "the name yourself as part of this response; no other model names you. "
        "Rename before researching, planning, asking questions or doing the task.\n"
    )


def require_self_name(name: str, conv_id: str) -> None:
    """Refuse task tools until the working agent has named its placeholder chat."""
    if not conv_id or name == "ducky_rename_self":
        return
    if self_naming_instruction("", conv_id):
        from frontend.ui_web.project_chats import load_conversation

        if load_conversation(conv_id) is not None:
            raise ValueError("Call ducky_rename_self first with a 2–4 word task name, then retry this tool.")
