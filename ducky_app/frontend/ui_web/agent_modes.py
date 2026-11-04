"""Ask / Plan / Agent mode dispatch for the React panel (ui_web only)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from functools import wraps
from collections.abc import Callable
from typing import Any

from frontend.ui_web.project_chats import append_message, load_conversation, save_conversation
from frontend.ui_web.token_usage import record_api_call, token_usage_report
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings, apply_workspace_env
from backend.agent.attachments import parse_attachment_dicts
from backend.agent.runner import AgentRunner, RunConfig
from backend.agent.secrets import get_key
from backend.agent.delegation_guard import append_delegation_warning, fake_delegation_warning
from backend.agent.write_claim_guard import (
    append_write_claim_warning,
    fake_write_warning,
    record_write_from_tool,
)
from backend.agent.verify_evidence import (
    append_verify_warning,
    fake_verify_warning,
    handoff_warning,
)
from backend.agent.toolsets import is_plan_safe_tool
from backend.agent.coding_agents.plans import PLAN_PROTOCOL

from frontend.ui_web.agent_turns import errors as _errors_part
from frontend.ui_web.agent_turns import pending as _pending_part
from frontend.ui_web.agent_turns import push as _push_part
from frontend.ui_web.agent_turns.pending import _with_admission
from frontend.ui_web.agent_turns import state as _state_part
from frontend.ui_web.agent_turns.errors import (
    _continue_on_claude_code,
    _crash_row,
    _is_transient,
    _log_agent_crash,
    _schedule_auto_resume,
)
from frontend.ui_web.agent_turns.push import (
    PushFn,
    _forward_to_panel,
    _in_bridge_process,
    _post_panel_run,
    _resolve_push,
    _set_active_conv_id,
    get_active_conv_id,
    get_panel_push,
    notify_chats_changed,
    notify_context_changed,
    push_ui_event,
    set_panel_push,
)

_ASK_SUFFIX = "\n\n[Mode: Ask] Read, search and discover evidence, then answer. Do not modify projects, plans, settings or assets, rename chats, delegate, or execute code."

_PLAN_SUFFIX = (
    "\n\n[Mode: Plan] Discovery and planning only. Prefer read-only inspection tools. "
    "Do not modify the level, devices, or project files until the user confirms. "
    "REQUIRED: end by calling `ducky_create_plan` (or `ducky_update_plan`) — never leave "
    "only a prose Fix plan / markdown checklist in chat. "
    f"{PLAN_PROTOCOL} "
    "Then STOP — user reviews and switches to Agent mode (or Send to ducky). "
    "Do not execute in Plan mode."
)

_AGENT_SUFFIX = (
    "\n\n[Mode: Agent] Multi-step work: if this chat has no plan yet, create one with "
    "`ducky_create_plan` after brief discovery (a few inspects), BEFORE mutators. "
    f"{PLAN_PROTOCOL} "
    "Never replace the tool plan with chat prose."
)


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
    from frontend.ui_web.project_chats import upsert_in_flight_assistant

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


def _backfill_video_frames(
    conv: Any, conv_id: str, provider: str, external: bool, push: Any, project_root: Any, model: str = ""
) -> None:
    """Give earlier videos frames when the current recipient can't take them natively."""
    if external:
        return  # coding agents only ever get the latest message's files, never history frames
    if not any(
        isinstance(m, dict)
        and m.get("role") == "user"
        and any(isinstance(r, dict) and r.get("kind") == "video" for r in m.get("attachments") or [])
        for m in conv.messages
    ):
        return
    from backend.agent.video.send import backfill_history_frames
    from frontend.ui_web.project_chats import get_conversations_dir

    changed = backfill_history_frames(
        conv.messages,
        conv_dir=get_conversations_dir(project_root) / conv_id,
        provider=provider,
        external=external,
        push_status=lambda text: push({"type": "status", "text": text, "conv_id": conv_id}),
        model=model,
    )
    if changed:
        try:
            save_conversation(conv)
        except Exception:
            pass


