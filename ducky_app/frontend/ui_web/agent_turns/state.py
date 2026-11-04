"""Run state: one session per chat, run admission, which chats run (here or in the panel),
what a live turn is doing, linked sub-agents, stopping a turn and the end-of-run bookkeeping."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from backend.agent.runner import AgentRunner
from frontend.ui_web.agent_turns.push import PushFn, _in_bridge_process
from frontend.ui_web.chats.conversations import load_conversation

SESSION_JOIN_TIMEOUT = 2.0


class AgentSession:
    def __init__(self, conv_id: str = "") -> None:
        self.conv_id = conv_id
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._runner: AgentRunner | None = None
        self.run_id: str = ""

    def set_runner(self, runner: AgentRunner | None, *, run_id: str) -> None:
        with _admission_lock:
            if self.run_id != run_id:
                return
            self._runner = runner

    def clear_runner(self, *, run_id: str) -> None:
        with _admission_lock:
            if self.run_id == run_id:
                self._runner = None

    def prepare_run(self, run_id: str) -> None:
        from frontend.ui_web.live_agent_runs import add_live_run_id, discard_live_run_id
        admission = getattr(_admission_context, "current", None)
        if admission:
            _check_admission(*admission)
        if not admission:
            self.cancel()
        old = self._thread
        if old is not None and old.is_alive():
            old.join(SESSION_JOIN_TIMEOUT)
        with _admission_lock:
            if admission:
                _check_admission(*admission)
            self._admission = admission
            self._cancel = threading.Event()
            old_rid = self.run_id
            self.run_id = run_id
            if old_rid and old_rid != run_id:
                discard_live_run_id(old_rid)
            add_live_run_id(run_id)

    def start(self, target: Callable[[], None], run_id: str) -> None:
        admission = getattr(_admission_context, "current", None)
        if admission:
            _check_admission(*admission)
        if self.run_id != run_id:
            self.prepare_run(run_id)
        admission = admission or getattr(self, "_admission", None)
        if admission:
            _check_admission(*admission)
        def run() -> None:
            try:
                if admission:
                    _check_admission(*admission)
                target()
            except _AdmissionCancelled:
                pass
            finally:
                discard_live_run_id(run_id)
                if admission:
                    _release_admission(*admission)
                # A finished chat keeps no session: one per chat ever run piled up all
                # day, and Stop all walked every one of them. A follow-up already
                # prepared on this session changed run_id and keeps it.
                with _admission_lock:
                    if self.conv_id and _sessions.get(self.conv_id) is self and self.run_id == run_id:
                        _sessions.pop(self.conv_id, None)
        worker = threading.Thread(target=run, daemon=True, name=f"agent-{run_id[:8]}")
        from frontend.ui_web.live_agent_runs import add_live_run_id, discard_live_run_id

        try:
            with _admission_lock:
                if admission:
                    _check_admission(*admission)
                if self.run_id != run_id:
                    raise _AdmissionCancelled()
                self._thread = worker
                add_live_run_id(run_id)
                if admission:
                    admission[1]["transferred"] = True
            worker.start()
        except Exception:
            discard_live_run_id(run_id)
            if admission:
                _release_admission(*admission)
            raise

    def cancel(self) -> None:
        self._cancel.set()
        runner = self._runner
        if runner is not None:
            runner.cancel()


_sessions: dict[str, AgentSession] = {}
_sessions_lock = threading.Lock()
_linked_parents: dict[str, str] = {}
_child_waiters: dict[str, set[str]] = {}
# Runs another chat or a workflow started (sub-agents, Agent steps): the parent's
# finish, or the workflow's, is the one that alerts.
_quiet_runs: set[str] = set()
# A reply quicker than this was most likely watched as it happened: no alert.
PHONE_PUSH_MIN_SECONDS = 20.0
_run_started: dict[str, float] = {}

# Briefing appends and local run admission use the same short critical section.
# Provider work, joins and user callbacks never run under this lock.
_admission_lock = threading.RLock()
_admission_changed = threading.Condition(_admission_lock)
_admission_context = threading.local()
_admissions: dict[str, dict[str, Any]] = {}

# Agent messages that arrived mid-turn (queue_if_busy): started when that turn ends.
# A person's Stop drops them, like the A2A inbox (cancel_agent).
_pending_agent_messages: dict[str, list[str]] = {}
_pending_lock = threading.Lock()
# A person's send with attachments into a running turn: its own next turn (attachments can't steer).
_pending_person_sends: dict[str, list[dict[str, Any]]] = {}
# steer id -> text of a person's send that is both queued and offered to the turn as a steer.
_pending_steered: dict[str, dict[str, str]] = {}


class _AdmissionCancelled(Exception):
    pass


def _reserve_admission(conv_id: str) -> dict[str, Any] | None:
    with _admission_lock:
        if is_agent_running(conv_id):
            return None
        token: dict[str, Any] = {"transferred": False}
        _admissions[conv_id] = token
        return token


def _release_admission(conv_id: str, token: dict[str, Any]) -> None:
    with _admission_changed:
        if _admissions.get(conv_id) is token:
            _admissions.pop(conv_id, None)
            _admission_changed.notify_all()


def _check_admission(conv_id: str, token: dict[str, Any]) -> None:
    with _admission_lock:
        if _admissions.get(conv_id) is not token:
            raise _AdmissionCancelled()


def append_if_idle(conv_id: str, append: Callable[[], bool | None]) -> bool:
    """Atomically append stored context only when no turn has been admitted."""
    with _admission_lock:
        if is_agent_running(conv_id):
            return False
        return append() is not False


def _running_here(conv_id: str) -> bool:
    with _admission_lock:
        if conv_id in _admissions:
            return True
    session = _sessions.get(conv_id)
    if session is None or session._thread is None:
        return False
    return session._thread.is_alive()


# The MCP bridge (Codex / Claude Code tool calls) runs no agents; the panel does. From the
# bridge, "is it running", "stop it" and "who started it" ask the panel (Oct 21 2026: a
# Codex lead saw its busy member as idle, sent all its parallel work into that one chat,
# and ducky_agent_stop stopped nothing).
_PANEL_RUNS_TTL_S = 1.0
_NO_PANEL_TTL_S = 10.0
_panel_runs_cache: tuple[float, dict[str, Any] | None] = (float("-inf"), None)


def _panel_runs(cancel: str = "", by_user: bool = True) -> dict[str, Any] | None:
    """The panel's runs ({running, parents}) asked from the MCP bridge; None = use this
    process's own (it is the panel, or no panel is open and the bridge runs turns itself)."""
    global _panel_runs_cache
    from backend.process_role import role

    if role() != "tool_server" or not _in_bridge_process():
        return None
    at, state = _panel_runs_cache
    if not cancel and time.monotonic() - at < (_PANEL_RUNS_TTL_S if state else _NO_PANEL_TTL_S):
        return state
    from backend.panel.rpc import panel_rpc

    out = panel_rpc("agent_runs", {"cancel": cancel, "by_user": by_user}, timeout=5.0)
    state = out if isinstance(out, dict) and isinstance(out.get("running"), list) else None
    _panel_runs_cache = (time.monotonic(), state)
    return state


