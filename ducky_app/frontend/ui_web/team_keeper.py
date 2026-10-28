"""Team keeper: runs a team the way sub-agents run: each member works its sub plan alone.

A team plan is one main plan with a sub plan per member (a section assigned to it, its points
inside). The keeper starts each member on its sub plan with one self-contained task, starts it
again when its turn ends with points left, and leaves it when it ends with "BLOCKED: ..."
(its lead is told once). Members send no messages and the lead dispatches nothing by hand:
the lead hears when a sub plan is finished (the plan reports it) or a member is blocked, and
is woken only when nothing in its plan can move on its own.

A coordinator agent only runs when a message wakes it. When it ended a turn with no
reply on the way, or the AI account ran out of credits, the whole team stopped until a
person noticed and nudged it. The keeper is that nudge, inside Ducky.

A plan is kept going while it has started (or gives a sub plan an owner, or its lead has
seated members), is not paused or finished, still has open steps, and its chat leads a
group. Pausing the plan stops the keeper for it. Three wakes
in a row that move nothing (no member works, no step changes status or owner) park it
until something does: a plan waiting on the user's own step was woken every hour, and
each wake re-read the coordinator's whole thread.

A team can also go in circles while busy: on Oct 11 2026 six agents worked for an hour,
sent 292 messages, and finished no step, all waiting on one export the plugin's own gate
would never start. A team that stays busy for ``_STALL_S`` without finishing a step gets
one circle notice for its coordinator, and the person is alerted. A long job is not a circle:
while a team chat's background terminal command or a workflow run is still running (a
25-minute release), the clocks wait. A plan whose open steps only the person can do (assigned
to the person, or their members are BLOCKED) wakes its lead once, then waits for a change.
"""

from __future__ import annotations

import re
import threading
import time
import zlib
from typing import Any, Callable

_TICK_S = 60.0
_IDLE_S = 120.0  # nobody running this long before a wake
_MAX_WAIT_S = 3600.0  # wakes that start no work back off up to this
_PARK_AFTER = 3  # wakes in a row that moved nothing: wait for a change instead
_PARKED_TABLE = "workspace_state"
# Steps are sized 15-30 minutes and builders work in parallel: a team busy this long
# with no finished step is going in circles (45 minutes was too long for the owner).
_STALL_S = 20 * 60.0

WAKE_TEXT = (
    "[Ducky keeper] Nothing in your plan can move on its own: {open_steps} open steps and no "
    "agent running for {minutes} minutes. Decide in this turn: give open work an owner (a sub "
    "plan assigned to a member), add what a blocked member needs as a point for whoever can give "
    "it (with `after`), or ask the person with ducky_ask_user if only they can decide. Ducky "
    "starts the members itself: don't message them."
)

STALL_TEXT = (
    "[Ducky keeper] Your team has been working for {minutes} minutes and no plan step has "
    "finished ({done} of {total} done). Busy without finishing is a circle. Read the busy "
    "members' transcripts (ducky_agent_transcript) and fix the cause in the plan: split a point "
    "that is too big, change one that can't be done, or give a stuck member a fresh start "
    "(ducky_recycle_member); ask the person with ducky_ask_user if only they can decide it. "
    "Don't message members for status."
)

_lock = threading.Lock()
_thread: threading.Thread | None = None
_stop = threading.Event()
# coordinator chat id -> {"quiet_since": t, "wakes": n, "next_wake": t, "moves": plan fingerprint}
_teams: dict[str, dict[str, Any]] = {}


def _open_steps(plan: dict[str, Any]) -> int:
    prog = plan.get("progress") or {}
    return int(prog.get("pending") or 0) + int(prog.get("in_progress") or 0)


def _started(plan: dict[str, Any]) -> bool:
    prog = plan.get("progress") or {}
    return int(prog.get("completed") or 0) + int(prog.get("in_progress") or 0) > 0


def _done(plan: dict[str, Any]) -> int:
    return int((plan.get("progress") or {}).get("completed") or 0)


def _total(plan: dict[str, Any]) -> int:
    prog = plan.get("progress") or {}
    return sum(int(prog.get(k) or 0) for k in ("pending", "in_progress", "completed", "cancelled"))


def _tell_person(cid: str, minutes: int) -> None:
    try:
        from frontend.notifications import alert
        from frontend.ui_web.project_chats import load_conversation

        conv = load_conversation(cid)
        name = str(getattr(conv, "title", "") or "").strip() or "Your team"
        alert(name, f"No plan step finished in {minutes} minutes of work.", level="info",
              kind="chat", target_id=cid)
    except Exception:
        pass


