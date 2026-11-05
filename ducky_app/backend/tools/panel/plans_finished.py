"""A finished plan takes no new steps: new work goes in a new plan.

Nov 5 2026: steps for unrelated work were added to a finished release plan, which reopened it,
and its keeper started waking the lead again.
"""

from __future__ import annotations

FINISHED_PLAN_ERROR = "This plan is finished: create a new plan for new work"


def plan_finished(chat_id: str, project_root: str | None, parent_id: str = "") -> bool:
    """The plan an add would land in is finished: the chat's own, or for a member its team's
    live plan (``plans._edit_target``, the same pick ``add_node`` makes). A member whose team
    plan is finished has no live team plan, so ``add_node`` answers it ("plan not found")."""
    from backend.agent.coding_agents.plans import _edit_target, _load_editable

    try:
        target = _edit_target(chat_id, parent_id, project_root)
        doc, _ = _load_editable(target, project_root=project_root, template_id=None)
    except ValueError:
        return False  # no plan yet: add_node answers that itself
    return str((doc or {}).get("status") or "").strip().lower() in ("finished", "completed")