def run_state(cancel: str = "", by_user: bool = True) -> dict[str, Any]:
    """This process's runs for the bridge's ``agent_runs`` request, after an optional stop."""
    if cancel:
        _cancel_here(cancel, by_user=by_user)
    return {"running": _running_here_all(), "parents": dict(_linked_parents)}


def _running_here_all() -> list[str]:
    with _admission_lock:
        candidates = set(_sessions) | set(_admissions)
        return [cid for cid in candidates if _running_here(cid)]


def is_agent_running(conv_id: str) -> bool:
    panel = _panel_runs()
    if panel is not None:
        return conv_id in panel["running"]
    return _running_here(conv_id)


def _parents() -> dict[str, str]:
    panel = _panel_runs()
    return (panel.get("parents") or {}) if panel is not None else _linked_parents


def linked_parent_of(conv_id: str) -> str | None:
    return _parents().get(conv_id)


def linked_children_of(conv_id: str) -> list[str]:
    return [child for child, parent in _parents().items() if parent == conv_id]


def list_running_agents() -> list[str]:
    panel = _panel_runs()
    return list(panel["running"]) if panel is not None else _running_here_all()


def running_turn_settings(conv_id: str) -> dict[str, str]:
    """The mode and model a running chat's turn uses, to continue it in another process."""
    config = getattr(getattr(_sessions.get(conv_id), "_runner", None), "config", None)
    return {"mode": str(getattr(config, "mode", "") or "agent"), "model": str(getattr(config, "model", "") or "")}


