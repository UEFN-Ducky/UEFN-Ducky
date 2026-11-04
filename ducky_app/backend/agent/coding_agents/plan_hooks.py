"""What runs when plan steps complete: the tick check, completion reports, and the pushes
that tell each owner's chat to re-read its part of the plan (which starts a member's turn).

``plans.py`` calls these; it never decides who hears about a completion itself.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def before_complete(plan: dict[str, Any], node: dict[str, Any], chat_id: str) -> None:
    """Runs before ``node`` is ticked completed; raise ValueError to refuse (the step keeps its status).

    P2's tick gate: a member can't complete a step while its lane has uncommitted files. ``chat_id``
    is the plan's chat (a member's tick lands in its lead's plan), so the gate checks the member
    actually calling; a tick from the plan view has no caller and checks the plan's chat (no lane).
    """
    if node.get("children"):
        return  # a section completes itself when its last step does
    from backend.agent.coding_agents import tick_gate
    from backend.agent.verify_evidence import current_conversation

    try:
        refusal = tick_gate.check_tick(current_conversation() or chat_id, node)
    except Exception:
        # A broken gate must not lock every member out of ticking.
        log.exception("tick gate failed for %s", node.get("id"))
        return
    if refusal:
        raise ValueError(refusal)


def on_step_completed(plan: dict[str, Any], node: dict[str, Any], project_root: str | None = None) -> None:
    """Runs (off the tick's call path) after a saved plan has ``node`` (a leaf) newly completed."""
    step_id = str(node.get("id") or "")
    hand_over(plan, step_id, project_root)  # "right after the split step"
    start_waiting_seats(plan, step_id, project_root)


def start_waiting_seats(plan: dict[str, Any], step_id: str, project_root: str | None = None) -> None:
    """Seat the swarm groups and seats that start after ``step_id`` (S2's start-later seats; they
    start through K3's seat queue). Their failure never fails the tick: logged, plus a lead point."""
    # A swarm built from the plan records the plan's id; one built from a preview, the lead's chat.
    keys = dict.fromkeys(k for k in (str(plan.get("id") or ""), str(plan.get("chat_id") or "")) if k)
    try:
        from backend.tools.panel import swarm_build

        for key in keys:
            swarm_build.on_step_completed(key, step_id)
    except Exception as exc:
        log.exception("start-later seats after %s failed", step_id)
        _lead_point(plan, f"Start the seats waiting on {step_id} by hand",
                    f"Seating the swarm groups that wait for `{step_id}` failed: {exc}. Invite and assign "
                    "them with ducky_group_invite and ducky_plan_update_node(assignee=...).", project_root)


def on_section_completed(plan: dict[str, Any], node: dict[str, Any], project_root: str | None = None) -> None:
    """Runs (off the tick's call path) after a saved plan has ``node`` (a section) newly completed."""
    hand_over(plan, str(node.get("id") or ""), project_root)


# ---- hand-overs: a finished section (or step) gives its files to the next seat ----------------

def hand_over(plan: dict[str, Any], node_id: str, project_root: str | None = None) -> None:
    """Move the files of every ``Hands over:`` row given by ``node_id`` from the giver's lane to
    the receiver's. A failed move never fails the tick: it is logged and the lead gets a point."""
    try:
        from backend.tools.panel import swarm_build

        rows = swarm_build.handovers_for(node_id)
    except Exception:
        log.exception("hand-over table unreadable for %s", node_id)
        return
    for row in rows:
        paths = [str(p) for p in row.get("paths") or []]
        giver = _owner(plan, str(row.get("from_section") or ""))
        receiver = _owner(plan, str(row.get("to_section") or ""))
        try:
            if not giver or not receiver:
                raise ValueError("the giving or receiving section has no owner")
            _move_lane(giver, receiver, paths)
            _push_note(plan, receiver, f"Files handed to {receiver}: {', '.join(paths)}")
        except Exception as exc:
            log.exception("hand-over %s -> %s failed", giver, receiver)
            _lead_point(plan, f"Move lane by hand: {', '.join(paths)} from {giver or '?'} to {receiver or '?'}",
                        f"The automatic hand-over after `{node_id}` failed: {exc}. Use ducky_group_set_lane "
                        "to take these paths out of the giver's lane and add them to the receiver's.",
                        project_root)


def _owner(plan: dict[str, Any], node_id: str) -> str:
    """The chat that owns ``node_id``: its assignee, else the nearest assigned section above it."""
    from backend.agent.coding_agents.plans import _path_to

    owners = [str(n["assignee"]) for n in _path_to(plan.get("nodes") or [], node_id) if n.get("assignee")]
    return owners[-1] if owners else ""


def _move_lane(giver: str, receiver: str, paths: list[str]) -> None:
    """Giver first, so the receiver's wider lane never overlaps it."""
    gone = set(paths)
    given = _lane(giver)
    if given is not None:
        _set_lane(giver, [p for p in given if p not in gone])
    taken = _lane(receiver)
    if taken is not None:  # None = unrestricted: it can already write them
        _set_lane(receiver, [*taken, *(p for p in paths if p not in taken)])


def _group_of(conv_id: str) -> str:
    from frontend.ui_web.chats.conversations import load_conversation_meta

    group = str(getattr(load_conversation_meta(conv_id), "parent_conv_id", "") or "")
    if not group:
        raise ValueError(f"{conv_id} is in no group")
    return group


def _lane(conv_id: str) -> list[str] | None:
    import json

    from backend.tools.panel.ducky_panel import ducky_group_get_lanes

    row = json.loads(ducky_group_get_lanes(group_id=_group_of(conv_id)))["lanes"].get(conv_id)
    if row is None:
        raise ValueError(f"{conv_id} is not a member of its group")
    lane = row.get("write_allowed")
    return None if lane is None else list(lane)


def _set_lane(conv_id: str, lane: list[str]) -> None:
    from backend.tools.panel.ducky_panel import ducky_group_set_lane

    ducky_group_set_lane(group_id=_group_of(conv_id), member_conv_id=conv_id, write_allowed=lane)


def _push_note(plan: dict[str, Any], receiver: str, text: str) -> None:
    try:
        from frontend.ui_web.agent_turns.push import _resolve_push

        push = _resolve_push(None)
        for cid in dict.fromkeys([str(plan.get("chat_id") or ""), receiver]):
            if cid:
                push({"type": "plan_note", "conv_id": cid, "plan_id": str(plan.get("id") or ""), "text": text})
    except Exception:
        pass  # a note on screen is a courtesy; the lane already moved


def _lead_point(plan: dict[str, Any], content: str, body: str, project_root: str | None) -> None:
    """A step for the lead (unassigned, top level of the lead's plan)."""
    from backend.agent.coding_agents import plans

    try:
        plans.add_node(str(plan.get("chat_id") or ""), content=content[:200], body_markdown=body,
                       project_root=project_root)
    except Exception:
        log.exception("could not add the lead point %r", content)


def completion_reports(previous: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, str]]:
    """The lead's reports for what this save finished (stored on the plan before it is written)."""
    from backend.agent.coding_agents.team_plan_events import completion_reports as reports

    return reports(previous, plan)


