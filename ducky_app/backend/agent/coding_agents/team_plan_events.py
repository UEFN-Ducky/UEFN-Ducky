"""Plan-owned completion reports and unfinished-turn notices for team coordinators."""
from __future__ import annotations

import logging


def assigned_nodes(nodes, owner=""):
    for node in nodes or []:
        assigned = str(node.get("assignee") or owner)
        yield node, assigned
        yield from assigned_nodes(node.get("children"), assigned)


def completion_reports(before: dict, after: dict) -> list[dict[str, str]]:
    if not before.get("nodes"):
        return []
    from backend.workspace.identity import resolve_context
    context = resolve_context()
    actor = getattr(context, "conv_id", "") or str(after.get("chat_id") or "")
    who = getattr(context, "ducky_name", "") or actor
    old = {n["id"]: n for n, _ in assigned_nodes(before.get("nodes"))}
    steps, sections = [], []
    for node, owner in assigned_nodes(after.get("nodes")):
        status = node.get("status")
        if owner and status in {"completed", "cancelled"} and old.get(node["id"], {}).get("status") not in {"completed", "cancelled"}:
            kind = "section" if node.get("children") else "step"
            body = f"{who} {status} {kind} {node['content']} ({node['id']}).\n{node.get('body_markdown', '')}"
            (sections if node.get("children") else steps).append({"sender": actor, "body": body})
    # In the order it happened: the step, then the sections it closed, innermost first.
    reports = steps + sections[::-1]
    if (any(owner for _, owner in assigned_nodes(after.get("nodes")))
            and after.get("status") == "finished" and before.get("status") != "finished"):
        reports.append({"sender": actor, "body": f"{who} finished plan {after.get('title', 'Plan')}.\n{after.get('body_markdown', '')}"})
    return reports


def deliver_reports(plan: dict, reports: list[dict]) -> None:
    """One notice per plan change: every notice is a coordinator turn that re-reads its thread."""
    from backend.agent.a2a_client import send_notice
    if not reports:
        return
    closing = ("Every step is finished: check the results and report to the user."
               if plan.get("status") == "finished" else "Dispatch the next open step at once.")
    body = "[Team plan] " + "\n".join(r["body"].rstrip("\n") for r in reports) + "\n" + closing
    try:
        send_notice(sender_conv_id=reports[0]["sender"], receiver_conv_id=plan["chat_id"], body=body)
    except Exception:
        # Stored reports remain available to the keeper if delivery is unavailable.
        logging.getLogger(__name__).exception("Could not deliver team plan report")


def member_stopped(chat_id: str) -> None:
    from backend.agent.coding_agents import plans
    from frontend.ui_web.project_chats import load_conversation
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
