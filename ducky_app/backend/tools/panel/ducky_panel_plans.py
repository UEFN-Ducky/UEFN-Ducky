"""Panel plan tools: read a chat's plan and add, update, delete or move its steps."""

from __future__ import annotations

from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json


def _resolve_plan_chat_id(chat_id: str = "") -> str:
    # Looked up on ducky_panel at call time: it owns the helper and tests patch it there.
    from backend.tools.panel import ducky_panel

    return ducky_panel._resolve_plan_chat_id(chat_id)


def _project_root() -> str:
    from backend.tools.panel import ducky_panel

    return ducky_panel._project_root()


def _other_root_with_plan(cid: str, root: str) -> str:
    """Another known project root where ``cid`` has a plan, or "".

    Oct 25 2026: a lead chat made before the project switched kept its plan under the old
    root, so its members' tool servers (on the new root) read no plan and every tick said
    "plan not found". Only asked when the current root has nothing for the caller.
    """
    import os

    from backend.agent.coding_agents.plans import load_plan_view

    try:
        from frontend.ui_web.known_projects import known_project_roots

        roots = known_project_roots()
    except Exception:
        return ""
    here = os.path.normcase(os.path.abspath(root)) if root else ""
    for other in roots:
        if not other or os.path.normcase(os.path.abspath(other)) == here:
            continue
        try:
            if load_plan_view(cid, project_root=other):
                return other
        except ValueError:
            continue
    return ""


