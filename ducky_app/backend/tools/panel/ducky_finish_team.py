"""ducky_finish_team: when a team's plan is done, the lead gets one "Finish team" card.

Oct 2026 swarms ended with 15 idle seats, "X lead" seats idling beside their "X (Opus)"
twins and 6 leftover terminals. The card (``ui: finish_team``) lists each seat's commits
since the plan started and what is still uncommitted in its lanes (``tick_gate.lane_dirty_files``).
``confirm=true`` archives every member with ``ducky_archive_chats`` (which asks the person
once, naming them) and closes their terminals; the lead stays. ``retire_swapped_seat`` archives
the old chat right away when a seat was swapped for a new one (another model).
"""

from __future__ import annotations

import json
from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json


def _members(plan: dict[str, Any], root: str | None) -> list[str]:
    """Seats owning a sub plan (a group owner counts all its chats), never the lead."""
    from frontend.ui_web.chats.conversations import list_conversations, load_conversation

    lead = str(plan.get("chat_id") or "")
    owners: list[str] = []
    stack = list(plan.get("nodes") or [])
    while stack:
        node = stack.pop(0)
        if not isinstance(node, dict):
            continue
        owner = str(node.get("assignee") or "").strip()
        if owner:
            owners.append(owner)
        stack.extend(node.get("children") or [])
    seats: list[str] = []
    convs = None
    for owner in dict.fromkeys(owners):
        conv = load_conversation(owner, project_root=root)
        if conv is None:
            continue
        if getattr(conv, "is_group", False):
            convs = convs if convs is not None else list_conversations(project_root=root)
            seats += [c.id for c in convs if getattr(c, "parent_conv_id", "") == owner and not getattr(c, "is_group", False)]
        else:
            seats.append(owner)
    return [s for s in dict.fromkeys(seats) if s and s != lead]


def _commits(lane: list[str], since: float) -> list[str]:
    """Short hashes of commits since ``since`` that touch the lane, newest first."""
    from backend.agent.coding_agents import tick_gate as tg

    hashes: list[str] = []
    for top in dict.fromkeys(t for t in map(tg._toplevel, tg._bases(lane)) if t is not None):
        args = ["log", "--no-merges", "--name-only", "-z", "--format=%x01%h"]
        if since:
            args.append(f"--since=@{int(since)}")
        out = tg._git(top, *args)
        if not out:
            continue
        prefix = top.as_posix().rstrip("/")
        for chunk in out.decode("utf-8", "replace").split("\x01")[1:]:
            head, *names = [t for t in chunk.replace("\0", "\n").split("\n") if t.strip()]
            if any(tg._in_lane(lane, f"{prefix}/{n.strip()}") for n in names):
                hashes.append(head.strip())
    return list(dict.fromkeys(hashes))


def _seat_name(conv_id: str, root: str | None) -> str:
    from frontend.ui_web.chats.conversations import load_conversation

    conv = load_conversation(conv_id, project_root=root)
    if conv is None:
        return conv_id
    return str(getattr(conv, "ducky_name", "") or getattr(conv, "title", "") or conv_id)


def finish_card(plan: dict[str, Any], root: str | None = None) -> dict[str, Any]:
    """The ``ui: finish_team`` payload for a finished (or finishing) team plan."""
    from backend.agent.coding_agents.tick_gate import lane_dirty_files
    from backend.workspace import lanes as lane_rules

    since = float(plan.get("created_at") or 0)
    seats = []
    for cid in _members(plan, root):
        lane = list(lane_rules.resolve_lane(cid) or [])
        seats.append({
            "conv_id": cid,
            "name": _seat_name(cid, root),
            "lanes": lane,
            "commits": _commits(lane, since) if lane else [],
            "dirty": lane_dirty_files(lane) if lane else [],
        })
    commits = sum(len(s["commits"]) for s in seats)
    dirty = sum(len(s["dirty"]) for s in seats)
    return {
        "ok": True,
        "ui": "finish_team",
        "status": "needs_confirm",
        "plan_id": str(plan.get("id") or ""),
        "plan_title": str(plan.get("title") or ""),
        "lead": str(plan.get("chat_id") or ""),
        "seats": seats,
        "totals": {"seats": len(seats), "commits": commits, "uncommitted": dirty},
        "text": f"Finish team: {len(seats)} seats · {commits} commits · {dirty} uncommitted · Archive team",
    }


def close_terminals(conv_ids: list[str]) -> list[str]:
    """Close every terminal these chats opened (the terminal manager's close; window or here)."""
    from backend.tools.panel.ducky_panel_terminal import _terminal_manager

    mgr = _terminal_manager()
    close = getattr(mgr, "close_chat_terminals", None)
    if close is not None:
        return list(close(conv_ids))
    ids = set(conv_ids)
    closed = []
    for s in mgr.list_sessions():  # the window's proxy: no bulk close, kill each one of theirs
        sid = s.get("session_id")
        if sid and (s.get("chat") or s.get("conv_id") or s.get("chat_id")) in ids and mgr.kill(sid).get("ok"):
            closed.append(sid)
    return closed


def archive_team(plan: dict[str, Any], root: str | None = None) -> dict[str, Any]:
    """Archive every member (one Yes/No from ``ducky_archive_chats``), keep the lead, close terminals."""
    from backend.tools.panel.ducky_panel import ducky_archive_chats

    members = _members(plan, root)
    if not members:
        return {"ok": True, "ui": "finish_team", "status": "done", "archived": [], "terminals_closed": []}
    out = json.loads(ducky_archive_chats(conv_ids=members))
    if not out.get("ok"):
        # A no keeps the card as it was (seats, commits, the button), not an empty "0 seats" one.
        return {**finish_card(plan, root), "ok": False, "error": out.get("error") or "Nothing was archived."}
    return {"ok": True, "ui": "finish_team", "status": "done", "lead": str(plan.get("chat_id") or ""),
            "archived": out.get("archived") or members, "terminals_closed": close_terminals(members)}


def retire_swapped_seat(old_conv_id: str, new_conv_id: str) -> bool:
    """A seat swapped for a new chat (another model): archive the old one now, close its
    terminals. A recycle that kept the same chat retires nothing. True when archived."""
    old, new = str(old_conv_id or "").strip(), str(new_conv_id or "").strip()
    if not old or old == new:
        return False
    from backend.tools.panel.ducky_panel import _panel_api
    from frontend.archive_folder import ARCHIVE_FOLDER_ID

    # ponytail: no Yes/No here: the person already chose the swap; a team finish still asks.
    _panel_api().move_conversation(old, ARCHIVE_FOLDER_ID)
    close_terminals([old])
    return True


@mcp.tool()
def ducky_finish_team(chat_id: str = "", confirm: bool = False, pretty: bool = False) -> str:
    """Finish a team whose plan is done: without confirm, the "Finish team" card (each seat's
    commits and uncommitted files in its lanes); confirm=true archives every member (the
    person says yes once), keeps the lead and closes the members' terminals. ``chat_id`` is
    the lead's chat (default: the calling chat)."""
    from backend.agent.coding_agents.plans import load_plan
    from backend.tools.panel.ducky_panel import _project_root
    from backend.tools.panel.panel_ui import _resolve_ask_user_conv_id

    lead = str(chat_id or _resolve_ask_user_conv_id() or "").strip()
    root = _project_root()
    plan = load_plan(lead, project_root=root) if lead else None
    if not plan:
        return tool_json({"ok": False, "error": f"No plan for chat {lead!r}."}, pretty=pretty)
    return tool_json(archive_team(plan, root) if confirm else finish_card(plan, root), pretty=pretty)
