"""Plan tick text: the exact ``ducky_plan_update_node`` call an agent should make next."""

from __future__ import annotations

from typing import Any

PAUSED_TICK_NOW = "Plan paused — do not start or tick steps until the owner resumes it."


def _paused(plan: dict[str, Any] | None) -> bool:
    from backend.agent.coding_agents.plans import plan_is_paused

    return plan_is_paused(plan)


def next_tick_dict(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Exact ducky_plan_update_node call the agent should make next."""
    from backend.agent.coding_agents.plans import next_open_leaf

    leaf = next_open_leaf(plan)
    if not leaf:
        return None
    lab, node = leaf
    nid = str(node.get("id") or "")
    status = str(node.get("status") or "pending")
    content = str(node.get("content") or "").strip().replace("\n", " ")[:160]
    if status == "in_progress":
        action = "completed"
        call = f'ducky_plan_update_node(node_id="{nid}", status="completed")'
    else:
        action = "in_progress"
        call = f'ducky_plan_update_node(node_id="{nid}", status="in_progress")'
    return {
        "n": lab,
        "id": nid,
        "status": status,
        "content": content,
        "action": action,
        "call": call,
    }


def format_plan_tick_nudge(plan: dict[str, Any] | None) -> str:
    # ponytail: next_tick_dict stays unguarded so the mutator gate still blocks work on a paused plan.
    if _paused(plan):
        return ""
    tick = next_tick_dict(plan)
    if not tick:
        return ""
    return (
        f"[PLAN TICK] CHECK OFF NOW: {tick['n']} `{tick['id']}` [{tick['status']}] "
        f"— {tick['content']}. Call {tick['call']} this turn. "
        "Re-check the plan every step; never leave done work pending."
    )


def _callers_part(plan: dict[str, Any], caller: str) -> dict[str, Any] | None:
    """The part of ``plan`` the caller works: all of it for the plan's own chat (or an unknown
    caller), else the sections assigned to it or its groups; None when it owns nothing.

    Nov 5 2026: every member's tick reply said "CHECK OFF NOW: 3.4", another seat's step."""
    from backend.agent.coding_agents import plans

    try:
        from backend.agent.verify_evidence import current_conversation

        who = (current_conversation() or caller or "").strip()
    except Exception:
        who = (caller or "").strip()
    if not who or who == str(plan.get("chat_id") or ""):
        return plan
    try:
        ids = set(plans._chat_and_group_ids(who, None)) or {who}
    except Exception:
        ids = {who}
    mine: list[dict[str, Any]] = []
    stack = list(plan.get("nodes") or [])
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        if str(node.get("assignee") or "") in ids:
            mine.append(node)
        else:
            stack[:0] = list(node.get("children") or [])
    return {**plan, "nodes": mine} if mine else None


def attach_next_tick(payload: dict[str, Any], plan: dict[str, Any] | None, caller: str = "") -> dict[str, Any]:
    """Add next_tick + tick_now onto a plan-tool JSON payload: the caller's own next step."""
    from backend.agent.coding_agents.plans import outline_numbers

    out = dict(payload)
    if plan and plan.get("assigned_from"):
        return out  # Read-only views must not suggest implicit own-chat writes.
    if _paused(plan):
        out["next_tick"] = None
        out["tick_now"] = PAUSED_TICK_NOW
        return out
    part = _callers_part(plan, caller) if plan else plan
    tick = next_tick_dict(part) if part is not None else None
    if tick:
        if part is not plan:  # number it as the whole plan does
            tick["n"] = next((lab for lab, n in outline_numbers(plan.get("nodes")) if str(n.get("id")) == tick["id"]),
                             tick["n"])
        out["next_tick"] = tick
        out["tick_now"] = (
            f"[PLAN TICK] CHECK OFF NOW: {tick['n']} `{tick['id']}` [{tick['status']}] "
            f"— {tick['content']}. Call {tick['call']} this turn. "
            "Re-check the plan every step; never leave done work pending."
        )
    return out