def run_message_and_wait(
    conv_id: str,
    text: str,
    mode: str = "agent",
    model: str = "",
    *,
    timeout_sec: float = 180.0,
    push: PushFn | None = None,
    cancel_on_timeout: bool = True,
    parent: str = "",
    attachments: list[dict[str, Any]] | None = None,
    started_by: str | None = None,
    queue_if_busy: bool = False,
    _local: bool = False,
) -> dict[str, Any]:
    """Send a message, run the target chat's agent, and block until done or timeout.

    started_by: the chat whose "Allow everything" covers this run ('' = none). None keeps
    what the chat had, unless ``parent`` names the chat that sent the work.
    queue_if_busy (another agent sends it): a busy chat keeps its turn and runs this message
    after it ({"status": "queued"}). Without it the new message replaces the running turn,
    as a person's follow-up does."""
    if not _local and _in_bridge_process():
        resp = _post_panel_run(
            {
                "conv_id": conv_id,
                "text": text,
                "mode": mode,
                "model": model,
                "wait": True,
                "queue_if_busy": bool(queue_if_busy),
                "timeout_sec": float(timeout_sec),
                "cancel_on_timeout": bool(cancel_on_timeout),
                # Carry the spawning chat so the panel nests the child under it.
                "parent_conv_id": parent,
                "attachments": attachments or None,
                **({} if started_by is None else {"started_by": started_by}),
            },
            http_timeout=(
                None
                if float(timeout_sec) <= 0
                else max(5.0, float(timeout_sec) + 15.0)
            ),
        )
        if resp is not None:
            if resp.get("_delegation_timeout"):
                # Panel is running the turn but hasn't answered — don't re-run.
                return {
                    "status": "timeout",
                    "conv_id": conv_id,
                    "error": (
                        f"No reply within {timeout_sec}s — the agent is still working; "
                        "its reply will arrive in your inbox"
                    ),
                }
            return resp
        # Panel unreachable — fall back to running the turn locally in the bridge.

    if get_active_conv_id() == conv_id:
        raise ValueError(
            "Cannot wait for a reply in the chat you are currently running in. "
            "Use wait_for_reply=false or target a different conv_id."
        )
    # Oct 21 2026: a lead's sends cancelled its busy member's turn four times in a row.
    if queue_if_busy and is_agent_running(conv_id):
        started = run_message(conv_id, text, mode, model, parent=parent, started_by=started_by,
                              attachments=attachments, queue_if_busy=True)
        if started == "queued":
            return {"status": "queued", "conv_id": conv_id, "error": BUSY_QUEUED}
        return {"status": "running", "conv_id": conv_id, "run_id": started}

    done = threading.Event()
    result: dict[str, Any] = {"status": "pending", "conv_id": conv_id}

    def collecting_push(event: dict[str, Any]) -> None:
        _resolve_push(push)(event)
        kind = event.get("type")
        if kind == "linked_agent" and event.get("child_conv_id") == conv_id:
            status = str(event.get("status") or "")
            if status in ("done", "error", "cancelled", "timeout"):
                conv = load_conversation(conv_id)
                result["status"] = "cancelled" if status == "cancelled" else ("error" if status != "done" else "done")
                if status == "done":
                    result["assistant_text"] = _last_assistant_text(conv) if conv else ""
                elif status == "timeout":
                    result["error"] = f"No reply within {timeout_sec}s"
                elif status == "cancelled":
                    result["error"] = "Cancelled"
                else:
                    result["error"] = str(event.get("error") or "Linked agent error")
                done.set()
            return
        if event.get("conv_id") != conv_id:
            return
        if kind == "assistant_done":
            conv = load_conversation(conv_id)
            result["status"] = "done"
            result["assistant_text"] = _last_assistant_text(conv) if conv else ""
            done.set()
        elif kind == "agent_stopped":
            reason = str(event.get("reason") or "error")
            result["status"] = "cancelled" if reason == "cancelled" else ("done" if reason == "done" else "error")
            if result["status"] == "done":
                conv = load_conversation(conv_id)
                result["assistant_text"] = _last_assistant_text(conv) if conv else ""
            elif result["status"] == "cancelled":
                result["error"] = "Cancelled"
            else:
                result["error"] = reason
            done.set()
        elif kind == "error":
            text = str(event.get("text") or "Agent error")
            result["status"] = "cancelled" if text == "Cancelled" else "error"
            result["error"] = text
            done.set()
        elif kind == "model_needed":
            result["status"] = "waiting_for_model"
            result["error"] = (f"{event.get('text') or 'This chat has no model.'} The person was asked to "
                               "pick one; your message runs then and its reply arrives in your inbox.")
            done.set()

    parent_active = (parent or "").strip() or get_active_conv_id()
    if parent_active and parent_active != conv_id:
        with _sessions_lock:
            _child_waiters.setdefault(parent_active, set()).add(conv_id)

    try:
        run_message(
            conv_id,
            text,
            mode,
            model,
            push=collecting_push,
            force=True,
            parent=parent,
            started_by=started_by,
            attachments=attachments,
        )
        # timeout_sec <= 0: wait until the agent finishes (no wall-clock interrupt).
        # A pending ask_user pauses the clock — the agent is waiting on the user, not stalled.
        if float(timeout_sec) <= 0:
            done.wait()
        else:
            from frontend.ui_web.ui_rpc import has_pending_for_conv

            remaining = max(1.0, float(timeout_sec))
            while remaining > 0 and not done.is_set():
                if has_pending_for_conv(conv_id):
                    done.wait(timeout=1.0)
                    continue
                t0 = time.monotonic()
                if done.wait(timeout=min(1.0, remaining)):
                    break
                remaining -= time.monotonic() - t0
            if not done.is_set():
                if cancel_on_timeout:
                    cancel_agent(conv_id)
                parent = _linked_parents.get(conv_id)
                if parent:
                    conv = load_conversation(conv_id)
                    title = conv.title if conv else "Chat"
                    _push_linked_agent(_resolve_push(push), parent, conv_id, title, "timeout")
                    _linked_parents.pop(conv_id, None)
                result["status"] = "timeout"
                result["error"] = (
                    f"No reply within {timeout_sec}s"
                    if cancel_on_timeout
                    else f"No reply within {timeout_sec}s — the agent is still working; its reply will arrive in your inbox"
                )
    finally:
        if parent_active and parent_active != conv_id:
            with _sessions_lock:
                _child_waiters.get(parent_active, set()).discard(conv_id)
    return result


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


