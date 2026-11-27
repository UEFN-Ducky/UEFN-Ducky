"""Stuck-call watch for the shared MCP daemon: a call past its limit is stopped, and a
call past the first check is recorded (events kind ``tool_stuck``) with where it waits."""

from __future__ import annotations

import asyncio
import os
import threading
import time
from typing import Any

from backend.bridge import call_limits

# Any other call still running this long: where it waits is recorded (events kind
# "tool_stuck") and it keeps running, since workflows, renders and questions take minutes.
STUCK_REPORT_S = 120.0
# The anonymous agent.stuck count is sent when the call ends, with how long it really ran.
# Sent at the first check, every report read "5m" (Nov 13-16 2026: 337 of them) whether the
# call answered at 121 s or never. A call still running this long is counted then ("more").
STUCK_COUNT_BY_S = 1201.0
# An Epic UEFN call answers at its own deadline (call_limits.epic_call_limit) with why it
# has not: its first check waits that long plus this, so a call that ended there is not stuck.
EPIC_GRACE_S = 15.0


def _first_check(inner: str, limit: float | None, arguments: dict[str, Any] | None) -> float:
    if inner.startswith("unreal__"):
        try:
            from backend.mcp_plugins.epic import epic_tool_heavy

            args = arguments if isinstance(arguments, dict) else {}
            if isinstance(args.get("name"), str) and isinstance(args.get("arguments"), dict):
                args = args["arguments"]  # ducky_call_tool(name, arguments)
            heavy = epic_tool_heavy(inner[len("unreal__"):], args)
        except Exception:  # noqa: BLE001 — the longer deadline never counts too early
            heavy = True
        return call_limits.epic_call_limit(heavy) + EPIC_GRACE_S
    return min(limit, STUCK_REPORT_S) if limit is not None else STUCK_REPORT_S


class _CallTimedOut(Exception):
    """A quick tool did not answer within its limit: the agent gets this text as an error."""


async def _watched(work: asyncio.Future[Any], name: str, thread: dict[str, Any], ident: Any,
                   waits: dict[str, Any] | None = None, arguments: dict[str, Any] | None = None) -> Any:
    """Await one tool call. A call past its :func:`call_limits.call_limit` is stopped and
    raises :class:`_CallTimedOut`; any call past the first check is recorded where it waits."""
    inner = name
    if name == "ducky_call_tool":
        from backend.agent.toolsets import effective_tool_name

        inner = effective_tool_name(name, arguments if isinstance(arguments, dict) else {})
    limit = call_limits.call_limit(name, inner)
    first = _first_check(inner, limit, arguments)
    began = time.monotonic()
    try:
        done, _ = await asyncio.wait({work}, timeout=first)
        if not done:
            # Where it waits at the first check: client_pool marks connect / call / sequence;
            # an Epic call not there yet waits for the editor lease.
            where = str((waits or {}).get("where") or ("lease" if inner.startswith("unreal__") else ""))
            counted = _report_stuck(name, first, work, thread, ident, waits, arguments)
            try:
                if limit is not None and limit > first:
                    done, _ = await asyncio.wait({work}, timeout=limit - first)
                if not done and limit is not None:
                    work.cancel()
                    raise _CallTimedOut(await asyncio.to_thread(call_limits.timed_out_message, inner, limit))
                if not done:
                    done, _ = await asyncio.wait({work}, timeout=max(STUCK_COUNT_BY_S - first, 0.0))
                if not done:
                    await asyncio.wait({work})
            finally:
                if counted:
                    try:
                        from frontend import usage_stats

                        # The whole run from its start. first + (end - after the report) left out a
                        # late first check and the stack capture: a 1 s call read 0.57 s on a Mac.
                        usage_stats.stuck(str(getattr(ident, "coding_agent", "") or ""),
                                          time.monotonic() - began, tool=inner, where=where)
                    except Exception:  # noqa: BLE001 — a count never breaks the call
                        pass
    except asyncio.CancelledError:
        work.cancel()  # the agent cancelled the call
        raise
    return work.result()


def _await_chain(work: Any) -> list[str]:
    """Where a suspended call waits: its coroutine await chain, innermost last."""
    lines: list[str] = []
    coro = work.get_coro() if isinstance(work, asyncio.Task) else None
    for _ in range(80):
        if coro is None:
            break
        frame = getattr(coro, "cr_frame", None) or getattr(coro, "gi_frame", None)
        if frame is None:
            lines.append(f"  awaiting {type(coro).__name__}\n")
            break
        lines.append(f'  File "{frame.f_code.co_filename}", line {frame.f_lineno}, in {frame.f_code.co_name}\n')
        coro = getattr(coro, "cr_await", None) or getattr(coro, "gi_yieldfrom", None)
    return lines


