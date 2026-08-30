"""Epoch-based per-chat context summaries — append-only prompt view, fold only at high-water."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

CONTEXT_MEMORY_PREFIX = (
    "[Context memory — compressed older turns; full history remains saved in this chat]"
)
HIGH_WATER_FRACTION = 0.65
OUTPUT_HEADROOM_TOKENS = 4_096
_PRUNE_RESULT_CHARS = 160

_SUMMARY_SYSTEM = (
    "You compress chat history for an AI coding agent working in UEFN/Verse. "
    "Write a dense markdown digest that preserves: goals, decisions, file/device paths, "
    "errors fixed, open TODOs, and user preferences. "
    "Always carry the original user goal verbatim. "
    "Carry the current state of files and devices touched so the agent does not re-read them. "
    "Omit chit-chat and raw code dumps. "
    "Keep under ~800 words. Do not invent facts."
)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token). Good enough for thresholds/UI."""
    return max(0, (len(text or "") + 3) // 4)


def _json_tokens(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return estimate_tokens(value)
    try:
        return estimate_tokens(json.dumps(value, default=str))
    except Exception:
        return 8


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Count the same bytes the provider receives (full tool results, no 2k cap)."""
    total = 0
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            total += _json_tokens(content)
        for block in m.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            name = str(block.get("name") or "")
            total += estimate_tokens(name) + 8
            if block.get("arguments") is not None:
                total += _json_tokens(block.get("arguments"))
            text = block.get("text")
            if isinstance(text, str):
                total += estimate_tokens(text)
            result = block.get("result")
            if isinstance(result, dict):
                data = result.get("data")
                total += _json_tokens(data) if data is not None else _json_tokens(result)
            elif result is not None:
                total += _json_tokens(result)
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


def token_high_water(
    settings: Any = None,
    *,
    conv: Any = None,
    context_limit: int | None = None,
) -> int:
    """Token high-water: min(setting, ~65% of advertised model window)."""
    _, toks = compress_thresholds(settings)
    limit = context_limit
    if limit is None and conv is not None:
        try:
            from frontend.ui_web.context_tokens import context_limit_for_model

            limit = context_limit_for_model(
                str(getattr(conv, "model", "") or ""),
                str(getattr(conv, "provider", "") or ""),
            )
        except Exception:
            limit = None
    if limit is not None and int(limit) > 0:
        return min(toks, max(1000, int(int(limit) * HIGH_WATER_FRACTION)))
    return toks


def epoch_num_ctx(model_max: int, settings: Any = None) -> int:
    """One-shot Ollama num_ctx: high-water + output headroom, capped at model max."""
    max_ctx = max(1, int(model_max))
    hw = token_high_water(settings, context_limit=max_ctx)
    return min(max_ctx, hw + OUTPUT_HEADROOM_TOKENS)


def _snap_boundary(messages: list[dict[str, Any]], through: int) -> int:
    """Don't start the live tail on a tool-result message (keep call+result together)."""
    n = len(messages)
    idx = max(0, min(int(through), n))
    while idx < n and str(messages[idx].get("role") or "") == "tool":
        idx += 1
    return idx


def should_compress(
    conv: Any,
    *,
    settings: Any = None,
    force: bool = False,
) -> bool:
    """True at high-water (or force) when there is something to fold past keep_last.

    ``memory_auto_compress`` does not block this: when auto is off the runner
    still mechanical-epochs so the append-only view cannot grow past the window.
    """
    from frontend.settings import PanelSettings

    s = settings or PanelSettings.load()
    messages = list(getattr(conv, "messages", None) or [])
    keep = keep_last_from_settings(s)
    through = int(getattr(conv, "context_summary_through", 0) or 0)
    foldable_end = max(0, len(messages) - keep)
    if foldable_end <= 0:
        return False
    if force:
        return foldable_end > through or (
            foldable_end > 0 and not str(getattr(conv, "context_summary", "") or "").strip()
        )
    if foldable_end <= through:
        return False
    msg_thresh, _ = compress_thresholds(s)
    msg_thresh = max(msg_thresh, keep + 1)
    if (len(messages) - through) >= msg_thresh:
        return True
    view = build_compacted_messages(
        messages,
        keep_last=keep,
        context_summary=str(getattr(conv, "context_summary", "") or ""),
        context_summary_through=through,
    )
    return estimate_messages_tokens(view) >= token_high_water(s, conv=conv)


def _slice_for_summary(messages: list[dict[str, Any]], through: int, keep_last: int) -> list[dict[str, Any]]:
    end = _snap_boundary(messages, max(0, len(messages) - keep_last))
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


def context_memory_head(summary: str) -> dict[str, Any]:
    return {
        "role": "user",
        "content": f"{CONTEXT_MEMORY_PREFIX}\n\n{(summary or '').strip()}",
    }


def build_compacted_messages(
    messages: list[dict[str, Any]],
    *,
    keep_last: int = 20,
    context_summary: str = "",
    context_summary_through: int = 0,
) -> list[dict[str, Any]]:
    """Append-only prompt view: frozen epoch head + messages[through:]. Never mutates ``messages``.

    ``keep_last`` is the epoch low-water (used at compress time), not a sliding window.
    """
    del keep_last
    summary = (context_summary or "").strip()
    through = int(context_summary_through or 0)
    if through > 0 and summary:
        through = min(through, len(messages))
        return [context_memory_head(summary)] + list(messages[through:])
    return list(messages)


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


def _original_user_goal(messages: list[dict[str, Any]]) -> str:
    for m in messages or []:
        if str(m.get("role") or "") != "user":
            continue
        content = m.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()[:2000]
    return ""


def _prune_tool_results_for_summary(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stub large tool payloads in the fold slice only — does not mutate stored history."""
    out: list[dict[str, Any]] = []
    for m in messages:
        copied = dict(m)
        blocks = copied.get("blocks")
        if isinstance(blocks, list):
            pruned_blocks: list[Any] = []
            for block in blocks:
                if not isinstance(block, dict):
                    pruned_blocks.append(block)
                    continue
                b2 = dict(block)
                result = b2.get("result")
                if isinstance(result, dict) and isinstance(result.get("data"), str):
                    data = result["data"]
                    if len(data) > _PRUNE_RESULT_CHARS:
                        b2["result"] = {
                            **result,
                            "data": data[:_PRUNE_RESULT_CHARS] + "…[pruned]",
                        }
                pruned_blocks.append(b2)
            copied["blocks"] = pruned_blocks
        out.append(copied)
    return out


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
                stub = f"- {block['name']} ({block.get('status', '?')})"
                result = block.get("result")
                if isinstance(result, dict) and isinstance(result.get("data"), str):
                    stub += f": {result['data'][:120]}"
                tools.append(stub)
        if tools:
            parts.append("tools:\n" + "\n".join(tools[:40]))
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n…[truncated]"
    return text


async def _llm_digest(
    prior_summary: str,
    new_slice: list[dict[str, Any]],
    settings: Any,
    *,
    original_goal: str = "",
) -> str:
    from frontend.ui_web.plugin_llm import _complete_text

    from backend.agent.batch_backends import supports_batch_complete

    provider_name, model = _resolve_summary_model(settings)
    if not supports_batch_complete(provider_name):
        raise ValueError("Summary needs an API model (Settings → LLMs gateway)")
    user_parts = []
    if original_goal.strip():
        user_parts.append("## Original user goal (carry verbatim)\n" + original_goal.strip())
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
    goal = _original_user_goal(messages)
    pruned = _prune_tool_results_for_summary(new_slice)
    digest = ""
    method = "mechanical"
    if use_llm and pruned:
        try:
            digest = asyncio.run(_llm_digest(prior, pruned, s, original_goal=goal)).strip()
            method = "llm"
        except RuntimeError:
            try:
                loop = asyncio.new_event_loop()
                try:
                    digest = loop.run_until_complete(
                        _llm_digest(prior, pruned, s, original_goal=goal)
                    ).strip()
                    method = "llm"
                finally:
                    loop.close()
            except Exception:
                digest = ""
        except Exception:
            digest = ""
    if not digest:
        merged_src = pruned or messages[: max(0, len(messages) - keep)]
        bridge = mechanical_digest(merged_src)
        if goal:
            bridge = f"Original goal: {goal}\n\n{bridge}"
        digest = (prior.strip() + "\n\n" + bridge).strip() if prior.strip() else bridge
        method = "mechanical"

    new_through = _snap_boundary(messages, max(through, len(messages) - keep))
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
    view = build_compacted_messages(
        messages,
        keep_last=keep,
        context_summary=summary,
        context_summary_through=through,
    )
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
        "estimated_history_tokens": estimate_messages_tokens(view),
        "compress_recommended": should_compress(conv, settings=s, force=False),
        "auto_compress": bool(getattr(s, "memory_auto_compress", True)),
    }
