"""Model context windows and token counting for the React panel."""

from __future__ import annotations

import asyncio
import json
import threading
from functools import lru_cache
from typing import Any

from frontend.settings import PANEL_LISTENER_PORT, PanelSettings, apply_workspace_env
from frontend.ui_web.project_chats import load_conversation
from frontend.ui_web.context_omit import (
    context_omit_set,
    mcp_tools_omitted,
    omitted_for_ui,
    tool_index_omitted,
)
from backend.agent.prompt import compact_messages, format_ducky_personality_block, get_system_prompt_parts
from backend.agent.tool_router import select_tools
from backend.agent.tools import list_mcp_tools, mcp_tool_to_anthropic, mcp_tool_to_gemini, mcp_tool_to_openai

_KEEP_LAST_MESSAGES = 20
# Flat per-image token heuristic (~1092x1092 image; Anthropic/OpenAI vision
# average). Attachments are not decoded here (hot per-keystroke path), so this
# is a fixed estimate rather than a pixel-accurate one.
_IMAGE_TOKEN_ESTIMATE = 1600


def _keep_last_messages(settings: PanelSettings | None = None) -> int:
    s = settings or PanelSettings.load()
    try:
        return max(1, min(100, int(getattr(s, "memory_keep_last_messages", _KEEP_LAST_MESSAGES) or _KEEP_LAST_MESSAGES)))
    except (TypeError, ValueError):
        return _KEEP_LAST_MESSAGES
# Cached listener project-match state (20s TTL inside listener_project_fields)
# so the per-keystroke estimate doesn't round-trip to the listener every call.
_LISTENER_PROJECT_CACHE: dict[str, Any] | None = None
# (provider, tool_name) -> schema token count; invalidated when MCP tool list changes.
_TOOL_SCHEMA_TOKEN_CACHE: dict[tuple[str, str, str], int] = {}
_TOOL_SCHEMA_TOTAL_CACHE: dict[tuple[str, str, tuple[str, ...]], int] = {}

_BREAKDOWN_COLORS: dict[str, str] = {
    "system": "#9ca3af",
    "personality": "#e879f9",
    "mcp_tools": "#a78bfa",
    "tools": "#a78bfa",
    "mcp": "#a78bfa",
    "tool_index": "#c4b5fd",
    "rules": "#4ade80",
    "skill": "#facc15",
    "subagents": "#60a5fa",
    "agent_internals": "#64748b",
    "summarized": "#f87171",
    "conversation": "#fb923c",
    "draft": "#38bdf8",
}

_BREAKDOWN_LABELS: dict[str, str] = {
    "system": "System prompt",
    "personality": "Ducky personality",
    "mcp_tools": "MCP Tools",
    "tools": "MCP Tools",
    "mcp": "MCP Tools",
    "tool_index": "Tool index (lazy)",
    "rules": "Rules",
    "skill": "Skills",
    "subagents": "Subagent definitions",
    "agent_internals": "Agent internals (est.)",
    "summarized": "Summarized conversation",
    "conversation": "Conversation",
    "draft": "Draft",
}




def context_limit_for_model(model: str, provider: str = "") -> int | None:
    """Context window from provider model API cache only. None when unknown — no invented sizes."""
    from backend.agent.model_fetch import get_model_info

    prov = (provider or "").strip().lower()
    if not prov:
        prov = (PanelSettings.load().agent_provider or "").strip().lower()
    info = get_model_info(prov, model)
    if info and info.context_limit:
        return int(info.context_limit)
    return None


@lru_cache(maxsize=32)
def _encoding_for_model(model: str, provider: str):
    try:
        import tiktoken
    except ImportError:
        return None
    m = (model or "").lower()
    p = (provider or "").lower()
    try:
        if p == "openai" or "gpt" in m or m.startswith("o1") or m.startswith("o3"):
            return tiktoken.encoding_for_model(model)
    except Exception:
        pass
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


def clear_tool_schema_token_cache() -> None:
    """Drop cached per-tool schema token counts (call when MCP tools change)."""
    _TOOL_SCHEMA_TOKEN_CACHE.clear()
    _TOOL_SCHEMA_TOTAL_CACHE.clear()
    try:
        from backend.agent.toolsets.tool_index import clear_tool_index_cache

        clear_tool_index_cache()
    except Exception:
        pass


def count_tokens(text: str, model: str = "", provider: str = "") -> int:
    raw = text or ""
    if not raw:
        return 0
    enc = _encoding_for_model(model, provider)
    if enc is not None:
        return len(enc.encode(raw))
    return max(1, int(len(raw) / 3.2))


def _message_text(message: dict[str, Any], *, tool_result_format: str = "toon") -> str:
    from backend.agent.serialization import format_tool_block_for_llm

    parts: list[str] = []
    content = message.get("content")
    if content:
        parts.append(str(content))
    for block in message.get("blocks") or []:
        if isinstance(block, dict) and block.get("type") == "tool_call":
            parts.append(
                format_tool_block_for_llm(
                    block,
                    fmt="json" if tool_result_format == "json" else "toon",  # type: ignore[arg-type]
                )
            )
        elif isinstance(block, dict):
            parts.append(json.dumps(block, ensure_ascii=False))
    return "\n".join(parts)


def _message_image_tokens(message: dict[str, Any]) -> int:
    """Flat token estimate for image attachments on a message (see _IMAGE_TOKEN_ESTIMATE)."""
    attachments = message.get("attachments")
    if not isinstance(attachments, list):
        return 0
    count = sum(
        1 for a in attachments if isinstance(a, dict) and str(a.get("kind") or "").strip().lower() == "image"
    )
    return count * _IMAGE_TOKEN_ESTIMATE


