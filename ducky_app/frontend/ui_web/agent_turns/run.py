"""The embedded agent turn: the AgentRunner event loop, its tool and usage pushes, and the
helpers a turn's start uses (who started it, video frames, resume payload)."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from backend.agent.delegation_guard import append_delegation_warning, fake_delegation_warning
from backend.agent.runner import AgentRunner, RunConfig
from backend.agent.toolsets import is_plan_safe_tool
from backend.agent.verify_evidence import (
    append_verify_warning,
    fake_verify_warning,
    handoff_warning,
)
from backend.agent.write_claim_guard import (
    append_write_claim_warning,
    fake_write_warning,
    record_write_from_tool,
)
from frontend.ui_web.agent_turns.errors import (
    _continue_on_claude_code,
    _crash_row,
    _is_transient,
    _log_agent_crash,
    _schedule_auto_resume,
)
from frontend.ui_web.agent_turns.push import PushFn, _set_active_conv_id, get_active_conv_id
from frontend.ui_web.agent_turns.state import (
    AgentSession,
    _child_waiters,
    _push_agent_stopped,
    _sessions_lock,
)
from frontend.ui_web.chats.saves import save_conversation
from frontend.ui_web.token_usage import record_api_call, token_usage_report


def _push_token_usage(conv, push: PushFn, *, call_input: int = 0, call_output: int = 0, step: int = 0, usage: dict[str, int] | None = None) -> None:
    report = token_usage_report(conv)
    u = usage or {}
    push(
        {
            "type": "usage",
            "conv_id": conv.id,
            "input_tokens": report["total_input"],
            "output_tokens": report["total_output"],
            "total_tokens": report["total_tokens"],
            "total_cache_read": report.get("total_cache_read", 0),
            "total_cache_write": report.get("total_cache_write", 0),
            "cache_hit_rate": report.get("cache_hit_rate", 0),
            "call_count": report["call_count"],
            "call_input": call_input,
            "call_output": call_output,
            "call_cache_read": int(u.get("cache_read_tokens") or 0),
            "call_cache_write": int(u.get("cache_write_tokens") or 0),
            "step": step,
            "calls": report["calls"],
        }
    )


def _last_assistant_text(conv) -> str:
    for m in reversed(conv.messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _tool_line(name: str, args: dict[str, Any], status: str = "", ms: int = 0) -> str:
    args_short = json.dumps(args, ensure_ascii=False)
    if len(args_short) > 120:
        args_short = args_short[:117] + "..."
    base = f"{name}({args_short})"
    if status and ms:
        return f"{base} · {status} · {ms}ms"
    return base


UI_TOOL_RESULT_MAX = 16_384


def _cap_ui_result(text: str, limit: int = UI_TOOL_RESULT_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… [truncated — ducky_read_tool_spill]"


def _tool_result_text(result: dict[str, Any] | None) -> str:
    if not result:
        return ""
    if result.get("data"):
        return str(result["data"])
    if result.get("error"):
        return str(result["error"])
    return json.dumps(result, ensure_ascii=False)


def _push_tool_done(push: PushFn, conv_id: str, rec: Any) -> None:
    args = dict(rec.arguments or {})
    status = getattr(rec, "status", "success")
    ms = int(getattr(rec, "duration_ms", 0) or 0)
    raw_result = getattr(rec, "result", None) or {}
    if not isinstance(raw_result, dict):
        raw_result = {}
    result_text = _cap_ui_result(_tool_result_text(raw_result))
    tool_payload: dict[str, Any] = {
        "id": str(getattr(rec, "id", "") or ""),
        "name": rec.name,
        "arguments": args,
        "status": status,
        "durationMs": ms,
        "result": result_text,
        "hint": str(raw_result.get("hint") or ""),
    }
    llm_tokens = int(getattr(rec, "llm_tokens", 0) or 0)
    if llm_tokens:
        tool_payload["llmTokens"] = llm_tokens
    try:
        from frontend.ui_web.verse_editor.agent_sync import build_file_edit_meta

        file_edit = build_file_edit_meta(rec.name, args, raw_result)
        if file_edit:
            tool_payload["fileEdit"] = file_edit
    except Exception:
        pass
    try:
        from frontend.perf_trace import trace

        trace(
            "tool_push",
            str(rec.name or "tool"),
            float(ms),
            result_bytes=len(result_text),
            status=status,
        )
    except Exception:
        pass
    try:
        from backend.agent.toolsets import effective_tool_name
        from frontend import usage_stats

        # A call the repeat guard blocked never ran: it is not a failed tool call (Oct 21
        # 2026: 604 "failed" project_memory_get were one looping agent). ducky_call_tool counts
        # as the tool it ran, so a failure names the tool that failed.
        if str(raw_result.get("hint") or "") not in ("repeat-call guard", "asked already"):
            ok = status in ("", "success", "ok")
            fail = "" if ok else str(raw_result.get("fail") or "") or usage_stats.fail_of(_tool_result_text(raw_result))
            usage_stats.tool_done(
                effective_tool_name(str(rec.name or "tool"), args), ok=ok,
                duration_ms=float(ms), conv_id=conv_id, args=args, fail=fail,
            )
    except Exception:
        pass
    push(
        {
            "type": "tool_done",
            "text": f"⚙ {_tool_line(rec.name, args, status, ms)}",
            "success": status == "success",
            "conv_id": conv_id,
            "tool": tool_payload,
        }
    )
    try:
        from frontend.ui_web.verse_editor.agent_sync import emit_editor_events

        emit_editor_events(push, conv_id, rec)
    except Exception:
        pass


def _approve_tool_calls(conv_id: str, pending: list[Any]) -> bool:
    """The chat's approval card for tool calls its approval mode asks about."""
    from backend.agent.toolsets import effective_tool_name
    from backend.tools.panel.permission_prompt import approve_ducky_tool

    for rec in pending:
        args = dict(rec.arguments or {}) if isinstance(rec.arguments, dict) else {}
        name = effective_tool_name(rec.name, args)
        if rec.name == "ducky_call_tool" and isinstance(args.get("arguments"), dict):
            args = dict(args["arguments"])
        if not approve_ducky_tool(conv_id, name, args):
            return False
    return True


