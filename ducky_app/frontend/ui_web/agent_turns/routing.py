"""Ask / Plan / Agent routing: start a chat's turn (here, or in the panel from the MCP bridge)
on Ducky's own agent or a coding agent, and send a message and wait for its reply."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import Any

from backend.agent.attachments import parse_attachment_dicts
from backend.agent.coding_agents.plans import PLAN_PROTOCOL
from backend.agent.runner import RunConfig
from backend.agent.secrets import get_key
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings, apply_workspace_env
from frontend.ui_web.agent_turns.pending import (
    BUSY_QUEUED,
    _hold_for_model,
    _make_broker_tap,
    _provider_label,
    _take_chat_model,
    _with_admission,
)
from frontend.ui_web.agent_turns.push import (
    PushFn,
    _in_bridge_process,
    _post_panel_run,
    _resolve_push,
    get_active_conv_id,
    notify_chats_changed,
)
from frontend.ui_web.agent_turns.run import (
    _backfill_video_frames,
    _last_assistant_text,
    _note_run_starter,
    _run_agent_loop,
    clear_last_interrupt,
    last_user_payload,
)
from frontend.ui_web.agent_turns.state import (
    _admission_context,
    _admission_lock,
    _alert_after_run,
    _check_admission,
    _child_waiters,
    _linked_parents,
    _make_run_scoped_push,
    _push_agent_stopped,
    _push_linked_agent,
    _quiet_runs,
    _run_started,
    _session,
    _sessions_lock,
    _started_by_workflow,
    _wrap_push_for_linked_child,
    cancel_agent,
    close_changeset_run,
    is_agent_running,
)
from frontend.ui_web.project_chats import append_message, load_conversation, save_conversation

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
    from backend.uefn_plugins.plugin_host.gateways import current_model_id, provider_option

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
            from backend.uefn_plugins.plugin_host.gateways import resolve_gateway_credential

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
        from backend.skills.selection import set_active_disabled_packs, set_active_enabled_subskills
        from backend.uefn_plugins.plugin_host.tool_registry import set_active_uefn_agent_plugin_ids

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
            from backend.skills.seeding import seed_skill_packs
            from backend.skills.selection import resolve_conversation_selection
            from backend.skills.skill_index import build_skill_prompt
            from backend.mcp_plugins.store import seed_mcp_plugins
            from backend.uefn_plugins.plugin_host.boot import ensure_plugins_loaded

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
