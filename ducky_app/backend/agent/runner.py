"""Streaming agent loop with tool execution and human approval."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.agent.attachments import attachments_from_message_dict
from backend.agent.multimodal_content import image_attachments
from backend.agent.prompt import compact_messages, get_system_prompt_parts
from backend.agent.prompt_cache import (
    LIVE_CONTEXT_PREFIX,
    PromptCachePayload,
    build_cache_payload,
    enrich_parts,
    invalidate_conv_cache,
    markers_only_payload,
    replace_frozen_tool_names,
    sticky_frozen_tool_names,
)
from backend.agent.providers import make_provider
from backend.agent.providers.base import ProviderMessage, StreamEvent, StreamEventKind, ToolCallRequest
from backend.agent.secrets import get_key
from backend.agent.run_context import reset_plan_only, set_plan_only
from backend.agent import hammer_guard
from backend.agent.tool_router import is_destructive, select_tools
from backend.agent.toolsets.destructive import allow_destructive_execution
from backend.agent.toolsets import effective_tool_name
from backend.agent.tools import (
    ToolCallRecord,
    execute_tool,
    list_mcp_tools,
    mcp_tool_to_anthropic,
    mcp_tool_to_gemini,
    mcp_tool_to_openai,
)
from backend.agent.serialization import (
    count_tool_llm_tokens,
    format_rejected_tool_result,
    format_tool_block_for_llm,
    format_tool_result_for_llm,
    set_tool_result_format,
)

STREAM_TIMEOUT_SEC = 180
# Local Ollama often spends many minutes on prompt eval (partial GPU offload).
# The OpenAI client read timeout is open-ended; this host deadline must not kill
# the turn before the first token while the model is still evaluating.
OLLAMA_STREAM_TIMEOUT_SEC = 1800

# Repeat-call guard: how many times an identical (tool, args) call may actually
# run within a single user turn before further identical calls are short-circuited.
# The model polling ducky_get_status 5x in a row is the pathology this stops.
_MAX_IDENTICAL_RESULT_CALLS = 2  # block once this many prior calls returned the same result
_MAX_SIGNATURE_CALLS = 4  # hard cap on identical (tool, args) calls even if results jitter
_MAX_HEAVY_TOOLS_PER_STEP = 1  # one wire/spawn/save per LLM tool-call batch — editor freezes otherwise


def _call_signature(name: str, arguments: dict[str, Any]) -> str:
    """Stable key for an identical tool call (name + canonical args)."""
    try:
        args = json.dumps(arguments or {}, sort_keys=True, default=str)
    except Exception:
        args = repr(arguments)
    return f"{name}\x00{args}"


# Read-only tools: an identical call is only redundant while nothing MUTATED in
# between. Any mutating tool call must reset the repeat history — the guard once
# blocked `workspace_list_verse_errors` right after `workspace_write_file`, so
# the model could never verify its fix and flailed for the rest of the turn.
_READONLY_TOOL_PREFIXES = (
    "ping",
    "get_",
    "list_",
    "search_",
    "find_",
    "inspect_",
    "describe_",
    "does_",
    "workspace_list_",
    "workspace_read_",
    "workspace_editor_get_",
    "ducky_get_",
    "ducky_list_",
    "ducky_read_",
    "ducky_terminal_read_",
    "project_memory_list",
    "project_memory_get",
    "ducky_memory_overview",
    "uefn_skill",
    "skill_read_subskill",
    "ik_retarget_capabilities",
)


def _is_readonly_tool(name: str) -> bool:
    return name.startswith(_READONLY_TOOL_PREFIXES)


def _repeat_guard_message(name: str, count: int) -> str:
    """Nudge returned in place of a re-run of the same call, so the model stops looping.

    Deliberately tool-agnostic: the old message pushed `workspace_list_verse_errors`
    on every loop, which was misleading when the repeated call was ping/status/a
    read. State the block, then give generic next-step options.
    """
    return (
        f"Repeated-call guard: `{name}` was already called {count}× this turn with identical "
        "arguments and returned the same result, so this call was blocked. Retrying it will not "
        "change anything. Do one of these instead: take a different concrete action, change the "
        "arguments if you need different data, or reply to the user with what you already have. "
        "If you were polling for the listener/editor to come online, it will not on its own — stop "
        "polling and proceed."
    )


class _CancelBridge:
    """Unify asyncio + threading cancel signals for Stop button."""

    __slots__ = ("_async_ev", "_thread_ev")

    def __init__(self, async_ev: asyncio.Event, thread_ev: threading.Event | None) -> None:
        self._async_ev = async_ev
        self._thread_ev = thread_ev

    def is_set(self) -> bool:
        if self._async_ev.is_set():
            return True
        return bool(self._thread_ev is not None and self._thread_ev.is_set())


def _partial_assistant_message(
    *,
    content: str,
    thinking: str,
    blocks: list[dict[str, Any]],
    usage: dict[str, int],
    error: str,
) -> dict[str, Any] | None:
    """Snapshot of what streamed before a crash, so callers can keep it.

    Returns None when nothing was streamed (no answer, reasoning, or tool
    blocks) — there is nothing worth persisting in that case.
    """
    if not (content.strip() or thinking.strip() or blocks):
        return None
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": content,
        "blocks": list(blocks),
        "ts": time.time(),
        "usage": dict(usage),
        "incomplete": True,
        "error": error,
    }
    if thinking.strip():
        msg["thinking"] = thinking
    return msg


def _cancelled_event(
    blocks: list[dict[str, Any]],
    assistant_text: str,
    turn_text: str,
    turn_thinking: str,
    usage: dict[str, int],
) -> AgentEvent:
    return AgentEvent(
        kind="error",
        text="Cancelled",
        partial_message=_partial_assistant_message(
            content=assistant_text + turn_text,
            thinking=turn_thinking,
            blocks=blocks,
            usage=usage,
            error="Stopped",
        ),
    )


def _tool_block_result_for_llm(block: dict[str, Any], *, fmt: str | None = None) -> str:
    """Rebuild tool result content for the LLM from a stored tool_call block."""
    active_fmt = fmt if fmt in ("toon", "json") else None
    return format_tool_block_for_llm(block, fmt=active_fmt)  # type: ignore[arg-type]


@dataclass
class RunConfig:
    provider: str = "anthropic"
    model: str = ""
    listener_port: int = 4200
    listener_online: bool = False
    listener_wedged: bool = False
    project_root: str = ""
    uefn_project_name: str = ""
    project_match: bool = True
    conv_id: str = ""
    max_turns: int = 25
    keep_last_messages: int = 20
    skill_override: str | None = None
    plan_only: bool = False
    context_omit: frozenset[str] = field(default_factory=frozenset)
    ducky_name: str = ""
    ducky_personality: str = ""
    conv: Any = None
    prompt_caching_enabled: bool = True
    freeze_prompt_prefix: bool = True
    anthropic_extended_cache_ttl: bool = False
    mode_suffix: str = ""
    tool_result_format: str = "toon"
    thinking_effort: str = "off"


@dataclass
class AgentEvent:
    kind: str  # status | text_delta | tool_start | tool_end | approval_needed | turn_done | usage_step | error | done
    text: str = ""
    tool: ToolCallRecord | None = None
    tools_pending: list[ToolCallRecord] = field(default_factory=list)
    usage: dict[str, int] = field(default_factory=dict)
    assistant_message: dict[str, Any] | None = None
    step: int = 0
    # On kind="error", the partial reasoning/answer streamed before the crash so
    # callers can persist it instead of discarding it. None when nothing streamed.
    partial_message: dict[str, Any] | None = None
    # Optional 0–100 for kind="status" (prompt-eval progress).
    percent: float | None = None


ApprovalCallback = Callable[[list[ToolCallRecord]], bool]


class AgentRunner:
    def __init__(self, config: RunConfig) -> None:
        self.config = config
        self._cancel = asyncio.Event()
        self._approval_callback: ApprovalCallback | None = None

    def set_approval_callback(self, cb: ApprovalCallback | None) -> None:
        self._approval_callback = cb

    def cancel(self) -> None:
        self._cancel.set()

    def _attach_llm_payload(self, rec: ToolCallRecord, llm_content: str) -> None:
        rec.llm_content = llm_content
        rec.llm_tokens = count_tool_llm_tokens(
            llm_content, model=self.config.model, provider=self.config.provider
        )

    async def _iter_provider_stream(
        self,
        provider: Any,
        *,
        system: str,
        messages: list[ProviderMessage],
        tools: list[dict[str, Any]],
        cache: PromptCachePayload | None = None,
        thread_cancel: threading.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Stream LLM events with timeout — providers use sync HTTP inside async gens."""
        from frontend.ui_web.provider_usage_log import bind_usage_context, reset_usage_context

        bridge = _CancelBridge(self._cancel, thread_cancel)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue()
        prov = str(self.config.provider or "").strip().lower()
        timeout_sec = (
            OLLAMA_STREAM_TIMEOUT_SEC if prov == "ollama" else STREAM_TIMEOUT_SEC
        )
        deadline = time.monotonic() + timeout_sec
        conv = self.config.conv
        usage_ctx = bind_usage_context(
            agent=str(getattr(conv, "coding_agent", "") or self.config.provider or "ducky"),
            conv_id=str(getattr(conv, "id", "") or ""),
            ducky_label=str(
                getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or ""
            ),
        )

        def producer() -> None:
            try:

                async def collect() -> None:
                    async for event in provider.stream_turn(
                        system=system,
                        messages=messages,
                        tools=tools,
                        cancel_event=bridge,
                        cache=cache,
                    ):
                        if bridge.is_set():
                            break
                        loop.call_soon_threadsafe(queue.put_nowait, event)

                asyncio.run(collect())
            except Exception as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        try:
            threading.Thread(target=producer, daemon=True).start()

            async for event in self._drain_provider_queue(
                queue, bridge=bridge, deadline=deadline, timeout_sec=timeout_sec
            ):
                yield event
        finally:
            reset_usage_context(usage_ctx)

    async def _drain_provider_queue(
        self,
        queue: asyncio.Queue[Any],
        *,
        bridge: Any,
        deadline: float,
        timeout_sec: int = STREAM_TIMEOUT_SEC,
    ) -> AsyncIterator[StreamEvent]:
        while True:
            # Poll the queue in short slices so a Stop press is observed within
            # ~250ms even while the provider is mid-request (no deltas arriving),
            # instead of blocking until the next event or the stream timeout.
            while True:
                if bridge.is_set():
                    yield StreamEvent(kind=StreamEventKind.ERROR, error="Cancelled")
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield StreamEvent(
                        kind=StreamEventKind.ERROR,
                        error=(
                            f"LLM request timed out after {timeout_sec}s "
                            "(still waiting on the first token — local prompt eval "
                            "can take a while). Click Stop and try again, or use a "
                            "smaller context / more GPU layers."
                        ),
                    )
                    return
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=min(0.25, remaining))
                    break
                except asyncio.TimeoutError:
                    continue
            if item is None:
                return
            if isinstance(item, Exception):
                yield StreamEvent(kind=StreamEventKind.ERROR, error=str(item))
                return
            yield item

    def _provider_tools(self, provider: str, tools: list) -> list[dict[str, Any]]:
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
        # anthropic or unknown — Anthropic-shaped tools are the historical default.
        return [mcp_tool_to_anthropic(t) for t in tools]

    def _tool_result_message(
        self,
        tc: ToolCallRequest,
        block: dict[str, Any],
        stub_tools: frozenset[str],
    ) -> ProviderMessage:
        if block.get("name") in stub_tools:
            content = (
                '{"ok":true,"tool":"%s","data":"(content already present in the '
                'system prompt — see the operator skill section)"}' % block["name"]
            )
        else:
            content = _tool_block_result_for_llm(block, fmt=self.config.tool_result_format)
        return ProviderMessage(role="tool", tool_call_id=tc.id, content=content)

    def _assistant_turn_to_provider(
        self,
        message: dict[str, Any],
        *,
        stub_tools: frozenset[str],
    ) -> list[ProviderMessage]:
        """Replay one stored turn as assistant → tool results → assistant.

        Storing the final reply on the same assistant message as the tool_calls made
        the next user line land right after tool results, so follow-ups looked new.
        """
        blocks = [b for b in (message.get("blocks") or []) if isinstance(b, dict)]
        final = str(message.get("content") or message.get("text") or "")
        out: list[ProviderMessage] = []
        text_parts: list[str] = []
        tool_batch: list[dict[str, Any]] = []
        thinking = ""

        def flush_tools() -> None:
            nonlocal tool_batch, text_parts, thinking
            if not tool_batch:
                return
            tcs: list[ToolCallRequest] = []
            seen: set[str] = set()
            kept: list[dict[str, Any]] = []
            for block in tool_batch:
                if not block.get("name"):
                    continue
                tid = str(block.get("id") or "").strip() or str(uuid.uuid4())
                if tid in seen:
                    continue
                seen.add(tid)
                kept.append(block)
                tcs.append(
                    ToolCallRequest(
                        id=tid,
                        name=block["name"],
                        arguments=dict(block.get("arguments") or {}),
                    )
                )
            if not tcs:
                tool_batch = []
                return
            out.append(
                ProviderMessage(
                    role="assistant",
                    content="\n".join(text_parts),
                    tool_calls=tcs,
                    thinking=thinking,
                )
            )
            for tc, block in zip(tcs, kept):
                out.append(self._tool_result_message(tc, block, stub_tools))
            tool_batch = []
            text_parts = []
            thinking = ""

        for block in blocks:
            btype = str(block.get("type") or "")
            if btype == "thinking" and str(block.get("text") or "").strip():
                thinking = str(block.get("text") or "")
            elif btype == "text" and str(block.get("text") or "").strip():
                if tool_batch:
                    flush_tools()
                text_parts.append(str(block.get("text") or "").strip())
            elif btype == "tool_call":
                tool_batch.append(block)
        flush_tools()

        if final.strip():
            out.append(
                ProviderMessage(
                    role="assistant",
                    content=final,
                    thinking=str(message.get("thinking") or thinking or ""),
                )
            )
        elif not out:
            out.append(
                ProviderMessage(
                    role="assistant",
                    content="\n".join(text_parts),
                    thinking=str(message.get("thinking") or thinking or ""),
                )
            )
        return out

    def _history_to_provider(
        self,
        messages: list[dict[str, Any]],
        *,
        stub_tools: frozenset[str] = frozenset(),
    ) -> list[ProviderMessage]:
        out: list[ProviderMessage] = []
        for m in messages:
            role = m.get("role", "user")
            if role == "assistant":
                out.extend(self._assistant_turn_to_provider(m, stub_tools=stub_tools))
                continue
            if role == "tool":
                out.append(
                    ProviderMessage(
                        role="tool",
                        content=m.get("content", ""),
                        tool_call_id=m.get("tool_call_id", ""),
                    )
                )
                continue
            out.append(
                ProviderMessage(
                    role="user",
                    content=str(m.get("content") or m.get("text") or ""),
                    attachments=image_attachments(
                        attachments_from_message_dict(
                            m,
                            conv_id=self.config.conv_id or None,
                            project_root=self.config.project_root or None,
                        )
                    ),
                )
            )
        return out

    async def run_turn(
        self,
        user_text: str,
        history: list[dict[str, Any]],
        *,
        user_attachments: list[dict[str, Any]] | None = None,
        thread_cancel: threading.Event | None = None,
    ) -> AsyncIterator[AgentEvent]:
        self._cancel = asyncio.Event()
        bridge = _CancelBridge(self._cancel, thread_cancel)
        set_tool_result_format(self.config.tool_result_format)
        plan_token = set_plan_only(bool(self.config.plan_only))
        hammer_token = hammer_guard.bind_conversation(self.config.conv_id)
        try:
            async for event in self._run_turn_inner(
                user_text,
                history,
                user_attachments=user_attachments,
                thread_cancel=thread_cancel,
                bridge=bridge,
            ):
                yield event
        finally:
            hammer_guard.reset_conversation(hammer_token)
            reset_plan_only(plan_token)

    async def _run_turn_inner(
        self,
        user_text: str,
        history: list[dict[str, Any]],
        *,
        user_attachments: list[dict[str, Any]] | None = None,
        thread_cancel: threading.Event | None = None,
        bridge: Any = None,
    ) -> AsyncIterator[AgentEvent]:
        if bridge is None:
            bridge = _CancelBridge(self._cancel, thread_cancel)
        api_key = get_key(self.config.provider)
        if not api_key:
            yield AgentEvent(kind="error", text=f"No API key for {self.config.provider}. Set one in Settings → Agent.")
            return
        if not (self.config.model or "").strip():
            yield AgentEvent(
                kind="error",
                text="No model selected. Test your API key in Settings to load models from the provider.",
            )
            return

        provider = make_provider(
            self.config.provider,
            api_key,
            self.config.model,
            thinking_effort=self.config.thinking_effort,
        )

        # Skill index is compact (pack/reference lines only); always include it.
        # Full SKILL.md bodies load on demand via skill_read_subskill.
        skill_text = self.config.skill_override or ""

        # Epoch compaction: one sanctioned full miss. Auto-off still mechanical-epochs
        # at high-water so the append-only view cannot grow past the model window.
        epoched = False
        if self.config.conv is not None:
            try:
                from backend.agent.context_memory import compress_conversation, should_compress
                from frontend.settings import PanelSettings

                settings = PanelSettings.load()
                if should_compress(self.config.conv, settings=settings):
                    result = compress_conversation(
                        self.config.conv,
                        settings=settings,
                        project_root=self.config.project_root,
                        force=False,
                        use_llm=bool(getattr(settings, "memory_auto_compress", True)),
                    )
                    epoched = bool(result.get("compressed"))
                    if epoched:
                        invalidate_conv_cache(self.config.conv)
            except Exception:
                pass

        from backend.agent.local_slim import provider_wants_local_slim

        local_slim = provider_wants_local_slim(self.config.provider)
        parts = get_system_prompt_parts(
            listener_online=self.config.listener_online,
            listener_port=self.config.listener_port,
            project_root=self.config.project_root,
            skill_text=skill_text,
            mode_suffix=self.config.mode_suffix,
            listener_wedged=self.config.listener_wedged,
            ducky_name=self.config.ducky_name,
            ducky_personality=self.config.ducky_personality,
            uefn_project_name=self.config.uefn_project_name,
            project_match=self.config.project_match,
            conv_id=self.config.conv_id or "",
            local_slim=local_slim,
        )
        parts = enrich_parts(parts, listener_port=self.config.listener_port, mode_suffix=self.config.mode_suffix)
        from backend.agent.providers.cache_utils import (
            anthropic_extended_cache_ttl_enabled,
            provider_cache_markers_enabled,
        )

        cache_enabled = provider_cache_markers_enabled(
            self.config.provider,
            fallback=bool(self.config.prompt_caching_enabled),
        )
        prompt_cache = build_cache_payload(
            self.config.conv,
            parts,
            omit=self.config.context_omit,
            enable_cache=cache_enabled,
            freeze_enabled=self.config.freeze_prompt_prefix,
            prompt_cache_key=self.config.conv_id,
            anthropic_extended_ttl=anthropic_extended_cache_ttl_enabled(
                fallback=bool(self.config.anthropic_extended_cache_ttl)
            ),
        )
        if self.config.conv is not None:
            try:
                from frontend.ui_web.project_chats import save_conversation

                save_conversation(self.config.conv, self.config.project_root or None)
            except Exception:
                pass
        # Frozen/dynamic split is host-owned and unconditional. enable_cache
        # only adds provider markers. Volatile memory/plan/status is a tail message.
        system = prompt_cache.frozen_system
        volatile_tail = (prompt_cache.dynamic_system or "").strip()
        stream_cache = markers_only_payload(prompt_cache) if prompt_cache.enable_cache else None

        working_history = list(history)
        working_history.append(
            {
                "role": "user",
                "content": user_text,
                "attachments": user_attachments or [],
                "ts": time.time(),
            }
        )

        assistant_blocks: list[dict[str, Any]] = []
        assistant_text = ""
        assistant_thinking = ""
        turn_text = ""
        turn_thinking = ""
        total_usage: dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }

        if bridge.is_set():
            yield _cancelled_event(assistant_blocks, assistant_text, turn_text, turn_thinking, total_usage)
            return

        all_tools = await list_mcp_tools()
        if bridge.is_set():
            yield _cancelled_event(assistant_blocks, assistant_text, turn_text, turn_thinking, total_usage)
            return
        omit_tools = "tools" in self.config.context_omit
        tool_schemas: list[dict[str, Any]] = []
        if not omit_tools:
            selected = select_tools(
                all_tools,
                user_text,
                plan_only=self.config.plan_only,
                listener_online=self.config.listener_online,
                history=working_history,
                conv=self.config.conv,
            )
            if skill_text:
                # Skill already rides in the system prompt — the tool would only duplicate it.
                selected = [t for t in selected if t.name != "uefn_skill"]
            if self.config.conv is not None:
                names = [t.name for t in selected]
                if epoched:
                    names = replace_frozen_tool_names(self.config.conv, names)
                else:
                    names = sticky_frozen_tool_names(self.config.conv, names)
                by_name = {t.name: t for t in all_tools}
                selected = [by_name[n] for n in names if n in by_name]
            tool_schemas = self._provider_tools(self.config.provider, selected)

        conv = self.config.conv
        provider_messages = self._history_to_provider(
            compact_messages(
                working_history,
                self.config.keep_last_messages,
                context_summary=str(getattr(conv, "context_summary", "") or "") if conv is not None else "",
                context_summary_through=int(getattr(conv, "context_summary_through", 0) or 0)
                if conv is not None
                else 0,
            ),
            stub_tools=frozenset({"uefn_skill"}) if skill_text else frozenset(),
        )

        # Per-turn record of executed (tool, args) → list of result payloads, used to
        # short-circuit the model re-running the same call over and over.
        executed_calls: dict[str, list[str]] = {}

        def _accumulate_usage(usage: dict[str, int] | None) -> dict[str, int]:
            if not usage:
                return {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                }
            return {
                "input_tokens": max(0, int(usage.get("input_tokens") or 0)),
                "output_tokens": max(0, int(usage.get("output_tokens") or 0)),
                "cache_read_tokens": max(0, int(usage.get("cache_read_tokens") or 0)),
                "cache_write_tokens": max(0, int(usage.get("cache_write_tokens") or 0)),
            }

        for turn in range(self.config.max_turns):
            if bridge.is_set():
                yield _cancelled_event(assistant_blocks, assistant_text, turn_text, turn_thinking, total_usage)
                return

            step_n = turn + 1
            if local_slim and str(self.config.provider or "").strip().lower() == "ollama":
                try:
                    from backend.agent.providers.ollama_progress import ollama_wait_status

                    wait_text, wait_pct = ollama_wait_status(step=step_n)
                    yield AgentEvent(kind="status", text=wait_text, percent=wait_pct)
                except Exception:
                    yield AgentEvent(kind="status", text=f"Waiting… step {step_n}")
            else:
                yield AgentEvent(kind="status", text=f"Thinking… step {step_n}")

            tool_calls: list[ToolCallRequest] = []
            turn_text = ""
            turn_thinking = ""
            turn_thinking_blocks: list[dict[str, Any]] = []
            step_usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            }

            stream_messages = provider_messages
            if volatile_tail:
                stream_messages = list(provider_messages) + [
                    ProviderMessage(
                        role="user",
                        content=f"{LIVE_CONTEXT_PREFIX}\n{volatile_tail}",
                    )
                ]

            async for event in self._iter_provider_stream(
                provider,
                system=system,
                messages=stream_messages,
                tools=tool_schemas,
                cache=stream_cache,
                thread_cancel=thread_cancel,
            ):
                if bridge.is_set():
                    yield _cancelled_event(assistant_blocks, assistant_text, turn_text, turn_thinking, total_usage)
                    return
                if event.kind == StreamEventKind.ERROR:
                    err = event.error or "LLM error"
                    yield AgentEvent(
                        kind="error",
                        text=err,
                        partial_message=_partial_assistant_message(
                            content=assistant_text + turn_text,
                            thinking=turn_thinking,
                            blocks=assistant_blocks,
                            usage=total_usage,
                            error=err,
                        ),
                    )
                    return
                if event.kind == StreamEventKind.STATUS:
                    yield AgentEvent(
                        kind="status",
                        text=event.text,
                        percent=event.percent,
                    )
                    continue
                if event.kind == StreamEventKind.TEXT_DELTA:
                    turn_text += event.text
                    yield AgentEvent(kind="text_delta", text=event.text)
                elif event.kind == StreamEventKind.THINKING:
                    assistant_thinking += event.text
                    turn_thinking += event.text
                    yield AgentEvent(kind="thinking", text=event.text)
                elif event.kind == StreamEventKind.TOOL_CALLS:
                    tool_calls = event.tool_calls
                    if event.thinking_blocks:
                        turn_thinking_blocks = list(event.thinking_blocks)
                    if event.usage:
                        step_usage = _accumulate_usage(event.usage)
                elif event.kind == StreamEventKind.DONE:
                    if event.text and not turn_text:
                        turn_text = event.text
                    if event.thinking_blocks:
                        turn_thinking_blocks = list(event.thinking_blocks)
                    if event.usage:
                        step_usage = _accumulate_usage(event.usage)

            if step_usage["input_tokens"] or step_usage["output_tokens"]:
                for key in total_usage:
                    total_usage[key] += step_usage.get(key, 0)
                yield AgentEvent(
                    kind="usage_step",
                    usage=dict(step_usage),
                    step=turn + 1,
                )

            assistant_text += turn_text

            if not tool_calls:
                msg = {
                    "role": "assistant",
                    "content": assistant_text,
                    "blocks": assistant_blocks,
                    "ts": time.time(),
                    "usage": total_usage,
                }
                # Intermediate steps' reasoning is already interleaved into
                # blocks; the top-level thinking is just this final step's, so
                # the closing bubble shows its own reasoning without repeating
                # what already appears inline above.
                if turn_thinking.strip():
                    msg["thinking"] = turn_thinking
                yield AgentEvent(kind="done", assistant_message=msg, usage=total_usage)
                return

            # Preserve this step's reasoning and narration as ordered blocks so
            # they stay interleaved with the tools in history, instead of being
            # collapsed into one block or dropped when assistant_text is reset.
            if turn_thinking.strip():
                assistant_blocks.append({"type": "thinking", "text": turn_thinking})
            if turn_text.strip():
                assistant_blocks.append({"type": "text", "text": turn_text})

            pending_records: list[ToolCallRecord] = []
            for tc in tool_calls:
                rec = ToolCallRecord(
                    id=tc.id or str(uuid.uuid4()),
                    name=tc.name,
                    arguments=dict(tc.arguments or {}),
                )
                pending_records.append(rec)

            approved_destructive = True
            destructive = [
                r
                for r in pending_records
                if is_destructive(effective_tool_name(r.name, r.arguments))
            ]
            if destructive:
                if self._approval_callback is not None:
                    yield AgentEvent(
                        kind="approval_needed",
                        tools_pending=destructive,
                        text="Approve destructive tool calls?",
                    )
                approved_destructive = allow_destructive_execution(
                    destructive, self._approval_callback
                )
                if not approved_destructive:
                    for r in destructive:
                        r.status = "rejected"

            provider_messages.append(
                ProviderMessage(
                    role="assistant",
                    content=turn_text,
                    tool_calls=tool_calls,
                    thinking=turn_thinking,
                    thinking_blocks=turn_thinking_blocks,
                )
            )

            from backend.bridge.serial import BUSY_HINT, HEAVY_MCP_TOOLS

            heavy_executed = 0
            for rec in pending_records:
                if bridge.is_set():
                    yield _cancelled_event(assistant_blocks, assistant_text, turn_text, turn_thinking, total_usage)
                    return
                if rec.status == "rejected":
                    llm_content = format_rejected_tool_result(fmt=self.config.tool_result_format)  # type: ignore[arg-type]
                    self._attach_llm_payload(rec, llm_content)
                    block = self._record_to_block(rec)
                    assistant_blocks.append(block)
                    yield AgentEvent(kind="tool_end", tool=rec)
                    provider_messages.append(
                        ProviderMessage(
                            role="tool",
                            tool_call_id=rec.id,
                            content=llm_content,
                        )
                    )
                    continue

                call_name = effective_tool_name(rec.name, rec.arguments)
                call_args = (
                    dict(rec.arguments.get("arguments") or {})
                    if rec.name == "ducky_call_tool" and isinstance(rec.arguments.get("arguments"), dict)
                    else rec.arguments
                )
                sig = _call_signature(call_name, call_args)
                prior = executed_calls.get(sig) or []
                identical_tail = (
                    len(prior) >= _MAX_IDENTICAL_RESULT_CALLS
                    and len(set(prior[-_MAX_IDENTICAL_RESULT_CALLS:])) == 1
                )
                if identical_tail or len(prior) >= _MAX_SIGNATURE_CALLS:
                    nudge = _repeat_guard_message(call_name, len(prior))
                    payload_json = json.dumps({"ok": False, "tool": rec.name, "error": nudge}, ensure_ascii=False)
                    llm_content = format_tool_result_for_llm(
                        rec.name,
                        payload_json,
                        fmt=self.config.tool_result_format,  # type: ignore[arg-type]
                    )
                    self._attach_llm_payload(rec, llm_content)
                    rec.status = "error"
                    rec.duration_ms = 0
                    rec.result = {"ok": False, "data": nudge, "hint": "repeat-call guard"}
                    block = self._record_to_block(rec)
                    assistant_blocks.append(block)
                    yield AgentEvent(kind="tool_end", tool=rec)
                    provider_messages.append(
                        ProviderMessage(role="tool", tool_call_id=rec.id, content=llm_content)
                    )
                    continue

                effective_name = effective_tool_name(rec.name, rec.arguments)
                if effective_name in HEAVY_MCP_TOOLS:
                    if heavy_executed >= _MAX_HEAVY_TOOLS_PER_STEP:
                        nudge = (
                            f"Skipped — only one heavy editor tool per step ({effective_name}). "
                            f"{BUSY_HINT}"
                        )
                        payload_json = json.dumps(
                            {"ok": False, "tool": rec.name, "error": nudge}, ensure_ascii=False
                        )
                        llm_content = format_tool_result_for_llm(
                            rec.name,
                            payload_json,
                            fmt=self.config.tool_result_format,  # type: ignore[arg-type]
                        )
                        self._attach_llm_payload(rec, llm_content)
                        rec.status = "error"
                        rec.duration_ms = 0
                        rec.result = {"ok": False, "data": nudge, "hint": "one-heavy-per-step"}
                        block = self._record_to_block(rec)
                        assistant_blocks.append(block)
                        yield AgentEvent(kind="tool_end", tool=rec)
                        provider_messages.append(
                            ProviderMessage(role="tool", tool_call_id=rec.id, content=llm_content)
                        )
                        continue
                    heavy_executed += 1

                yield AgentEvent(kind="tool_start", tool=rec)
                rec.started = time.time()
                result = await execute_tool(rec.name, rec.arguments, cancel_event=bridge)
                rec.duration_ms = result.duration_ms
                rec.result = {
                    "ok": result.ok,
                    "data": result.data if result.ok else result.error,
                    "hint": result.hint,
                }
                rec.status = "success" if result.ok else "error"
                payload_json = result.to_json_str()
                llm_content = format_tool_result_for_llm(
                    rec.name,
                    payload_json,
                    fmt=self.config.tool_result_format,  # type: ignore[arg-type]
                )
                self._attach_llm_payload(rec, llm_content)
                if not _is_readonly_tool(effective_tool_name(rec.name, rec.arguments)):
                    # State changed: identical re-reads can now legitimately
                    # return different results (write file → re-check errors),
                    # so reset every OTHER call's repeat history. Keep this
                    # call's own history so a no-op mutation repeated with
                    # identical args (rewriting the same content over and
                    # over) still trips the guard instead of looping forever.
                    own = executed_calls.get(sig)
                    executed_calls.clear()
                    if own is not None:
                        executed_calls[sig] = own
                executed_calls.setdefault(sig, []).append(llm_content)
                block = self._record_to_block(rec)
                assistant_blocks.append(block)
                yield AgentEvent(kind="tool_end", tool=rec)
                provider_messages.append(
                    ProviderMessage(
                        role="tool",
                        tool_call_id=rec.id,
                        content=llm_content,
                    )
                )
                # Capture tools: attach PNG for vision on the next model turn
                # (providers only send images on role=user).
                if result.ok:
                    try:
                        from backend.agent.capture_vision import (
                            vision_attachments_from_capture_result,
                        )

                        caps = vision_attachments_from_capture_result(
                            effective_tool_name(rec.name, rec.arguments),
                            result.data,
                        )
                        if caps:
                            provider_messages.append(
                                ProviderMessage(
                                    role="user",
                                    content=(
                                        f"[capture attached: {caps[0].name} — "
                                        "image follows; use tool result path for file work]"
                                    ),
                                    attachments=caps,
                                )
                            )
                    except Exception:
                        pass

                try:
                    from frontend.error_log import record_error

                    if not result.ok:
                        record_error("agent", f"{rec.name}: {result.error[:500]}")
                except Exception:
                    pass

            assistant_text = ""

        err = f"Reached max turns ({self.config.max_turns})"
        # The last step's reasoning/narration are already interleaved into
        # blocks, so keep content/thinking empty here to avoid repeating them.
        yield AgentEvent(
            kind="error",
            text=err,
            partial_message=_partial_assistant_message(
                content="",
                thinking="",
                blocks=assistant_blocks,
                usage=total_usage,
                error=err,
            ),
        )

    def _record_to_block(self, rec: ToolCallRecord) -> dict[str, Any]:
        display_name = effective_tool_name(rec.name, rec.arguments)
        display_args = rec.arguments
        if rec.name == "ducky_call_tool" and isinstance(rec.arguments, dict):
            nested = rec.arguments.get("arguments")
            if isinstance(nested, dict):
                display_args = nested
        block: dict[str, Any] = {
            "type": "tool_call",
            "id": rec.id,
            "name": display_name,
            "arguments": display_args,
            "started": rec.started,
            "duration_ms": rec.duration_ms,
            "result": rec.result,
            "status": rec.status,
        }
        # llm_content is kept for conversation reload (rebuilds the model-facing result),
        # llm_tokens for the plain per-tool token count. No TOON/JSON comparison is stored.
        if rec.llm_content:
            block["llm_content"] = rec.llm_content
        if rec.llm_tokens:
            block["llm_tokens"] = rec.llm_tokens
        try:
            from frontend.ui_web.verse_editor.agent_sync import build_file_edit_meta

            file_edit = build_file_edit_meta(
                display_name, dict(display_args or {}), rec.result
            )
            if file_edit:
                block["file_edit"] = file_edit
        except Exception:
            pass
        return block
