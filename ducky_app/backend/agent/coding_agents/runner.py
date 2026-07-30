"""Run a BYOA coding-agent turn for a conversation (Claude/Codex/Cursor).

Traycer-style semantics: adapters stream events live and resume a persisted
upstream session (`claude --resume` / `codex exec resume`), so an external
coding agent keeps its memory across every turn of the same panel chat.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

from backend.agent.coding_agents.base import get_adapter, normalize_coding_agent
from backend.agent.coding_agents.mcp_inject import (
    bootstrap_system_prompt,
    launch_env,
    write_prompt_file,
    write_uefn_mcp_config,
)
from backend.agent.coding_agents.settings_helpers import coding_agent_cfg
from frontend.chat_store import Conversation
from frontend.settings import PanelSettings, apply_workspace_env

PushFn = Callable[[dict[str, Any]], None]

_HISTORY_PREFIX_MAX_CHARS = 6000


def _thinking_env(agent_id: str, thinking_effort: str) -> dict[str, str]:
    """Optional env from plugin ``register_coding_agent(thinking_env=…)``."""
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        fn = (get_coding_agent_registration(agent_id) or {}).get("thinking_env")
        if callable(fn):
            out = fn(thinking_effort)
            return dict(out) if isinstance(out, dict) else {}
    except Exception:
        pass
    return {}


def _normalize_launch_model(agent_id: str, model: str) -> str:
    mid = (model or "").strip()
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        fn = (get_coding_agent_registration(agent_id) or {}).get("normalize_model")
        if callable(fn):
            return str(fn(mid) or "").strip()
    except Exception:
        pass
    return "" if mid.lower() == "default" else mid


def read_session_id(conv: Conversation, agent_id: str) -> str:
    """Upstream session id persisted as '<agent>:<sid>'; ignore other agents' ids."""
    raw = (getattr(conv, "upstream_session_id", "") or "").strip()
    if not raw or ":" not in raw:
        return ""
    prefix, sid = raw.split(":", 1)
    return sid.strip() if prefix == agent_id else ""


def store_session_id(conv: Conversation, agent_id: str, session_id: str) -> None:
    conv.upstream_session_id = f"{agent_id}:{session_id.strip()}" if session_id.strip() else ""


def record_coding_agent_usage(
    conv: Conversation,
    agent_id: str,
    selected_model: str,
    result: Any,
    push: PushFn | None = None,
) -> None:
    """Log the CLI's real usage into conv.token_usage + a UI snapshot.

    Provider is the coding-agent id (claude_code/codex/cursor) so per-call rows
    are attributed to the actual backend; the reported cost (when present) is
    authoritative over pricing tables.
    """
    usage = getattr(result, "usage", None)
    if not isinstance(usage, dict) or not usage:
        return
    from frontend.ui_web.token_usage import record_api_call, token_usage_report

    model = str(usage.get("model") or selected_model or "").strip()
    cost = usage.get("cost_usd")
    record_api_call(
        conv,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_tokens") or 0),
        cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
        provider=agent_id,
        model=model,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
    )
    stats: dict[str, Any] = {
        "coding_agent": agent_id,
        "model": model,
        "context_tokens": int(usage.get("context_tokens") or 0),
        "num_turns": int(usage.get("num_turns") or 0),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else None,
        "updated": time.time(),
    }
    limit = int(usage.get("context_limit") or 0)
    if limit > 0:
        stats["context_limit"] = limit
    conv.coding_agent_stats = stats
    if push is not None:
        report = token_usage_report(conv)
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
                "calls": report["calls"],
            }
        )


def collect_image_paths(conv: Conversation, project_root: str | None = None) -> list[str]:
    """Absolute paths of image attachments on the chat's latest user message.

    Uploaded images are persisted under conversations/<id>/attachments/ with a
    relative ``path``; resolve them so the coding agent can actually see them.
    """
    messages = list(getattr(conv, "messages", None) or [])
    last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    if not isinstance(last_user, dict):
        return []
    attachments = last_user.get("attachments")
    if not isinstance(attachments, list) or not attachments:
        return []
    from frontend.ui_web.project_chats import get_conversations_dir

    conv_dir = get_conversations_dir(project_root) / conv.id
    out: list[str] = []
    for att in attachments:
        if not isinstance(att, dict) or att.get("kind") != "image":
            continue
        rel = str(att.get("path") or "").strip()
        if not rel:
            continue
        full = (conv_dir / rel).resolve()
        if full.is_file():
            out.append(str(full))
    return out


def build_history_prefix(conv: Conversation, *, max_chars: int = _HISTORY_PREFIX_MAX_CHARS) -> str:
    """Bounded transcript prefix for adapters that cannot resume an upstream session."""
    from backend.agent.a2a_format import flatten_transcript

    history = [m for m in conv.messages[:-1] if isinstance(m, dict)]
    if not history:
        return ""
    flat = flatten_transcript(history, max_chars=max_chars)
    if not flat.strip():
        return ""
    return (
        "Earlier turns of this conversation (you have no session memory — read this first):\n"
        f"{flat}\n\n--- current request ---\n"
    )