def _mode_suffix(mode: str) -> str:
    m = (mode or "agent").lower()
    if m == "ask":
        return "\n\n[Mode: Ask] Answer in plain language only. Do not call tools or propose tool use."
    if m == "plan":
        return (
            "\n\n[Mode: Plan] Discovery and planning only. Prefer read-only inspection tools. "
            "Do not modify the level, devices, or project files until the user confirms."
        )
    return ""


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content", "")
            if isinstance(content, str) and content.strip():
                return content
    return ""


def _provider_tool_schemas(provider: str, tools: list) -> list[dict[str, Any]]:
    schema = ""
    try:
        from backend.uefn_plugins.host import get_llm_provider_registration

        schema = str(
            (get_llm_provider_registration(provider) or {}).get("tool_schema") or ""
        ).strip().lower()
    except Exception:
        schema = ""
    if schema == "openai":
        return [mcp_tool_to_openai(t) for t in tools]
    if schema == "gemini":
        return [mcp_tool_to_gemini(t) for t in tools]
    return [mcp_tool_to_anthropic(t) for t in tools]


def _tool_server(name: str) -> str:
    """Human label for the server a tool belongs to (mirrors Claude's grouping)."""
    raw = name or ""
    if raw.startswith("mcp__"):
        parts = raw.split("__")
        if len(parts) >= 3 and parts[1]:
            return parts[1]
    if "_" in raw:
        return raw.split("_", 1)[0]
    return "core"


async def _tool_definition_report(
    messages: list[dict[str, Any]],
    *,
    mode: str,
    model: str,
    provider: str,
) -> tuple[int, list[dict[str, Any]]]:
    """Return (total tool-schema tokens, per-tool items) for the next call's toolset."""
    try:
        all_tools = await list_mcp_tools()
    except Exception:
        return 0, []
    plan_only = (mode or "agent").lower() == "plan"
    listener_online = (mode or "agent").lower() == "agent"
    selected = select_tools(
        all_tools,
        _last_user_message(messages),
        plan_only=plan_only,
        listener_online=listener_online,
        history=messages,
    )
    schemas = _provider_tool_schemas(provider, selected)
    # Header total counts the whole array once (commas/brackets included) so it
    # matches what is actually sent; per-tool items are for attribution only.
    name_key = tuple(sorted(t.name for t in selected))
    total_key = (provider, model, name_key)
    total = _TOOL_SCHEMA_TOTAL_CACHE.get(total_key)
    if total is None:
        total = count_tokens(json.dumps(schemas, ensure_ascii=False), model, provider)
        _TOOL_SCHEMA_TOTAL_CACHE[total_key] = total
    items = []
    for tool, schema in zip(selected, schemas):
        tok_key = (provider, model, tool.name)
        tokens = _TOOL_SCHEMA_TOKEN_CACHE.get(tok_key)
        if tokens is None:
            tokens = count_tokens(json.dumps(schema, ensure_ascii=False), model, provider)
            _TOOL_SCHEMA_TOKEN_CACHE[tok_key] = tokens
        items.append(
            {
                "label": tool.name,
                "sublabel": _tool_server(tool.name),
                "tokens": tokens,
                "content": json.dumps(schema, indent=2, ensure_ascii=False),
            }
        )
    items.sort(key=lambda it: it["tokens"], reverse=True)
    return total, items


# Stale tool-schema estimate so get_context_usage never holds the pywebview bridge
# for list_mcp_tools (can take seconds). Background refresh fills the cache.
_TOOL_REPORT_CACHE: dict[tuple[str, str, str], tuple[int, list[dict[str, Any]]]] = {}
_TOOL_REPORT_LOCK = threading.Lock()
_TOOL_REPORT_INFLIGHT: set[tuple[str, str, str]] = set()
_TOOL_REPORT_POOL: Any = None  # lazy ThreadPoolExecutor


def _tool_report_pool():
    import concurrent.futures

    global _TOOL_REPORT_POOL
    if _TOOL_REPORT_POOL is None:
        _TOOL_REPORT_POOL = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="ctx-tools"
        )
    return _TOOL_REPORT_POOL


def _tool_definition_report_sync(
    messages: list[dict[str, Any]],
    *,
    mode: str,
    model: str,
    provider: str,
) -> tuple[int, list[dict[str, Any]]]:
    import concurrent.futures

    cache_key = (provider or "", model or "", (mode or "agent").lower())
    with _TOOL_REPORT_LOCK:
        cached = _TOOL_REPORT_CACHE.get(cache_key)

    def _compute() -> tuple[int, list[dict[str, Any]]]:
        return asyncio.run(
            _tool_definition_report(messages, mode=mode, model=model, provider=provider)
        )

    def _store(result: tuple[int, list[dict[str, Any]]]) -> None:
        with _TOOL_REPORT_LOCK:
            _TOOL_REPORT_CACHE[cache_key] = result
            _TOOL_REPORT_INFLIGHT.discard(cache_key)

    # Never block the bridge >50ms — return stale/zeros and warm in background.
    future = _tool_report_pool().submit(_compute)
    try:
        result = future.result(timeout=0.05)
        _store(result)
        return result
    except concurrent.futures.TimeoutError:
        with _TOOL_REPORT_LOCK:
            warming = cache_key in _TOOL_REPORT_INFLIGHT
            if not warming:
                _TOOL_REPORT_INFLIGHT.add(cache_key)

        def _finish() -> None:
            try:
                _store(future.result(timeout=30))
            except Exception:
                with _TOOL_REPORT_LOCK:
                    _TOOL_REPORT_INFLIGHT.discard(cache_key)

        if not warming:
            threading.Thread(target=_finish, daemon=True, name="ctx-tools-warm").start()
        return cached if cached is not None else (0, [])