def after_save(previous: dict[str, Any], plan: dict[str, Any], reports: list[dict[str, str]],
               project_root: str | None) -> None:
    """Side work of a written plan: acknowledge the lead's own change, send the reports,
    then the per-node completion hooks, steps before the sections they closed."""
    from backend.agent.a2a_client import acknowledge_saved_plan
    from backend.agent.coding_agents.plans import _flatten_nodes
    from backend.agent.coding_agents.team_plan_events import deliver_reports

    acknowledge_saved_plan(previous, plan, project_root)
    deliver_reports(plan, reports)
    was = {str(n.get("id")): n.get("status") for n in _flatten_nodes(previous.get("nodes"))}
    done = [n for n in _flatten_nodes(plan.get("nodes"))
            if n.get("status") == "completed" and was.get(str(n.get("id"))) != "completed"]
    if done:
        # Hand-overs and swarm starts call group tools: off the tick's call path, in save order.
        from backend.agent.coding_agents.plans import _after_save

        _after_save(_run_completion_hooks, plan, done, project_root)


def _run_completion_hooks(plan: dict[str, Any], done: list[dict[str, Any]], project_root: str | None) -> None:
    for node in [n for n in done if not n.get("children")] + [n for n in reversed(done) if n.get("children")]:
        hook = on_section_completed if node.get("children") else on_step_completed
        try:
            hook(plan, node, project_root)
        except Exception:
            log.exception("completion hook failed for %s", node.get("id"))
    if any(_is_sub_plan(n) for n in done) and team_finished(plan):
        post_finish_card(plan, project_root)