def live_changeset_run_ids() -> frozenset[str]:
    """Run ids whose agent thread is still alive — the only ledgers that may stay ``running``."""
    with _sessions_lock:
        return frozenset(
            session.run_id for cid, session in _sessions.items() if session.run_id and is_agent_running(cid)
        )


# Per chat: the live run's last output time and its Ducky tools started but not done
# ({tool id: (name, started_at)}), for ducky_health's agent states. Written by the run's
# own push; plain dict swaps, so readers never wait on a turn.
_turn_activity: dict[str, dict[str, Any]] = {}


def _note_activity(conv_id: str, run_id: str, event: dict[str, Any]) -> None:
    act = _turn_activity.get(conv_id)
    if act is None or act["run_id"] != run_id:
        act = _turn_activity[conv_id] = {"run_id": run_id, "last_output": 0.0, "tools": {}}
    now = time.time()
    act["last_output"] = now
    kind, tool = event.get("type"), event.get("tool") or {}
    if kind == "tool" and tool.get("status") == "pending":
        act["tools"][str(tool.get("id") or tool.get("name") or "")] = (str(tool.get("name") or ""), now)
    elif kind == "tool_done":
        act["tools"].pop(str(tool.get("id") or tool.get("name") or ""), None)


def _live_activity(conv_id: str) -> dict[str, Any] | None:
    act = _turn_activity.get(conv_id)
    with _sessions_lock:
        session = _sessions.get(conv_id)
    if act is None or session is None or session.run_id != act["run_id"] or not _running_here(conv_id):
        return None
    return act


def running_tool(conv_id: str) -> dict[str, Any] | None:
    """The Ducky tool the chat's live turn is in (last started, not done): ``{name, started_at}``."""
    act = _live_activity(conv_id)
    if act is None or not act["tools"]:
        return None
    name, started = max(list(act["tools"].values()), key=lambda t: t[1])
    return {"name": name, "started_at": started}


def last_output_age_s(conv_id: str) -> float | None:
    """Seconds since the chat's live turn last sent anything (text, tool, status)."""
    act = _live_activity(conv_id)
    if act is None or not act["last_output"]:
        return None
    return max(0.0, time.time() - act["last_output"])


def _make_run_scoped_push(push: PushFn, session: AgentSession, conv_id: str, run_id: str) -> PushFn:
    def scoped(event: dict[str, Any]) -> None:
        if session.run_id != run_id:
            return
        out = dict(event)
        out.setdefault("conv_id", conv_id)
        out["run_id"] = run_id
        try:
            _note_activity(conv_id, run_id, out)
        except Exception:  # noqa: BLE001 - a meter never stops a turn
            pass
        push(out)

    return scoped


def close_changeset_run(run_id: str, reason: str) -> None:
    """Close the run's changeset ledger (done/error/cancelled). Never raises."""
    try:
        from backend.workspace.journal import FileChangeJournal
        from backend.workspace.runtime import get_writer

        writer = get_writer()
        journal = writer.journal
        if not isinstance(journal, FileChangeJournal) or not run_id:
            return
        try:
            root = writer.root()
        except Exception:
            from backend.bridge import workspace_roots

            roots = workspace_roots()
            if not roots:
                return
            root = roots[0]
        status = {"done": "done", "cancelled": "cancelled", "stopped": "cancelled"}.get(reason, "error")
        journal.end_run(run_id, status, project_root=root)
    except Exception:  # noqa: BLE001 - ledger bookkeeping must never break a stop
        pass


def _alert_finished(conv_id: str, reason: str) -> None:
    """Alert that this chat finished or failed (frontend.notifications). Off-thread:
    loading the chat never holds up the agent's own thread."""

    def _send() -> None:
        try:
            conv = load_conversation(conv_id)
            # Group members report to their hub; the hub's own run is the one to announce.
            if conv is None or str(getattr(conv, "parent_conv_id", "") or ""):
                return
            from frontend.notifications import alert

            name = str(getattr(conv, "title", "") or "").strip() or "Your ducky"
            if reason == "done":
                alert(name, "Finished.", level="ok", kind="chat", target_id=conv_id)
            else:
                alert(name, "Stopped with an error.", level="error", kind="chat", target_id=conv_id)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True, name="chat-alert").start()


