"""plan_progress: step counts of many chats' plans in one cheap call. Mixin for PanelApi.

The team bar called the full ``get_plan`` once per owner chat (a 116-step plan with
every body is ~40 KB) through the busy bridge, so its "88/88 steps" showed 5+ s late.
Counts come from node statuses only and stay cached per chat until a plan event for
that chat arrives (``drop_plan_progress``).
"""

from __future__ import annotations

import threading
import time
from typing import Any

# ponytail: the TTL only bounds staleness if an event never arrives.
_TTL_S = 30.0
_PLAN_EVENTS = frozenset({"plan_updated", "plan_assignment_changed"})
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def drop_plan_progress(event: dict[str, Any]) -> None:
    """A pushed plan event makes only its chat's entry stale (no chat id: all of them)."""
    if str(event.get("type") or "") not in _PLAN_EVENTS:
        return
    cid = str(event.get("conv_id") or "")
    with _lock:
        if cid:
            _cache.pop(cid, None)
        else:
            _cache.clear()


def plan_counts(plan: dict[str, Any] | None) -> dict[str, Any]:
    from backend.agent.coding_agents.plans import _flatten_nodes, todo_progress

    progress = todo_progress(plan)
    flat = _flatten_nodes((plan or {}).get("nodes"))
    finished = {str(n.get("id")) for n in flat if str(n.get("status")) in ("completed", "cancelled")}
    # A pending step whose `after` steps aren't finished can't start yet.
    blocked = sum(1 for n in flat if str(n.get("status")) == "pending"
                  and any(str(a) not in finished for a in (n.get("after") or [])))
    return {
        "done": progress["completed"],
        "total": progress["total"],
        "in_progress": progress["in_progress"],
        "blocked": blocked,
        "updated_at": str((plan or {}).get("updated_at") or ""),
        # Epoch seconds: the team bar's time-left estimate starts here.
        "created_at": float((plan or {}).get("created_at") or 0),
    }


class PanelApiPlanProgressMixin:
    def plan_progress(self, chat_ids: list[str], project_root: str | None = None) -> dict[str, Any]:
        """``{chat_id: {done, total, in_progress, blocked, updated_at}}`` for each chat's plan view."""
        from backend.agent.coding_agents.plans import load_plan_view

        ids = list(dict.fromkeys(str(c).strip() for c in chat_ids or [] if str(c).strip()))
        now = time.monotonic()
        out: dict[str, Any] = {}
        with _lock:
            for cid in ids:
                hit = _cache.get(cid)
                if hit is not None and hit[0] > now:
                    out[cid] = hit[1]
        missing = [cid for cid in ids if cid not in out]
        if missing:
            if project_root is None:
                from frontend.settings import PanelSettings

                project_root = PanelSettings.load().uefn_project_root
            for cid in missing:
                try:
                    counts = plan_counts(load_plan_view(cid, project_root=project_root))
                except Exception as exc:  # e.g. open steps in two plans: shown, not cached
                    out[cid] = {**plan_counts(None), "error": str(exc)}
                    continue
                out[cid] = counts
                with _lock:
                    _cache[cid] = (now + _TTL_S, counts)
        return {"ok": True, "progress": {cid: out[cid] for cid in ids}}