async def _run_agent_loop(
    conv,
    user_text: str,
    history: list[dict[str, Any]],
    *,
    config: RunConfig,
    push: PushFn,
    session: AgentSession,
    run_id: str,
    plan_filter: bool = False,
    user_attachments: list[dict[str, Any]] | None = None,
    resume: bool = False,
) -> str:
    cancel = session._cancel
    stop_reason = "error"
    err_text = ""
    runner = AgentRunner(config)
    runner.set_approval_callback(lambda pending: _approve_tool_calls(conv.id, pending))
    session.set_runner(runner, run_id=run_id)
    _set_active_conv_id(conv.id)
    t_start = time.monotonic()
    t_first: float | None = None
    delegation_tools_called: set[str] = set()
    written_relpaths: set[str] = set()
    write_failures: list[str] = []
    tools_run = 0
    asked_user = False
    turn_started = time.time()  # pictures made for this chat from now on show in the reply
    from backend.agent.coding_agents.runner import _TurnCheckpoint
    from backend.agent.toolsets import effective_tool_name
    from frontend.ui_web.chats.saves import upsert_in_flight_assistant

    ckpt = _TurnCheckpoint(conv, "ducky", run_id)
    push = ckpt.wrap(push)
    ckpt.seed()
    turn = runner.run_turn(
        user_text, history, user_attachments=user_attachments,
        thread_cancel=cancel, resume=resume,
    )
    try:
        async for event in turn:
            if cancel.is_set():
                runner.cancel()
                stop_reason = "cancelled"
                partial = event.partial_message
                if partial:
                    upsert_in_flight_assistant(conv, partial, run_id=run_id)
                else:
                    ckpt.flush(error="Stopped")
                return stop_reason
            if t_first is None and event.kind in ("text_delta", "thinking"):
                t_first = time.monotonic()
            if event.kind == "tool_start" and event.tool:
                rec = event.tool
                if rec.name in ("ducky_spawn_chat", "ducky_send_chat_message"):
                    delegation_tools_called.add(rec.name)
                if effective_tool_name(rec.name, rec.arguments) == "ducky_ask_user":
                    asked_user = True
                if plan_filter and not is_plan_safe_tool(rec.name):
                    continue
                push(
                    {
                        "type": "tool",
                        "text": f"⚙ {_tool_line(rec.name, dict(rec.arguments or {}))}",
                        "conv_id": conv.id,
                        "tool": {
                            "id": str(getattr(rec, "id", "") or ""),
                            "name": rec.name,
                            "arguments": dict(rec.arguments or {}),
                            "status": "pending",
                        },
                    }
                )
            elif event.kind == "tool_end" and event.tool:
                rec = event.tool
                tools_run += 1
                record_write_from_tool(rec, written_relpaths, write_failures)
                if plan_filter and not is_plan_safe_tool(rec.name):
                    continue
                _push_tool_done(push, conv.id, rec)
            elif event.kind == "status":
                payload: dict[str, Any] = {
                    "type": "status",
                    "text": event.text,
                    "conv_id": conv.id,
                }
                if event.percent is not None:
                    payload["percent"] = event.percent
                push(payload)
            elif event.kind == "text_delta":
                push({"type": "text_delta", "text": event.text, "conv_id": conv.id})
            elif event.kind == "thinking":
                push({"type": "thinking", "text": event.text, "conv_id": conv.id})
            elif event.kind == "error":
                if event.text == "Cancelled":
                    partial = event.partial_message
                    if partial:
                        upsert_in_flight_assistant(conv, partial, run_id=run_id)
                    stop_reason = "cancelled"
                else:
                    partial = event.partial_message
                    err_text = event.text or "LLM error"
                    auto = bool(tools_run) and _is_transient(err_text)
                    if auto:
                        auto = _schedule_auto_resume(conv.id, err_text, "plan" if plan_filter else "agent")
                    # Persist so reload / empty stream still shows the crash and what the
                    # turn already did (UI used to drop standalone role=error rows).
                    upsert_in_flight_assistant(
                        conv,
                        _crash_row(partial, err_text, tools_run=tools_run,
                                   written=written_relpaths, auto_resume=auto),
                        run_id=run_id,
                    )
                    _log_agent_crash(
                        conv,
                        provider=config.provider,
                        model=config.model,
                        error=err_text,
                        partial=partial,
                        elapsed_s=time.monotonic() - t_start,
                        first_token_s=(t_first - t_start) if t_first is not None else None,
                    )
                    push(
                        {
                            "type": "error",
                            "text": err_text,
                            "conv_id": conv.id,
                            "run_id": run_id,
                            "kept_partial": bool(partial),
                        }
                    )
                    from backend.agent.providers import is_plan_limit

                    if is_plan_limit(err_text):
                        # Off this thread: detect can probe the CLI for seconds.
                        threading.Thread(
                            target=_continue_on_claude_code,
                            args=(conv.id, "plan" if plan_filter else "agent", push),
                            daemon=True,
                        ).start()
                return stop_reason
            elif event.kind == "usage_step":
                u = event.usage or {}
                record_api_call(
                    conv,
                    input_tokens=int(u.get("input_tokens") or 0),
                    output_tokens=int(u.get("output_tokens") or 0),
                    cache_read_tokens=int(u.get("cache_read_tokens") or 0),
                    cache_write_tokens=int(u.get("cache_write_tokens") or 0),
                    step=int(event.step or 0),
                    provider=config.provider,
                    model=config.model,
                )
                save_conversation(conv)
                _push_token_usage(
                    conv,
                    push,
                    call_input=int(u.get("input_tokens") or 0),
                    call_output=int(u.get("output_tokens") or 0),
                    step=int(event.step or 0),
                    usage=u,
                )
            elif event.kind == "done" and event.assistant_message:
                assistant_msg = event.assistant_message
                from frontend.ui_web.generated_images import turn_attachments

                generated_atts = turn_attachments(conv.id, turn_started)
                if generated_atts:
                    assistant_msg = dict(assistant_msg)
                    existing = list(assistant_msg.get("attachments") or [])
                    assistant_msg["attachments"] = existing + generated_atts
                warning = fake_delegation_warning(conv, assistant_msg, delegation_tools_called)
                if warning:
                    assistant_msg = append_delegation_warning(assistant_msg, warning)
                    push({"type": "delegation_warning", "text": warning, "conv_id": conv.id})
                write_warning = fake_write_warning(assistant_msg, written_relpaths, write_failures)
                if write_warning:
                    assistant_msg = append_write_claim_warning(assistant_msg, write_warning)
                    push({"type": "delegation_warning", "text": write_warning, "conv_id": conv.id})
                verify_warning = fake_verify_warning(assistant_msg)
                if verify_warning:
                    assistant_msg = append_verify_warning(assistant_msg, verify_warning)
                    push({"type": "delegation_warning", "text": verify_warning, "conv_id": conv.id})
                handoff = handoff_warning(assistant_msg, asked_user=asked_user)
                if handoff:
                    assistant_msg = append_verify_warning(
                        assistant_msg, handoff, title="Hand-off"
                    )
                    push({"type": "delegation_warning", "text": handoff, "conv_id": conv.id})
                upsert_in_flight_assistant(conv, assistant_msg, run_id=run_id)
                save_conversation(conv)
                stop_reason = "done"
                push({"type": "assistant_done", "conv_id": conv.id})
                return stop_reason
        return stop_reason
    finally:
        # Close in the consuming task, before asyncio shutdown creates another
        # task/context to finalize the generator's ContextVar tokens.
        await turn.aclose()
        session.clear_runner(run_id=run_id)
        if get_active_conv_id() == conv.id:
            _set_active_conv_id(None)
        # Always close THIS run's ledger. A follow-up in the same chat already
        # replaced session.run_id; gating on it left Revert stuck on "running".
        _push_agent_stopped(push, conv.id, run_id, stop_reason, detail=err_text)
        with _sessions_lock:
            waiters = _child_waiters.get(conv.id)
            if waiters is not None and not waiters:
                _child_waiters.pop(conv.id, None)
        from frontend.ui_web.live_agent_runs import discard_live_run_id

        discard_live_run_id(run_id)


