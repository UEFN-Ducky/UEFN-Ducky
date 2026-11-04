"""The one door every UEFN editor call goes through.

Editor work leaves Ducky three ways: listener HTTP commands (``bridge.client``),
Epic's in-editor MCP (``unreal__*``, ``mcp_plugins.epic``) and the editor's own
process (launch / close / restart in ``tools.panel.ducky_panel``). Each of them
calls :func:`call` (or :func:`acall` for coroutines), so a safety rule written
here holds for all three at once.

``op`` names the call (a listener command, ``unreal__<tool>``, ``ducky_restart_uefn``);
``mutating`` is False for reads that never change the editor or the island.
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


def current_chat_id() -> str:
    """The chat whose agent is making this call, '' for the panel or an unknown caller."""
    try:
        from backend.workspace import identity

        # The call's own context, else the agent process Ducky started (its env names the chat).
        ctx = identity.current() or identity.from_env()
    except Exception:
        return ""
    return str(getattr(ctx, "conv_id", "") or "")


def call(chat_id: str, op: str, mutating: bool, fn: Callable[[], T]) -> T:
    """Run one editor call ``fn`` for ``chat_id``. No rules yet: it only runs ``fn``."""
    return fn()


async def acall(chat_id: str, op: str, mutating: bool, fn: Callable[[], Awaitable[Any]]) -> Any:
    """:func:`call` for a coroutine: ``fn()`` returns an awaitable, which is awaited."""
    result = fn()
    if inspect.isawaitable(result):
        return await result
    return result
