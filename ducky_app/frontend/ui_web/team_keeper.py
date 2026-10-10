"""Team keeper: wakes a team's coordinator when its plan has open work and nobody is working.

A coordinator agent only runs when a message wakes it. When it ended a turn with no
reply on the way, or the AI account ran out of credits, the whole team stopped until a
person noticed and nudged it. The keeper is that nudge, inside Ducky.

A plan is kept going while it has started, is not paused or finished, still has open
steps, and its chat leads a group. Pausing the plan stops the keeper for it.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

_TICK_S = 60.0
_IDLE_S = 120.0  # nobody running this long before a wake
_MAX_WAIT_S = 3600.0  # wakes that start no work back off up to this

WAKE_TEXT = (
    "[Ducky keeper] Your team has had no agent running for {minutes} minutes and the plan "
    "still has {open_steps} open steps. Re-read the charter and the plan, check each "
    "in-progress step and its agent (ducky_agent_list, ducky_agent_transcript), and dispatch "
    "the work that can move now. If you are blocked on a decision only the user can make, "
    "say exactly which one and end your turn."
)

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
# coordinator chat id -> {"quiet_since": t, "wakes": n, "next_wake": t}
_teams: dict[str, dict[str, float]] = {}


def _open_steps(plan: dict[str, Any]) -> int:
    prog = plan.get("progress") or {}
    return int(prog.get("pending") or 0) + int(prog.get("in_progress") or 0)


def _started(plan: dict[str, Any]) -> bool:
    prog = plan.get("progress") or {}
    return int(prog.get("completed") or 0) + int(prog.get("in_progress") or 0) > 0


def _team_leaders(project_root: str | None) -> set[str]:
    from frontend.ui_web.group_orchestrator import is_group_conversation
    from frontend.ui_web.project_chats import list_all_conversation_metadata

    leaders: set[str] = set()
    for conv in list_all_conversation_metadata(project_root):
        if is_group_conversation(conv):
            lid = (getattr(conv, "leader_conv_id", None) or "").strip()
            if lid:
                leaders.add(lid)
    return leaders


def kept_plans(project_root: str | None = None) -> list[dict[str, Any]]:
    """Team plans the keeper looks after right now."""
    from backend.agent.coding_agents.plans import list_plans

    leaders = _team_leaders(project_root)
    out: list[dict[str, Any]] = []
    for plan in list_plans(project_root):
        if str(plan.get("status") or "open") in ("paused", "finished"):
            continue
        if not _started(plan) or not _open_steps(plan):
            continue
        if str(plan.get("chat_id") or "") in leaders:
            out.append(plan)
    return out


def _wake(chat_id: str, text: str) -> None:
    from frontend.ui_web.agent_modes import run_message
    from frontend.ui_web.project_chats import load_conversation

    conv = load_conversation(chat_id)
    model = str(getattr(conv, "model", "") or "") if conv is not None else ""
    # A follow-up in the coordinator's own chat: it keeps that chat's approvals.
    run_message(chat_id, text, "agent", model, queue_if_busy=True)


def _team_context(plan: dict[str, Any]) -> tuple[set[str], str]:
    from backend.agent.coding_agents.plans import _chat_and_group_ids
    from backend.agent.coding_agents.team_plan_events import assigned_nodes
    from frontend.ui_web.project_chats import list_all_conversation_metadata

    nodes = list(assigned_nodes(plan.get("nodes")))
    owners = {owner for _, owner in nodes if owner}
    members = {}
    for conv in list_all_conversation_metadata(plan.get("project_root")) if owners else []:
        cid = str(getattr(conv, "id", ""))
        if not getattr(conv, "is_group", False) and owners.intersection(_chat_and_group_ids(cid, plan.get("project_root"))):
            members[cid] = getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or cid
    open_names = [f"{n['content']} ({n['id']})" for n, _ in nodes
                  if n.get("status") not in {"completed", "cancelled"} and not n.get("children")]
    from backend.agent.a2a_broker import unanswered_reports

    reports = [r["body"] for r in plan.get("team_reports", [])]
    reports += [r["body"] for r in unanswered_reports(str(plan.get("chat_id") or ""))]
    detail = "\nOpen steps: " + ("; ".join(open_names) or "see plan")
    detail += "\nIdle members: " + ("; ".join(f"{name} ({cid})" for cid, name in members.items()) or "none listed")
    if reports:
        detail += "\nReports awaiting next dispatch:\n" + "\n".join(reports)
    return set(members) | owners, detail


def tick(
    *,
    now: float | None = None,
    plans: list[dict[str, Any]] | None = None,
    running: list[str] | None = None,
    wake: Callable[[str, str], None] | None = None,
) -> list[str]:
    """One check. Returns the coordinator chats it woke (arguments are for tests)."""
    from backend.agent.a2a_broker import automatic_work_blocked

    now = time.time() if now is None else now
    if plans is None:
        plans = kept_plans()
    if running is None:
        from frontend.ui_web.agent_modes import list_running_agents

        running = list(list_running_agents())
    wake = wake or _wake
    busy = set(running)
    woke: list[str] = []
    with _lock:
        live = {str(p.get("chat_id") or "") for p in plans}
        for gone in [cid for cid in _teams if cid not in live]:
            _teams.pop(gone, None)
        for plan in plans:
            cid = str(plan.get("chat_id") or "")
            st = _teams.setdefault(cid, {"quiet_since": now, "wakes": 0.0, "next_wake": 0.0})
            member_ids, details = _team_context(plan)
            team_busy = busy.intersection(member_ids | {cid}) if member_ids else busy
            if team_busy:
                st["quiet_since"] = now
                if team_busy - {cid}:
                    st["wakes"] = 0.0  # the team is working again: drop the backoff
                    st["next_wake"] = 0.0
                continue
            if automatic_work_blocked(cid) or now - st["quiet_since"] < _IDLE_S or now < st["next_wake"]:
                continue
            minutes = int((now - st["quiet_since"]) // 60)
            text = WAKE_TEXT.format(minutes=minutes, open_steps=_open_steps(plan)) + details
            try:
                wake(cid, text)
            except Exception:
                continue
            st["wakes"] += 1
            # A wake that starts no work waits longer each time: 2, 4, 8, 16, 32, 60 minutes.
            st["next_wake"] = now + min(_IDLE_S * (2 ** (st["wakes"] - 1)), _MAX_WAIT_S)
            st["quiet_since"] = now
            woke.append(cid)
    return woke


def _loop() -> None:
    while not _stop.wait(_TICK_S):
        try:
            tick()
        except Exception:
            pass


def start_team_keeper() -> None:
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, daemon=True, name="team-keeper")
        _thread.start()


def stop_team_keeper() -> None:
    _stop.set()


def reset_for_tests() -> None:
    with _lock:
        _teams.clear()