def _note_run_starter(conv_id: str, parent: str, started_by: str | None) -> None:
    """Who started this run: "Allow everything" in that chat covers it. Set by the chat that
    sent the work (parent) or by the caller (started_by, '' = nobody); a plain follow-up
    keeps the link it had. Never the chat that merely happens to be open."""
    if started_by is None and not (parent or "").strip():
        return
    try:
        from backend.tools.panel.permission_prompt import note_started_by

        note_started_by(conv_id, parent if started_by is None else started_by)
    except Exception:
        pass


def clear_last_interrupt(conv) -> bool:
    """Drop the interrupt footer. Keep partial text, thinking, ts, usage, blocks.

    Empty error-only assistant rows (and standalone role=error) are removed so
    resume does not feed a blank assistant to the model.
    """
    msgs = getattr(conv, "messages", None)
    if not isinstance(msgs, list) or not msgs:
        return False
    last = msgs[-1]
    if not isinstance(last, dict):
        return False
    if not (last.get("incomplete") or last.get("role") == "error"):
        return False
    empty = (
        not str(last.get("content") or last.get("text") or "").strip()
        and not str(last.get("thinking") or "").strip()
        and not last.get("blocks")
    )
    if empty or last.get("role") == "error":
        msgs.pop()
    else:
        last.pop("incomplete", None)
        last.pop("error", None)
    return True


def last_user_payload(conv) -> tuple[str, Any, list[dict[str, Any]]] | None:
    for m in reversed(getattr(conv, "messages", None) or []):
        if isinstance(m, dict) and m.get("role") == "user":
            text = str(m.get("text") or "")
            content = m.get("content", text)
            if content is None or content == "":
                content = text
            atts = m.get("attachments")
            return text, content, list(atts) if isinstance(atts, list) else []
    return None