def _split_sections(
    text: str,
    *,
    model: str,
    provider: str,
    preamble_label: str,
) -> list[dict[str, Any]]:
    """Split a markdown block into per-``## heading`` items for the breakdown."""
    if not (text or "").strip():
        return []
    sections: list[tuple[str, list[str]]] = []
    label = preamble_label
    buf: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if buf:
                sections.append((label, buf))
            label = line[3:].strip() or preamble_label
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sections.append((label, buf))
    items: list[dict[str, Any]] = []
    for sec_label, sec_lines in sections:
        sec_text = "\n".join(sec_lines)
        tokens = count_tokens(sec_text, model, provider)
        if tokens > 0:
            items.append({"label": sec_label, "tokens": tokens, "content": sec_text})
    return items


def _message_item(message: dict[str, Any], index: int, tokens: int) -> dict[str, Any]:
    role = str(message.get("role") or "message").strip() or "message"
    text = " ".join((message.get("content") or "").split()) if isinstance(message.get("content"), str) else ""
    if not text:
        for block in message.get("blocks") or []:
            if isinstance(block, dict) and block.get("type") == "tool_call":
                text = f"→ {block.get('name') or 'tool call'}"
                break
    preview = text[:80] + ("…" if len(text) > 80 else "")
    return {"label": f"{role} #{index}", "sublabel": preview, "tokens": tokens}


def _conversation_report(
    messages: list[dict[str, Any]],
    *,
    model: str,
    provider: str,
    tool_result_format: str = "toon",
    keep_last: int | None = None,
    context_summary: str = "",
    context_summary_through: int = 0,
    context_summary_tokens: int = 0,
) -> tuple[int, int, list[dict[str, Any]], list[dict[str, Any]]]:
    """(summarized_tokens, conversation_tokens, summarized_items, conversation_items)."""
    if not messages:
        return 0, 0, [], []
    keep = keep_last if keep_last is not None else _keep_last_messages()
    compacted = compact_messages(
        list(messages),
        keep,
        context_summary=context_summary,
        context_summary_through=context_summary_through,
    )
    if len(compacted) < len(messages):
        summarized = count_tokens(
            _message_text(compacted[0], tool_result_format=tool_result_format),
            model,
            provider,
        )
        if not summarized and context_summary_tokens:
            summarized = int(context_summary_tokens)
        conv_msgs = compacted[1:]
        summarized_items: list[dict[str, Any]] = (
            [{"label": "Rolling context summary", "tokens": summarized}] if summarized else []
        )
    else:
        summarized = 0
        conv_msgs = messages
        summarized_items = []
    conversation = 0
    conv_items: list[dict[str, Any]] = []
    for i, m in enumerate(conv_msgs, start=1):
        tokens = count_tokens(_message_text(m, tool_result_format=tool_result_format), model, provider)
        tokens += _message_image_tokens(m)
        conversation += tokens
        if tokens > 0:
            conv_items.append(_message_item(m, i, tokens))
    return summarized, conversation, summarized_items, conv_items


def _conversation_token_split(
    messages: list[dict[str, Any]],
    *,
    model: str,
    provider: str,
    tool_result_format: str = "toon",
    keep_last: int | None = None,
    context_summary: str = "",
    context_summary_through: int = 0,
) -> tuple[int, int]:
    """Return (summarized_tokens, conversation_tokens) mirroring agent compaction."""
    summarized, conversation, _, _ = _conversation_report(
        messages,
        model=model,
        provider=provider,
        tool_result_format=tool_result_format,
        keep_last=keep_last,
        context_summary=context_summary,
        context_summary_through=context_summary_through,
    )
    return summarized, conversation


def _breakdown_item_omitted(item_id: str, omit: frozenset[str]) -> bool:
    if item_id == "mcp_tools":
        return mcp_tools_omitted(omit)
    return item_id in omit


# Segments that make up the frozen (VS Code-style) prefix — mcp/skill/personality/rules
# are stable across turns once frozen, so they're the ones that actually get cached.
_FROZEN_PREFIX_IDS = frozenset({"mcp_tools", "skill", "personality", "rules"})


def _cache_mode_for_provider(provider: str, settings: PanelSettings) -> str | None:
    """How the frozen prefix is cached for the active provider, or None if not cached.

    Mode comes from the gateway plugin registration (``cache_mode``):
    "cached" / "implicit" / "local". Explicit marker providers still honor
    ``provider_cache_markers_enabled``.
    """
    if not settings.freeze_prompt_prefix:
        return None
    p = (provider or "").strip().lower()
    mode = ""
    try:
        from backend.uefn_plugins.host import get_llm_provider_registration

        reg = get_llm_provider_registration(p) or {}
        mode = str(reg.get("cache_mode") or "").strip().lower()
    except Exception:
        mode = ""
    if mode == "local":
        return "local"
    if mode == "implicit":
        return "implicit"
    if mode == "cached":
        from backend.agent.providers.cache_utils import provider_cache_markers_enabled

        if provider_cache_markers_enabled(
            p, fallback=bool(settings.prompt_caching_enabled)
        ):
            return "cached"
        return None
    return None


