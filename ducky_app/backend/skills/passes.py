"""One skills pass: work shared by every pack lookup inside it.

One indexing pass (backend.search.indexer) or one skill deploy: per-pack stat passes, the
plugin skill map and the bundled packs folder are computed once per pass instead of two
or three times per pack. Per thread/context, so passes never share it.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_PASS_MEMO: ContextVar[dict[Any, Any] | None] = ContextVar("skill_pass_memo", default=None)


@contextmanager
def pass_memo():
    token = _PASS_MEMO.set({})
    try:
        yield
    finally:
        _PASS_MEMO.reset(token)


@contextmanager
def shared_pass_memo():
    """Join the open :func:`pass_memo` pass, or open one when none is running."""
    if _PASS_MEMO.get() is not None:
        yield
        return
    with pass_memo():
        yield


def pass_cached(key: Any, compute):
    """``compute()`` once per open :func:`pass_memo` pass under ``key``; uncached outside one."""
    memo = _PASS_MEMO.get()
    if memo is None:
        return compute()
    slot = ("pass_cached", key)
    if slot not in memo:
        memo[slot] = compute()
    return memo[slot]