def _long_job_chats() -> set[str]:
    """Chats with a background terminal command still running, and "*" while any workflow run
    runs. Oct 24 2026: the keeper called a 25-minute Store release a circle."""
    out: set[str] = set()
    try:
        import functools

        from frontend.ui_web.terminal.manager import _process_snapshot, get_terminal_manager

        mgr = get_terminal_manager()
        with mgr._lock:
            entries = [(e.session, e.chat) for e in mgr._sessions.values() if e.chat]
        snapshot = functools.cache(_process_snapshot)
        out.update(chat for session, chat in entries if session.background_alive(snapshot))
    except Exception:
        pass
    try:
        from backend.automations import live_runs

        # ponytail: runs carry no chat or step id, so any live run counts for every team;
        # tie runs to steps when an unrelated workflow hides a real circle.
        if any(run.get("state") == "started" for run in live_runs.runs()):
            out.add("*")
    except Exception:
        pass
    return out


_PERSON = {"person", "user", "owner", "you", "me", "human"}


def _person_only(plan: dict[str, Any]) -> bool:
    """Every open step that can start now is the person's: assigned to them, or owned by a
    member that ended BLOCKED. Oct 24 2026: owner-only Google Cloud steps woke the lead with
    "nothing can move" again and again."""
    lead = str(plan.get("chat_id") or "")
    blocked = {st.get("owner") for st in _members.values() if st.get("lead") == lead and st.get("blocked")}
    owners = [owner for _, owner, _ in _open_leaves(plan)]
    return bool(owners) and all(owner.lower() in _PERSON or owner in blocked for owner in owners)


def _moves(plan: dict[str, Any]) -> str:
    """What counts as the plan moving: the plan's status and each step's status and owner."""
    parts = [str(plan.get("status") or "")]
    stack = list(plan.get("nodes") or [])
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            parts.append(f"{node.get('id')}:{node.get('status')}:{node.get('assignee') or ''}")
            stack.extend(node.get("children") or [])
    return "|".join(sorted(parts))


def _work_text(work: dict[str, Any]) -> str:
    """A member's sub plan and open points as written (title, description, `after`)."""
    rows = [work.get("section") or {}, *(work.get("points") or [])]
    text = "\0".join(f"{n.get('id')}\0{n.get('content') or ''}\0{n.get('body_markdown') or ''}\0"
                     f"{','.join(str(a) for a in n.get('after') or [])}" for n in rows if isinstance(n, dict))
    return f"{zlib.crc32(text.encode('utf-8', 'replace')):08x}"


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


def _project_roots(project_root: str | None) -> list[str]:
    """The open project and the recent ones: a team keeps working in its own project while the
    person has another open, or none (Oct 12 2026: after a restart with no project open, the
    keeper saw no team at all)."""
    if project_root is not None:
        return [project_root]
    from frontend.settings import PanelSettings
    from frontend.ui_web.recent_projects import load_recent_projects

    roots: list[str] = []
    seen: set[str] = set()
    for root in [PanelSettings.load().uefn_project_root or "", *load_recent_projects()]:
        key = str(root or "").replace("\\", "/").rstrip("/").lower()
        if key and key not in seen:
            seen.add(key)
            roots.append(str(root))
    return roots


_DORMANT_S = 2 * 3600.0  # a team plan unchanged this long is left alone until it changes again


def _dormant(plan: dict[str, Any], now: float) -> bool:
    """An old team's plan nobody touched for hours: the keeper must not bring that team back.
    Oct 12 2026: scanning recent projects woke six chats of three earlier teams at once."""
    updated = float(plan.get("updated_at") or 0.0)
    return updated > 0 and now - updated > _DORMANT_S


def _has_owner(plan: dict[str, Any]) -> bool:
    """A sub plan with an agent: assigning it is what starts that agent."""
    stack = list(plan.get("nodes") or [])
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("assignee"):
                return True
            stack.extend(node.get("children") or [])
    return False


def kept_plans(project_root: str | None = None) -> list[dict[str, Any]]:
    """Team plans the keeper looks after right now, in every project the person works in."""
    from backend.agent.coding_agents.plans import list_plans

    out: list[dict[str, Any]] = []
    now = time.time()
    for root in _project_roots(project_root):
        live = [plan for plan in list_plans(root)
                if str(plan.get("status") or "open") not in ("paused", "finished", "completed", "archived")
                and _open_steps(plan) and not _dormant(plan, now)]
        if not live:
            continue  # the team lookup reads every chat of the project: only when needed
        leaders = _team_leaders(root)
        staffed: set[str] | None = None
        for plan in live:
            cid = str(plan.get("chat_id") or "")
            if cid not in leaders:
                continue
            if not (_started(plan) or _has_owner(plan)):
                # Nothing assigned yet. A lead whose turn ended after writing the plan and
                # seating members, before giving any sub plan an owner, was never woken.
                if staffed is None:
                    staffed = _staffed_leaders(root)
                if cid not in staffed:
                    continue
            plan.setdefault("project_root", root)
            out.append(plan)
    return out