def _is_sub_plan(node: dict[str, Any]) -> bool:
    return bool(node.get("children")) and bool(node.get("assignee"))


def team_finished(plan: dict[str, Any]) -> bool:
    """The plan has member sub plans and every one of them is done."""
    from backend.agent.coding_agents.plans import _DONE, _flatten_nodes

    subs = [n for n in _flatten_nodes(plan.get("nodes")) if _is_sub_plan(n)]
    return bool(subs) and all(str(n.get("status")) in _DONE for n in subs)


def post_finish_card(plan: dict[str, Any], project_root: str | None = None) -> None:
    """Put K4's "Finish team" card in the lead's chat, as a ducky_finish_team result."""
    import json
    import time

    try:
        from backend.tools.panel.ducky_finish_team import finish_card
    except ImportError:
        return  # no Finish team tool in this build
    lead = str(plan.get("chat_id") or "")
    try:
        from frontend.ui_web.chats.conversations import load_conversation_meta
        from frontend.ui_web.chats.saves import append_message

        card = finish_card(plan, project_root)
        conv = load_conversation_meta(lead, project_root=project_root)
        if conv is None:
            return
        block = {"type": "tool_call", "id": f"finish-team-{plan.get('id')}", "name": "ducky_finish_team",
                 "arguments": {"chat_id": lead}, "status": "success",
                 "result": {"data": json.dumps(card, ensure_ascii=False)}}
        append_message(conv, {"role": "assistant", "content": "", "blocks": [block], "ts": time.time()},
                       project_root=project_root)
    except Exception:
        log.exception("could not post the Finish team card to %s", lead)


def _section_audience(owners: set[str], project_root: str | None) -> set[str]:
    """Every chat that owns a part of the plan: the owners, chats filed under an owning group
    at any depth, and an owning group's roster and leader (a lead seated with as_leader stays
    filed in the outer group). Reads chat metadata only: walking parents with load_conversation
    loaded every chat's whole history on each tick (4-15 s per tick on Oct 24 2026)."""
    from frontend.ui_web.group_orchestrator import group_members
    from frontend.ui_web.chats.conversations import list_all_conversation_metadata

    convs = {str(getattr(c, "id", "")): c for c in list_all_conversation_metadata(project_root)}
    out = set(owners)
    for cid in convs:
        cur, seen = cid, set()
        while cur and cur not in seen and cur in convs:
            if cur in owners:
                out.add(cid)
                break
            seen.add(cur)
            cur = str(getattr(convs[cur], "parent_conv_id", "") or "").strip()
    for gid in owners.intersection(convs):
        group = convs[gid]
        if getattr(group, "is_group", False):
            out.add(str(getattr(group, "leader_conv_id", "") or ""))
            out.update(m["member_conv_id"] for m in group_members(group))
    out.discard("")
    return out


def _push_assignment_invalidated(before: dict[str, Any], after: dict[str, Any],
                                 project_root: str | None) -> None:
    """Tell every chat that owns a part of this plan to re-read its section of the ONE plan,
    without sending the lead's plan body: ``plan_assignment_changed`` when owners changed, else
    ``plan_updated`` (a tick), so each member's plan bar moves with the lead's."""
    from backend.agent.coding_agents.plans import _flatten_nodes

    def owners(doc: dict[str, Any]) -> dict[str, str]:
        return {str(n["id"]): str(n["assignee"]) for n in _flatten_nodes(doc.get("nodes")) if n.get("assignee")}

    was, now = owners(before), owners(after)
    if not was and not now:
        return
    try:
        from frontend.ui_web.agent_turns.push import _resolve_push

        changed = was != now
        recipients = _section_audience(set(was.values()) | set(now.values()) if changed else set(now.values()),
                                       project_root)
        push = _resolve_push(None)
        for cid in sorted(recipients - {str(after.get("chat_id") or "")}):
            if changed:
                push({"type": "plan_assignment_changed", "conv_id": cid})
            else:
                push({"type": "plan_updated", "conv_id": cid, "plan_id": str(after.get("id") or "")})
    except Exception:
        # Saving the authoritative document must not depend on a UI connection.
        pass