@_with_admission
def run_message(
    conv_id: str,
    user_text: str,
    mode: str,
    model: str,
    *,
    push: PushFn | None = None,
    attachments: list[dict[str, Any]] | None = None,
    force: bool = False,
    parent: str = "",
    resume: bool = False,
    started_by: str | None = None,
    queue_if_busy: bool = False,
    _local: bool = False,
) -> str:
    """Start a turn. ``queue_if_busy`` (agent-to-agent delivery): when the chat is mid-turn,
    hold the text and start it when that turn ends (returns "queued") instead of the
    Stop → follow-up a person gets, which cancelled the receiver and dropped its inbox."""
    if not _local and _in_bridge_process():
        resp = _post_panel_run(
            {
                "conv_id": conv_id,
                "text": user_text,
                "mode": mode,
                "model": model,
                "wait": False,
                "force": bool(force),
                "queue_if_busy": bool(queue_if_busy),
                "resume": bool(resume),
                "attachments": attachments or None,
                # Carry the spawning chat across the process hop so the delegated
                # run in the panel nests the child under its parent (linked_agent).
                "parent_conv_id": parent,
                **({} if started_by is None else {"started_by": started_by}),
            },
            http_timeout=30.0,
        )
        if resp is not None:
            # On a timeout the panel has already started the run — don't re-run.
            if resp.get("_delegation_timeout"):
                return ""
            return str(resp.get("run_id") or "")
        # Panel unreachable — fall back to running the turn locally in the bridge.
        return run_message(conv_id, user_text, mode, model, push=push,
                           attachments=attachments, force=force, parent=parent,
                           resume=resume, started_by=started_by,
                           queue_if_busy=queue_if_busy, _local=True)

    push = _make_broker_tap(_resolve_push(push), conv_id)
    conv = load_conversation(conv_id)
    if not conv:
        push({"type": "error", "text": "Conversation not found", "conv_id": conv_id})
        return ""
    _note_run_starter(conv_id, parent, started_by)

    run_id = str(uuid.uuid4())
    # Recorded once the turn really starts below: a send refused here (no model, no
    # key, a bad attachment) never reaches agent_stopped to remove it again.
    started_at = time.monotonic()

    settings = PanelSettings.load()
    apply_workspace_env(settings.uefn_project_root)
    from backend.agent.attachments import prepare_outgoing_user_message
    from backend.agent.coding_agents.base import normalize_coding_agent

    coding_agent = normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky")

    # Exact model for this conversation only. A Ducky chat with none takes the Default
    # Model, else UEFN Ducky's own model; with neither, the message waits for the person
    # to pick one (it used to error, and the agent or team that sent it stopped there).
    turn_model = (model or getattr(conv, "model", None) or "").strip()
    if turn_model.lower() == "default":
        turn_model = ""
    why = ""
    if not turn_model and coding_agent == "ducky" and not getattr(conv, "is_group", False):
        turn_model, coding_agent, why = _take_chat_model(conv, settings, push)
    if not turn_model:
        why = why or "This chat has no model. Pick a model below and your message runs."
        if resume or not (user_text or "").strip():
            push({"type": "error", "conv_id": conv_id, "text": why})
            return ""
        return _hold_for_model(conv, user_text, mode, attachments, parent, started_by, why, push)

    # A plugin's remote gateway (its ``remote`` option) runs the model on a server: the turn
    # is Ducky's own agent, never a coding agent's CLI with that gateway's model id. A model
    # id the gateway renamed (``model_aliases``) moves to the current one.
    from backend.agent.model_pricing import infer_provider
    from backend.uefn_plugins.host import current_model_id, provider_option

    recorded_provider = (getattr(conv, "provider", None) or "").strip().lower()
    remote = next(
        (p for p in (recorded_provider, infer_provider(turn_model)) if p and provider_option(p, "remote")), ""
    )
    if remote:
        coding_agent = "ducky"
        conv.coding_agent = "ducky"
        conv.provider = remote
        if ":" in turn_model:
            turn_model = turn_model.split(":")[-1].strip() or turn_model
        turn_model = current_model_id(remote, turn_model)
        conv.model = turn_model
        try:
            save_conversation(conv)
        except Exception:
            pass
    external = coding_agent != "ducky"

    if external:
        provider_name = (getattr(conv, "provider", None) or "").strip()
    else:
        from backend.agent.model_pricing import resolve_provider_for_model

        provider_name = resolve_provider_for_model(
            turn_model, (getattr(conv, "provider", None) or "").strip()
        ) or (settings.agent_provider or "")
        if provider_name and provider_name != (getattr(conv, "provider", None) or "").strip().lower():
            conv.provider = provider_name
            try:
                save_conversation(conv)
            except Exception:
                pass
        try:
            from backend.uefn_plugins.host import resolve_gateway_credential

            connected = bool(resolve_gateway_credential(provider_name))
        except Exception:
            connected = bool(get_key(provider_name))
        if not connected:
            # Said so a person can act on it ("No API key configured" meant nothing to most),
            # and the composer shows Connect <gateway> / Pick another model for it.
            label = _provider_label(provider_name)
            push({"type": "provider_not_connected", "conv_id": conv_id, "provider": provider_name,
                  "label": label, "model": turn_model})
            push({"type": "error", "conv_id": conv_id,
                  "text": f"{label} isn't connected yet, so {turn_model} can't answer. "
                          f"Connect {label} or pick another model below."})
            return ""

    if resume:
        clear_last_interrupt(conv)
        payload = last_user_payload(conv)
        if not payload:
            push({"type": "error", "text": "Nothing to continue", "conv_id": conv_id})
            return ""
        user_text, content, _stored_atts = payload
        current_user_attachments: list[dict[str, Any]] = []
        try:
            save_conversation(conv)
        except Exception:
            pass
        _backfill_video_frames(
            conv, conv_id, provider_name or "", external, push, settings.uefn_project_root, turn_model
        )
        history = list(conv.messages)
    else:
        try:
            content, _stored = prepare_outgoing_user_message(
                user_text,
                attachments,
                provider=provider_name or "",
                model=turn_model,
                external_agent=external,
                coding_agent=coding_agent,
                inline_files=False,
            )
        except ValueError as e:
            push({"type": "error", "text": str(e), "conv_id": conv_id})
            return ""

        if getattr(settings, "prompt_dedupe_exact_blocks", False):
            from backend.agent.prompt_dedupe import dedupe_exact_blocks

            content = dedupe_exact_blocks(content)
            user_text = dedupe_exact_blocks(user_text)

        try:
            attachments_parsed = parse_attachment_dicts(
                attachments, current=True, provider=provider_name or "", model=turn_model
            )
        except ValueError as e:
            push({"type": "error", "text": str(e), "conv_id": conv_id})
            return ""
        ts = time.time()
        from frontend.ui_web.conversation_attachments import persist_message_attachments
        from frontend.ui_web.project_chats import get_conversations_dir

        conversations_dir = get_conversations_dir(settings.uefn_project_root)
        try:
            stored_attachments = persist_message_attachments(
                conv_id,
                ts,
                attachments_parsed,
                conversations_dir,
                settings.uefn_project_root,
            )
        except ValueError as e:
            push({"type": "error", "text": str(e), "conv_id": conv_id})
            return ""
        conv_dir_path = conversations_dir / conv_id
        from backend.agent.attachments import add_file_blocks

        # Files go in after saving: a big one is sent by path for the agent to read in parts.
        content = add_file_blocks(
            content, attachments_parsed, stored_attachments, conv_dir_path,
            provider=provider_name or "", model=turn_model, coding_agent=coding_agent,
        )
        from backend.agent.video.send import (
            external_video_hint,
            prepare_video_frames,
            runtime_video_dict,
        )

        if any(r.get("kind") == "video" for r in stored_attachments):
            try:
                prepare_video_frames(
                    stored_attachments,
                    conv_dir=conv_dir_path,
                    provider=provider_name or "",
                    external=external,
                    push_status=lambda text: push({"type": "status", "text": text, "conv_id": conv_id}),
                    model=turn_model,
                )
            except ValueError as e:
                push({"type": "error", "text": str(e), "conv_id": conv_id})
                return ""
            if external:
                hints = [
                    external_video_hint(r, conv_dir_path)
                    for r in stored_attachments
                    if r.get("kind") == "video"
                ]
                content = (content + "\n\n" if content else "") + "\n".join(hints)
        current_user_attachments = [
            {
                "kind": a.kind,
                "name": a.name,
                "mime": a.mime,
                **({"data_base64": a.data_base64} if a.kind == "image" else {"text": a.text}),
            }
            for a in attachments_parsed
            if a.kind != "video"
        ] + [
            runtime_video_dict(r, conv_dir_path)
            for r in stored_attachments
            if r.get("kind") == "video"
        ]

        user_msg: dict[str, Any] = {"role": "user", "content": content, "text": user_text, "ts": ts}
        if stored_attachments:
            user_msg["attachments"] = stored_attachments
        with _admission_lock:
            _check_admission(*_admission_context.current)
            append_message(conv, user_msg)
        if len(conv.messages) == 1:
            from backend.agent.chat_title import start_auto_title

            start_auto_title(conv, user_text or content, push=push)
        _backfill_video_frames(
            conv, conv_id, provider_name or "", external, push, settings.uefn_project_root, turn_model
        )
        history = list(conv.messages[:-1])

    from frontend.ui_web.context_omit import context_omit_set

    omit = context_omit_set(conv)
    session = _session(conv_id)
    m = (mode or "agent").lower()
    plan_filter = m == "plan"

    # Resolve the parent before branching to embedded vs external agents. The
    # external path used to return early, so it never emitted linked_agent
    # events and the parent chat had no live sub-agent card.
    active_parent = (parent or "").strip() or get_active_conv_id()
    parent_conv_id: str | None = None
    if active_parent and active_parent != conv_id:
        parent_conv_id = active_parent
        _linked_parents[conv_id] = parent_conv_id
        _quiet_runs.add(run_id)
    elif _started_by_workflow():
        _quiet_runs.add(run_id)
    child_title = conv.title or "Chat"
    _run_started[run_id] = started_at
    try:
        from frontend import usage_stats

        usage_stats.run_begin(
            run_id, conv_id, coding_agent=coding_agent, provider=provider_name or "", model=turn_model or "",
        )
    except Exception:
        pass

    if external:
        # BYOA path: Claude Code / Codex / Cursor — no embedded AgentRunner.
        session.prepare_run(run_id)
        base_push = _wrap_push_for_linked_child(
            push, parent_conv_id=parent_conv_id, child_conv_id=conv_id, child_title=child_title
        )
        push = _make_run_scoped_push(base_push, session, conv_id, run_id)
        if parent_conv_id:
            _push_linked_agent(push, parent_conv_id, conv_id, child_title, "running")
            # Group roundtables must not auto-open member tabs (breaks immersion).
            from frontend.ui_web.group_orchestrator import is_group_conversation

            parent_conv = load_conversation(parent_conv_id)
            if not (parent_conv and is_group_conversation(parent_conv)):
                notify_chats_changed(conv_id, child_title, conv.folder_id, push=push)
        cancel_event = session._cancel

        def work_external() -> None:
            fresh = conv
            # The coding-agent runner pushes its own agent_stopped: its reason is the alert's.
            ended: dict[str, str] = {}

            def watched_push(event: dict[str, Any]) -> None:
                if event.get("type") == "agent_stopped" and event.get("run_id", run_id) == run_id:
                    if "reason" not in ended:
                        ended["reason"] = str(event.get("reason") or "")
                        ended["detail"] = str(event.get("detail") or "")
                push(event)

            try:
                from backend.agent.coding_agents.runner import run_coding_agent_message
                from frontend.agent_host import turns as host_turns

                # The whole message, not just the typed words: attached files, picture
                # paths and video notes live in ``content``.
                turn_text = (
                    "Continue."
                    if resume
                    else ((content if isinstance(content, str) else str(content)) or user_text)
                )
                # The CLI and its reader threads run in an agent-host process when one starts.
                outcome = (
                    host_turns.run_in_host(conv_id, run_id, turn_text, model=turn_model, mode=m,
                                           push=watched_push, cancel=cancel_event)
                    if host_turns.enabled() else None
                )
                if outcome:
                    raise host_turns.HostTurnError(outcome)
                if outcome is None:
                    # Reload conv so append_message in runner sees the user turn we just saved.
                    fresh = load_conversation(conv_id) or conv
                    run_coding_agent_message(
                        fresh,
                        turn_text,
                        model=turn_model,
                        push=watched_push,
                        run_id=run_id,
                        cancel=cancel_event,
                        mode=m,
                    )
            except Exception as e:
                from backend.agent.coding_agents.runner import checkpoint_coding_turn
                from frontend.agent_host.turns import HostTurnError

                host_error = isinstance(e, HostTurnError)
                interrupted = host_error and e.interrupted
                if interrupted:
                    fresh = load_conversation(conv_id) or fresh  # the dead host's last checkpoint
                partial = next((msg for msg in reversed(fresh.messages)
                                if msg.get("role") == "assistant" and msg.get("run_id") == run_id), {})
                if not host_error or interrupted:  # a live host kept its own partial reply
                    checkpoint_coding_turn(
                        fresh, agent_id=coding_agent, run_id=run_id,
                        blocks=partial.get("blocks"), reply=partial.get("text", ""),
                        error=str(e), requested_mode=m, effective_mode="",
                    )

                def error_push(event: dict[str, Any]) -> None:
                    push({**event, "requested_mode": m, "effective_mode": ""})

                error_push({"type": "error", "text": str(e), "conv_id": conv_id, "run_id": run_id})
                # Interrupted: the session id stays on the chat, so the next message resumes it.
                _push_agent_stopped(error_push, conv_id, run_id, "interrupted" if interrupted else "error",
                                    detail=str(e))
            finally:
                from frontend.ui_web.live_agent_runs import discard_live_run_id

                discard_live_run_id(run_id)
                close_changeset_run(run_id, "done")
                # The coding-agent runner reports its own agent_stopped, which never
                # cleared these; every external turn left both behind.
                # Still booked: no _push_agent_stopped above has alerted for this run.
                if ended.get("reason") and run_id in _run_started:
                    _alert_after_run(conv_id, run_id, ended["reason"], str(ended.get("detail") or ""))
                _run_started.pop(run_id, None)
                _quiet_runs.discard(run_id)

        session.start(work_external, run_id)
        return run_id

    session.prepare_run(run_id)
    base_push = _wrap_push_for_linked_child(
        push, parent_conv_id=parent_conv_id, child_conv_id=conv_id, child_title=child_title
    )
    push = _make_run_scoped_push(base_push, session, conv_id, run_id)
    if parent_conv_id:
        _push_linked_agent(push, parent_conv_id, conv_id, child_title, "running")
        from frontend.ui_web.group_orchestrator import is_group_conversation

        parent_conv = load_conversation(parent_conv_id)
        if not (parent_conv and is_group_conversation(parent_conv)):
            notify_chats_changed(conv_id, child_title, conv.folder_id, push=push)

    def work() -> None:
        # Scope MCP plugin + built-in tool group availability to this chat for
        # the whole run (worker thread context; asyncio.run below inherits it).
        from backend.agent.builtin_toolsets import set_active_builtin_groups
        from backend.mcp_plugins.store import set_active_plugin_ids
        from backend.skills.store import set_active_disabled_packs, set_active_enabled_subskills
        from backend.uefn_plugins.host import set_active_uefn_agent_plugin_ids

        try:
            from frontend.duckyos_account import fetch_agent_caps

            fetch_agent_caps()
        except Exception:
            pass
        set_active_plugin_ids(conv.mcp_plugins)
        set_active_builtin_groups(conv.builtin_toolsets)
        # None = follow Store enable (all enabled app-plugin tools). Explicit
        # list (including empty) is a per-chat allowlist / opt-out.
        set_active_uefn_agent_plugin_ids(conv.uefn_plugins)
        set_active_disabled_packs(list(getattr(conv, "disabled_packs", None) or []))
        set_active_enabled_subskills(
            getattr(conv, "enabled_subskills", None)
            if isinstance(getattr(conv, "enabled_subskills", None), dict)
            else None
        )
        # Who is writing, for the whole run (embedded loop and coding-agent path).
        from backend.workspace import identity as run_identity
        from frontend.ui_web.workspace_bootstrap import build_run_context

        run_ctx = build_run_context(conv, run_id=run_id)
        from frontend.ui_web.live_agent_runs import set_live_writer

        set_live_writer(run_id, run_ctx.as_writer())
        identity_token = run_identity.bind(run_ctx)
        try:
            # Prompt build + listener health run HERE, in the worker thread, not on
            # the caller's send path. fetch_listener_status() can block for seconds
            # when UEFN is offline/wedged; doing it before session.start() used to
            # delay run_id (hence is_agent_running) long enough that the UI's
            # reconcile tore the fresh run down. The thread is already live now, so
            # the run reads as running the instant send_message returns.
            from backend.skills.store import build_skill_prompt, resolve_conversation_selection, seed_skill_packs
            from backend.mcp_plugins.store import seed_mcp_plugins
            from backend.uefn_plugins.host import ensure_plugins_loaded

            seed_skill_packs()
            seed_mcp_plugins()
            ensure_plugins_loaded()
            try:
                if "skill" in omit:
                    skill = ""
                elif conv.skill_snapshot.strip():
                    # Frozen index: refresh it if the packs on disk changed since
                    # this chat was created, so an open chat still learns about
                    # subskills added by a plugin update. No-op when nothing moved.
                    try:
                        from frontend.ui_web.project_chats import sync_skill_snapshot

                        sync_skill_snapshot(conv, settings)
                    except Exception:
                        pass  # never let a refresh break a send
                    skill = conv.skill_snapshot
                else:
                    sel = resolve_conversation_selection(conv, settings)
                    skill = build_skill_prompt(sel)
            except FileNotFoundError:
                skill = "" if "skill" in omit else (conv.skill_snapshot or "")

            from backend.bridge import set_port_override
            from backend.bridge.status import fetch_listener_status

            set_port_override(PANEL_LISTENER_PORT)
            listener_status = fetch_listener_status(
                PANEL_LISTENER_PORT,
                selected_project_root=settings.uefn_project_root,
            )
            listener_online = bool(listener_status.get("online"))
            listener_wedged = bool(listener_status.get("wedged"))

            mode_suffix = _ASK_SUFFIX if m == "ask" else (_PLAN_SUFFIX if plan_filter else _AGENT_SUFFIX)
            skill_override = skill + mode_suffix
            keep_last = max(1, min(100, int(getattr(settings, "memory_keep_last_messages", 20) or 20)))
            config = RunConfig(
                provider=provider_name,
                model=turn_model,
                listener_port=PANEL_LISTENER_PORT,
                listener_online=listener_online,
                listener_wedged=listener_wedged,
                project_root=settings.uefn_project_root,
                uefn_project_name=str(listener_status.get("uefn_project_name") or ""),
                project_match=bool(listener_status.get("project_match", True)),
                conv_id=conv_id,
                conv=conv,
                keep_last_messages=keep_last,
                skill_override=skill_override,
                plan_only=plan_filter,
                mode=m,
                context_omit=omit,
                ducky_name=(conv.ducky_name or conv.title or "").strip(),
                ducky_personality=conv.ducky_personality or "",
                prompt_caching_enabled=bool(settings.prompt_caching_enabled),
                freeze_prompt_prefix=bool(settings.freeze_prompt_prefix),
                anthropic_extended_cache_ttl=bool(settings.anthropic_extended_cache_ttl),
                mode_suffix=mode_suffix,
                tool_result_format=settings.tool_result_format or "toon",
                thinking_effort=str(getattr(conv, "thinking_effort", "") or "off"),
                run_id=run_id,
                profile_id=run_ctx.profile_id,
                group_id=run_ctx.group_id,
                leader_conv_id=run_ctx.leader_conv_id,
                coding_agent=run_ctx.coding_agent,
            )

            asyncio.run(
                _run_agent_loop(
                    conv,
                    content,
                    history,
                    config=config,
                    push=push,
                    session=session,
                    run_id=run_id,
                    plan_filter=plan_filter,
                    user_attachments=current_user_attachments,
                    resume=resume,
                )
            )
        except Exception as e:
            push({"type": "error", "text": str(e), "conv_id": conv_id})
            _push_agent_stopped(push, conv_id, run_id, "error", detail=str(e))
        finally:
            run_identity.reset(identity_token)

    session.start(work, run_id)
    return run_id


