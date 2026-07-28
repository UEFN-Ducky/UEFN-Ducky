"""Per-conversation context reset and omit management."""

from __future__ import annotations

import shutil
from typing import Any

from frontend.chat_store import Conversation
from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import load_conversation, save_conversation

from frontend.ui_web.context_omit import (
    CONTEXT_OMIT_IDS,
    VALID_SEGMENT_IDS,
    context_omit_set,
    omitted_for_ui,
)


def _normalize_segments(segments: list[str]) -> set[str]:
    out: set[str] = set()
    for seg in segments or []:
        key = str(seg or "").strip().lower()
        if not key:
            continue
        if key not in VALID_SEGMENT_IDS:
            raise ValueError(f"Unknown context segment: {seg!r}")
        out.add(key)
    if not out:
        raise ValueError("segments must not be empty")
    return out


def _project_root() -> str:
    return PanelSettings.load().uefn_project_root.strip()


def _ensure_omit_list(conv: Conversation) -> list[str]:
    if conv.context_omit is None:
        conv.context_omit = []
    return conv.context_omit


def _add_omit(conv: Conversation, segment_id: str) -> None:
    if segment_id not in CONTEXT_OMIT_IDS:
        return
    omit = _ensure_omit_list(conv)
    if segment_id not in omit:
        omit.append(segment_id)


def _remove_omit(conv: Conversation, segment_id: str) -> None:
    if not conv.context_omit:
        return
    conv.context_omit = [x for x in conv.context_omit if x != segment_id]
    if not conv.context_omit:
        conv.context_omit = None


def _guard_conversation_clear(conv_id: str) -> None:
    from frontend.ui_web.agent_modes import get_active_conv_id, is_agent_running

    if get_active_conv_id() == conv_id and is_agent_running(conv_id):
        raise ValueError(
            "Cannot clear conversation on the chat that is currently running. Cancel the agent first."
        )


def clear_conversation_messages(conv: Conversation, project_root: str | None = None) -> None:
    from frontend.ui_web.conversation_attachments import conversation_attachments_dir
    from frontend.ui_web.project_chats import get_conversations_dir
    from frontend.ui_web.token_usage import clear_token_usage
    from backend.agent.prompt_cache import invalidate_conv_cache

    conv.messages = []
    # Drop poisoned coding-agent resume sessions (e.g. huge screenshot base64).
    # Clearing messages alone left upstream_session_id and the next turn rehydrated
    # the broken Claude/Cursor session.
    conv.upstream_session_id = ""
    conv.coding_agent_stats = None
    clear_token_usage(conv)
    invalidate_conv_cache(conv)
    root = project_root if project_root is not None else _project_root()
    att_dir = conversation_attachments_dir(conv.id, root, get_conversations_dir(root))
    if att_dir.is_dir():
        try:
            shutil.rmtree(att_dir)
        except OSError:
            pass


def _clear_skill_data(conv: Conversation) -> None:
    from backend.agent.prompt_cache import invalidate_conv_cache
    from backend.skill import list_pack_ids

    conv.skill_snapshot = ""
    conv.disabled_packs = list(list_pack_ids())
    conv.enabled_packs = None
    conv.enabled_subskills = None
    conv.enabled_skills = None
    invalidate_conv_cache(conv)


def _restore_skill_data(conv: Conversation, project_root: str | None = None) -> None:
    from backend.skill import build_skill_prompt, resolve_conversation_selection, seed_skill_packs

    seed_skill_packs()
    from backend.mcp_plugins.store import seed_mcp_plugins

    seed_mcp_plugins()
    conv.disabled_packs = []
    settings = PanelSettings.load()
    sel = resolve_conversation_selection(conv, settings)
    conv.skill_snapshot = build_skill_prompt(sel)


def _expand_all(segments: set[str]) -> set[str]:
    if "all" not in segments:
        return segments
    expanded = set(segments)
    expanded.discard("all")
    expanded.add("conversation")
    expanded.update(CONTEXT_OMIT_IDS)
    return expanded


