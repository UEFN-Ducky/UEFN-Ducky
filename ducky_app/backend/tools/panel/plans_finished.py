"""Whether the plan an add would land in is finished.

Its lead may still add to a finished plan (that reopens it); this only tells the two apart.
"""

from __future__ import annotations


def _has_step(plan: dict, step_id: str) -> bool:
    from backend.agent.coding_agents.plans import outline_numbers

    return any(str(n.get("id")) == step_id for _, n in outline_numbers(plan.get("nodes")))


def plan_finished(chat_id: str, project_root: str | None, parent_id: str = "") -> bool:
    """The plan an add would land in is finished: the chat's own, or for a member its team's
    live plan (the same pick ``add_node`` makes). A member whose team plan is finished has no
    live team plan, so ``add_node`` answers it ("plan not found")."""
    from backend.agent.coding_agents.plans import load_plan, team_plan_for

    cid, sid = (chat_id or "").strip(), (parent_id or "").strip()
    if not cid:
        return False
    doc = load_plan(cid, project_root)
    if doc is None or (sid and not _has_step(doc, sid)):
        try:
            team = team_plan_for(cid, project_root)
        except Exception:
            team = None
        if team is not None and (not sid or _has_step(team, sid)):
            doc = team
    if not doc:
        return False  # no plan yet: add_node answers that itself
    return str(doc.get("status") or "").strip().lower() in ("finished", "completed")