def _alert_after_run(conv_id: str, run_id: str, reason: str, detail: str = "") -> None:
    """A run ended: a failure always alerts, a finish only after long work (a quick reply
    was watched), a sub-agent's or a workflow step's never. Clears the run's bookkeeping."""
    quiet = run_id in _quiet_runs
    _quiet_runs.discard(run_id)
    started = _run_started.pop(run_id, None)
    try:
        from frontend import usage_stats

        usage_stats.run_end(run_id, reason, detail)
    except Exception:
        pass
    if quiet:
        return
    if reason in ("error", "timeout"):
        _alert_finished(conv_id, reason)
    elif reason == "done" and not (started is not None and time.monotonic() - started < PHONE_PUSH_MIN_SECONDS):
        _alert_finished(conv_id, reason)


def _started_by_workflow() -> bool:
    """This run starts inside a workflow run (an Agent or Prompt step)."""
    try:
        from backend.automations.runner import _LIVE

        return _LIVE.get() is not None
    except Exception:
        return False


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
    close_changeset_run(run_id, reason)
    _alert_after_run(conv_id, run_id, reason, str(event.get("detail") or ""))
    if reason != "done":
        return
    # Private DM with a group member → short note on the group hub for everyone.
    try:
        from frontend.ui_web.group_orchestrator import announce_private_member_talk

        announce_private_member_talk(conv_id, push=push)
    except Exception:
        pass


def wait_for_idle(conv_id: str, timeout: float = SESSION_JOIN_TIMEOUT) -> bool:
    deadline = time.monotonic() + max(0.0, float(timeout))
    with _admission_changed:
        while conv_id in _admissions:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _admission_changed.wait(remaining)
    session = _sessions.get(conv_id)
    if session is None or session._thread is None:
        return True
    if not session._thread.is_alive():
        return True
    if session._thread is threading.current_thread():
        return False
    session._thread.join(max(0.0, deadline - time.monotonic()))
    return not is_agent_running(conv_id)


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


def cancel_agent(conv_id: str | None = None, *, by_user: bool = True) -> None:
    """Stop a chat's agent. ``by_user=False``: a new turn replaces the running one; that is
    not a Stop, so the chat stays free for automatic work and nobody is told it was stopped
    (Oct 12 2026: a recycle's fresh-session message told the lead "stopped by the user", and
    the lead stood its team down)."""
    if conv_id and _panel_runs(cancel=conv_id, by_user=by_user) is not None:
        return  # the panel runs it and stopped it
    _cancel_here(conv_id, by_user=by_user)


def _cancel_here(conv_id: str | None = None, *, by_user: bool = True) -> None:
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
        with _admission_lock:
            targets = list(set(targets) | set(_admissions))

    for cid in targets:
        with _admission_lock:
            cancelling = {"transferred": False, "cancelling": True}
            _admissions[cid] = cancelling
            session = _sessions.get(cid)
            runner = None
            insist = False
            if session is not None:
                insist = by_user and session._cancel.is_set()
                session._cancel.set()
                runner = session._runner
        try:
            if insist:  # a second Stop while the agent is asked to stop: kill now
                from backend.agent.coding_agents import proc_exec

                proc_exec.terminate_conv_process(cid)
                from frontend.agent_host import turns as host_turns

                host_turns.kill(cid)
            if runner is not None:
                try:
                    runner.cancel()
                except Exception:
                    pass
            with _pending_lock:
                _pending_agent_messages.pop(cid, None)
                _pending_person_sends.pop(cid, None)
                _pending_steered.pop(cid, None)
            if by_user:
                from frontend.ui_web import admission

                admission.drop(cid)  # a Stopped chat leaves the memory wait line
                from backend.agent.a2a_broker import on_agent_cancelled_by_user

                on_agent_cancelled_by_user(cid)
        except Exception:
            pass
        finally:
            _release_admission(cid, cancelling)


def _session(conv_id: str) -> AgentSession:
    if conv_id not in _sessions:
        _sessions[conv_id] = AgentSession(conv_id)
    return _sessions[conv_id]