def _expand_mcp_tools(segments: set[str]) -> set[str]:
    """MCP Tools is one panel row; reset/restore touches both stored omit keys."""
    if "mcp_tools" not in segments:
        return segments
    expanded = set(segments)
    expanded.discard("mcp_tools")
    expanded.update({"tools", "mcp"})
    return expanded


def _usage_after(conv_id: str, model: str, *, mode: str = "agent") -> dict[str, Any]:
    from frontend.ui_web.context_tokens import compute_context_usage

    usage = compute_context_usage(conv_id, model, mode=mode)
    return {
        "used_tokens": usage.get("used_tokens", 0),
        "context_limit": usage.get("context_limit", 0),
        "omitted": usage.get("omitted", []),
    }


def reset_context(
    conv_id: str,
    segments: list[str],
    *,
    project_root: str | None = None,
    model: str = "",
    mode: str = "agent",
) -> dict[str, Any]:
    """Reset selected context segments on a conversation."""
    root = project_root if project_root is not None else _project_root()
    conv = load_conversation(conv_id.strip(), root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")

    seg_set = _expand_mcp_tools(_expand_all(_normalize_segments(segments)))
    cleared: list[str] = []

    from frontend.ui_web.agent_modes import cancel_agent, is_agent_running, wait_for_idle

    if is_agent_running(conv.id):
        cancel_agent(conv.id)
        wait_for_idle(conv.id, 2.0)

    if "conversation" in seg_set or "summarized" in seg_set:
        _guard_conversation_clear(conv.id)
        clear_conversation_messages(conv, root)
        cleared.extend(x for x in ("conversation", "summarized") if x in seg_set)

    if "skill" in seg_set:
        _add_omit(conv, "skill")
        _clear_skill_data(conv)
        cleared.append("skill")

    mcp_tools_cleared = False
    for omit_id in CONTEXT_OMIT_IDS:
        if omit_id in seg_set and omit_id != "skill":
            _add_omit(conv, omit_id)
            if omit_id in {"tools", "mcp"}:
                mcp_tools_cleared = True
            else:
                cleared.append(omit_id)
    if mcp_tools_cleared:
        cleared.append("mcp_tools")

    save_conversation(conv, root)
    notify_context_changed(conv.id)

    settings = PanelSettings.load()
    usage_model = model or conv.model or settings.agent_model or ""

    return {
        "ok": True,
        "conv_id": conv.id,
        "title": conv.title,
        "cleared": sorted(set(cleared)),
        "omitted": omitted_for_ui(context_omit_set(conv)),
        "usage_after": _usage_after(conv.id, usage_model, mode=mode),
    }


def restore_context(
    conv_id: str,
    segments: list[str],
    *,
    project_root: str | None = None,
    model: str = "",
    mode: str = "agent",
) -> dict[str, Any]:
    """Restore previously omitted static context segments."""
    root = project_root if project_root is not None else _project_root()
    conv = load_conversation(conv_id.strip(), root)
    if conv is None:
        raise ValueError(f"Conversation not found: {conv_id!r}")

    seg_set = _expand_mcp_tools(_normalize_segments(segments))
    if "all" in seg_set:
        seg_set = set(CONTEXT_OMIT_IDS)

    restored: list[str] = []
    mcp_tools_restored = False
    for omit_id in CONTEXT_OMIT_IDS:
        if omit_id not in seg_set:
            continue
        _remove_omit(conv, omit_id)
        if omit_id in {"tools", "mcp"}:
            mcp_tools_restored = True
        else:
            restored.append(omit_id)
        if omit_id == "skill":
            _restore_skill_data(conv, root)
    if mcp_tools_restored:
        restored.append("mcp_tools")

    save_conversation(conv, root)
    notify_context_changed(conv.id)

    settings = PanelSettings.load()
    usage_model = model or conv.model or settings.agent_model or ""

    return {
        "ok": True,
        "conv_id": conv.id,
        "title": conv.title,
        "restored": sorted(set(restored)),
        "omitted": omitted_for_ui(context_omit_set(conv)),
        "usage_after": _usage_after(conv.id, usage_model, mode=mode),
    }


def notify_context_changed(conv_id: str) -> None:
    from frontend.ui_web.agent_modes import notify_context_changed as _notify

    _notify(conv_id)
