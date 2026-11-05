"""A finished plan takes no new steps: new work goes in a new plan.

Nov 5 2026: steps for unrelated work were added to a finished release plan, which reopened it,
and its keeper started waking the lead again.
"""

from __future__ import annotations

FINISHED_PLAN_ERROR = "This plan is finished: create a new plan for new work"


def plan_finished(chat_id: str, project_root: str | None) -> bool:
    """The plan an add would land in (the chat's own, or its lead's for a member) is finished."""
    from backend.agent.coding_agents.plans import _load_editable

    try:
        doc, _ = _load_editable(chat_id, project_root=project_root, template_id=None)
    except ValueError:
        return False  # no plan yet: add_node answers that itself
    return str((doc or {}).get("status") or "").strip().lower() in ("finished", "completed")