def _staffed_leaders(project_root: str | None) -> set[str]:
    """Leaders of groups that hold at least one other member (a chat or a nested group)."""
    try:
        from frontend.ui_web.group_orchestrator import group_members, is_group_conversation
        from frontend.ui_web.project_chats import list_all_conversation_metadata

        out: set[str] = set()
        for conv in list_all_conversation_metadata(project_root):
            if not is_group_conversation(conv):
                continue
            lid = str(getattr(conv, "leader_conv_id", None) or "").strip()
            if lid and any(str(m.get("member_conv_id") or "") not in ("", lid) for m in group_members(conv)):
                out.add(lid)
        return out
    except Exception:
        return set()


def _wake(chat_id: str, text: str) -> None:
    from frontend.ui_web.agent_modes import run_message
    from frontend.ui_web.project_chats import load_conversation

    conv = load_conversation(chat_id)
    model = str(getattr(conv, "model", "") or "") if conv is not None else ""
    # A follow-up in the coordinator's own chat: it keeps that chat's approvals.
    run_message(chat_id, text, "agent", model, queue_if_busy=True)


def _enqueue(member: str, start: Callable[[], Any]) -> str:
    """A member's turn starts through K3's seat queue (its cap sits on admission's memory check):
    ``started``, ``queued`` (starts when a turn ends) or ``busy`` (already running or waiting).
    Oct 24 2026: keeper wakes started member turns past "4 running · 6 waiting"."""
    from frontend.ui_web.seat_queue import request

    return request(member, start)


def _waiting(member: str) -> bool:
    """The member runs a turn or waits for a seat: its team can still move."""
    try:
        from frontend.ui_web.seat_queue import is_busy

        return is_busy(member)
    except Exception:
        return False


def _wake_member(member: str, text: str, wake: Callable[[str, str], None],
                 plan: dict[str, Any] | None = None, work: dict[str, Any] | None = None,
                 new_section: bool = False) -> bool:
    """Hand a member's wake to the seat queue; False when it is already running or waiting.

    The member starts a fresh session instead, with its whole task (the lead's spec and its
    points), when it asked for one (ducky_fresh_session: its own handoff), when it gets a new
    sub plan (no handoff: the old work isn't needed), or when it is past its context limit
    (it writes a handoff first)."""
    if plan is None or work is None:
        return _enqueue(member, lambda: wake(member, text)) != "busy"
    from frontend.ui_web import context_limit as cl

    root = plan.get("project_root")
    asked = cl.requested_handoff(member)
    over = None if asked is not None or new_section else cl.over_limit(
        member, (work.get("points") or [None])[0], project_root=root)
    if asked is None and not new_section and not over:
        return _enqueue(member, lambda: wake(member, text)) != "busy"
    task = _task_text(plan, work)
    handoff = asked if asked is not None else ("" if new_section else None)
    used, limit = over or (0, 0)

    def fresh() -> bool:
        cl.clear_request(member)
        return cl.start_fresh(member, task, used=used, limit=limit, handoff=handoff, project_root=root)

    return _enqueue(member, fresh) != "busy"


def _team_context(plan: dict[str, Any], *, busy: set[str] = frozenset()) -> tuple[set[str], str]:
    from backend.agent.coding_agents.plans import _chat_and_group_ids
    from backend.agent.coding_agents.team_plan_events import assigned_nodes
    from frontend.ui_web.project_chats import list_all_conversation_metadata

    nodes = list(assigned_nodes(plan.get("nodes")))
    owners = {owner for _, owner in nodes if owner}
    from frontend.ui_web.group_orchestrator import group_members

    convs = list(list_all_conversation_metadata(plan.get("project_root"))) if owners else []
    names = {str(getattr(c, "id", "")): getattr(c, "ducky_name", "") or getattr(c, "title", "") or str(getattr(c, "id", ""))
             for c in convs}
    groups = {str(getattr(c, "id", "")): c for c in convs if getattr(c, "is_group", False)}
    members = {}
    for conv in convs:
        cid = str(getattr(conv, "id", ""))
        if cid not in groups and owners.intersection(_chat_and_group_ids(cid, plan.get("project_root"))):
            members[cid] = names[cid]
    # The owning group's roster too: a lead seated with as_leader stays filed in the outer group.
    for gid in owners.intersection(groups):
        group = groups[gid]
        seated = [str(getattr(group, "leader_conv_id", "") or ""), *(m["member_conv_id"] for m in group_members(group))]
        for cid in seated:
            if cid and cid not in groups:
                members.setdefault(cid, names.get(cid) or cid)
    open_names = [f"{n['content']} ({n['id']})" for n, _ in nodes
                  if n.get("status") not in {"completed", "cancelled"} and not n.get("children")]
    from backend.agent.a2a_broker import unanswered_reports

    reports = [r["body"] for r in plan.get("team_reports", [])]
    reports += [r["body"] for r in unanswered_reports(str(plan.get("chat_id") or ""))]
    detail = "\nOpen steps: " + ("; ".join(open_names) or "see plan")
    # Oct 24 2026: every member was listed as idle, so a stall wake told the lead a busy team sat idle.
    working = "; ".join(f"{name} ({cid})" for cid, name in members.items() if cid in busy) or "none"
    idle = "; ".join(f"{name} ({cid})" for cid, name in members.items() if cid not in busy) or "none"
    detail += f"\nWorking: {working} · Idle: {idle}"
    if reports:
        detail += "\nReports awaiting next dispatch:\n" + "\n".join(reports)
    return set(members) | owners, detail


