"""Tell each agent plainly when a move changed its team (from ``sync_membership``'s result).

A moved lead kept reporting to its old lead, a member kept ticking the old plan, and the old
lead kept waiting on a seat that had left. Each moved chat gets one ``[ducky:team]`` note
naming the team it left, the team it is in now and its plan; the old lead and the new lead get
one note each. A move that changed no team or leader sends nothing.
"""

from __future__ import annotations

from typing import Any, Callable

_DONE = {"completed", "cancelled", "done", "skipped"}


def _flatten(nodes: Any) -> list[dict]:
    out: list[dict] = []
    for node in nodes or []:
        if isinstance(node, dict):
            out.append(node)
            out.extend(_flatten(node.get("children")))
    return out


def open_steps(plan: dict | None, conv_id: str) -> list[str]:
    """Ids of the unfinished steps a plan gives this chat (its own sections' steps included)."""
    if not isinstance(plan, dict):
        return []
    found: list[str] = []

    def walk(nodes: Any, owned: bool) -> None:
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            mine = owned or str(node.get("assignee") or "").strip() == conv_id
            kids = [k for k in node.get("children") or [] if isinstance(k, dict)]
            if mine and not kids and str(node.get("status") or "pending") not in _DONE:
                found.append(str(node.get("id") or ""))
            walk(kids, mine)

    walk(plan.get("nodes"), False)
    return [nid for nid in found if nid]


def team_notices(
    moves: dict[str, dict],
    *,
    name_of: Callable[[str], str],
    plan_title_of: Callable[[str], str],
    open_steps_of: Callable[[str, str], list[str]],
) -> list[tuple[str, str]]:
    """``[(receiver chat id, note)]`` for every move that changed a team or a leader."""
    notes: list[tuple[str, str]] = []
    for cid, move in moves.items():
        left, joined = str(move.get("left") or ""), str(move.get("joined") or "")
        old_lead, new_lead = str(move.get("old_leader") or ""), str(move.get("new_leader") or "")
        if not (left or joined or move.get("leader_changed")):
            continue
        parts = []
        if left:
            lead = f" (lead {name_of(old_lead)})" if old_lead and old_lead != cid else ""
            parts.append(f'You left team "{name_of(left)}"{lead}.')
        team = joined or str(move.get("team") or "")
        lead = "you" if new_lead == cid else (name_of(new_lead) if new_lead else "none yet")
        if team and new_lead == cid:
            parts.append(f'You now lead "{name_of(team)}".')
        elif joined:
            parts.append(f'You are now in "{name_of(team)}" (lead {lead}).')
        elif team:
            parts.append(f'Your team "{name_of(team)}" now has lead {lead}.')
        else:
            parts.append("You are not in a team now.")
        title = plan_title_of(cid)
        parts.append(f"Your plan is now: {title or 'none: make your own'}.")
        notes.append((cid, "[ducky:team] " + " ".join(parts)))
        if left and old_lead and old_lead != cid:
            steps = open_steps_of(old_lead, cid)
            notes.append((old_lead, f'[ducky:team] "{name_of(cid)}" left your team "{name_of(left)}"; '
                                    f"its open steps: {', '.join(steps) if steps else 'none'}."))
        if joined and new_lead and new_lead not in (cid, old_lead):
            notes.append((new_lead, f'[ducky:team] "{name_of(cid)}" joined your team "{name_of(joined)}".'))
    return notes


def _name_of(root: str | None) -> Callable[[str], str]:
    from frontend.ui_web.chats.conversations import load_conversation_meta

    def name(cid: str) -> str:
        try:
            conv = load_conversation_meta(cid, root)
        except Exception:  # noqa: BLE001 - a chat that can't be read is named by its id
            conv = None
        return str(getattr(conv, "title", "") or "").strip() or cid

    return name


def _plan_title_of(root: str | None) -> Callable[[str], str]:
    def title(cid: str) -> str:
        try:
            from backend.agent.coding_agents import plans

            plan = plans.team_plan_for(cid, root) or plans.load_plan(cid, root)
        except Exception:  # noqa: BLE001 - no readable plan reads as none
            plan = None
        if not isinstance(plan, dict) or str(plan.get("status") or "open") in ("archived", "finished", "completed"):
            return ""
        return str(plan.get("title") or "").strip()

    return title


def _open_steps_of(root: str | None) -> Callable[[str, str], list[str]]:
    def steps(lead: str, cid: str) -> list[str]:
        try:
            from backend.agent.coding_agents import plans

            return open_steps(plans.load_plan(lead, root), cid)
        except Exception:  # noqa: BLE001
            return []

    return steps


def _deliver(receiver: str, note: str) -> None:
    """Into the agent's inbox, the way team notices go: in the window directly, from the tool
    server over the window's broker. Outside both (tests, scripts) nothing is sent."""
    from frontend.ui_web.agent_turns.push import get_panel_push

    if get_panel_push() is not None:
        from backend.agent import a2a_broker

        a2a_broker.send_notice(sender_conv_id="", receiver_conv_id=receiver, body=note)
        return
    from backend.workspace.identity import resolve_context

    if resolve_context() is not None:
        from backend.agent import a2a_client

        a2a_client.send_notice(sender_conv_id="", receiver_conv_id=receiver, body=note)


def tell_moves(moves: dict[str, dict], root: str | None = None,
               deliver: Callable[[str, str], None] | None = None) -> list[tuple[str, str]]:
    """Send each note for these moves; returns what was sent. A failed note never fails a move.

    Nothing to refresh for the moved chat's next turn: its team (and the team prompt cache,
    ``session_refresh.team_cache_env``) is read from the rosters each turn."""
    notes = team_notices(moves, name_of=_name_of(root), plan_title_of=_plan_title_of(root),
                         open_steps_of=_open_steps_of(root))
    send = deliver or _deliver
    sent = []
    for receiver, note in notes:
        try:
            send(receiver, note)
            sent.append((receiver, note))
        except Exception:  # noqa: BLE001 - the move stands; the agent sees its team next turn
            continue
    return sent
