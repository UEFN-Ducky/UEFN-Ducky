"""Ask / Plan / Agent mode dispatch for the React panel (ui_web only)."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

SESSION_JOIN_TIMEOUT = 2.0

from frontend.ui_web.project_chats import append_message, auto_title, load_conversation, save_conversation
from frontend.ui_web.token_usage import record_api_call, token_usage_report
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings, apply_workspace_env
from backend.agent.attachments import attachments_from_message_dict, parse_attachment_dicts
from backend.agent.multimodal_content import image_attachments
from backend.agent.providers import make_provider
from backend.agent.providers.base import ProviderMessage, StreamEventKind
from backend.agent.runner import AgentRunner, RunConfig
from backend.agent.secrets import get_key
from backend.agent.delegation_guard import append_delegation_warning, fake_delegation_warning
from backend.agent.toolsets import is_plan_safe_tool

PushFn = Callable[[dict[str, Any]], None]

_ASK_SUFFIX = "\n\n[Mode: Ask] Answer in plain language only. Do not call tools or propose tool use."

_PLAN_SUFFIX = (
    "\n\n[Mode: Plan] Discovery and planning only. Prefer read-only inspection tools. "
    "Do not modify the level, devices, or project files until the user confirms. "
    "REQUIRED: end by calling `ducky_create_plan` (or `ducky_update_plan`) — never leave "
    "only a prose Fix plan / markdown checklist in chat. "
    "Followable `nodes`: Diagnose → Fix → Verify; each leaf = one action + Done-when "
    "(name the tool when known). Nest subplans; rearrange with "
    "`ducky_plan_move_node` / add/update/delete. "
    "Templates: `ducky_create_plan_template` / `ducky_instantiate_plan_template`. "
    "Then STOP — user reviews and switches to Agent mode (or Send to ducky). "
    "Do not execute in Plan mode."
)

_AGENT_SUFFIX = (
    "\n\n[Mode: Agent] Multi-step work: if this chat has no plan yet, create one with "
    "`ducky_create_plan` after brief discovery (a few inspects), BEFORE mutators. "
    "If a plan is already in context / `ducky_get_plan`, FOLLOW it — depth-first open "
    "leaves, `ducky_plan_update_node` in_progress→completed. Never replace the tool "
    "plan with chat prose. Off-plan thrashing (retrying diagnoses without updating "
    "the tree) is forbidden — rewrite the outline first when the approach changes."
)

_panel_push: PushFn | None = None
_active_conv_id = threading.local()


def set_panel_push(push: PushFn | None) -> None:
    """Register UI push handler so MCP chat tools update the React panel."""
    global _panel_push
    _panel_push = push


def get_panel_push() -> PushFn | None:
    return _panel_push


def get_active_conv_id() -> str | None:
    return getattr(_active_conv_id, "conv_id", None)


def _set_active_conv_id(conv_id: str | None) -> None:
    _active_conv_id.conv_id = conv_id


def _log_agent_crash(
    conv,
    *,
    provider: str,
    model: str,
    error: str,
    partial: dict[str, Any] | None = None,
    elapsed_s: float,
    first_token_s: float | None,
    thinking: str = "",
    answer: str = "",
) -> None:
    """Persist a full-transcript crash record + a short entry in the error panel.

    ``thinking``/``answer`` fall back to the partial message's fields, so the
    Agent-loop path can just pass the partial while the Ask path passes its own
    accumulated buffers.
    """
    if partial:
        thinking = thinking or str(partial.get("thinking") or "")
        answer = answer or str(partial.get("content") or "")
    try:
        from frontend.agent_crash_log import record_crash

        record_crash(
            conv_id=getattr(conv, "id", "") or "",
            provider=provider,
            model=model,
            error=error,
            thinking=thinking,
            answer=answer,
            elapsed_s=elapsed_s,
            first_token_s=first_token_s,
        )
    except Exception:
        pass
    try:
        from frontend.error_log import record_error

        ft = f"{first_token_s:.1f}s" if first_token_s is not None else "n/a"
        record_error(
            "agent",
            f"turn crashed after {elapsed_s:.1f}s (first token {ft}): {error}",
        )
    except Exception:
        pass


# --- Cross-process push forwarding (bridge process → panel process) ---------
# ducky_spawn_chat / ducky_send_chat_message run inside the stdio MCP bridge,
# a DIFFERENT process from the panel UI, where _panel_push is never set. Without
# this, notify_chats_changed and the sub-agent's stream events go to a no-op, so
# spawned chats/agents run invisibly (created on disk but never shown live). We
# forward every event to the panel's loopback HTTP server, which replays it onto
# its own push pipeline. A single daemon sender coalesces bursts into one POST so
# high-frequency stream deltas don't block the agent thread.
import queue as _queue
import urllib.request as _urlreq

_forward_queue: _queue.Queue[dict[str, Any]] = _queue.Queue(maxsize=4000)
_forward_started = False
_forward_lock = threading.Lock()


# #region agent log
def _dbg_vis(hyp: str, location: str, message: str, data: dict[str, Any]) -> None:
    """Debug-session instrumentation for spawned-chat visibility flow (bridge<->panel)."""
    try:
        import json as _j
        import time as _t
        _line = _j.dumps({
            "sessionId": "77e3f2",
            "hypothesisId": hyp,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(_t.time() * 1000),
        })
        with open(r"C:\Users\tas13\Documents\GitHub\UEFN-Ducky\debug-77e3f2.log", "a", encoding="utf-8") as _f:
            _f.write(_line + "\n")
            _f.flush()
    except Exception:
        pass
# #endregion


def _panel_event_url() -> str:
    return f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_event"


def _forward_sender() -> None:
    url = _panel_event_url()
    while True:
        batch = [_forward_queue.get()]
        try:
            while len(batch) < 250:
                batch.append(_forward_queue.get_nowait())
        except _queue.Empty:
            pass
        try:
            data = json.dumps(batch, ensure_ascii=False).encode("utf-8")
            req = _urlreq.Request(
                url, data=data, headers={"Content-Type": "application/json"}, method="POST"
            )
            _urlreq.urlopen(req, timeout=2.0).close()
            # #region agent log
            _dbg_vis("V-A", "agent_modes.py:_forward_sender", "forward POST ok",
                     {"url": url, "batch_len": len(batch),
                      "types": [str(e.get("type")) for e in batch[:8] if isinstance(e, dict)]})
            # #endregion
        except Exception as _exc:
            # Panel closed / not yet listening — drop this batch silently.
            # #region agent log
            _dbg_vis("V-A", "agent_modes.py:_forward_sender", "forward POST FAILED",
                     {"url": url, "batch_len": len(batch), "err": repr(_exc)[:200]})
            # #endregion
            pass


def _ensure_forwarder() -> None:
    global _forward_started
    with _forward_lock:
        if _forward_started:
            return
        _forward_started = True
    threading.Thread(target=_forward_sender, name="bridge-panel-forward", daemon=True).start()


def _forward_to_panel(event: dict[str, Any]) -> None:
    _ensure_forwarder()
    try:
        _forward_queue.put_nowait(dict(event))
    except _queue.Full:
        pass


def _resolve_push(push: PushFn | None) -> PushFn:
    if push is not None:
        return push
    if _panel_push is not None:
        return _panel_push
    # No local panel (we're in the stdio bridge process) — forward to the panel.
    return _forward_to_panel


def push_ui_event(event: dict[str, Any]) -> None:
    """Send a one-off UI event to the panel from any process.

    Routes to the local panel push when running inside the panel process, and
    forwards over loopback HTTP when running in the stdio MCP bridge — so
    settings/plugin change notifications from UI tools reach React either way.
    """
    _resolve_push(None)(event)


# --- Run delegation (bridge process → panel process) ------------------------
# A run started from the stdio MCP bridge (ducky_spawn_chat / ducky_send_chat_
# message called by an external coding agent like Cursor) would otherwise
# execute the agent loop inside the bridge process, invisible to the panel's
# session registry, running-agent dots, streaming, and reconcile safety net. We
# instead ask the panel to run it, so the spawned duck is a first-class panel
# session. When no panel is reachable we fall back to running locally so spawns
# still work headless.
def _in_bridge_process() -> bool:
    return _panel_push is None


def _post_panel_run(payload: dict[str, Any], *, http_timeout: float) -> dict[str, Any] | None:
    """POST a run request to the panel.

    Returns the panel's JSON outcome on success; ``{"_delegation_timeout": True}``
    if the panel accepted the connection but didn't answer in time (it is running
    the turn — callers must NOT re-run locally or they'd duplicate it); ``None``
    only when the panel is unreachable (no panel open → safe to run locally).
    """
    import socket

    url = f"http://127.0.0.1:{PANEL_LISTENER_PORT - 1}/__panel_run"
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = _urlreq.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        with _urlreq.urlopen(req, timeout=http_timeout) as resp:
            body = resp.read().decode("utf-8")
        # #region agent log
        _dbg_vis("V-A", "agent_modes.py:_post_panel_run", "delegation OK (panel ran it)",
                 {"url": url, "conv_id": payload.get("conv_id")})
        # #endregion
        return json.loads(body) if body else {}
    except (TimeoutError, socket.timeout):
        # #region agent log
        _dbg_vis("V-A", "agent_modes.py:_post_panel_run", "delegation TIMEOUT (panel still running)",
                 {"url": url, "conv_id": payload.get("conv_id")})
        # #endregion
        return {"_delegation_timeout": True}
    except Exception as _exc:
        # #region agent log
        _dbg_vis("V-A", "agent_modes.py:_post_panel_run", "delegation UNREACHABLE -> local fallback",
                 {"url": url, "conv_id": payload.get("conv_id"), "err": repr(_exc)[:200]})
        # #endregion
        return None


def notify_chats_changed(
    conv_id: str = "",
    title: str = "",
    folder_id: str = "",
    *,
    push: PushFn | None = None,
) -> None:
    """Tell the React panel to reload the sidebar (new chat/folder from MCP tools)."""
    event: dict[str, Any] = {"type": "chats_changed"}
    if conv_id:
        event["conv_id"] = conv_id
        event["title"] = title
        event["folder_id"] = folder_id
    # #region agent log
    _dbg_vis("V-B", "agent_modes.py:notify_chats_changed", "notify fired",
             {"conv_id": conv_id, "in_bridge": _in_bridge_process(),
              "route": ("explicit" if push is not None else ("panel" if _panel_push is not None else "forward"))})
    # #endregion
    _resolve_push(push)(event)


def notify_context_changed(conv_id: str, *, push: PushFn | None = None) -> None:
    """Tell the React panel to reload messages/context for a chat after context reset."""
    _resolve_push(push)({"type": "context_changed", "conv_id": conv_id})


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


def _build_prompt_cache(
    conv,
    settings: PanelSettings,
    *,
    skill: str,
    listener_online: bool,
    listener_wedged: bool,
    project_root: str,
    omit: frozenset[str],
    mode_suffix: str = "",
) -> Any:
    from backend.agent.prompt import get_system_prompt_parts
    from backend.agent.prompt_cache import build_cache_payload, enrich_parts

    parts = get_system_prompt_parts(
        listener_online=listener_online,
        listener_port=PANEL_LISTENER_PORT,
        project_root=project_root,
        skill_text=skill or "",
        mode_suffix=mode_suffix,
        listener_wedged=listener_wedged,
        ducky_name=(conv.ducky_name or conv.title or "").strip(),
        ducky_personality=conv.ducky_personality or "",
        conv_id=str(getattr(conv, "id", "") or ""),
    )
    parts = enrich_parts(parts, listener_port=PANEL_LISTENER_PORT, mode_suffix=mode_suffix)
    provider = settings.agent_provider or ""
    from backend.agent.providers.cache_utils import (
        anthropic_extended_cache_ttl_enabled,
        provider_cache_markers_enabled,
    )

    enable = provider_cache_markers_enabled(
        provider, fallback=bool(settings.prompt_caching_enabled)
    )
    return build_cache_payload(
        conv,
        parts,
        omit=omit,
        enable_cache=enable,
        freeze_enabled=bool(settings.freeze_prompt_prefix),
        prompt_cache_key=conv.id,
        anthropic_extended_ttl=anthropic_extended_cache_ttl_enabled(
            fallback=bool(settings.anthropic_extended_cache_ttl)
        ),
    )


def _last_assistant_text(conv) -> str:
    for m in reversed(conv.messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content", "")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


class AgentSession:
    def __init__(self) -> None:
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._runner: AgentRunner | None = None
        self.run_id: str = ""

    def set_runner(self, runner: AgentRunner | None, *, run_id: str) -> None:
        if self.run_id != run_id:
            return
        self._runner = runner

    def clear_runner(self, *, run_id: str) -> None:
        if self.run_id == run_id:
            self._runner = None

    def prepare_run(self, run_id: str) -> None:
        self.cancel()
        old = self._thread
        # #region agent log
        join_started = time.perf_counter()
        old_alive = bool(old is not None and old.is_alive())
        # #endregion
        if old is not None and old.is_alive():
            old.join(SESSION_JOIN_TIMEOUT)
        # #region agent log
        _dbg_thread_state(
            "agent session prepared",
            runId=run_id,
            oldAlive=old_alive,
            oldStillAlive=bool(old is not None and old.is_alive()),
            joinMs=round((time.perf_counter() - join_started) * 1000.0, 1),
        )
        # #endregion
        self._cancel = threading.Event()
        self.run_id = run_id

    def start(self, target: Callable[[], None], run_id: str) -> None:
        if self.run_id != run_id:
            self.prepare_run(run_id)
        self._thread = threading.Thread(target=target, daemon=True, name=f"agent-{run_id[:8]}")
        self._thread.start()
        # #region agent log
        _dbg_thread_state("agent thread started", runId=run_id, threadName=self._thread.name)
        # #endregion

    def cancel(self) -> None:
        self._cancel.set()
        runner = self._runner
        if runner is not None:
            runner.cancel()


_sessions: dict[str, AgentSession] = {}
_sessions_lock = threading.Lock()
_linked_parents: dict[str, str] = {}
_child_waiters: dict[str, set[str]] = {}

# #region agent log
def _dbg_thread_state(message: str, **data: Any) -> None:
    try:
        threads = threading.enumerate()
        with open(r"C:\Users\tas13\Documents\GitHub\UEFN-Ducky\debug-77e3f2.log", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "sessionId": "77e3f2", "runId": "thread-repro", "hypothesisId": "T-A,T-B,T-D",
                "location": "frontend/ui_web/agent_modes.py:AgentSession",
                "message": message,
                "data": {
                    **data,
                    "threadCount": len(threads),
                    "agentThreads": [t.name for t in threads if t.name.startswith("agent-")],
                    "httpThreads": sum("process_request_thread" in t.name for t in threads),
                    "sessionCount": len(_sessions),
                    "liveSessions": sum(
                        1 for session in _sessions.values()
                        if session._thread is not None and session._thread.is_alive()
                    ),
                },
                "timestamp": int(time.time() * 1000),
            }) + "\n")
    except Exception:
        pass
# #endregion


def is_agent_running(conv_id: str) -> bool:
    session = _sessions.get(conv_id)
    if session is None or session._thread is None:
        return False
    return session._thread.is_alive()


def linked_parent_of(conv_id: str) -> str | None:
    return _linked_parents.get(conv_id)


def linked_children_of(conv_id: str) -> list[str]:
    return [child for child, parent in _linked_parents.items() if parent == conv_id]


def list_running_agents() -> list[str]:
    return [cid for cid in _sessions if is_agent_running(cid)]


def _make_run_scoped_push(push: PushFn, session: AgentSession, conv_id: str, run_id: str) -> PushFn:
    def scoped(event: dict[str, Any]) -> None:
        if session.run_id != run_id:
            return
        out = dict(event)
        out.setdefault("conv_id", conv_id)
        out["run_id"] = run_id
        push(out)

    return scoped


def _push_agent_stopped(
    push: PushFn, conv_id: str, run_id: str, reason: str, detail: str = ""
) -> None:
    event: dict[str, Any] = {
        "type": "agent_stopped",
        "conv_id": conv_id,
        "run_id": run_id,
        "reason": reason,
    }
    if detail:
        event["detail"] = detail
    elif reason == "timeout":
        event["detail"] = "Agent timed out before finishing its reply"
    elif reason == "error":
        event["detail"] = "Agent errored before finishing its reply"
    push(event)
    if reason != "done":
        return
    # Private DM with a group member → short note on the group hub for everyone.
    try:
        from frontend.ui_web.group_orchestrator import announce_private_member_talk

        announce_private_member_talk(conv_id, push=push)
    except Exception:
        pass


def wait_for_idle(conv_id: str, timeout: float = SESSION_JOIN_TIMEOUT) -> bool:
    session = _sessions.get(conv_id)
    if session is None or session._thread is None:
        return True
    if not session._thread.is_alive():
        return True
    session._thread.join(max(0.05, float(timeout)))
    return not session._thread.is_alive()


def join_running_agents(timeout: float = 0.5) -> None:
    cancel_agent()
    deadline = time.time() + max(0.05, float(timeout))
    for cid in list(_sessions.keys()):
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        wait_for_idle(cid, remaining)


def _push_linked_agent(
    push: PushFn,
    parent_conv_id: str,
    child_conv_id: str,
    title: str,
    status: str,
) -> None:
    push(
        {
            "type": "linked_agent",
            "parent_conv_id": parent_conv_id,
            "child_conv_id": child_conv_id,
            "title": title or "Chat",
            "status": status,
        }
    )


def _wrap_push_for_linked_child(
    push: PushFn,
    *,
    parent_conv_id: str | None,
    child_conv_id: str,
    child_title: str,
) -> PushFn:
    if not parent_conv_id:
        return push

    def linked_push(event: dict[str, Any]) -> None:
        push(event)
        if event.get("conv_id") != child_conv_id:
            return
        kind = event.get("type")
        if kind == "assistant_done":
            _push_linked_agent(push, parent_conv_id, child_conv_id, child_title, "done")
            _linked_parents.pop(child_conv_id, None)
        elif kind in ("error", "agent_stopped"):
            if kind == "agent_stopped":
                reason = str(event.get("reason") or "")
                if reason == "done":
                    status = "done"
                elif reason == "cancelled":
                    status = "cancelled"
                else:
                    status = "error"
            else:
                text = str(event.get("text") or "")
                status = "cancelled" if text == "Cancelled" else "error"
            _push_linked_agent(push, parent_conv_id, child_conv_id, child_title, status)
            _linked_parents.pop(child_conv_id, None)

    return linked_push


def cancel_agent(conv_id: str | None = None) -> None:
    targets: list[str] = []
    if conv_id:
        targets.append(conv_id)
        with _sessions_lock:
            for child in _child_waiters.get(conv_id, set()):
                if child not in targets:
                    targets.append(child)
    else:
        with _sessions_lock:
            targets = list(_sessions.keys())

    for cid in targets:
        session = _sessions.get(cid)
        if session is not None:
            session.cancel()
        # Drop A2A inbox so cancel does not immediately kick a new run from
        # queued agent-to-agent messages (panel Stop must match ducky_agent_stop).
        try:
            from backend.agent.a2a_broker import on_agent_cancelled_by_user

            on_agent_cancelled_by_user(cid)
        except Exception:
            pass


def _session(conv_id: str) -> AgentSession:
    if conv_id not in _sessions:
        _sessions[conv_id] = AgentSession()
    return _sessions[conv_id]


def _tool_line(name: str, args: dict[str, Any], status: str = "", ms: int = 0) -> str:
    args_short = json.dumps(args, ensure_ascii=False)
    if len(args_short) > 120:
        args_short = args_short[:117] + "..."
    base = f"{name}({args_short})"
    if status and ms:
        return f"{base} · {status} · {ms}ms"
    return base


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
    result_text = _tool_result_text(raw_result)
    tool_payload: dict[str, Any] = {
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


async def _run_ask_async(
    conv,
    user_text: str,
    history: list[dict[str, Any]],
    *,
    provider_name: str,
    model: str,
    listener_online: bool,
    project_root: str,
    skill: str | None,
    push: PushFn,
    cancel: threading.Event,
    session: AgentSession,
    run_id: str,
    user_attachments: list[dict[str, Any]] | None = None,
    context_omit: frozenset[str] | None = None,
) -> str:
    stop_reason = "error"
    from frontend.ui_web.provider_usage_log import bind_usage_context, reset_usage_context

    usage_ctx = bind_usage_context(
        agent=str(getattr(conv, "coding_agent", "") or "ducky"),
        conv_id=str(getattr(conv, "id", "") or ""),
        ducky_label=str(getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or ""),
    )
    try:
        api_key = get_key(provider_name)
        if not api_key:
            try:
                from backend.uefn_plugins.host import (
                    get_contributions,
                    get_llm_provider_registration,
                )

                reg = get_llm_provider_registration(provider_name) or {}
                if reg.get("key_optional"):
                    for row in get_contributions().get("llm_providers") or []:
                        if str(row.get("id") or "").strip().lower() == provider_name:
                            api_key = str(row.get("default_url") or "").strip()
                            break
                    api_key = api_key or "http://localhost:11434"
            except Exception:
                api_key = ""
        if not api_key:
            push({"type": "error", "text": f"No API key for {provider_name}", "conv_id": conv.id})
            return stop_reason
        provider = make_provider(
            provider_name,
            api_key,
            model,
            thinking_effort=str(getattr(conv, "thinking_effort", "") or "off"),
        )
        settings = PanelSettings.load()
        omit_set = context_omit or frozenset()
        prompt_cache = _build_prompt_cache(
            conv,
            settings,
            skill=skill or "",
            listener_online=listener_online,
            listener_wedged=False,
            project_root=project_root,
            omit=omit_set,
        )
        save_conversation(conv, project_root)
        # Always use cache payload system (freeze works for every provider; markers are optional).
        system = prompt_cache.frozen_system + prompt_cache.dynamic_system + _ASK_SUFFIX
        messages = []
        for m in history:
            role = "user" if m.get("role") == "user" else "assistant"
            if role == "user":
                messages.append(
                    ProviderMessage(
                        role="user",
                        content=str(m.get("content", "")),
                        attachments=image_attachments(
                            attachments_from_message_dict(m, conv_id=conv.id, project_root=project_root)
                        ),
                    )
                )
            else:
                messages.append(ProviderMessage(role="assistant", content=str(m.get("content", ""))))
        current_images = image_attachments(parse_attachment_dicts(user_attachments))
        messages.append(ProviderMessage(role="user", content=user_text, attachments=current_images))
        text = ""
        thinking = ""
        usage: dict[str, int] = {}
        t_start = time.monotonic()
        t_first: float | None = None
        async for event in provider.stream_turn(
            system=system,
            messages=messages,
            tools=[],
            cancel_event=cancel,
            cache=prompt_cache if prompt_cache.enable_cache else None,
        ):
            if cancel.is_set():
                stop_reason = "cancelled"
                if text.strip() or thinking.strip():
                    partial = {
                        "role": "assistant",
                        "content": text,
                        "blocks": [],
                        "ts": time.time(),
                        "usage": usage,
                        "incomplete": True,
                        "error": "Stopped",
                    }
                    if thinking.strip():
                        partial["thinking"] = thinking
                    append_message(conv, partial)
                    save_conversation(conv)
                return stop_reason
            if event.kind == StreamEventKind.TEXT_DELTA:
                if t_first is None:
                    t_first = time.monotonic()
                text += event.text
                push({"type": "text_delta", "text": event.text, "conv_id": conv.id})
            elif event.kind == StreamEventKind.THINKING:
                if t_first is None:
                    t_first = time.monotonic()
                thinking += event.text
                push({"type": "thinking", "text": event.text, "conv_id": conv.id})
            elif event.kind == StreamEventKind.DONE:
                usage = dict(event.usage or {})
            elif event.kind == StreamEventKind.ERROR:
                err = event.error or "LLM error"
                kept = bool(text.strip() or thinking.strip())
                if kept:
                    partial = {
                        "role": "assistant",
                        "content": text,
                        "blocks": [],
                        "ts": time.time(),
                        "usage": usage,
                        "incomplete": True,
                        "error": err,
                    }
                    if thinking.strip():
                        partial["thinking"] = thinking
                    append_message(conv, partial)
                    save_conversation(conv)
                _log_agent_crash(
                    conv,
                    provider=provider_name,
                    model=model,
                    error=err,
                    thinking=thinking,
                    answer=text,
                    elapsed_s=time.monotonic() - t_start,
                    first_token_s=(t_first - t_start) if t_first is not None else None,
                )
                push({"type": "error", "text": err, "conv_id": conv.id, "kept_partial": kept})
                return stop_reason
        if cancel.is_set():
            stop_reason = "cancelled"
            return stop_reason
        msg = {"role": "assistant", "content": text, "blocks": [], "ts": time.time(), "usage": usage}
        if thinking.strip():
            msg["thinking"] = thinking
        append_message(conv, msg)
        if usage:
            record_api_call(
                conv,
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                cache_read_tokens=int(usage.get("cache_read_tokens") or 0),
                cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
                step=1,
                provider=provider_name,
                model=model,
            )
            save_conversation(conv)
        _push_token_usage(
            conv,
            push,
            call_input=int(usage.get("input_tokens") or 0),
            call_output=int(usage.get("output_tokens") or 0),
            step=1,
            usage=usage,
        )
        push({"type": "assistant_done", "conv_id": conv.id})
        stop_reason = "done"
        return stop_reason
    finally:
        reset_usage_context(usage_ctx)
        if session.run_id == run_id:
            _push_agent_stopped(push, conv.id, run_id, stop_reason)


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
) -> str:
    cancel = session._cancel
    stop_reason = "error"
    runner = AgentRunner(config)
    session.set_runner(runner, run_id=run_id)
    _set_active_conv_id(conv.id)
    t_start = time.monotonic()
    t_first: float | None = None
    delegation_tools_called: set[str] = set()
    try:
        async for event in runner.run_turn(
            user_text,
            history,
            user_attachments=user_attachments,
            thread_cancel=cancel,
        ):
            if cancel.is_set():
                runner.cancel()
                stop_reason = "cancelled"
                return stop_reason
            if t_first is None and event.kind in ("text_delta", "thinking"):
                t_first = time.monotonic()
            if event.kind == "tool_start" and event.tool:
                rec = event.tool
                if rec.name in ("ducky_spawn_chat", "ducky_send_chat_message"):
                    delegation_tools_called.add(rec.name)
                if plan_filter and not is_plan_safe_tool(rec.name):
                    continue
                push(
                    {
                        "type": "tool",
                        "text": f"⚙ {_tool_line(rec.name, dict(rec.arguments or {}))}",
                        "conv_id": conv.id,
                        "tool": {
                            "name": rec.name,
                            "arguments": dict(rec.arguments or {}),
                            "status": "pending",
                        },
                    }
                )
            elif event.kind == "tool_end" and event.tool:
                rec = event.tool
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
                        append_message(conv, partial)
                        save_conversation(conv)
                    stop_reason = "cancelled"
                else:
                    partial = event.partial_message
                    err_text = event.text or "LLM error"
                    if partial:
                        append_message(conv, partial)
                    else:
                        # Persist so reload / empty stream still shows the crash
                        # (UI used to drop standalone role=error rows → empty Done).
                        append_message(
                            conv,
                            {
                                "role": "assistant",
                                "content": "",
                                "incomplete": True,
                                "error": err_text,
                                "ts": time.time(),
                            },
                        )
                    save_conversation(conv)
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
                warning = fake_delegation_warning(conv, assistant_msg, delegation_tools_called)
                if warning:
                    assistant_msg = append_delegation_warning(assistant_msg, warning)
                    push({"type": "delegation_warning", "text": warning, "conv_id": conv.id})
                append_message(conv, assistant_msg)
                stop_reason = "done"
                push({"type": "assistant_done", "conv_id": conv.id})
                return stop_reason
        return stop_reason
    finally:
        session.clear_runner(run_id=run_id)
        if get_active_conv_id() == conv.id:
            _set_active_conv_id(None)
        if session.run_id == run_id:
            _push_agent_stopped(push, conv.id, run_id, stop_reason)
        with _sessions_lock:
            waiters = _child_waiters.get(conv.id)
            if waiters is not None and not waiters:
                _child_waiters.pop(conv.id, None)
            # Drop idle sessions so long-lived panels do not retain one entry per chat.
            if (
                _sessions.get(conv.id) is session
                and session._thread is not None
                and not session._thread.is_alive()
                and not session._runner
            ):
                _sessions.pop(conv.id, None)


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
    _local: bool = False,
) -> dict[str, Any]:
    """Send a message, run the target chat's agent, and block until done or timeout."""
    if not _local and _in_bridge_process():
        resp = _post_panel_run(
            {
                "conv_id": conv_id,
                "text": text,
                "mode": mode,
                "model": model,
                "wait": True,
                "timeout_sec": float(timeout_sec),
                "cancel_on_timeout": bool(cancel_on_timeout),
                # Carry the spawning chat so the panel nests the child under it.
                "parent_conv_id": parent,
            },
            http_timeout=max(5.0, float(timeout_sec) + 15.0),
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

    parent_active = (parent or "").strip() or get_active_conv_id()
    if parent_active and parent_active != conv_id:
        with _sessions_lock:
            _child_waiters.setdefault(parent_active, set()).add(conv_id)

    try:
        run_message(conv_id, text, mode, model, push=collecting_push, force=True, parent=parent)
        if not done.wait(timeout=max(1.0, float(timeout_sec))):
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


def _make_broker_tap(push: PushFn, conv_id: str) -> PushFn:
    """Feed turn-lifecycle events to the A2A broker (inbox drain + owed-reply notices)."""

    def tapped(event: dict[str, Any]) -> None:
        push(event)
        if event.get("conv_id") != conv_id or event.get("type") != "agent_stopped":
            return
        try:
            from backend.agent.a2a_broker import on_agent_stopped

            on_agent_stopped(
                conv_id,
                str(event.get("reason") or "done"),
                detail=str(event.get("detail") or event.get("text") or ""),
            )
        except Exception:
            pass

    return tapped


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
    _local: bool = False,
) -> str:
    if not _local and _in_bridge_process():
        resp = _post_panel_run(
            {
                "conv_id": conv_id,
                "text": user_text,
                "mode": mode,
                "model": model,
                "wait": False,
                "force": bool(force),
                "attachments": attachments or None,
                # Carry the spawning chat across the process hop so the delegated
                # run in the panel nests the child under its parent (linked_agent).
                "parent_conv_id": parent,
            },
            http_timeout=30.0,
        )
        if resp is not None:
            # On a timeout the panel has already started the run — don't re-run.
            if resp.get("_delegation_timeout"):
                return ""
            return str(resp.get("run_id") or "")
        # Panel unreachable — fall back to running the turn locally in the bridge.

    push = _make_broker_tap(_resolve_push(push), conv_id)
    conv = load_conversation(conv_id)
    if not conv:
        push({"type": "error", "text": "Conversation not found", "conv_id": conv_id})
        return ""

    # Cursor-style Stop → follow-up: UI goes idle immediately while the old
    # thread is still unwinding. Cancel + join so the new turn can start with
    # full prior context (partial assistant reply already persisted on cancel).
    if is_agent_running(conv_id) and not force:
        cancel_agent(conv_id)
        wait_for_idle(conv_id, SESSION_JOIN_TIMEOUT)
        if is_agent_running(conv_id):
            push({"type": "error", "text": "Agent already running for this chat", "conv_id": conv_id})
            return ""

    run_id = str(uuid.uuid4())

    settings = PanelSettings.load()
    apply_workspace_env(settings.uefn_project_root)
    from backend.agent.attachments import prepare_outgoing_user_message
    from backend.agent.coding_agents.base import normalize_coding_agent

    coding_agent = normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky")
    external = coding_agent != "ducky"

    # Exact model for this conversation only — never invent from global settings.
    turn_model = (model or getattr(conv, "model", None) or "").strip()
    if not turn_model or turn_model.lower() == "default":
        push(
            {
                "type": "error",
                "text": (
                    "No exact model selected for this chat. Pick a model in the composer "
                    "(or set a Default Model in Settings → LLMs) before sending."
                ),
                "conv_id": conv_id,
            }
        )
        return ""

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
        if not get_key(provider_name):
            # URL gateways (Ollama) are key_optional — default localhost is fine.
            try:
                from backend.uefn_plugins.host import get_llm_provider_registration

                reg = get_llm_provider_registration(provider_name) or {}
                if not reg.get("key_optional"):
                    push({"type": "error", "text": "No API key configured", "conv_id": conv_id})
                    return ""
            except Exception:
                push({"type": "error", "text": "No API key configured", "conv_id": conv_id})
                return ""

    try:
        content, _stored = prepare_outgoing_user_message(
            user_text,
            attachments,
            provider=provider_name or "",
            model=turn_model,
            external_agent=external,
        )
    except ValueError as e:
        push({"type": "error", "text": str(e), "conv_id": conv_id})
        return ""

    if getattr(settings, "prompt_dedupe_exact_blocks", False):
        from backend.agent.prompt_dedupe import dedupe_exact_blocks

        content = dedupe_exact_blocks(content)
        user_text = dedupe_exact_blocks(user_text)

    attachments_parsed = parse_attachment_dicts(attachments)
    ts = time.time()
    from frontend.ui_web.conversation_attachments import persist_message_attachments
    from frontend.ui_web.project_chats import get_conversations_dir

    stored_attachments = persist_message_attachments(
        conv_id,
        ts,
        attachments_parsed,
        get_conversations_dir(settings.uefn_project_root),
        settings.uefn_project_root,
    )
    current_user_attachments = [
        {
            "kind": a.kind,
            "name": a.name,
            "mime": a.mime,
            **({"data_base64": a.data_base64} if a.kind == "image" else {"text": a.text}),
        }
        for a in attachments_parsed
    ]

    user_msg: dict[str, Any] = {"role": "user", "content": content, "text": user_text, "ts": ts}
    if stored_attachments:
        user_msg["attachments"] = stored_attachments
    append_message(conv, user_msg)
    if len(conv.messages) == 1:
        auto_title(conv, user_text or content)
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
    child_title = conv.title or "Chat"
    # #region agent log
    _dbg_vis("N-A", "agent_modes.py:run_message", "parent link resolved",
             {"child": conv_id, "explicit_parent": (parent or ""), "active": get_active_conv_id(),
              "resolved_parent": parent_conv_id or "", "in_bridge": _in_bridge_process(),
              "local": _local, "external": external})
    # #endregion

    if external:
        # BYOA path: Claude Code / Codex / Cursor — no embedded AgentRunner.
        with _sessions_lock:
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
            try:
                from backend.agent.coding_agents.runner import run_coding_agent_message

                # Reload conv so append_message in runner sees the user turn we just saved.
                fresh = load_conversation(conv_id) or conv
                run_coding_agent_message(
                    fresh,
                    user_text or (content if isinstance(content, str) else str(content)),
                    model=turn_model,
                    push=push,
                    run_id=run_id,
                    cancel=cancel_event,
                )
            except Exception as e:
                push({"type": "error", "text": str(e), "conv_id": conv_id, "run_id": run_id})
                if session.run_id == run_id:
                    _push_agent_stopped(push, conv_id, run_id, "error")
            # #region agent log
            finally:
                _dbg_thread_state("external agent thread finished", runId=run_id, convId=conv_id)
            # #endregion

        session.start(work_external, run_id)
        return run_id

    with _sessions_lock:
        if is_agent_running(conv_id) and not force:
            push({"type": "error", "text": "Agent already running for this chat", "conv_id": conv_id})
            return ""
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
        from backend.builtin_toolsets import set_active_builtin_groups
        from backend.mcp_plugins.store import set_active_plugin_ids
        from backend.skill import set_active_disabled_packs, set_active_enabled_subskills
        from backend.uefn_plugins.host import set_active_uefn_agent_plugin_ids

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
        try:
            # Prompt build + listener health run HERE, in the worker thread, not on
            # the caller's send path. fetch_listener_status() can block for seconds
            # when UEFN is offline/wedged; doing it before session.start() used to
            # delay run_id (hence is_agent_running) long enough that the UI's
            # reconcile tore the fresh run down. The thread is already live now, so
            # the run reads as running the instant send_message returns.
            from backend.skill import build_skill_prompt, resolve_conversation_selection, seed_skill_packs
            from backend.mcp_plugins.store import seed_mcp_plugins
            from backend.uefn_plugins.store import seed_uefn_plugins
            from backend.uefn_plugins.host import ensure_plugins_loaded

            seed_skill_packs()
            seed_mcp_plugins()
            seed_uefn_plugins()
            ensure_plugins_loaded()
            try:
                if "skill" in omit:
                    skill = ""
                elif conv.skill_snapshot.strip():
                    skill = conv.skill_snapshot
                else:
                    sel = resolve_conversation_selection(conv, settings)
                    skill = build_skill_prompt(sel)
            except FileNotFoundError:
                skill = "" if "skill" in omit else (conv.skill_snapshot or "")

            from backend.bridge import set_port_override
            from backend.listener_status import fetch_listener_status

            set_port_override(PANEL_LISTENER_PORT)
            listener_status = fetch_listener_status(
                PANEL_LISTENER_PORT,
                selected_project_root=settings.uefn_project_root,
            )
            listener_online = bool(listener_status.get("online"))
            listener_wedged = bool(listener_status.get("wedged"))

            mode_suffix = _PLAN_SUFFIX if plan_filter else _AGENT_SUFFIX
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
                max_turns=settings.agent_max_turns if not plan_filter else min(settings.agent_max_turns, 12),
                keep_last_messages=keep_last,
                skill_override=skill_override,
                plan_only=plan_filter,
                context_omit=omit,
                ducky_name=(conv.ducky_name or conv.title or "").strip(),
                ducky_personality=conv.ducky_personality or "",
                prompt_caching_enabled=bool(settings.prompt_caching_enabled),
                freeze_prompt_prefix=bool(settings.freeze_prompt_prefix),
                anthropic_extended_cache_ttl=bool(settings.anthropic_extended_cache_ttl),
                mode_suffix=mode_suffix,
                tool_result_format=settings.tool_result_format or "toon",
                thinking_effort=str(getattr(conv, "thinking_effort", "") or "off"),
            )

            if m == "ask":
                asyncio.run(
                    _run_ask_async(
                        conv,
                        content,
                        history,
                        provider_name=provider_name,
                        model=turn_model,
                        listener_online=listener_online,
                        project_root=settings.uefn_project_root,
                        skill=skill,
                        push=push,
                        cancel=session._cancel,
                        session=session,
                        run_id=run_id,
                        user_attachments=current_user_attachments,
                        context_omit=omit,
                    )
                )
            else:
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
                    )
                )
        except Exception as e:
            push({"type": "error", "text": str(e), "conv_id": conv_id})
            if session.run_id == run_id:
                _push_agent_stopped(push, conv_id, run_id, "error")

    session.start(work, run_id)
    return run_id