def tick(
    *,
    now: float | None = None,
    plans: list[dict[str, Any]] | None = None,
    running: list[str] | None = None,
    wake: Callable[[str, str], None] | None = None,
    jobs: set[str] | None = None,
) -> list[str]:
    """One check. Returns the coordinator chats it woke (arguments are for tests)."""
    from backend.agent.a2a_broker import automatic_work_blocked

    now = time.time() if now is None else now
    if plans is None:
        plans = kept_plans()
    if running is None:
        from frontend.ui_web.agent_modes import list_running_agents

        running = list(list_running_agents())
    if jobs is None:
        jobs = _long_job_chats()
    wake = wake or _wake
    busy = set(running)
    woke: list[str] = []
    with _lock:
        live = {str(p.get("chat_id") or "") for p in plans}
        for gone in [cid for cid in _teams if cid not in live]:
            _teams.pop(gone, None)
        member_woke, movable = _dispatch_members(plans, busy, now, wake)
        for plan in plans:
            cid = str(plan.get("chat_id") or "")
            st = _teams.get(cid)
            if st is None:
                st = _teams[cid] = {"quiet_since": now, "wakes": 0.0, "next_wake": 0.0}
                parked = _load_parked(cid)
                if parked is not None:
                    # Parked before a restart: three more wakes per app start added up.
                    st["moves"], st["wakes"] = parked, float(_PARK_AFTER)
            done = _done(plan)
            if "done" not in st or done > st["done"]:
                st["done"], st["progress_at"], st["next_stall"] = done, now, 0.0
            member_ids, details = _team_context(plan, busy=busy)
            team_busy = busy.intersection(member_ids | {cid}) if member_ids else busy
            long_job = "*" in jobs or bool(jobs.intersection(member_ids | {cid}) if member_ids else jobs)
            if long_job:
                st["progress_at"] = now  # time spent on a release or a workflow is not a circle
            if team_busy or long_job:
                st["quiet_since"] = now
                if team_busy - {cid}:
                    _unpark(cid, st)  # the team is working again: drop the backoff
                if not long_job and now - st["progress_at"] >= _STALL_S and now >= st["next_stall"]:
                    minutes = int((now - st["progress_at"]) // 60)
                    try:
                        wake(cid, STALL_TEXT.format(minutes=minutes, done=done, total=_total(plan)) + details)
                    except Exception:
                        continue
                    _tell_person(cid, minutes)
                    st["next_stall"] = now + _STALL_S
                    woke.append(cid)
                continue
            moves = _moves(plan)
            if cid in movable:
                st["quiet_since"] = now  # Ducky is starting or continuing its members: the lead waits
                continue
            if _lead_told.get(cid) == moves:
                # A block went to the lead (or the person) and the plan hasn't changed since.
                # When the lead's turn ended without changing it, only the person can move the
                # team now: tell them once. Oct 15 2026 a team sat idle for good after its lead
                # answered a block, and nobody was told.
                if now - st["quiet_since"] >= _IDLE_S and _person_told.get(cid) != moves:
                    _person_told[cid] = moves
                    try:
                        _alert_waiting(cid)
                    except Exception:
                        pass
                continue
            if st.get("moves") != moves:
                st["moves"] = moves
                _unpark(cid, st)  # a step moved: the next quiet spell wakes it again
            # Only the person can move it: one wake, then quiet until the plan changes.
            limit = 1 if _person_only(plan) else _PARK_AFTER
            if st["wakes"] >= limit:
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
            if st["wakes"] >= limit:
                st["wakes"] = float(_PARK_AFTER)
                _save_parked(cid, moves)
            # A wake that starts no work waits longer each time: 2, 4, 8, 16, 32, 60 minutes.
            st["next_wake"] = now + min(_IDLE_S * (2 ** (st["wakes"] - 1)), _MAX_WAIT_S)
            st["quiet_since"] = now
            woke.append(cid)
        woke.extend(member_woke)
    return woke


# Oct 24 2026: members ticked all their points in one burst at the end, so the plan bars sat
# still for an hour and then jumped.
_ONE_AT_A_TIME = (
    "Work one point at a time: mark it in_progress when you start it, and tick it completed with "
    "its notes the moment its change and tests are done, before starting the next point. Never "
    "save ticks for the end."
)
UNTICKED_TEXT = "You finished work without ticking: tick the points you completed now."
TASK_TEXT = (
    "[Ducky] Your task: the sub plan “{section}” in the plan “{plan}”.\n{spec}{about}"
    "Points:\n{points}\n\n"
    "Work like a sub-agent: alone, writing the code, until every point is ticked. Send no messages "
    "to teammates or your lead (Ducky refuses them). Everything you need is above: don't search "
    "workflows or skills (find_workflows, skill reads) for this task and don't browse folders; open "
    "only the files you change or use. " + _ONE_AT_A_TIME + " A point is done when its change is made and its "
    "tests pass: tick it then with ducky_plan_update_node(node_id=..., status=\"completed\", "
    "body_markdown=\"<what you changed and how you tested it>\"). Never stop for bookkeeping or for "
    "checks your tools can't do (change-ledger read-backs, live panel captures, the person's own "
    "files): write what is still unchecked in the point's notes and tick it. Ticking the last point "
    "tells your lead. A point that waits for a teammate's step (`after`) is not yours yet: finish the "
    "others and end your turn; Ducky starts you again when it can start. Only when the code itself "
    "can't go on (a decision, access, a teammate's change), end your turn with one line: "
    "BLOCKED: <what you need>."
)
CONTINUE_TEXT = (
    "[Ducky] Points left in your sub plan “{section}”:\n{points}\nContinue with the code. "
    + _ONE_AT_A_TIME + " Tick a point when its change is made and its tests pass; note checks your tools can't do and tick it "
    "anyway. Ticking the last one tells your lead. Only if the code can't go on, end your turn with "
    "one line: BLOCKED: <what you need>."
)
BLOCKED_TEXT = (
    "[Team plan] {who} is blocked on “{section}”: {reason}\n"
    "Decide once, then end your turn and wait. If it needs something only the person can see or do "
    "(the live panel on screen, a real file of theirs, a decision), ask the person with ducky_ask_user; "
    "never add a step that asks for the same thing again. Otherwise give what it needs to whoever can "
    "provide it as one point (with `after`), or change its points. Don't message the member: Ducky "
    "starts it again when the plan changes. If it blocks again without finishing a point, Ducky asks "
    "the person, not you."
)
_MEMBER_IDLE_S = 90.0  # a member idle this long with points left gets its next turn
_MEMBER_TRIES = 2  # turns that move nothing in the plan: wait for the plan to move
# ponytail: reads the member's last reply for a commit and its hash, not git; members are told
# to report their commit, so this holds until one commits silently.
_COMMITTED_RE = re.compile(r"(?is)\bcommit\w*\b.*?\b[0-9a-f]{7,40}\b")
_BLOCKED_RE = re.compile(r"(?im)^[\s>*_`-]*BLOCKED\s*[:\-]\s*(.+)$")
_members: dict[str, dict[str, Any]] = {}
_lead_told: dict[str, str] = {}  # lead -> plan fingerprint when a block was last handled
_person_told: dict[str, str] = {}  # lead -> plan fingerprint when the person heard the team waits
_had_work: set[str] = set()  # members that had startable points at the last check


def _work_by_member(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Each owner's open work: its sub plan and the points that can start now.

    The owner comes down from the sub plan to its points. Steps (and sub plans) whose ``after``
    steps are unfinished are left out: a builder told to "continue" with a step that runs last
    checked it and said "waiting" every few minutes."""
    out: dict[str, dict[str, Any]] = {}
    for node, owner, section in _open_leaves(plan):
        if owner:
            out.setdefault(owner, {"section": section, "points": []})["points"].append(node)
    return out


def _open_leaves(plan: dict[str, Any]) -> list[tuple[dict[str, Any], str, Any]]:
    """Open steps that can start now, in plan order: (step, owner from it or above, sub plan)."""
    from backend.agent.coding_agents.plans import waiting_on

    out: list[tuple[dict[str, Any], str, Any]] = []
    stack: list[tuple[Any, bool, str, Any]] = [(node, False, "", None) for node in plan.get("nodes") or []]
    while stack:
        node, held, above, section = stack.pop(0)
        if not isinstance(node, dict):
            continue
        held = held or bool(waiting_on(plan, node))
        own = str(node.get("assignee") or "").strip()
        owner = own or above
        if own:
            section = node
        kids = node.get("children") or []
        stack[:0] = [(kid, held, owner, section) for kid in kids]
        if not kids and not held and node.get("status") in ("pending", "in_progress"):
            out.append((node, owner, section))
    return out


def _open_leaves_by_member(plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {owner: work["points"] for owner, work in _work_by_member(plan).items()}


def _member_chat(owner: str, project_root: str | None) -> str:
    """The chat that works an owner's sub plan: the member, or the lead of an owning group."""
    from frontend.ui_web.project_chats import load_conversation

    try:
        conv = load_conversation(owner, project_root=project_root)
    except Exception:
        conv = None
    if conv is None:
        return ""
    if getattr(conv, "is_group", False):
        return str(getattr(conv, "leader_conv_id", "") or "").strip()
    return owner


def _points(nodes: list[dict[str, Any]], *, bodies: bool) -> str:
    rows = []
    for node in nodes:
        rows.append(f"- `{node.get('id')}` {node.get('content')} ({node.get('status')})")
        body = str(node.get("body_markdown") or "").strip()
        if bodies and body:
            rows.append("  " + body.replace("\n", "\n  "))
    return "\n".join(rows)


def _section_title(work: dict[str, Any]) -> str:
    return str((work.get("section") or {}).get("content") or "your steps")


def _section_text(work: dict[str, Any]) -> str:
    """The sub plan's shape: added, renamed or reordered points change it; a tick doesn't (nor the
    notes a member writes when it ticks, so bodies stay out)."""
    rows, stack = [], [work.get("section") or {}]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            rows.append(f"{node.get('id')}\0{node.get('content') or ''}\0{','.join(map(str, node.get('after') or []))}")
            stack.extend(node.get("children") or [])
    return f"{zlib.crc32(chr(1).join(rows).encode('utf-8', 'replace')):08x}"


def _spec(plan: dict[str, Any]) -> str:
    """The main plan's description: the lead's spec for the whole team, as one block.

    Oct 15 2026: members given only their own points put their files at the project root
    instead of the spec's pocket_ledger/ and renamed a field the spec named. Oct 24 2026: a
    1200-character cut dropped hard rules mid-sentence, so team_brief keeps whole sections."""
    from frontend.ui_web.team_brief import spec_brief

    return spec_brief(plan)


def _task_text(plan: dict[str, Any], work: dict[str, Any]) -> str:
    section = work.get("section") or {}
    about = str(section.get("body_markdown") or "").strip() if section.get("children") else ""
    return TASK_TEXT.format(section=_section_title(work), plan=plan.get("title") or "Plan", spec=_spec(plan),
                            about=(about + "\n") if about else "", points=_points(work["points"], bodies=True))


def _blocked_reason(member: str) -> str:
    """The "BLOCKED: ..." line of the member's last reply, if it ended its turn blocked."""
    from backend.agent.a2a_broker import _last_assistant_text

    found = _BLOCKED_RE.search(_last_assistant_text(member) or "")
    return found.group(1).strip()[:500] if found else ""


def _reply_mark(member: str) -> int:
    """How many replies the member has written: a BLOCKED in a newer reply is a new block."""
    from frontend.ui_web.project_chats import load_conversation

    try:
        conv = load_conversation(member)
    except Exception:
        return 0
    return sum(1 for m in (conv.messages if conv else []) if m.get("role") == "assistant")


def _said_committed(member: str) -> bool:
    """Its last reply reports a commit (a commit word and a hash)."""
    from backend.agent.a2a_broker import _last_assistant_text

    return bool(_COMMITTED_RE.search(_last_assistant_text(member) or ""))


def _tell_lead(plan: dict[str, Any], member: str, work: dict[str, Any], reason: str) -> None:
    from backend.agent.a2a_client import send_notice
    from frontend.ui_web.project_chats import load_conversation

    conv = load_conversation(member)
    who = str(getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or member) if conv else member
    send_notice(sender_conv_id=member, receiver_conv_id=str(plan.get("chat_id") or ""),
                body=BLOCKED_TEXT.format(who=who, section=_section_title(work), reason=reason))


def _done_points(section: dict[str, Any] | None) -> int:
    stack = list((section or {}).get("children") or [])
    done = 0
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            kids = node.get("children") or []
            stack.extend(kids)
            done += int(not kids and node.get("status") in ("completed", "cancelled"))
    return done


def _ask_person(plan: dict[str, Any], member: str, work: dict[str, Any], reason: str) -> None:
    """A member blocked again on the same sub plan: the lead already decided once and nothing
    got done since, so the person hears it (Oct 12 2026: a lead answered each block with one
    more step asking for the same live-panel evidence, and the worker blocked on it again)."""
    from frontend.notifications import alert
    from frontend.ui_web.project_chats import load_conversation

    conv = load_conversation(member)
    who = str(getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or member) if conv else member
    # The status line may reach the phone; the block reason stays on this PC.
    alert(who, "Stuck again: needs your answer.", level="ask", kind="chat", target_id=member,
          detail=f"Stuck again on “{_section_title(work)}”: {reason[:300]} Answer in its chat or change the plan.")


def _inherit_lead(member: str, lead: str) -> None:
    """The member works its lead's plan, so it runs under the lead's approvals ("Allow
    everything" passes down "started by" links only) and its question and command cards also
    show in the lead's chat. Oct 15 2026: Ducky started members with no link, so a team whose
    lead allowed everything stopped on a member's terminal card that nobody saw."""
    try:
        from backend.tools.panel.permission_prompt import note_started_by

        note_started_by(member, lead)
    except Exception:
        pass


def _alert_waiting(lead: str) -> None:
    """Tell the person a team can't move until they act: its lead already had the block."""
    from frontend.notifications import alert
    from frontend.ui_web.project_chats import load_conversation

    reasons = [str(st["blocked"]) for st in _members.values() if st.get("lead") == lead and st.get("blocked")]
    conv = load_conversation(lead)
    who = str(getattr(conv, "title", "") or "").strip() or "Your team" if conv else "Your team"
    # The status line may reach the phone; the block reasons stay on this PC.
    alert(who, "The team is waiting on you.", level="ask", kind="chat", target_id=lead,
          detail=("Blocked: " + " · ".join(r[:200] for r in reasons[:3])) if reasons
          else "Its lead had a blocked member and the plan hasn't changed since. Open its chat.")


def _dispatch_members(plans: list[dict[str, Any]], busy: set[str], now: float,
                      wake: Callable[[str, str], None]) -> tuple[list[str], set[str]]:
    """Start each member on its sub plan, and again when its turn ends with points left.

    Leads used to dispatch, resume and chase members by message (one sent 91 messages in 50
    minutes); members reported back and waited. Now a member gets its whole sub plan as one
    task, works it alone, and is started again (twice at most until the plan moves) when a
    turn ends with points left. A member that ends with "BLOCKED: ..." is left alone and its
    lead is told once. Returns the members woken and the leads whose team can still move.
    Must hold ``_lock``."""
    from backend.agent.a2a_broker import automatic_work_blocked

    woke: list[str] = []
    movable: set[str] = set()
    with_work: set[str] = set()
    for plan in plans:
        lead = str(plan.get("chat_id") or "")
        moves = _moves(plan)
        # One task per member chat. A group's sub plan goes to the group's leader, which may
        # also own a sub plan of its own: two owners, one chat, and the task alternated
        # between them on every tick (each switch also reset the member's tries).
        by_member: dict[str, tuple[str, dict[str, Any]]] = {}
        for owner, work in _work_by_member(plan).items():
            if owner.lower() in _PERSON:
                continue  # the person's own steps: no chat works them
            member = _member_chat(owner, plan.get("project_root"))
            if not member or member == lead:
                continue
            if member in by_member:
                by_member[member][1]["points"].extend(work["points"])
            else:
                by_member[member] = (owner, {"section": work["section"], "points": list(work["points"])})
        for member, (owner, work) in by_member.items():
            st = _members.setdefault(member, {"idle_since": now, "tries": 0, "next": 0.0, "moves": moves,
                                              "task": "", "blocked": "", "cleared": "", "cleared_mark": 0,
                                              "told_done": None})
            st["lead"], st["owner"] = lead, owner
            with_work.add(member)
            if member not in _had_work and st["task"]:
                # Its points only now became startable. A BLOCKED it ended with while it waited
                # on them is answered: on Oct 15 2026 a member that said "BLOCKED: needs the CLI"
                # while its point waited on the CLI was reported blocked after the CLI was done.
                st.update(tries=0, next=0.0, blocked="", cleared=_blocked_reason(member) or st["cleared"],
                          cleared_mark=_reply_mark(member))
            text = _work_text(work)
            if st["moves"] != moves or (st["blocked"] and st.get("blocked_text") != text):
                # The plan moved, or someone rewrote the blocked member's points (it isn't running,
                # so the lead or the person did): whatever blocked it may be solved, so it goes on.
                # Oct 15 2026 a lead answered a block by rewriting the point without a status
                # change; that counted as nothing, and the team sat idle with nobody told.
                st.update(moves=moves, tries=0, next=0.0, cleared=st["blocked"] or st["cleared"], blocked="",
                          cleared_mark=_reply_mark(member))
            if member in busy or _waiting(member):
                st["idle_since"] = None  # idle time counts from the first quiet look
                movable.add(lead)
                continue
            if st["idle_since"] is None:
                st["idle_since"] = now
            if automatic_work_blocked(member):
                continue
            # The sub plan's text is in the key: a member first woken before its steps existed gets
            # the whole task again once they do, not "continue".
            task = (f"{plan.get('id') or plan.get('plan_id') or lead}:"
                    f"{(work.get('section') or {}).get('id') or owner}:{_section_text(work)}")
            if st["task"] != task:
                # The same sub plan, rewritten: an answered block stays answered and a repeat
                # block still goes to the person. A new one starts in a fresh session.
                same = st["task"].rsplit(":", 1)[0] == task.rsplit(":", 1)[0]
                _inherit_lead(member, lead)
                try:
                    if not _wake_member(member, _task_text(plan, work), wake, plan, work,
                                        new_section=bool(st["task"]) and not same):
                        continue
                except Exception:
                    continue
                st.update(task=task, tries=0, idle_since=now, next=now + _MEMBER_IDLE_S, blocked="",
                          cleared=st["cleared"] if same else "", told_done=st["told_done"] if same else None,
                          done_at_wake=_done_points(work.get("section")))
                woke.append(member)
                movable.add(lead)
                continue
            if st["blocked"]:
                continue
            reason = _blocked_reason(member)
            # A cleared block is only the reply it was cleared on. Oct 28 2026: a member woken
            # again after other members' ticks cleared its block hit the same problem and said it
            # in the same words; that read as "already answered", it got two "continue" nudges
            # and went quiet, and its lead never heard while the rest of the team kept working.
            stale = reason == st["cleared"] and _reply_mark(member) <= st.get("cleared_mark", 0)
            if reason and not stale:
                st["blocked"], st["blocked_text"] = reason, text
                done = _done_points(work.get("section"))
                repeat = st.get("told_done") is not None and done <= st["told_done"]
                try:
                    if repeat:
                        _ask_person(plan, member, work, reason)
                        _person_told[lead] = moves  # asked already: no second "waiting on you"
                    else:
                        _tell_lead(plan, member, work, reason)
                except Exception:
                    pass
                st["told_done"] = done
                _lead_told[lead] = moves
                continue
            if st["tries"] >= _MEMBER_TRIES:
                continue
            movable.add(lead)
            if now - st["idle_since"] < _MEMBER_IDLE_S or now < st["next"]:
                continue
            _inherit_lead(member, lead)
            done = _done_points(work.get("section"))
            text = CONTINUE_TEXT.format(section=_section_title(work), points=_points(work["points"], bodies=False))
            if st.get("done_at_wake") is not None and done <= st["done_at_wake"] and _said_committed(member):
                text = text.replace("[Ducky] ", f"[Ducky] {UNTICKED_TEXT}\n", 1)
            try:
                if not _wake_member(member, text, wake, plan, work):
                    continue
            except Exception:
                continue
            st["done_at_wake"] = done
            st["tries"] += 1
            st["idle_since"] = now
            st["next"] = now + _MEMBER_IDLE_S * 2
            woke.append(member)
    _had_work.clear()
    _had_work.update(with_work)
    return woke, movable


def stop_team(group_id: str, cancel: Callable[[str], Any]) -> dict[str, Any]:
    """Stop every running member's turn in a group and the groups nested in it, never the
    group's lead (it is often the one stopping its team). Oct 24 2026: stopping a team also
    stopped its lead. Nothing is deleted; members can be started again."""
    from frontend.ui_web.agent_modes import is_agent_running
    from frontend.ui_web.group_orchestrator import (
        cancel_group_run,
        group_and_nested_ids,
        group_members,
        is_group_conversation,
    )
    from frontend.ui_web.project_chats import load_conversation

    gid = (group_id or "").strip()
    group = load_conversation(gid)
    if group is None or not is_group_conversation(group):
        return {"ok": False, "error": "Not a group chat"}
    lead = str(getattr(group, "leader_conv_id", "") or "").strip()
    ids = group_and_nested_ids(gid)
    stopped: list[str] = []
    for hub_id in ids:
        cancel_group_run(hub_id)
        hub = load_conversation(hub_id)
        if hub is None:
            continue
        for row in group_members(hub):
            cid = str(row.get("member_conv_id") or "").strip()
            if not cid or cid == lead or row.get("is_group") or cid in stopped or not is_agent_running(cid):
                continue
            cancel(cid)
            stopped.append(cid)
    return {"ok": True, "stopped": len(stopped), "groups": len(ids), "stopped_ids": stopped, "lead_kept": lead}


def _unpark(cid: str, st: dict[str, Any]) -> None:
    if st["wakes"] >= _PARK_AFTER:
        _save_parked(cid, None)
    st["wakes"] = 0.0
    st["next_wake"] = 0.0


def _parked_key(cid: str) -> str:
    return f"team_keeper_parked:{cid}"


def _load_parked(cid: str) -> str | None:
    """The plan fingerprint this coordinator was parked at, kept across restarts."""
    try:
        from backend.store.switch import use_db

        if not use_db(_PARKED_TABLE):
            return None
        from backend.store.repos import kv

        doc = kv.get_doc(_PARKED_TABLE, _parked_key(cid))
        return str(doc["moves"]) if isinstance(doc, dict) and "moves" in doc else None
    except Exception:
        return None


def _save_parked(cid: str, moves: str | None) -> None:
    try:
        from backend.store.switch import use_db

        if not use_db(_PARKED_TABLE):
            return
        from backend.store.repos import kv

        if moves is None:
            kv.delete_doc(_PARKED_TABLE, _parked_key(cid))
        else:
            kv.set_doc(_PARKED_TABLE, _parked_key(cid), {"moves": moves, "at": time.time()})
    except Exception:
        pass  # parking is a token saver; never let it stop the keeper


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
        _members.clear()
        _lead_told.clear()
        _person_told.clear()
        _had_work.clear()
