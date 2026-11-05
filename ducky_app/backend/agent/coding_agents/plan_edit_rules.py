"""Plan edit rules: who may change which step, the status gate, rollup and closing a plan.

The lead (the plan's own chat) and the person edit anything at any time; a member manages the
steps of its own sub plan. ``plans`` re-exports every name here. Helpers that still live in
``plans`` are read from it at call time (tests patch ``plans._chat_and_group_ids``).
"""

from __future__ import annotations

import contextvars
import re
from typing import Any

_DONE = frozenset({"completed", "cancelled"})
_STARTED_NODE = frozenset({"in_progress", "completed", "cancelled"})
_NO_CHECK_NOTE = "(no check tool ran this turn)"
# A Verify step starts with the word (after any numbering or markdown). A build step
# that merely mentions "verification state" in its done-when text is not one.
_VERIFY_STEP = re.compile(r"^[\W\d_]*(?:verify|verification)\b", re.IGNORECASE)

# The chat that called a step edit, before it was redirected to its team's plan.
_edit_actor: contextvars.ContextVar[str] = contextvars.ContextVar("plan_edit_actor", default="")


def _plans():
    from backend.agent.coding_agents import plans

    return plans


def plan_is_paused(plan: dict[str, Any] | None) -> bool:
    return isinstance(plan, dict) and str(plan.get("status") or "").strip().lower() == "paused"


def plan_structure_locked(plan: dict[str, Any] | None) -> bool:
    """True when the plan plays (a step started) or is finished: a read-only flag the panel may
    show. It never refuses an edit: the person, Nov 5 2026: "the LEAD should be able to edit
    the plan ALWAYS"."""
    if not isinstance(plan, dict):
        return False
    st = str(plan.get("status") or "").strip().lower()
    if st == "finished":
        return True
    if st == "paused":
        return False
    return any(str(n.get("status") or "") in _STARTED_NODE for n in _plans()._flatten_nodes(plan.get("nodes")))


def _is_done_node(node: dict[str, Any] | None) -> bool:
    return isinstance(node, dict) and str(node.get("status") or "") in _DONE


def _actor_for(chat_id: str) -> str:
    try:
        from backend.agent.verify_evidence import current_conversation

        return (current_conversation() or chat_id or "").strip()
    except Exception:
        return (chat_id or "").strip()


def _refuse_outside_own_section(doc: dict[str, Any], node_id: str, *, by_user: bool = False,
                                notes_only: bool = False, project_root: str | None = None) -> None:
    """The lead (the plan's own chat) and the person edit anything. A member edits every step
    inside its own sub plan, completed ones too; the sub plan itself takes notes and status
    only, and a step in another member's sub plan is refused with its owner."""
    actor = _edit_actor.get()
    if by_user or not actor or doc.get("kind") == "template" or actor == str(doc.get("chat_id") or ""):
        return
    plans = _plans()
    trail = plans._path_to(list(doc.get("nodes") or []), node_id)
    owned = [n for n in trail if n.get("assignee")]
    if not owned:
        return
    mine = set(plans._chat_and_group_ids(actor, project_root)) or {actor}
    ours = [n for n in owned if str(n.get("assignee")) in mine]
    if not ours:
        label = next((lab for lab, n in plans.outline_numbers(doc.get("nodes")) if n is trail[-1]), node_id)
        raise ValueError(f"Step {label} belongs to {owned[-1].get('assignee')}'s sub plan: ask your lead")
    if not notes_only and trail[-1] is ours[0] and trail[-1].get("children"):
        raise ValueError("Your sub plan itself takes notes and status only: edit, move or delete "
                         "the steps inside it, or ask your lead to change the section")


def _reopen_above(roots: list[dict[str, Any]], node_id: str) -> None:
    """A step that is open again reopens the finished sections above it."""
    for up in _plans()._path_to(roots, node_id)[:-1]:
        if str(up.get("status")) == "completed":
            up["status"] = "in_progress"


def _descendants_done(node: dict[str, Any]) -> bool:
    for child in node.get("children") or []:
        if not isinstance(child, dict):
            continue
        if str(child.get("status")) not in _DONE or not _descendants_done(child):
            return False
    return True


def _rollup_completed_parents(nodes: list[dict[str, Any]] | None) -> None:
    """Mark a subplan completed when every child (and descendant) is done.

    Empty subplans stay pending. Already-cancelled parents are left alone.
    """

    def walk(n: dict[str, Any]) -> None:
        kids = [c for c in (n.get("children") or []) if isinstance(c, dict)]
        for child in kids:
            walk(child)
        if not kids or str(n.get("status")) in _DONE:
            return
        if all(str(c.get("status")) in _DONE and _descendants_done(c) for c in kids):
            n["status"] = "completed"

    for node in nodes or []:
        if isinstance(node, dict):
            walk(node)


def is_verify_leaf(node: dict[str, Any] | None) -> bool:
    """True for a leaf whose id or content names a Verify step."""
    if not isinstance(node, dict):
        return False
    kids = node.get("children") or []
    if isinstance(kids, list) and kids:
        return False
    if "verify" in str(node.get("id") or "").lower():
        return True
    return bool(_VERIFY_STEP.match(str(node.get("content") or "")))


def _apply_status_gate(node: dict[str, Any], status: str, chat_id: str = "", *, by_user: bool = False,
                       owners: set[str] | None = None, project_root: str | None = None) -> str:
    """Refuse completed unless descendants are done; a Verify leaf ticked with no check this
    turn is ticked anyway, its notes saying so (it used to be refused).

    *by_user*: the user ticked it in the plan view; their word is the evidence.
    *owners*: the assignees on the step and the sections above it; a chat among them (or in
    one of those groups) ticks with its own checks, though the plan belongs to its lead."""
    st = _plans()._normalize_status(status)
    if st == "completed" and not _descendants_done(node):
        raise ValueError("cannot complete a subplan while nested subplans are unfinished")
    if st == "completed" and not by_user and is_verify_leaf(node) and str(node.get("status")) != "completed":
        from backend.agent.verify_evidence import has_evidence

        if not has_evidence(chat_id) and not _owner_has_evidence(owners or set(), project_root):
            body = str(node.get("body_markdown") or "").rstrip()
            if _NO_CHECK_NOTE not in body:
                node["body_markdown"] = f"{body}\n{_NO_CHECK_NOTE}".strip()
    return st


def _owner_has_evidence(owners: set[str], project_root: str | None) -> bool:
    """The ticking chat owns this step (itself or through its group) and ran a check this turn."""
    from backend.agent.verify_evidence import current_conversation, has_evidence

    actor = current_conversation()
    if not actor or not owners:
        return False
    return bool(owners & set(_plans()._chat_and_group_ids(actor, project_root))) and has_evidence(actor)


def _close_open_steps(nodes: list[dict[str, Any]] | None, reason: str = "") -> None:
    """X every unfinished step, its notes saying why: the lead closed the plan. Sections then
    complete themselves (the rollup), so their finished work still reads as done."""
    why = (reason or "").strip()
    line = f"Cancelled: plan closed — {why}" if why else "Cancelled: plan closed"
    for node in _plans()._flatten_nodes(nodes):
        if str(node.get("status") or "pending") in _DONE or node.get("children"):
            continue
        node["status"] = "cancelled"
        body = str(node.get("body_markdown") or "").rstrip()
        node["body_markdown"] = f"{body}\n\n{line}".strip()