@mcp.tool()
def ducky_get_plan(chat_id: str = "", pretty: bool = False, section: str = "", outline: bool = False) -> str:
    """Load the project Plan for this conversation (or chat_id). Returns outline numbering 1, 1.1, 1.1.1, …

    ``section=<node id>`` returns only that sub plan, its steps and their notes.
    ``outline=true`` returns titles, status and owner per step, no notes and no full plan:
    read a big plan that way, then the part you need with ``section=``.
    """
    from backend.agent.coding_agents.plans import attach_next_tick, load_plan_view, outline_numbers, todo_progress

    cid = _resolve_plan_chat_id(chat_id)
    if not cid:
        return tool_json({"ok": False, "error": "chat_id required (no active conversation)"}, pretty=pretty)
    root = _project_root()
    try:
        plan = load_plan_view(cid, project_root=root)
    except ValueError as exc:
        return tool_json({"ok": False, "plan": None, "error": str(exc)}, pretty=pretty)
    if not plan:
        other = _other_root_with_plan(cid, root)
        plan = load_plan_view(cid, project_root=other) if other else None
    if not plan:
        return tool_json(
            {"ok": True, "plan": None, "progress": todo_progress(None), "outline": []},
            pretty=pretty,
        )
    head = {key: plan.get(key) for key in ("id", "chat_id", "title", "status", "kind", "assigned_from") if plan.get(key)}
    if (section or "").strip():
        found = next((n for n in _walk_nodes(plan.get("nodes")) if n.get("id") == section.strip()), None)
        if found is None:
            return tool_json({"ok": False, "error": f"no step {section.strip()!r} in this plan"}, pretty=pretty)
        label = next((lab for lab, n in outline_numbers(plan.get("nodes")) if n is found), "")
        return tool_json(
            attach_next_tick({"ok": True, "plan": head, "n": label, "section": found,
                              "progress": todo_progress({"nodes": [found]})}, plan),
            pretty=pretty,
        )
    if outline:
        rows = []
        for lab, n in outline_numbers(plan.get("nodes")):
            row = {"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]}
            if n.get("assignee"):
                row["owner"] = n["assignee"]
            rows.append(row)
        return tool_json(
            attach_next_tick({"ok": True, "plan": head, "progress": todo_progress(plan), "outline": rows}, plan),
            pretty=pretty,
        )
    return tool_json(
        attach_next_tick(
            {
                "ok": True,
                "plan": plan,
                "progress": todo_progress(plan),
                "outline": [{"n": lab, "id": n["id"], "content": n["content"], "status": n["status"]} for lab, n in outline_numbers(plan.get("nodes"))],
            },
            plan,
        ),
        pretty=pretty,
    )


@mcp.tool()
def ducky_plan_add_node(
    content: str,
    parent_id: str = "",
    index: int = -1,
    chat_id: str = "",
    template_id: str = "",
    kind: str = "",
    body_markdown: str = "",
    pretty: bool = False,
    assignee: str = "",
    after: list[str] | None = None,
) -> str:
    """Add a step or subplan node. kind=step|subplan. parent_id empty = root. template_id edits a template.
    ``assignee`` = the chat or group that owns it; ``after`` = step ids it waits for.
    Team plans: one main plan with one sub plan per agent or group (a section assigned to
    its chat or group id, its points inside), never several loose steps to one agent. Members see that section in their own plan panel.
    Finish every assignment: completed, or cancelled with the reason; set notes
    (body_markdown) and status in one call, preserving original notes. A section
    completes itself when its last step does, so put your result in that step's
    notes; never tick or annotate the finished section. The lead (the plan's own
    chat) and the person edit, move, delete, X and reopen any step at any time, completed
    ones and finished plans included: no pause. A member does the same inside its own
    sub plan (the section itself: notes and status); another member's steps are refused.
    Assigning a sub plan starts its member: Ducky sends it the task, starts it again
    when a turn ends with points left, and tells the lead when the sub plan is finished
    (with its notes) or the member ends with "BLOCKED: ...". Nobody dispatches by message.
    A team works in ONE plan, its lead's: members never create their own. Members tick
    and split their points without chat_id (the edit lands in the team's plan); the last
    point checks the sub plan off and tells the lead, so members send no reports. ``after``
    = ids of steps that must finish first: such a step can't start, and its owner is
    not woken until they finish. A member blocked by a teammate's work: the lead adds
    the fix as a step for that teammate and sets the blocked step's ``after`` to it.
    """
    from backend.agent.coding_agents.plans import add_node, push_plan_updated

    cid = _resolve_plan_chat_id(chat_id) if not (template_id or "").strip() else ""
    try:
        plan = add_node(
            cid,
            content=content,
            parent_id=parent_id,
            index=None if index < 0 else index,
            kind=kind,
            body_markdown=body_markdown,
            project_root=_project_root() if not (template_id or "").strip() else None,
            template_id=(template_id or "").strip() or None,
            assignee=assignee,
            after=after,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if plan.get("kind") != "template":
        push_plan_updated(plan)
    from backend.agent.coding_agents.plans import attach_next_tick, with_plan_review

    added = next((n for n in reversed(list(_walk_nodes(plan.get("nodes")))) if n.get("content") == content), None)
    result = with_plan_review(_plan_edit_result(plan, added), plan)
    return tool_json(attach_next_tick(result, plan if plan.get("kind") != "template" else None), pretty=pretty)


@mcp.tool()
def ducky_plan_update_node(
    node_id: str,
    content: str = "",
    status: str = "",
    chat_id: str = "",
    template_id: str = "",
    kind: str = "",
    body_markdown: str = "",
    pretty: bool = False,
    assignee: str | None = None,
    after: list[str] | None = None,
    planned_assignee: str | None = None,
) -> str:
    """Update a node's content, status, kind, body_markdown, assignee and/or after.

    ``after`` = ids of the steps this one waits for (``[]`` clears); allowed while playing.

    ``planned_assignee`` = the ducky profile or chat id picked to own this sub plan later
    (``""`` clears it). It never starts anyone: Launch seats the planned owners.

    Any field, any time; ``status`` may also reopen a step (completed or cancelled back to
    pending). A Verify step ticked with no check this turn is ticked, its notes saying so.
    ``assignee`` is the chat or
    group id that owns this part (``""`` clears it): that group's chats then show this
    part as their plan instead of "No plan".

    Team plans: one main plan with one sub plan per agent or group (a section assigned to
    its chat or group id, its points inside), never several loose steps to one agent. Members see that section in their own plan panel.
    Finish every assignment: completed, or cancelled with the reason; set notes
    (body_markdown) and status in one call, preserving original notes. A section
    completes itself when its last step does, so put your result in that step's
    notes; never tick or annotate the finished section. The lead (the plan's own
    chat) and the person edit, move, delete, X and reopen any step at any time, completed
    ones and finished plans included: no pause. A member does the same inside its own
    sub plan (the section itself: notes and status); another member's steps are refused.
    Assigning a sub plan starts its member: Ducky sends it the task, starts it again
    when a turn ends with points left, and tells the lead when the sub plan is finished
    (with its notes) or the member ends with "BLOCKED: ...". Nobody dispatches by message.
    A team works in ONE plan, its lead's: members never create their own. Members tick
    and split their points without chat_id (the edit lands in the team's plan); the last
    point checks the sub plan off and tells the lead, so members send no reports. ``after``
    = ids of steps that must finish first: such a step can't start, and its owner is
    not woken until they finish. A member blocked by a teammate's work: the lead adds
    the fix as a step for that teammate and sets the blocked step's ``after`` to it.
    """
    from backend.agent.coding_agents.plans import attach_next_tick, push_plan_updated, update_node

    cid = _resolve_plan_chat_id(chat_id) if not (template_id or "").strip() else ""
    root = _project_root() if not (template_id or "").strip() else None

    def edit(project_root: str | None) -> dict[str, Any]:
        return update_node(
            cid,
            node_id,
            content=content if content else None,
            status=status if status else None,
            kind=kind if kind else None,
            body_markdown=body_markdown if body_markdown else None,
            project_root=project_root,
            template_id=(template_id or "").strip() or None,
            assignee=assignee,
            after=after,
            planned_assignee=planned_assignee,
        )

    try:
        try:
            plan = edit(root)
        except ValueError as exc:
            # The team's plan may live under another project root (see _other_root_with_plan).
            other = _other_root_with_plan(cid, root) if root is not None and "not found" in str(exc) else ""
            if not other:
                raise
            plan = edit(other)
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if plan.get("kind") != "template":
        push_plan_updated(plan)
    from backend.agent.coding_agents.plans import with_plan_review

    # The changed step and the progress: not the whole plan with every step's text (8.5 KB per
    # tick; one team ticked 67 times in 50 minutes). ducky_get_plan reads it all.
    changed = next((n for n in _walk_nodes(plan.get("nodes")) if n.get("id") == node_id), None)
    result = _plan_edit_result(plan, changed)
    if content or kind or after is not None or assignee is not None:
        result = with_plan_review(result, plan)
    closed = _sections_closed_by(plan.get("nodes"), node_id) if status in ("completed", "cancelled") else []
    if closed:
        # Members then tried to tick or annotate their finished section and were refused.
        names = ", ".join(f"{n.get('content')} ({n.get('id')})" for n in closed)
        result["sections_completed"] = [n.get("id") for n in closed]
        result["note"] = (f"{names} completed itself with this step. The note on this step is its "
                          "record: do not tick or annotate the section.")
    return tool_json(attach_next_tick(result, plan if plan.get("kind") != "template" else None), pretty=pretty)


_NOTES_IN_REPLY = 240


def _plan_edit_result(plan: dict[str, Any], node: dict[str, Any] | None) -> dict[str, Any]:
    """The changed step and the progress: never the whole plan or its outline (25k characters
    on a 260-step plan, every tick). ducky_get_plan(outline=true / section=) reads the rest."""
    from backend.agent.coding_agents.plans import todo_progress

    slim = {key: value for key, value in (node or {}).items() if key != "children"}
    notes = str(slim.get("body_markdown") or "")
    if len(notes) > _NOTES_IN_REPLY:
        slim["body_markdown"] = notes[:_NOTES_IN_REPLY] + "… (ducky_get_plan(section=…) has the rest)"
    return {
        "ok": True,
        "plan": {key: plan.get(key) for key in ("id", "chat_id", "title", "status", "kind")},
        "node": slim,
        "progress": todo_progress(plan) if plan.get("kind") != "template" else None,
    }


def _walk_nodes(nodes: Any):
    for node in nodes or []:
        if isinstance(node, dict):
            yield node
            yield from _walk_nodes(node.get("children"))


def _sections_closed_by(nodes: Any, node_id: str) -> list[dict[str, Any]]:
    """The finished sections above ``node_id``, innermost first."""
    def path(items: Any, trail: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        for node in items or []:
            if not isinstance(node, dict):
                continue
            if node.get("id") == node_id:
                return trail
            found = path(node.get("children"), [*trail, node])
            if found is not None:
                return found
        return None

    above = path(nodes, []) or []
    return [n for n in reversed(above) if n.get("status") == "completed"]


@mcp.tool()
def ducky_plan_delete_node(
    node_id: str,
    chat_id: str = "",
    template_id: str = "",
    pretty: bool = False,
) -> str:
    """Delete a step or subplan and its nested children, completed ones included (a member: only
    inside its own sub plan)."""
    from backend.agent.coding_agents.plans import delete_node, push_plan_updated

    cid = _resolve_plan_chat_id(chat_id) if not (template_id or "").strip() else ""
    try:
        plan = delete_node(
            cid,
            node_id,
            project_root=_project_root() if not (template_id or "").strip() else None,
            template_id=(template_id or "").strip() or None,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if plan.get("kind") != "template":
        push_plan_updated(plan)
    from backend.agent.coding_agents.plans import attach_next_tick

    result = _plan_edit_result(plan, {"id": node_id, "deleted": True})
    return tool_json(attach_next_tick(result, plan if plan.get("kind") != "template" else None), pretty=pretty)


@mcp.tool()
def ducky_plan_move_node(
    node_id: str,
    parent_id: str = "",
    index: int = 0,
    chat_id: str = "",
    template_id: str = "",
    pretty: bool = False,
) -> str:
    """Move a step or subplan under parent_id (empty = root) at index, any time. Renumbers the
    outline. A member moves steps within its own sub plan."""
    from backend.agent.coding_agents.plans import move_node, push_plan_updated

    cid = _resolve_plan_chat_id(chat_id) if not (template_id or "").strip() else ""
    try:
        plan = move_node(
            cid,
            node_id,
            parent_id=parent_id,
            index=index,
            project_root=_project_root() if not (template_id or "").strip() else None,
            template_id=(template_id or "").strip() or None,
        )
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    if plan.get("kind") != "template":
        push_plan_updated(plan)
    from backend.agent.coding_agents.plans import attach_next_tick

    moved = next((n for n in _walk_nodes(plan.get("nodes")) if n.get("id") == node_id), None)
    result = _plan_edit_result(plan, moved)
    return tool_json(attach_next_tick(result, plan if plan.get("kind") != "template" else None), pretty=pretty)
