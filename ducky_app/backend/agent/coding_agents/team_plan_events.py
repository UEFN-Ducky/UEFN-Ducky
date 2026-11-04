"""Plan-owned completion reports and unfinished-turn notices for team coordinators."""
from __future__ import annotations

import logging


def assigned_nodes(nodes, owner=""):
    for node in nodes or []:
        assigned = str(node.get("assignee") or owner)
        yield node, assigned
        yield from assigned_nodes(node.get("children"), assigned)


def _leaves(node):
    kids = node.get("children") or []
    if not kids:
        yield node
    for kid in kids:
        yield from _leaves(kid)


def progress_line(section: dict) -> str:
    """How far a sub plan is, counted from the plan itself (never written by the AI)."""
    steps = list(_leaves(section))
    done = [s for s in steps if s.get("status") in {"completed", "cancelled"}]
    if len(done) == len(steps):
        return f"All {len(steps)} steps done"
    following = next(s for s in steps if s not in done)
    return (f"Progress: {len(done)}/{len(steps)} steps ({round(100 * len(done) / len(steps))}%)"
            f" · next: {following['content']}")


def completion_reports(before: dict, after: dict) -> list[dict[str, str]]:
    if not before.get("nodes"):
        return []
    from backend.workspace.identity import resolve_context
    context = resolve_context()
    actor = getattr(context, "conv_id", "") or str(after.get("chat_id") or "")
    who = getattr(context, "ducky_name", "") or actor
    # The lead's own change is no news to the lead (Oct 22 2026: an Opus lead cancelled a step
    # after reporting and was woken to answer "nothing has changed since"). A tick in the plan
    # view (no calling agent) still goes to it.
    own = {"quiet": True} if getattr(context, "conv_id", "") and actor == str(after.get("chat_id") or "") else {}
    old = {n["id"]: n for n, _ in assigned_nodes(before.get("nodes"))}
    done = {"completed", "cancelled"}
    newly = {node["id"] for node, _ in assigned_nodes(after.get("nodes"))
             if node.get("status") in done and old.get(node["id"], {}).get("status") not in done}
    parent = {child["id"]: node for node, _ in assigned_nodes(after.get("nodes")) for child in node.get("children") or []}

    def inside_open_sub_plan(node_id: str) -> bool:
        # A sub plan nested in another one that is still open (a member's part of its group's
        # sub plan): Ducky starts whatever waited on it, so the lead has nothing to do yet.
        up = parent.get(node_id)
        while up is not None:
            if up.get("assignee") and up.get("status") not in done:
                return True
            up = parent.get(up["id"])
        return False

    steps, sections = [], []
    for node, owner in assigned_nodes(after.get("nodes")):
        status = node.get("status")
        if owner and node["id"] in newly:
            up = parent.get(node["id"])
            if (status == "completed" and not node.get("children") and not node.get("assignee")
                    and up is not None and up["id"] not in newly):
                # A point of an agent's sub plan: the lead hears once, when the whole sub plan is done.
                continue
            kind = "section" if node.get("children") else "step"
            # The member's sub plan: this section itself, or the nearest assigned section above.
            sub = node if node.get("children") else parent.get(node["id"])
            while sub is not None and not sub.get("assignee"):
                sub = parent.get(sub["id"])
            progress = f"{progress_line(sub)}\n" if sub is not None else ""
            body = f"{who} {status} {kind} {node['content']} ({node['id']}).\n{progress}{node.get('body_markdown', '')}"
            report = {"sender": actor, "body": body, **own}
            if status == "completed" and inside_open_sub_plan(node["id"]):
                # Kept on the plan (the keeper's wake lists it), not a lead turn: on Oct 15 2026 a
                # nested team woke its lead seven times, four of them for inner sub plans.
                report["quiet"] = True
            (sections if node.get("children") else steps).append(report)
    # In the order it happened: the step, then the sections it closed, innermost first.
    reports = steps + sections[::-1]
    if (any(owner for _, owner in assigned_nodes(after.get("nodes")))
            and after.get("status") == "finished" and before.get("status") != "finished"):
        reports.append({"sender": actor, "body": f"{who} finished plan {after.get('title', 'Plan')}.\n{after.get('body_markdown', '')}",
                        **own})
    return reports


def deliver_reports(plan: dict, reports: list[dict]) -> None:
    """One notice per plan change: every notice is a coordinator turn that re-reads its thread.
    Quiet reports (an inner sub plan done while its outer one is open) are not sent."""
    from backend.agent.a2a_client import send_notice
    reports = [r for r in reports if not r.get("quiet")]
    if not reports:
        return
    closing = ("Every step is finished: check the results and report to the user."
               if plan.get("status") == "finished" else
               "Ducky starts the members whose work can go on: change the plan only if this result needs it.")
    body = "[Team plan] " + "\n".join(r["body"].rstrip("\n") for r in reports) + "\n" + closing
    try:
        send_notice(sender_conv_id=reports[0]["sender"], receiver_conv_id=plan["chat_id"], body=body)
    except Exception:
        # Stored reports remain available to the keeper if delivery is unavailable.
        logging.getLogger(__name__).exception("Could not deliver team plan report")


def member_stopped(chat_id: str, reason: str = "error", detail: str = "") -> None:
    """Tell the lead a member's turn failed mid-point. A turn that simply ended is not news:
    the team keeper starts the member again, so the lead is not woken for it.

    Every ended turn also frees its place in K3's seat queue (``detail`` = the turn's error text,
    empty on success): a start that failed ("Ducky's tools didn't connect") is retried once there."""
    try:
        from frontend.ui_web import seat_queue

        seat_queue.on_turn_end(chat_id, reason, detail if reason == "error" else "")
    except Exception:
        logging.getLogger(__name__).exception("Could not tell the seat queue that %s stopped", chat_id)
    if reason != "error":
        return
    from backend.agent.coding_agents import plans
    from frontend.ui_web.chats.conversations import load_conversation
    ids = set(plans._chat_and_group_ids(chat_id, None))
    member = load_conversation(chat_id)
    who = getattr(member, "ducky_name", "") or getattr(member, "title", "") or chat_id
    for plan in plans.list_plans():
        if plan.get("chat_id") == chat_id or plan.get("status") in {"paused", "archived", "finished"}:
            continue
        for node, owner in assigned_nodes(plan.get("nodes")):
            if owner in ids and node.get("status") == "in_progress" and not node.get("children"):
                deliver_reports(plan, [{"sender": chat_id, "body":
                    f"{who} stopped without finishing {node['content']} ({node['id']}).\n{node.get('body_markdown', '')}"}])
