"""Helpers for surfacing model reasoning ("thinking") from streamed deltas.

Two sources are supported:
  1. Provider reasoning fields on the streaming delta (``reasoning`` /
     ``reasoning_content``) — used by Ollama's OpenAI-compatible endpoint and
     DeepSeek-style models.
  2. Inline ``<think>…</think>`` tags embedded in the visible content stream —
     emitted by many local models (e.g. qwen3). ``ThinkSplitter`` separates
     these from the user-visible answer, handling tags split across chunks.
"""

from __future__ import annotations

from typing import Any

_OPEN = "<think>"
_CLOSE = "</think>"


def reasoning_from_delta(delta: Any) -> str:
    """Best-effort reasoning text from a streaming chat delta, or ""."""
    for attr in ("reasoning", "reasoning_content"):
        val = getattr(delta, attr, None)
        if isinstance(val, str) and val:
            return val
    extra = getattr(delta, "model_extra", None)
    if isinstance(extra, dict):
        for key in ("reasoning", "reasoning_content"):
            val = extra.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def _partial_suffix_len(text: str, marker: str) -> int:
    """Longest k<len(marker) where text ends with marker[:k] — a maybe-split tag."""
    max_k = min(len(marker) - 1, len(text))
    for k in range(max_k, 0, -1):
        if text.endswith(marker[:k]):
            return k
    return 0


class ThinkSplitter:
    """Incrementally split a content stream into ("text"|"think", segment) parts.

    Feed raw content deltas; a small tail is held back when it could be the
    start of a ``<think>``/``</think>`` marker split across chunks. Call
    ``flush()`` at end-of-stream to drain any remainder.
    """

    __slots__ = ("_in_think", "_tail")

    def __init__(self) -> None:
        self._in_think = False
        self._tail = ""

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        if not chunk:
            return []
        self._tail += chunk
        out: list[tuple[str, str]] = []
        while True:
            marker = _CLOSE if self._in_think else _OPEN
            idx = self._tail.find(marker)
            if idx != -1:
                seg = self._tail[:idx]
                if seg:
                    out.append(("think" if self._in_think else "text", seg))
                self._tail = self._tail[idx + len(marker) :]
                self._in_think = not self._in_think
                continue
            hold = _partial_suffix_len(self._tail, marker)
            emit = self._tail[: len(self._tail) - hold]
            if emit:
                out.append(("think" if self._in_think else "text", emit))
            self._tail = self._tail[len(self._tail) - hold :]
            break
        return out

    def flush(self) -> list[tuple[str, str]]:
        if not self._tail:
            return []
        out = [("think" if self._in_think else "text", self._tail)]
        self._tail = ""
        return out
