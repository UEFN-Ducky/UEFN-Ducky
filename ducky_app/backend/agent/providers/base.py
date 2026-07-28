"""LLM provider abstractions for the embedded agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Protocol


class StreamEventKind(str, Enum):
    STATUS = "status"
    TEXT_DELTA = "text_delta"
    THINKING = "thinking"
    TOOL_CALLS = "tool_calls"
    DONE = "done"
    ERROR = "error"


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class StreamEvent:
    kind: StreamEventKind
    text: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    stop_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    error: str = ""
    thinking_blocks: list[dict[str, Any]] = field(default_factory=list)


from backend.agent.message_attachment import MessageAttachment
from backend.agent.prompt_cache import PromptCachePayload


@dataclass
class ProviderMessage:
    role: str  # user | assistant | tool
    content: str = ""
    tool_call_id: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    attachments: list[MessageAttachment] = field(default_factory=list)
    thinking: str = ""
    # Raw Anthropic thinking content blocks (with signatures) for tool-loop round-trips.
    thinking_blocks: list[dict[str, Any]] = field(default_factory=list)


class LLMProvider(Protocol):
    async def stream_turn(
        self,
        *,
        system: str,
        messages: list[ProviderMessage],
        tools: list[dict[str, Any]],
        cancel_event: Any | None = None,
        cache: PromptCachePayload | None = None,
    ) -> AsyncIterator[StreamEvent]: ...

    async def test_connection(self) -> tuple[bool, str]: ...