# A frozen app names stdlib files relative ('File "asyncio\windows_events.py"'), with no
# folder before them: matching only "\asyncio\" missed every Nov 2026 report in the
# installed app, so each showed the idle loop instead of where the call waited.
_LOOP_FILES = (f"{os.sep}asyncio{os.sep}", f'"asyncio{os.sep}', f"{os.sep}selectors.py", '"selectors.py')


def _idle_in_loop(lines: list[str]) -> bool:
    """The stack ends in an event loop waiting for work (``windows_events.py _poll``)."""
    last = lines[-1] if lines else ""
    return any(part in last for part in _LOOP_FILES)


def _call_stack(work: Any, thread: dict[str, Any]) -> list[str]:
    """Where one call waits. A sync tool runs on a thread with its own loop: while its
    coroutine awaits (a ``to_thread``, a question gate) that thread only sits in the loop's
    ``_poll``, which every Oct 24 2026 report showed. Then the coroutine's await chain and
    the loop's worker threads name the wait."""
    import sys
    import traceback

    frames = sys._current_frames()
    tid = thread.get("id")
    lines = traceback.format_stack(frames[tid]) if tid and tid in frames else []
    if lines and not _idle_in_loop(lines):
        return lines
    task = thread.get("task")
    chain = _await_chain(task if isinstance(task, asyncio.Task) else work)
    executor = getattr(thread.get("loop"), "_default_executor", None)
    for worker in list(getattr(executor, "_threads", None) or ()):
        stack = traceback.format_stack(frames[worker.ident]) if worker.ident in frames else []
        # A busy worker runs a work item; an idle one waits on its queue.
        if any("futures" in line and ", in run" in line for line in stack):
            chain += [f"{worker.name}:\n", *stack[-12:]]
    return chain or lines


def _report_stuck(name: str, seconds: float, work: Any, thread: dict[str, Any], ident: Any,
                  waits: dict[str, Any] | None = None, arguments: dict[str, Any] | None = None) -> bool:
    """One events row (kind ``tool_stuck``) with where the call waits. Never raises.
    True when the call also counts as an anonymous agent.stuck (not a wait by design).
    ducky_call_tool reports as the tool it ran, as _usage_tool_done counts it: 9 of 15
    stuck calls on Oct 31 2026 said ducky_call_tool and hid the plugin."""
    try:
        import sys
        import traceback

        from backend.agent.toolsets import effective_tool_name

        name = effective_tool_name(name, arguments if isinstance(arguments, dict) else {})

        frames = sys._current_frames()
        lines = _call_stack(work, thread)
        # A wait on another loop (nested MCP servers) continues on that loop's thread.
        # An idle pool loop (sitting in _poll) says nothing about this call: skip it.
        for t in threading.enumerate():
            if t.name == "mcp-plugin-pool" and t.ident in frames:
                pool = traceback.format_stack(frames[t.ident])
                if not _idle_in_loop(pool):
                    lines += ["mcp-plugin-pool:\n", *pool[-12:]]
        from backend.store.repos import events
        from backend.util.privacy import scrub

        now = time.time()
        stack = scrub("".join(lines))[-8000:]
        conv_id = str(getattr(ident, "conv_id", "") or "")
        payload: dict[str, Any] = {"tool": name, "seconds": seconds, "conv_id": conv_id,
                                   "coding_agent": str(getattr(ident, "coding_agent", "") or ""), "stack": stack}
        waiting = dict(waits or {})
        if waiting:
            payload["waiting_on"] = waiting
        if name == "ducky_terminal_run":
            # Root cause 11: a run that waits on the person's approval card looks like a stuck command.
            payload["phase"] = "approval card" if waiting.get("panel_rpc") == "ask_user" else "running command"
        what = payload.get("phase") or (f"panel_rpc {waiting['panel_rpc']}" if waiting.get("panel_rpc") else "")
        message = (f"{name}: waiting_on {what} (conv {conv_id[:8]}, {seconds:.0f} s)" if what
                   else f"{name} still running after {seconds:.0f} s")
        events.insert("tool_stuck", ts=now, source=name, message=message, payload=payload)
        events.trim("tool_stuck", older_than=now - 30 * 86400, keep=200)
        return not (name in call_limits.WAITS_BY_DESIGN or waiting.get("panel_rpc") == "ask_user")
    except Exception:  # noqa: BLE001 — a report never breaks the call
        return False