def _breakdown_item(
    item_id: str,
    tokens: int,
    items: list[dict[str, Any]] | None = None,
    content: str | None = None,
    *,
    active_tokens: int | None = None,
    gated: bool = False,
    cache_mode: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": item_id,
        "label": _BREAKDOWN_LABELS[item_id],
        "tokens": max(0, int(tokens)),
        "color": _BREAKDOWN_COLORS[item_id],
    }
    if items:
        entry["items"] = items
    if cache_mode:
        entry["cache_mode"] = cache_mode
    if content and content.strip():
        entry["content"] = content
    if gated:
        entry["gated"] = True
        entry["active_tokens"] = max(0, int(active_tokens or 0))
    return entry


def _breakdown_used_tokens(item: dict[str, Any]) -> int:
    if item.get("gated"):
        return int(item.get("active_tokens") or 0)
    return int(item["tokens"])


def _external_agent_token_provider(agent_id: str) -> str:
    """Provider id used for tiktoken / tool-schema estimates for a coding agent."""
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        reg = get_coding_agent_registration(agent_id) or {}
        tp = str(reg.get("token_provider") or "").strip().lower()
        if tp:
            return tp
    except Exception:
        pass
    return agent_id or ""


def _coding_agent_context_limit(agent_id: str, model: str) -> int | None:
    """Context window from the model catalog only (no hardcoded provider defaults)."""
    m = (model or "").lower()
    provider = _external_agent_token_provider(agent_id)
    if provider:
        return context_limit_for_model(model, provider)
    if "claude" in m or "sonnet" in m or "opus" in m or "haiku" in m:
        return context_limit_for_model(model, "anthropic")
    if "gpt" in m or "codex" in m or m.startswith("o"):
        return context_limit_for_model(model, "openai")
    return context_limit_for_model(model, "")


def _deployed_skill_token_report(
    agent_id: str,
    *,
    model: str,
    provider: str,
    include_content: bool,
) -> tuple[int, list[dict[str, Any]], list[str]]:
    """Count tokens in deployed skill-pack SKILL.md files the coding agent can load."""
    from pathlib import Path

    try:
        from backend.agent.coding_agents.mcp_inject import deployed_skill_packs

        root, names = deployed_skill_packs(agent_id)
    except Exception:
        return 0, [], []
    if not root or not names:
        return 0, [], names or []
    total = 0
    items: list[dict[str, Any]] = []
    for name in names:
        path = Path(root) / name / "SKILL.md"
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        tokens = count_tokens(text, model, provider)
        if tokens <= 0:
            continue
        total += tokens
        entry: dict[str, Any] = {"label": name, "tokens": tokens}
        if include_content:
            entry["content"] = text
        items.append(entry)
    items.sort(key=lambda it: it["tokens"], reverse=True)
    return total, items, names


def _external_agent_breakdown(
    conv: Any,
    settings: PanelSettings,
    agent_id: str,
    *,
    model: str,
    context_tokens: int,
    include_content: bool,
) -> list[dict[str, Any]]:
    """Best-effort breakdown of what Ducky controls plus an internals remainder.

    Meter ``used_tokens`` stays agent-reported; these rows are estimates so the
    Context Usage panel is not a single opaque "Conversation" blob.
    """
    provider = _external_agent_token_provider(agent_id)
    messages = list(getattr(conv, "messages", None) or [])
    fmt = str(getattr(settings, "tool_result_format", None) or "toon")

    mcp_tokens, mcp_items = _tool_definition_report_sync(
        messages, mode="agent", model=model, provider=provider
    )
    skill_tokens, skill_items, _ = _deployed_skill_token_report(
        agent_id, model=model, provider=provider, include_content=include_content
    )
    summarized_tokens, conversation_tokens, summarized_items, conversation_items = _conversation_report(
        messages,
        model=model,
        provider=provider,
        tool_result_format=fmt,
        keep_last=_keep_last_messages(settings),
        context_summary=str(getattr(conv, "context_summary", "") or ""),
        context_summary_through=int(getattr(conv, "context_summary_through", 0) or 0),
        context_summary_tokens=int(getattr(conv, "context_summary_tokens", 0) or 0),
    )

    known = mcp_tokens + skill_tokens + summarized_tokens + conversation_tokens
    internals = max(0, int(context_tokens) - known) if context_tokens > 0 else 0

    est_note = (
        "Estimated from what Ducky injects / stores for this chat — not the "
        "coding agent's private system prompt."
        if include_content
        else None
    )
    breakdown: list[dict[str, Any]] = []
    if mcp_tokens > 0:
        breakdown.append(
            _breakdown_item(
                "mcp_tools",
                mcp_tokens,
                mcp_items if include_content else None,
                content=est_note,
            )
        )
    if skill_tokens > 0:
        breakdown.append(
            _breakdown_item(
                "skill",
                skill_tokens,
                skill_items if include_content else None,
                content=est_note,
            )
        )
    if summarized_tokens > 0:
        breakdown.append(
            _breakdown_item(
                "summarized",
                summarized_tokens,
                summarized_items if include_content else None,
                content=est_note,
            )
        )
    breakdown.append(
        _breakdown_item(
            "conversation",
            conversation_tokens if (conversation_tokens or known or internals) else context_tokens,
            conversation_items if include_content else None,
            content=(
                "Chat history Ducky keeps for this session (estimate)."
                if include_content
                else None
            ),
        )
    )
    if internals > 0:
        breakdown.append(
            _breakdown_item(
                "agent_internals",
                internals,
                content=(
                    "Remainder of the agent's reported context window after Ducky-known "
                    "segments (CLI system prompt, its own tools, etc.)."
                    if include_content
                    else None
                ),
            )
        )
    return breakdown