# --- Old import path for the code moved to agent_turns/ ----------------------
# Callers and tests still read and patch ``agent_modes.<name>``. A patch here is applied to
# every agent_turns module that holds that name, so the moved code sees it exactly as it
# did when it all lived in this one module.
import sys as _sys
import types as _types

_PARTS: tuple[_types.ModuleType, ...] = (_push_part, _errors_part, _state_part, _pending_part)
# Rebound inside their own module (``global``): always read live, never copied here.
_LIVE = frozenset({"_panel_push", "_forward_started", "_panel_runs_cache"})


class _AgentModesShim(_types.ModuleType):
    def __getattr__(self, name: str) -> Any:
        for part in _PARTS:
            if name in vars(part):
                return vars(part)[name]
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value: Any) -> None:
        for part in _PARTS:
            if name in vars(part):
                setattr(part, name, value)
        if name not in _LIVE:
            super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        for part in _PARTS:
            if name in vars(part):
                delattr(part, name)
        if name in self.__dict__:
            super().__delattr__(name)


for _part in _PARTS:
    for _name, _value in vars(_part).items():
        if not _name.startswith("__") and _name not in _LIVE:
            globals().setdefault(_name, _value)
del _part, _name, _value
_sys.modules[__name__].__class__ = _AgentModesShim
