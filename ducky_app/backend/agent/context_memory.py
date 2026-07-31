"""Rolling per-chat context summaries — shrink prompt view without deleting history."""

from __future__ import annotations

import asyncio
import re
from typing import Any

_SUMMARY_SYSTEM = (
    "You compress chat history for an AI coding agent working in UEFN/Verse. "
    "Write a dense markdown digest that preserves: goals, decisions, file/device paths, "
    "errors fixed, open TODOs, and user preferences. Omit chit-chat and raw code dumps. "
    "Keep under ~800 words. Do not invent facts."
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Good enough for thresholds/UI."""
    return max(0, (len(text or "") + 3) // 4)


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        for block in m.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            name = str(block.get("name") or "")
            total += estimate_tokens(name) + 8
            result = block.get("result")
            if isinstance(result, dict):
                data = result.get("data")
                if isinstance(data, str):
                    total += estimate_tokens(data[:2000])
    return total


def keep_last_from_settings(settings: Any = None) -> int:
    from frontend.settings import PanelSettings

    s = settings or PanelSettings.load()
    return max(1, min(100, int(getattr(s, "memory_keep_last_messages", 20) or 20)))


def compress_thresholds(settings: Any = None) -> tuple[int, int]:
    from frontend.settings import PanelSettings

    s = settings or PanelSettings.load()
    msgs = max(2, int(getattr(s, "memory_compress_messages", 40) or 40))
    toks = max(1000, int(getattr(s, "memory_compress_tokens", 80_000) or 80_000))
    return msgs, toks


def should_compress(
    conv: Any,
    *,
    settings: Any = None,
    force: bool = False,
) -> bool:
    """True when there are uncovered older turns past keep_last (or force)."""
    from frontend.settings import PanelSettings

    s = settings or PanelSettings.load()
    messages = list(getattr(conv, "messages", None) or [])
    keep = keep_last_from_settings(s)
    if len(messages) <= keep:
        return False
    through = int(getattr(conv, "context_summary_through", 0) or 0)
    uncovered_end = len(messages) - keep
    has_new = uncovered_end > through
    if force:
        return has_new or (uncovered_end > 0 and not str(getattr(conv, "context_summary", "") or "").strip())
    if not has_new:
        return False
    if not bool(getattr(s, "memory_auto_compress", True)):
        return False
    msg_thresh, tok_thresh = compress_thresholds(s)
    if len(messages) >= msg_thresh:
        return True
    est = estimate_messages_tokens(messages) + estimate_tokens(str(getattr(conv, "context_summary", "") or ""))
    return est >= tok_thresh


def _slice_for_summary(messages: list[dict[str, Any]], through: int, keep_last: int) -> list[dict[str, Any]]:
    end = max(0, len(messages) - keep_last)
    start = max(0, min(through, end))
    return messages[start:end]


def mechanical_digest(messages: list[dict[str, Any]], *, max_lines: int = 40) -> str:
    parts: list[str] = []
    for m in messages:
        role = str(m.get("role") or "?")
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(f"{role}: {content.strip()[:240]}")
        for block in m.get("blocks") or []:
            if isinstance(block, dict) and block.get("type") == "tool_call" and block.get("name"):
                parts.append(f"tool {block['name']}: {block.get('status', '?')}")
    lines = parts[-max_lines:]
    return "Earlier conversation (mechanical digest):\n" + "\n".join(lines)


def build_compacted_messages(
    messages: list[dict[str, Any]],
    *,
    keep_last: int = 20,
    context_summary: str = "",
    context_summary_through: int = 0,
) -> list[dict[str, Any]]:
    """Prompt view: optional rolling summary + live tail. Never mutates ``messages``."""
    if len(messages) <= keep_last:
        return list(messages)
    tail = messages[-keep_last:]
    summary = (context_summary or "").strip()
    through = int(context_summary_through or 0)
    uncovered = messages[through : max(0, len(messages) - keep_last)]
    chunks: list[str] = []
    if summary:
        chunks.append(summary)
    if uncovered:
        # Turns not yet in the rolling summary — mechanical bridge so nothing is dropped from the prompt view.
        chunks.append(mechanical_digest(uncovered, max_lines=30))
    if not chunks:
        chunks.append(mechanical_digest(messages[:-keep_last], max_lines=30))
    head = {
        "role": "user",
        "content": (
            "[Context memory — compressed older turns; full history remains saved in this chat]\n\n"
            + "\n\n".join(chunks)
        ),
    }
    return [head] + list(tail)


def _resolve_summary_model(settings: Any) -> tuple[str, str]:
    from frontend.ui_web.plugin_llm import _resolve_api_model

    preferred = str(getattr(settings, "memory_summary_model", "") or "").strip()
    if preferred:
        return _resolve_api_model(model=preferred)
    voice = str(getattr(settings, "voice_model", "") or "").strip()
    if voice:
        try:
            return _resolve_api_model(model=voice)
        except Exception:
            pass
    return _resolve_api_model(model="")


def _format_messages_for_llm(messages: list[dict[str, Any]], *, max_chars: int = 24_000) -> str:
    parts: list[str] = []
    for m in messages:
        role = str(m.get("role") or "?")
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            parts.append(f"## {role}\n{content.strip()[:4000]}")
        tools: list[str] = []
        for block in m.get("blocks") or []:
            if isinstance(block, dict) and block.get("type") == "tool_call" and block.get("name"):
                tools.append(f"- {block['name']} ({block.get('status', '?')})")
        if tools:
            parts.append("tools:\n" + "\n".join(tools[:40]))
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…[truncated]"
    return text


async def _llm_digest(prior_summary: str, new_slice: list[dict[str, Any]], settings: Any) -> str:
    from frontend.ui_web.plugin_llm import _complete_text

    from backend.agent.batch_backends import supports_batch_complete

    provider_name, model = _resolve_summary_model(settings)
    if not supports_batch_complete(provider_name):
        raise ValueError("Summary needs an API model (Settings → LLMs gateway)")
    user_parts = []
    if prior_summary.strip():
        user_parts.append("## Existing digest (merge / update)\n" + prior_summary.strip()[:6000])
    user_parts.append("## New turns to fold in\n" + _format_messages_for_llm(new_slice))
    user_parts.append("Return ONLY the updated digest markdown.")
    return await _complete_text(
        provider_name=provider_name,
        model=model,
        system=_SUMMARY_SYSTEM,
        user="\n\n".join(user_parts),
    )


def _maybe_extract_durable(
    digest: str,
    *,
    ducky_name: str,
    project_root: str,
) -> None:
    """Best-effort: stash a short durable note under agent/<ducky>/session-notes."""
    if not digest.strip() or not (ducky_name or "").strip():
        return
    # Pull bullet-like durable lines (conventions/paths), skip if none.
    bullets = [
        ln.strip()
        for ln in digest.splitlines()
        if re.match(r"^[-*]\s+", ln.strip())
        and any(k in ln.lower() for k in ("convention", "standard", "path", "device", "always", "never", "prefer"))
    ][:3]
    if not bullets:
        return
    try:
        from backend.memory.project import append_entry, slugify_entry_name

        who = ducky_name.strip()
        slug_who = slugify_entry_name(who).replace("/", "-")
        name = f"agent/{slug_who}/session-notes"
        append_entry(
            name,
            "\n".join(bullets),
            author=who,
            description=f"Durable notes auto-extracted for {who}",
            project_root=project_root,
        )
    except Exception:
        pass


def compress_conversation(
    conv: Any,
    *,
    settings: Any = None,
    project_root: str = "",
    force: bool = False,
    use_llm: bool = True,
) -> dict[str, Any]:
    """Advance rolling summary for ``conv``. Never deletes ``messages``."""
    from frontend.settings import PanelSettings
    from frontend.ui_web.project_chats import save_conversation

    s = settings or PanelSettings.load()
    messages = list(getattr(conv, "messages", None) or [])
    keep = keep_last_from_settings(s)
    if not should_compress(conv, settings=s, force=force) and not force:
        return {
            "ok": True,
            "compressed": False,
            "reason": "under_threshold",
            "message_count": len(messages),
            "context_summary_through": int(getattr(conv, "context_summary_through", 0) or 0),
            "context_summary_tokens": int(getattr(conv, "context_summary_tokens", 0) or 0),
        }

    through = int(getattr(conv, "context_summary_through", 0) or 0)
    new_slice = _slice_for_summary(messages, through, keep)
    if not new_slice and not force:
        return {
            "ok": True,
            "compressed": False,
            "reason": "nothing_new",
            "message_count": len(messages),
            "context_summary_through": through,
            "context_summary_tokens": int(getattr(conv, "context_summary_tokens", 0) or 0),
        }

    prior = str(getattr(conv, "context_summary", "") or "")
    digest = ""
    method = "mechanical"
    if use_llm and new_slice:
        try:
            digest = asyncio.run(_llm_digest(prior, new_slice, s)).strip()
            method = "llm"
        except RuntimeError:
            # Already in an event loop (agent worker) — use a fresh loop via to_thread pattern.
            try:
                loop = asyncio.new_event_loop()
                try:
                    digest = loop.run_until_complete(_llm_digest(prior, new_slice, s)).strip()
                    method = "llm"
                finally:
                    loop.close()
            except Exception:
                digest = ""
        except Exception:
            digest = ""
    if not digest:
        merged_src = new_slice or messages[: max(0, len(messages) - keep)]
        bridge = mechanical_digest(merged_src)
        digest = (prior.strip() + "\n\n" + bridge).strip() if prior.strip() else bridge
        method = "mechanical"

    new_through = max(through, len(messages) - keep)
    conv.context_summary = digest
    conv.context_summary_through = new_through
    conv.context_summary_tokens = estimate_tokens(digest)
    save_conversation(conv, project_root or None, touch_updated=False)

    _maybe_extract_durable(
        digest,
        ducky_name=str(getattr(conv, "ducky_name", "") or ""),
        project_root=project_root or str(getattr(s, "uefn_project_root", "") or ""),
    )

    return {
        "ok": True,
        "compressed": True,
        "method": method,
        "message_count": len(messages),
        "kept_live": keep,
        "context_summary_through": new_through,
        "context_summary_tokens": conv.context_summary_tokens,
        "summary_preview": digest[:400],
    }


def clear_conversation_summary(conv: Any, *, project_root: str = "") -> dict[str, Any]:
    from frontend.ui_web.project_chats import save_conversation

    conv.context_summary = ""
    conv.context_summary_through = 0
    conv.context_summary_tokens = 0
    save_conversation(conv, project_root or None, touch_updated=False)
    return {"ok": True, "cleared": True}


def chat_context_memory_status(conv: Any, *, settings: Any = None) -> dict[str, Any]:
    from frontend.settings import PanelSettings

    s = settings or PanelSettings.load()
    messages = list(getattr(conv, "messages", None) or [])
    keep = keep_last_from_settings(s)
    summary = str(getattr(conv, "context_summary", "") or "")
    through = int(getattr(conv, "context_summary_through", 0) or 0)
    tokens = int(getattr(conv, "context_summary_tokens", 0) or 0) or estimate_tokens(summary)
    return {
        "ok": True,
        "conv_id": str(getattr(conv, "id", "") or ""),
        "title": str(getattr(conv, "title", "") or ""),
        "ducky_name": str(getattr(conv, "ducky_name", "") or ""),
        "message_count": len(messages),
        "keep_last": keep,
        "context_summary": summary,
        "context_summary_through": through,
        "context_summary_tokens": tokens,
        "estimated_history_tokens": estimate_messages_tokens(messages),
        "compress_recommended": should_compress(conv, settings=s, force=False),
        "auto_compress": bool(getattr(s, "memory_auto_compress", True)),
    }