def _external_agent_report(
    conv: Any,
    settings: PanelSettings,
    agent_id: str,
    model: str,
    *,
    include_content: bool,
) -> dict[str, Any]:
    """Real backend info for a chat running on Claude Code / Codex / Cursor.

    Meter and API totals use the agent's reported usage. Breakdown rows are a
    best-effort estimate of Ducky-controlled segments (MCP bridge tools, deployed
    skills, conversation) plus an "agent internals" remainder.
    """
    from backend.agent.coding_agents.base import CODING_AGENT_LABELS
    from backend.agent.coding_agents.settings_helpers import coding_agent_cfg
    from frontend.ui_web.token_usage import resolve_context_window_tokens, token_usage_report
    from backend.agent.model_pricing import call_cost_usd, usage_cost_report

    stats = conv.coding_agent_stats if isinstance(conv.coding_agent_stats, dict) else {}
    cfg = coding_agent_cfg(settings, agent_id)
    stored_sid = (conv.upstream_session_id or "").strip()
    session_active = stored_sid.startswith(f"{agent_id}:") and len(stored_sid) > len(agent_id) + 1

    real_model = str(stats.get("model") or model or "default").strip()
    stored_context = int(stats.get("context_tokens") or 0)
    stored_limit = int(stats.get("context_limit") or 0)
    catalog_limit = _coding_agent_context_limit(agent_id, real_model) or 0
    limit = stored_limit if stored_limit > 0 else catalog_limit
    num_turns = int(stats.get("num_turns") or 0)

    agent_info: dict[str, Any] = {
        "coding_agent": agent_id,
        "label": CODING_AGENT_LABELS.get(agent_id, agent_id),
        "model": real_model,
        "enabled": bool(cfg.get("enabled", True)),
        "session_active": session_active,
        "num_turns": num_turns,
        "context_tokens": stored_context,
        "has_run": bool(stats),
    }
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        reg = get_coding_agent_registration(agent_id) or {}
        defaults = reg.get("settings_defaults") or {}
        if "permission_mode" in defaults:
            agent_info["permission_mode"] = str(
                cfg.get("permission_mode") or defaults.get("permission_mode") or "acceptEdits"
            )
    except Exception:
        reg = {}

    # The availability/login probe can spawn a subprocess (claude), so only run
    # it on the panel's on-open fetch, never the per-keystroke hot path.
    if include_content:
        try:
            from backend.agent.coding_agents.base import get_adapter

            adapter = get_adapter(agent_id)
            if adapter is not None:
                info = adapter.detect(settings)
                agent_info["available"] = bool(info.available)
                agent_info["status"] = info.status
                low = (info.status or "").lower()
                marker = str((reg or {}).get("login_status_ok") or "").strip().lower()
                if marker:
                    agent_info["logged_in"] = marker in low and f"not {marker}" not in low
        except Exception:
            pass
        try:
            from backend.agent.coding_agents.mcp_inject import deployed_skill_packs

            _, skill_names = deployed_skill_packs(agent_id)
            agent_info["skills"] = skill_names
        except Exception:
            agent_info["skills"] = []

    usage_report = token_usage_report(conv)
    raw_calls = list(usage_report.get("calls") or [])
    last_call = raw_calls[-1] if raw_calls else {}
    context_tokens = resolve_context_window_tokens(
        stored_context_tokens=stored_context,
        input_tokens=int(last_call.get("input_tokens") or 0),
        cache_read_tokens=int(last_call.get("cache_read_tokens") or 0),
        cache_write_tokens=int(last_call.get("cache_write_tokens") or 0),
        num_turns=num_turns,
    )
    agent_info["context_tokens"] = context_tokens
    cost_usd = usage_cost_report(agent_id, real_model, raw_calls) if raw_calls else None
    priced_calls = [
        {**call, "cost_usd": call_cost_usd(call, fallback_provider=agent_id, fallback_model=real_model)}
        for call in raw_calls
    ]

    try:
        breakdown = _external_agent_breakdown(
            conv,
            settings,
            agent_id,
            model=real_model,
            context_tokens=context_tokens,
            include_content=include_content,
        )
    except Exception:
        breakdown = [
            _breakdown_item(
                "conversation",
                context_tokens,
                content=(
                    "This chat runs on an external coding agent. Breakdown estimate unavailable."
                    if include_content
                    else None
                ),
            )
        ]
    return {
        "used_tokens": context_tokens,
        "context_limit": limit,
        "input_tokens": int(usage_report.get("total_input") or 0),
        "output_tokens": int(usage_report.get("total_output") or 0),
        "total_tokens": int(usage_report.get("total_tokens") or 0),
        "total_cache_read": int(usage_report.get("total_cache_read") or 0),
        "total_cache_write": int(usage_report.get("total_cache_write") or 0),
        "cache_hit_rate": float(usage_report.get("cache_hit_rate") or 0),
        "cache_hit_rate_cumulative": float(usage_report.get("cache_hit_rate_cumulative") or 0),
        "call_count": int(usage_report.get("call_count") or 0),
        "calls": priced_calls,
        "cost_usd": cost_usd,
        "breakdown": breakdown,
        "omitted": [],
        "agent_info": agent_info,
    }


