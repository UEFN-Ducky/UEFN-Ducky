"""Run a BYOA coding-agent turn for a conversation.

Adapters stream events live. If the plugin opts into resume, core only
passes and stores a session_id — how resume works is the plugin's job.
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
    """Host effort plus optional plugin ``register_coding_agent(thinking_env=…)``."""
    from backend.agent.thinking_effort import normalize_thinking_effort

    out: dict[str, str] = {
        "DUCKY_THINKING_EFFORT": normalize_thinking_effort(thinking_effort),
    }
    try:
        from backend.uefn_plugins.host import get_coding_agent_registration

        fn = (get_coding_agent_registration(agent_id) or {}).get("thinking_env")
        if callable(fn):
            extra = fn(thinking_effort)
            if isinstance(extra, dict):
                out.update({str(k): str(v) for k, v in extra.items()})
    except Exception:
        pass
    return out


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
    """Log the CLI run into the Settings ledger + (when known) conv.token_usage.

    Always writes a ledger row so cancelled / errored / empty-usage launches
    still appear. Provider is the coding-agent id (claude_code/codex/cursor).
    ``cost_usd`` is authoritative when the CLI reported one.
    """
    usage = getattr(result, "usage", None) if result is not None else None
    if not isinstance(usage, dict):
        usage = {}
    from frontend.ui_web.provider_usage_log import log_call
    from frontend.ui_web.token_usage import record_api_call, token_usage_report

    model = str(usage.get("model") or selected_model or "").strip()
    cost = usage.get("cost_usd")
    cost_usd = float(cost) if isinstance(cost, (int, float)) else None
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cache_read = int(usage.get("cache_read_tokens") or 0)
    cache_write = int(usage.get("cache_write_tokens") or 0)
    has_usage = bool(inp or out or cache_read or cache_write)
    try:
        log_call(
            provider=agent_id,
            model=model,
            input_tokens=inp if has_usage else 1,
            output_tokens=out,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cost_usd=cost_usd,
            conv_id=str(getattr(conv, "id", "") or ""),
            agent=str(getattr(conv, "coding_agent", "") or agent_id),
            ducky_label=str(
                getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or ""
            ),
        )
    except Exception:
        pass
    if not has_usage:
        return
    record_api_call(
        conv,
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        provider=agent_id,
        model=model,
        cost_usd=cost_usd,
    )
    stats: dict[str, Any] = {
        "coding_agent": agent_id,
        "model": model,
        "context_tokens": int(usage.get("context_tokens") or 0),
        "num_turns": int(usage.get("num_turns") or 0),
        "cost_usd": cost_usd,
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


def checkpoint_coding_turn(
    conv: Conversation,
    *,
    agent_id: str,
    run_id: str,
    blocks: list[dict[str, Any]] | None = None,
    reply: str = "",
    error: str = "",
    project_root: str | None = None,
) -> None:
    """Write the in-flight assistant to disk. Survives taskkill /F (no shutdown hooks)."""
    try:
        from frontend.ui_web.project_chats import upsert_in_flight_assistant

        text = (reply or "").strip()
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": text,
            "text": text,
            "ts": time.time(),
            "coding_agent": agent_id,
            "run_id": run_id,
            "incomplete": True,
        }
        if blocks:
            msg["blocks"] = [dict(b) if isinstance(b, dict) else b for b in blocks]
        if error:
            msg["error"] = error
        upsert_in_flight_assistant(conv, msg, run_id=run_id, project_root=project_root)
    except Exception:
        pass


class _TurnCheckpoint:
    """Rebuild persisted blocks from live push events; flush after every tool."""

    def __init__(
        self,
        conv: Conversation,
        agent_id: str,
        run_id: str,
        *,
        project_root: str | None = None,
    ) -> None:
        self.conv = conv
        self.agent_id = agent_id
        self.run_id = run_id
        self.project_root = project_root
        self.blocks: list[dict[str, Any]] = []
        self._last = 0.0

    def wrap(self, push: PushFn) -> PushFn:
        def wrapped(ev: dict[str, Any]) -> None:
            self._ingest(ev if isinstance(ev, dict) else {})
            push(ev)

        return wrapped

    def seed(self) -> None:
        checkpoint_coding_turn(
            self.conv,
            agent_id=self.agent_id,
            run_id=self.run_id,
            blocks=[],
            project_root=self.project_root,
        )

    def flush(self, *, error: str = "", force: bool = True) -> None:
        now = time.monotonic()
        if not force and now - self._last < 0.8:
            return
        self._last = now
        checkpoint_coding_turn(
            self.conv,
            agent_id=self.agent_id,
            run_id=self.run_id,
            blocks=self.blocks,
            error=error,
            project_root=self.project_root,
        )

    def _ingest(self, ev: dict[str, Any]) -> None:
        t = ev.get("type")
        text = ev.get("text") if isinstance(ev.get("text"), str) else ""
        if t == "thinking" and text:
            if self.blocks and self.blocks[-1].get("type") == "thinking":
                self.blocks[-1]["text"] = (self.blocks[-1].get("text") or "") + text
            else:
                self.blocks.append({"type": "thinking", "text": text})
            self.flush(force=False)
        elif t in ("text_delta", "text") and text:
            if self.blocks and self.blocks[-1].get("type") == "text":
                self.blocks[-1]["text"] = (self.blocks[-1].get("text") or "") + text
            else:
                self.blocks.append({"type": "text", "text": text})
            self.flush(force=False)
        elif t == "tool":
            tool = ev.get("tool") if isinstance(ev.get("tool"), dict) else {}
            self.blocks.append(
                {
                    "type": "tool_call",
                    "id": str(tool.get("id") or tool.get("name") or "tool") + f":{len(self.blocks)}",
                    "name": str(tool.get("name") or "tool"),
                    "arguments": tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {},
                    "status": "pending",
                    "result": {"ok": False, "data": "", "hint": ""},
                }
            )
            self.flush(force=True)
        elif t == "tool_done":
            tool = ev.get("tool") if isinstance(ev.get("tool"), dict) else {}
            name = str(tool.get("name") or "")
            status = str(tool.get("status") or ("error" if ev.get("success") is False else "success"))
            result_text = tool.get("result") if isinstance(tool.get("result"), str) else ""
            found = False
            for b in reversed(self.blocks):
                if (
                    b.get("type") == "tool_call"
                    and b.get("status") == "pending"
                    and (not name or b.get("name") == name)
                ):
                    b["status"] = status
                    b["duration_ms"] = int(tool.get("durationMs") or 0)
                    b["result"] = {
                        "ok": status != "error",
                        "data": result_text,
                        "hint": str(tool.get("hint") or ""),
                    }
                    if isinstance(tool.get("fileEdit"), dict):
                        b["file_edit"] = tool["fileEdit"]
                    found = True
                    break
            if not found:
                self.blocks.append(
                    {
                        "type": "tool_call",
                        "id": name or f"tool:{len(self.blocks)}",
                        "name": name or "tool",
                        "arguments": tool.get("arguments") if isinstance(tool.get("arguments"), dict) else {},
                        "status": status,
                        "duration_ms": int(tool.get("durationMs") or 0),
                        "result": {
                            "ok": status != "error",
                            "data": result_text,
                            "hint": str(tool.get("hint") or ""),
                        },
                        **(
                            {"file_edit": tool["fileEdit"]}
                            if isinstance(tool.get("fileEdit"), dict)
                            else {}
                        ),
                    }
                )
            self.flush(force=True)


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
    from frontend.ui_web.project_chats import save_conversation, upsert_in_flight_assistant

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
        "run_id": run_id,
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
    upsert_in_flight_assistant(conv, msg, run_id=run_id)
    if terminal_session_id:
        conv.terminal_session_id = terminal_session_id
    save_conversation(conv)
    from frontend.ui_web.agent_modes import close_changeset_run

    if ok:
        push({"type": "assistant_done", "conv_id": conv.id, "run_id": run_id})
        push({"type": "agent_stopped", "reason": "done", "conv_id": conv.id, "run_id": run_id})
        close_changeset_run(run_id, "done")
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
        close_changeset_run(run_id, stop_reason)
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
    project_root = (settings.uefn_project_root or "").strip()
    ckpt = _TurnCheckpoint(conv, agent_id, rid, project_root=project_root or None)
    push = ckpt.wrap(push)
    push({"type": "status", "text": f"Starting {adapter.label}…", "conv_id": conv.id, "run_id": rid})

    from backend.bridge import set_port_override
    from backend.bridge.status import fetch_listener_status
    from frontend.settings import PANEL_LISTENER_PORT

    set_port_override(PANEL_LISTENER_PORT)
    listener_status = fetch_listener_status(
        PANEL_LISTENER_PORT,
        selected_project_root=settings.uefn_project_root,
    )
    listener_online = bool(listener_status.get("online"))
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
    from frontend.ui_web.workspace_bootstrap import build_run_context, record_external_edits

    run_ctx = build_run_context(conv, run_id=rid, model=(model or conv.model or ''), coding_agent=agent_id)
    from frontend.ui_web.live_agent_runs import set_live_writer

    # Native Edit/Write hits disk outside the writer pipeline. The watcher
    # must pin those files to this duck, not "You".
    set_live_writer(rid, run_ctx.as_writer())
    mcp_path = write_uefn_mcp_config(conv_id=conv.id, settings=settings, identity=run_ctx)
    prompt_path = write_prompt_file(prompt_text, conv_id=conv.id)
    env = launch_env(
        prompt=prompt_text,
        prompt_file=prompt_path,
        system_prompt=system_prompt,
        conv_id=conv.id,
        project_root=project_root,
        extra=_thinking_env(agent_id, getattr(conv, "thinking_effort", "")),
        identity=run_ctx,
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
        ckpt.seed()
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
        ckpt.flush(error=str(exc), force=True)
        record_coding_agent_usage(conv, agent_id, model, None, push=push)
        push({"type": "error", "text": str(exc), "conv_id": conv.id, "run_id": rid})
        push({"type": "agent_stopped", "reason": "error", "conv_id": conv.id, "run_id": rid})
        return {"ok": False, "error": str(exc), "run_id": rid}
    finally:
        for path in (mcp_path, prompt_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # Persist whatever session id the plugin returned. How it resumes is plugin-owned.
    if adapter.capabilities.resume:
        new_sid = (result.upstream_session_id or "").strip()
        if new_sid != session_id:
            store_session_id(conv, agent_id, new_sid)

    # Record the CLI's REAL token usage/cost so each chat's Context panel shows
    # what the coding agent actually spent — not the embedded ducky's estimate.
    record_coding_agent_usage(conv, agent_id, model, result, push=push)
    # The CLI edited files in its own process; attribute those versions to this run.
    try:
        record_external_edits(result.blocks, run_ctx)
    except Exception:  # noqa: BLE001 - attribution must never fail the turn
        pass

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