def _emit_assistant(
    conv: Conversation,
    *,
    agent_id: str,
    reply: str,
    push: PushFn,
    run_id: str,
    ok: bool = True,
    error: str = "",
    terminal_session_id: str = "",
    status: str = "done",
    streamed: bool = False,
    blocks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from frontend.ui_web.project_chats import append_message, save_conversation

    text = (reply or "").strip()
    if not text and not blocks:
        text = (
            f"{agent_id} finished with no captured reply."
            + (f"\n\n{error}" if error else "")
        )
    if not streamed:
        chunk = 80
        for i in range(0, len(text), chunk):
            push({"type": "text_delta", "text": text[i : i + chunk], "conv_id": conv.id, "run_id": run_id})
    msg: dict[str, Any] = {
        "role": "assistant",
        "content": text,
        "text": text,
        "ts": time.time(),
        "coding_agent": agent_id,
        "terminal_session_id": terminal_session_id or "",
    }
    # Interleaved thinking/text/tool_call steps in the embedded agent's format,
    # so the turn's tool steps survive a panel reload (load_messages rebuilds
    # the rows from these, exactly like embedded-ducky turns).
    if blocks:
        msg["blocks"] = list(blocks)
    if not ok:
        msg["incomplete"] = True
        if error:
            msg["error"] = error
    append_message(conv, msg)
    if terminal_session_id:
        conv.terminal_session_id = terminal_session_id
    save_conversation(conv)
    if ok:
        push({"type": "assistant_done", "conv_id": conv.id, "run_id": run_id})
        push({"type": "agent_stopped", "reason": "done", "conv_id": conv.id, "run_id": run_id})
        # Same as embedded path: private DM with a group member → hub note.
        try:
            from frontend.ui_web.group_orchestrator import announce_private_member_talk

            announce_private_member_talk(conv.id, push=push)
        except Exception:
            pass
    else:
        push(
            {
                "type": "error",
                "text": error or f"{agent_id} failed",
                "conv_id": conv.id,
                "run_id": run_id,
                "kept_partial": True,
            }
        )
        stop_reason = status if status in ("needs_login", "timeout", "cancelled") else "error"
        push(
            {
                "type": "agent_stopped",
                "reason": stop_reason,
                "detail": error or f"{agent_id} {stop_reason}",
                "conv_id": conv.id,
                "run_id": run_id,
            }
        )
    return {
        "ok": ok,
        "run_id": run_id,
        "reply": text,
        "terminal_session_id": terminal_session_id,
        "error": error,
        "status": status,
    }


def run_coding_agent_message(
    conv: Conversation,
    user_text: str,
    *,
    model: str,
    push: PushFn,
    run_id: str = "",
    timeout_s: float = 0.0,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Execute one external coding-agent turn; persist assistant reply on conv.

    ``timeout_s`` <= 0 means no wall-clock limit — the CLI runs until it finishes
    or the user cancels. Long UEFN builds must not be killed at 15 minutes.
    """
    agent_id = normalize_coding_agent(getattr(conv, "coding_agent", None) or "ducky")
    if agent_id == "ducky":
        return {"ok": False, "error": "not an external coding agent"}

    adapter = get_adapter(agent_id)
    if adapter is None:
        return {"ok": False, "error": f"unknown coding agent: {agent_id}"}

    settings = PanelSettings.load()
    apply_workspace_env(settings.uefn_project_root)
    cfg = coding_agent_cfg(settings, agent_id)
    if not cfg.get("enabled", True):
        push({"type": "error", "text": f"{adapter.label} is disabled in Settings → LLMs", "conv_id": conv.id})
        return {"ok": False, "error": "disabled"}

    info = adapter.detect(settings)
    if not info.available:
        push({"type": "error", "text": info.status, "conv_id": conv.id})
        return {"ok": False, "error": info.status}

    rid = run_id or str(uuid.uuid4())
    push({"type": "status", "text": f"Starting {adapter.label}…", "conv_id": conv.id, "run_id": rid})

    from backend.bridge import set_port_override
    from backend.listener_status import fetch_listener_status
    from frontend.settings import PANEL_LISTENER_PORT

    set_port_override(PANEL_LISTENER_PORT)
    listener_status = fetch_listener_status(
        PANEL_LISTENER_PORT,
        selected_project_root=settings.uefn_project_root,
    )
    listener_online = bool(listener_status.get("online"))
    project_root = (settings.uefn_project_root or "").strip()
    cwd = project_root or "."
    cli_path = str(cfg.get("cli_path") or "")

    from backend.uefn_plugins.host import get_coding_agent_registration

    reg = get_coding_agent_registration(agent_id) or {}

    prompt_text = user_text
    before_launch = reg.get("before_launch")
    if callable(before_launch):
        auth_result = before_launch(
            conv=conv,
            user_text=user_text,
            cli_path=cli_path,
            cwd=cwd,
            push=push,
            run_id=rid,
            agent_id=agent_id,
            emit_assistant=_emit_assistant,
        )
        if isinstance(auth_result, dict) and "__run_prompt__" in auth_result:
            prompt_text = str(auth_result["__run_prompt__"] or user_text)
            push(
                {
                    "type": "status",
                    "text": f"{adapter.label} logged in — continuing…",
                    "conv_id": conv.id,
                    "run_id": rid,
                }
            )
        elif auth_result is not None:
            return auth_result

    session_id = read_session_id(conv, agent_id) if adapter.capabilities.resume else ""

    image_paths = collect_image_paths(conv, project_root)

    if not adapter.capabilities.resume:
        history_prefix = build_history_prefix(conv)
        if history_prefix:
            prompt_text = history_prefix + prompt_text

    from backend.agent.coding_agents.mcp_inject import deployed_skill_packs

    skills_dir, skill_names = deployed_skill_packs(agent_id)
    system_prompt = bootstrap_system_prompt(
        project_root=project_root,
        listener_online=listener_online,
        conv_id=conv.id,
        ducky_name=(conv.ducky_name or "").strip(),
        ducky_personality=(conv.ducky_personality or "").strip(),
        skills_dir=skills_dir,
        skill_names=skill_names,
        native_skills=bool(reg.get("native_skills")),
    )
    mcp_path = write_uefn_mcp_config(conv_id=conv.id, settings=settings)
    prompt_path = write_prompt_file(prompt_text, conv_id=conv.id)
    env = launch_env(
        prompt=prompt_text,
        prompt_file=prompt_path,
        system_prompt=system_prompt,
        conv_id=conv.id,
        project_root=project_root,
        extra=_thinking_env(agent_id, getattr(conv, "thinking_effort", "")),
    )

    try:
        launch_model = _normalize_launch_model(
            agent_id, (model or conv.model or "").strip()
        )
        if not launch_model:
            err = (
                f"No model selected for {agent_id}. "
                "Pick one on this chat or Ducky profile."
            )
            push({"type": "error", "text": err, "conv_id": conv.id, "run_id": rid})
            push({"type": "agent_stopped", "reason": "error", "conv_id": conv.id, "run_id": rid})
            return {"ok": False, "error": err, "run_id": rid}
        result = adapter.launch(
            prompt=prompt_text,
            system_prompt=system_prompt,
            cwd=cwd,
            conv_id=conv.id,
            model=launch_model,
            mcp_config_path=str(mcp_path),
            extra_args=str(cfg.get("default_args") or ""),
            cli_path=cli_path,
            env=env,
            push=push,
            session_id=session_id,
            run_id=rid,
            cancel=cancel,
            timeout_s=float(timeout_s),
            image_paths=image_paths,
        )
    except Exception as exc:
        push({"type": "error", "text": str(exc), "conv_id": conv.id, "run_id": rid})
        push({"type": "agent_stopped", "reason": "error", "conv_id": conv.id, "run_id": rid})
        return {"ok": False, "error": str(exc), "run_id": rid}
    finally:
        for path in (mcp_path, prompt_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # Persist the upstream session so the next turn resumes with full memory.
    if adapter.capabilities.resume:
        new_sid = (result.upstream_session_id or "").strip()
        if new_sid != session_id:
            store_session_id(conv, agent_id, new_sid)

    # Record the CLI's REAL token usage/cost so each chat's Context panel shows
    # what the coding agent actually spent — not the embedded ducky's estimate.
    record_coding_agent_usage(conv, agent_id, model, result, push=push)

    reply = (result.reply_text or "").strip()
    if not reply and not result.blocks and result.output_tail:
        # Raw-tail salvage only when the turn produced nothing structured —
        # with blocks present an empty final text is legitimate.
        reply = result.output_tail.strip()[-8000:]

    on_needs_login = reg.get("on_needs_login")
    if callable(on_needs_login) and not result.ok and result.status == "needs_login":
        return on_needs_login(
            conv=conv,
            user_text=prompt_text,
            cli_path=cli_path,
            cwd=cwd,
            push=push,
            run_id=rid,
            agent_id=agent_id,
            reply=reply,
            result=result,
            emit_assistant=_emit_assistant,
        )

    return _emit_assistant(
        conv,
        agent_id=agent_id,
        reply=reply,
        push=push,
        run_id=rid,
        ok=result.ok,
        error=result.error or "",
        terminal_session_id=result.terminal_session_id or "",
        status=result.status or ("done" if result.ok else "error"),
        streamed=result.streamed,
        blocks=result.blocks,
    )