def compute_context_report(
    conv_id: str,
    model: str,
    *,
    mode: str = "agent",
    draft_text: str = "",
    include_content: bool = False,
) -> dict[str, Any]:
    """Estimate prompt tokens for the next model call with category breakdown.

    When ``include_content`` is set, each breakdown item/sub-item carries the
    exact text it accounts for (``content``) so the UI can show it on click.
    It is off by default because the report is refreshed on every keystroke —
    only the panel's on-open fetch asks for the (much larger) content payload.

    A chat backed by an external coding agent short-circuits to a real-backend
    report (its own model/session/usage), since the embedded prompt breakdown
    would not reflect what that agent actually sent.
    """
    settings = PanelSettings.load()
    conv = load_conversation(conv_id)

    # This chat's gateway + model — never Settings → agent_provider (that was
    # showing OpenAI's 400k while the composer ran ollama:qwen…).
    turn_model = (model or "").strip()
    if not turn_model and conv is not None:
        turn_model = (getattr(conv, "model", None) or "").strip()
    if not turn_model:
        turn_model = (settings.agent_model or "").strip()
    model = turn_model

    provider = ""
    if conv is not None:
        provider = (getattr(conv, "provider", None) or "").strip().lower()
    if not provider and model:
        try:
            from backend.agent.model_pricing import resolve_provider_for_model

            provider = resolve_provider_for_model(model, settings.agent_provider or "")
        except Exception:
            provider = (settings.agent_provider or "").strip().lower()
    if not provider:
        provider = (settings.agent_provider or "").strip().lower()

    # Catalog / API only — 0 when unknown (no invented provider defaults).
    limit = context_limit_for_model(model, provider) or 0

    if not conv:
        return {
            "used_tokens": 0,
            "context_limit": limit,
            "input_tokens": 0,
            "output_tokens": 0,
            "breakdown": [],
        }

    from backend.agent.coding_agents.base import normalize_coding_agent

    coding_agent = normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky")
    if coding_agent != "ducky":
        return _external_agent_report(
            conv, settings, coding_agent, model, include_content=include_content
        )

    apply_workspace_env(settings.uefn_project_root)
    from backend.skills.store import build_skill_prompt, resolve_conversation_selection, seed_skill_packs

    seed_skill_packs()
    from backend.mcp_plugins.store import seed_mcp_plugins

    seed_mcp_plugins()
    omit = context_omit_set(conv)
    draft = (draft_text or "").strip()
    skill_enabled = ""
    try:
        if "skill" not in omit:
            if conv.skill_snapshot.strip():
                skill_enabled = conv.skill_snapshot
            else:
                sel = resolve_conversation_selection(conv, settings)
                skill_enabled = build_skill_prompt(sel)
    except FileNotFoundError:
        skill_enabled = "" if "skill" in omit else (conv.skill_snapshot or "")

    # Runner sends skills only when verse/device intent matches (or prefix is frozen).
    skill_active = skill_enabled
    skill_gated = False
    if skill_active and (mode or "agent").lower() != "plan" and "skill" not in omit:
        from backend.agent.prompt_cache import snapshot_has_block
        from backend.agent.toolsets.intents import skill_intent_matched

        if not skill_intent_matched(draft, conv.messages) and not snapshot_has_block(conv, "skill"):
            skill_active = ""
            skill_gated = bool(skill_enabled.strip())

    from backend.bridge import listener_get_health

    health = listener_get_health(PANEL_LISTENER_PORT)
    listener_online = health is not None and health.get("status") == "ok"
    listener_wedged = bool(health and health.get("wedged"))

    uefn_project_name = ""
    project_match = True
    if listener_online and not listener_wedged:
        global _LISTENER_PROJECT_CACHE
        from backend.bridge.status import listener_project_fields

        _, uefn_project_name, project_match, _LISTENER_PROJECT_CACHE = listener_project_fields(
            PANEL_LISTENER_PORT,
            selected_project_root=settings.uefn_project_root,
            cache=_LISTENER_PROJECT_CACHE,
        )
    elif _LISTENER_PROJECT_CACHE:
        uefn_project_name = str(_LISTENER_PROJECT_CACHE.get("uefn_project_name") or "")
        project_match = bool(_LISTENER_PROJECT_CACHE.get("project_match", True))

    mode_suffix = _mode_suffix(mode)
    parts = get_system_prompt_parts(
        listener_online=listener_online,
        listener_port=PANEL_LISTENER_PORT,
        project_root=settings.uefn_project_root,
        skill_text=skill_active,
        mode_suffix=mode_suffix,
        listener_wedged=listener_wedged,
        ducky_name=(conv.ducky_name or conv.title or "").strip(),
        ducky_personality=conv.ducky_personality or "",
        uefn_project_name=uefn_project_name,
        project_match=project_match,
    )

    intro = (
        "You are the UEFN Ducky agent embedded in UEFN-Ducky.exe. "
        "You edit Fortnite Creative / UEFN projects using MCP tools only.\n\n"
    )
    items_by_id: dict[str, list[dict[str, Any]]] = {}

    contents_by_id: dict[str, str] = {}

    system_tokens = 0
    if "system" not in omit:
        runtime_text = intro + parts["runtime"]
        runtime_tokens = count_tokens(runtime_text, model, provider)
        system_tokens = runtime_tokens
        system_items = [{"label": "Runtime context", "tokens": runtime_tokens, "content": runtime_text}]
        if parts["memory"].strip():
            memory_tokens = count_tokens(parts["memory"], model, provider)
            system_tokens += memory_tokens
            system_items.append(
                {"label": "Project memory", "tokens": memory_tokens, "content": parts["memory"]}
            )
        # Cache drift: when the frozen prefix (mcp/skill/rules/personality) has
        # snapshotted stale content but the live version changed, the runner
        # appends a "Context updates" block to the dynamic (uncached) suffix on
        # the next call. Count it here so the estimate matches what's sent.
        if bool(settings.freeze_prompt_prefix):
            from backend.agent.prompt_cache import _blocks_from_parts, drift_block, frozen_prefix_for_conv

            live_blocks = _blocks_from_parts(parts, omit=omit)
            frozen_blocks, _ = frozen_prefix_for_conv(conv, parts, omit=omit, freeze_enabled=True)
            drift_text = drift_block(live_blocks, frozen_blocks)
            if drift_text.strip():
                drift_wrapped = f"\n## Context updates (not in frozen system prompt)\n{drift_text}\n"
                drift_tokens = count_tokens(drift_wrapped, model, provider)
                system_tokens += drift_tokens
                system_items.append(
                    {"label": "Cache drift (live vs frozen prefix)", "tokens": drift_tokens, "content": drift_wrapped}
                )
        items_by_id["system"] = system_items

    personality_tokens = 0
    if "personality" not in omit:
        personality_block = format_ducky_personality_block(conv.title or "", conv.ducky_personality or "")
        if personality_block.strip():
            personality_tokens = count_tokens(personality_block, model, provider)
            contents_by_id["personality"] = personality_block

    # Header wrappers ("## MCP server instructions" / "## UEFN operator skill …")
    # are added around these blocks at assembly time — count them too so totals
    # match the exact bytes sent, not just the raw block text.
    mcp_tools_omitted_flag = mcp_tools_omitted(omit)
    mcp_wrapped = f"\n## MCP server instructions\n{parts['mcp']}\n" if parts["mcp"].strip() else parts["mcp"]
    mcp_tokens = 0 if mcp_tools_omitted_flag else count_tokens(mcp_wrapped, model, provider)
    tool_index_text = (parts.get("tool_index") or "").strip()
    tool_index_omitted_flag = tool_index_omitted(omit)
    tool_index_tokens = (
        0 if tool_index_omitted_flag or not tool_index_text else count_tokens(tool_index_text, model, provider)
    )
    _SKILL_HEADER = "\n## UEFN operator skill (follow exactly for wiring and Verse devices)\n"
    skill_wrapped_enabled = f"{_SKILL_HEADER}{skill_enabled}\n" if skill_enabled.strip() else skill_enabled
    skill_wrapped_active = f"{_SKILL_HEADER}{skill_active}\n" if skill_active.strip() else skill_active
    skill_tokens_enabled = 0 if "skill" in omit else count_tokens(skill_wrapped_enabled, model, provider)
    skill_tokens_active = 0 if "skill" in omit else count_tokens(skill_wrapped_active, model, provider)
    rules_tokens = 0 if "rules" in omit else count_tokens(parts["rules"], model, provider)
    if "skill" not in omit:
        items_by_id["skill"] = _split_sections(
            skill_enabled, model=model, provider=provider, preamble_label="Skill preamble"
        )
    if "rules" not in omit:
        rules_items = _split_sections(
            parts.get("offline_rules") or "",
            model=model,
            provider=provider,
            preamble_label="Listener/offline rules",
        )
        rules_items += _split_sections(
            parts.get("static_rules") or "", model=model, provider=provider, preamble_label="Rules"
        )
        items_by_id["rules"] = rules_items
    tool_tokens = 0
    tool_items: list[dict[str, Any]] = []
    if not mcp_tools_omitted_flag:
        try:
            tool_tokens, tool_items = _tool_definition_report_sync(
                conv.messages,
                mode=mode,
                model=model,
                provider=provider,
            )
        except Exception:
            tool_tokens, tool_items = 0, []
    mcp_tools_items: list[dict[str, Any]] = []
    if not mcp_tools_omitted_flag:
        for sec in _split_sections(
            parts["mcp"], model=model, provider=provider, preamble_label="Server instructions"
        ):
            mcp_tools_items.append({**sec, "sublabel": "Server instructions"})
        mcp_tools_items.extend(tool_items)
        if not mcp_tools_items and (tool_tokens > 0 or mcp_tokens > 0):
            if mcp_tokens > 0:
                mcp_tools_items.append(
                    {"label": "Server instructions", "tokens": mcp_tokens, "content": parts["mcp"]}
                )
            if tool_tokens > 0:
                mcp_tools_items.append({"label": "Tool definitions (floor)", "tokens": tool_tokens})
    if tool_index_tokens > 0 and not tool_index_omitted_flag:
        mcp_tools_items.append(
            {
                "label": "Tool index (names + blurbs; schemas via ducky_get_tools)",
                "tokens": tool_index_tokens,
                "content": tool_index_text if include_content else None,
            }
        )
    if mcp_tools_items:
        items_by_id["mcp_tools"] = mcp_tools_items
    summarized_tokens, conversation_tokens, summarized_items, conversation_items = _conversation_report(
        conv.messages,
        model=model,
        provider=provider,
        tool_result_format=settings.tool_result_format or "toon",
        keep_last=_keep_last_messages(settings),
        context_summary=str(getattr(conv, "context_summary", "") or ""),
        context_summary_through=int(getattr(conv, "context_summary_through", 0) or 0),
        context_summary_tokens=int(getattr(conv, "context_summary_tokens", 0) or 0),
    )
    items_by_id["summarized"] = summarized_items
    items_by_id["conversation"] = conversation_items

    draft_tokens = count_tokens(draft, model, provider) if draft else 0

    from frontend.ui_web.token_usage import token_usage_report
    from backend.agent.model_pricing import call_cost_usd, estimate_cost_usd, usage_cost_report

    usage_report = token_usage_report(conv)
    input_reported = int(usage_report.get("total_input") or 0)
    output_reported = int(usage_report.get("total_output") or 0)

    # Each call is priced with the provider/model recorded when it was made;
    # current selection is only the fallback. None = unknown pricing, UI hides the row.
    raw_calls = list(usage_report.get("calls") or [])
    if raw_calls:
        cost_usd = usage_cost_report(provider, model, raw_calls)
    elif input_reported or output_reported:
        # Legacy conversations without a per-call log: estimate from totals.
        cost_usd = estimate_cost_usd(
            provider,
            model,
            input_tokens=input_reported,
            output_tokens=output_reported,
            cache_read_tokens=int(usage_report.get("total_cache_read") or 0),
            cache_write_tokens=int(usage_report.get("total_cache_write") or 0),
        )
    else:
        cost_usd = None
    priced_calls = [
        {**call, "cost_usd": call_cost_usd(call, fallback_provider=provider, fallback_model=model)}
        for call in raw_calls
    ]
    if input_reported == 0 and output_reported == 0:
        for message in conv.messages:
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            input_reported += int(usage.get("input_tokens") or 0)
            output_reported += int(usage.get("output_tokens") or 0)

    # Floor schemas + server instructions + compact index (full schemas deferred).
    mcp_tools_tokens = tool_tokens + mcp_tokens + tool_index_tokens

    breakdown: list[dict[str, Any]] = []
    for item_id, tokens in (
        ("system", system_tokens),
        ("personality", personality_tokens),
        ("mcp_tools", mcp_tools_tokens),
        ("rules", rules_tokens),
        ("skill", skill_tokens_enabled),
        ("summarized", summarized_tokens),
        ("conversation", conversation_tokens),
        ("draft", draft_tokens),
    ):
        omitted_item = _breakdown_item_omitted(item_id, omit)
        # Skills row always shows enabled selection; gated skills use active_tokens in the total.
        if tokens > 0 or item_id in ("system", "conversation", "skill") or omitted_item:
            sub_items = None if omitted_item else items_by_id.get(item_id)
            content = None if omitted_item else contents_by_id.get(item_id)
            entry = _breakdown_item(
                item_id,
                tokens if not omitted_item else 0,
                sub_items,
                content,
                active_tokens=skill_tokens_active if item_id == "skill" else None,
                gated=skill_gated if item_id == "skill" and not omitted_item else False,
                cache_mode=(
                    _cache_mode_for_provider(provider, settings)
                    if item_id in _FROZEN_PREFIX_IDS and not omitted_item
                    else None
                ),
            )
            breakdown.append(entry)

    used_tokens = sum(_breakdown_used_tokens(item) for item in breakdown)

    # The per-keystroke hot path (include_content=False) must stay lean: the
    # text was only needed for token counting. Drop it unless the caller — the
    # panel's on-open fetch — explicitly asked for viewable content.
    if not include_content:
        for item in breakdown:
            item.pop("content", None)
            for sub in item.get("items") or []:
                sub.pop("content", None)

    return {
        "used_tokens": used_tokens,
        "context_limit": limit,
        "input_tokens": input_reported,
        "output_tokens": output_reported,
        "total_tokens": input_reported + output_reported,
        "total_cache_read": int(usage_report.get("total_cache_read") or 0),
        "total_cache_write": int(usage_report.get("total_cache_write") or 0),
        "cache_hit_rate": float(usage_report.get("cache_hit_rate") or 0),
        "cache_hit_rate_cumulative": float(usage_report.get("cache_hit_rate_cumulative") or 0),
        "call_count": int(usage_report.get("call_count") or 0),
        "calls": priced_calls,
        "cost_usd": cost_usd,
        "breakdown": breakdown,
        "omitted": omitted_for_ui(omit),
    }


