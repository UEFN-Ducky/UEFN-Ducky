"""A coding agent that reads the same unchanged file twice in one run gets a short "unchanged"
note instead of the whole text again.

Every tool result stays in the agent's thread and is re-read on each later call. Nov 17 2026
token audit (92 chats): 2,645 of 6,254 reads repeated the same file and arguments within one
context, about $95-120 of re-reads in 9 days. The ledger is per run (one coding-agent launch),
keyed on the normalised path and arguments, and checks mtime + size, so any edit (a write tool,
a shell command, the editor) makes the next read return the full text. A run whose agent was
summarized mid-turn passes force=true to read it again. The embedded Ducky agent trims its own
context, so only external coding agents use the ledger.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from typing import Any

_MAX_RUNS = 64
_lock = threading.Lock()
_runs: OrderedDict[str, dict[tuple[Any, ...], tuple[int, int]]] = OrderedDict()


def _run_id() -> str:
    try:
        from backend.workspace.identity import resolve_context

        ctx = resolve_context()
    except Exception:
        return ""
    if ctx is None or (ctx.coding_agent or "ducky") == "ducky":
        return ""
    return str(ctx.run_id or "")


def repeat_note(tool: str, path: str, args: tuple[Any, ...] = (), *, force: bool = False) -> str:
    """'' the first time (the read is recorded), else the note to send instead of the text.
    ``force`` always reads (and records) again."""
    run = _run_id()
    if not run:
        return ""
    try:
        st = os.stat(path)
    except OSError:
        return ""
    stamp = (st.st_mtime_ns, st.st_size)
    key = (tool, os.path.normcase(os.path.abspath(path)), *args)
    with _lock:
        seen = _runs.setdefault(run, {})
        _runs.move_to_end(run)
        while len(_runs) > _MAX_RUNS:
            _runs.popitem(last=False)
        if not force and seen.get(key) == stamp:
            return (f"Unchanged since your earlier {tool} of {path} in this turn (same arguments, file not "
                    "modified): use the text you already have. If it is no longer in your context (the "
                    "chat was summarized), call again with force=true.")
        seen[key] = stamp
    return ""
