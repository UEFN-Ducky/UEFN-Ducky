"""Team side of workflows: the folders the list shows, sync rounds, and bringing
signed-out workflows into the account.

Load rule (the 2026-09-16 outage was a desktop poller): rounds run while the
Workflows view is open (on open, on focus, each minute; the host caps one per
team per minute), plus one background round every 15 minutes, and only for teams
where this PC runs a scheduled or triggered team workflow.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from backend.automations import store

BACKGROUND_EVERY_S = 15 * 60.0
_LAST_BACKGROUND: dict[str, float] = {}


def _announce() -> None:
    try:
        from frontend.ui_web.agent_modes import push_ui_event

        push_ui_event({"type": "graphs_changed"})
    except Exception:
        pass


def owners() -> dict[str, Any]:
    """Folders for the list: Local, then each team, with its sync line."""
    if not store.use_db("automations"):
        return {"ok": True, "owners": [{**store._LOCAL_OWNER, "folders": store.folders_of(store.LOCAL)}],
                "signedIn": False, "teamsEnabled": False, "localImport": 0}
    from backend.automations import owned
    from backend.store.repos import plugin_data as data
    from backend.store.repos import workflows as runtime
    from backend.uefn_plugins.scopes import LOCAL
    from backend.uefn_plugins.team_sync import teams_enabled

    aid = owned.account()
    rows = store.all_workflows()
    out = []
    for scope in owned.owner_scopes(aid):
        view = owned.owner_view(scope)
        # Every folder, empty ones included: a team's list syncs with its workflows.
        view["folders"] = store.folders_of(view["id"], rows)
        if scope["kind"] == "team":
            st = data.sync_get(aid, scope["id"])
            view["sync"] = {
                "state": scope["state"],
                "error": st["error"],
                "syncedAt": float(st["synced_at"]) or None,
                "pending": data.count_dirty(aid, scope["id"]),
                "members": int(st["members"]),
            }
            view["slug"] = runtime.team_slug(aid, scope["id"])
        out.append(view)
    return {"ok": True, "owners": out, "signedIn": aid != LOCAL, "teamsEnabled": teams_enabled(),
            "localImport": owned.local_import_count(aid)}


def refresh_teams() -> dict[str, Any]:
    """Ask the Store which teams this account has (one hub call, only when the view
    opens, never on a timer); it saves labels and Manage automations per team."""
    if store.use_db("automations"):
        from backend.uefn_plugins.team_sync import remember_workflow_teams

        try:
            remember_workflow_teams()
        except Exception:
            pass
    return owners()


def _team_ids() -> list[tuple[str, str]]:
    from backend.automations import owned

    return [(s["account"], s["id"]) for s in owned.owner_scopes() if s["kind"] == "team"]


def _round(targets: list[tuple[str, str]], force: bool) -> None:
    from backend.uefn_plugins.team_sync import sync_team

    changed = False
    for account, team in targets:
        out = sync_team(account, team, force=force)
        changed = changed or bool(out.get("changed")) or not out.get("skipped")
    if changed:
        _announce()


def sync(*, force: bool = False, team_id: str = "", upload: bool = False) -> dict[str, Any]:
    """One round per visible team. A named team (Save, Update online) runs now and,
    with ``upload``, queues that team's workflows first so a saved copy goes up
    even when an earlier round had given up on it."""
    if not store.use_db("automations"):
        return {"ok": True, "started": False}
    targets = _team_ids()
    want = (team_id or "").strip()
    if want:
        targets = [pair for pair in targets if pair[1] == want]
        if not targets:
            return {"ok": False, "started": False, "error": "That team isn't on this PC."}
    if not targets:
        return {"ok": True, "started": False}
    if want or upload:
        return _sync_now(targets, upload=upload or bool(want))
    threading.Thread(target=_round, args=(targets, force), name="workflow-team-sync", daemon=True).start()
    return {"ok": True, "started": True}


def _sync_now(targets: list[tuple[str, str]], *, upload: bool) -> dict[str, Any]:
    from backend.store.repos import plugin_data as data
    from backend.uefn_plugins.team_sync import WORKFLOW_DOCS, sync_team

    errors: list[str] = []
    for account, team_key in targets:
        if upload:
            data.mark_plugin_dirty(account, team_key, WORKFLOW_DOCS)
        out = sync_team(account, team_key, force=True)
        err = str(out.get("error") or "")
        if err:
            errors.append(err)
    _announce()
    message = "; ".join(errors)
    return {"ok": not message, "started": True, "error": message}


def background(workflows: list[dict[str, Any]], now: float | None = None) -> list[str]:
    """Scheduler hook: a round every 15 minutes for teams this PC runs schedules or
    triggers of, so an edit made on another PC arrives without opening the view."""
    now = time.time() if now is None else now
    teams = sorted({
        str((wf.get("owner") or {}).get("id") or "")
        for wf in workflows
        if (wf.get("owner") or {}).get("kind") == "team" and wf.get("enabled") and wf.get("run_here")
        and store.trigger_of((wf.get("graph") or {}).get("nodes") or [])["kind"] in ("schedule", "event")
    } - {""})
    due = [t for t in teams if now - _LAST_BACKGROUND.get(t, 0.0) >= BACKGROUND_EVERY_S]
    if not due:
        return []
    for team in due:
        _LAST_BACKGROUND[team] = now
    targets = [(a, t) for a, t in _team_ids() if t in due]
    if targets:
        threading.Thread(target=_round, args=(targets, False), name="workflow-team-sync-bg", daemon=True).start()
    return due


def open_web(team: str) -> dict[str, Any]:
    """The team's Workflows tab on the website."""
    from urllib.parse import quote

    from backend.automations import owned
    from backend.store.repos import workflows as runtime
    from frontend.duckyos_account import open_site_path

    slug = runtime.team_slug(owned.account(), team)
    if not slug:
        return {"ok": False, "error": "Open the team list once so the app knows this team's page."}
    return open_site_path(f"/profile/teams/{quote(slug)}?tab=workflows")


def import_local() -> dict[str, Any]:
    """Bring the workflows made while signed out on this PC into the account."""
    from backend.automations import owned

    moved = owned.import_local(owned.account())
    if moved:
        _announce()
    return {"ok": True, "moved": moved}