def compute_context_usage(
    conv_id: str,
    model: str,
    *,
    mode: str = "agent",
    draft_text: str = "",
    include_content: bool = False,
) -> dict[str, Any]:
    """Estimate prompt tokens for the next model call."""
    report = compute_context_report(
        conv_id, model, mode=mode, draft_text=draft_text, include_content=include_content
    )
    out: dict[str, Any] = {
        "used_tokens": report["used_tokens"],
        "context_limit": report["context_limit"],
        "input_tokens": report["input_tokens"],
        "output_tokens": report["output_tokens"],
        "total_tokens": report.get("total_tokens", report["input_tokens"] + report["output_tokens"]),
        "total_cache_read": report.get("total_cache_read", 0),
        "total_cache_write": report.get("total_cache_write", 0),
        "cache_hit_rate": report.get("cache_hit_rate", 0),
        "cache_hit_rate_cumulative": report.get("cache_hit_rate_cumulative", 0),
        "call_count": report.get("call_count", 0),
        "calls": report.get("calls", []),
        "cost_usd": report.get("cost_usd"),
        "breakdown": report.get("breakdown", []),
        "omitted": report.get("omitted", []),
    }
    if report.get("agent_info") is not None:
        out["agent_info"] = report["agent_info"]
    return out


def format_token_count(n: int) -> str:
    return f"{int(n):,}"
