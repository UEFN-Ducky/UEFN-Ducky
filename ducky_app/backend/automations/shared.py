"""Shared side of workflows: the owners the list shows, sync rounds, and bringing
signed-out workflows into the account.

Owners are Local plus every shared space this PC keeps a copy of (its sync rows). The
spaces provider (:mod:`backend.uefn_plugins.spaces`) refreshes which spaces exist and
runs the rounds. Load rule (the 2026-09-16 outage was a desktop poller): the view asks
for rounds when it opens and after a save (the provider caps one per space per minute),
a background round every 15 minutes covers spaces where this PC runs a scheduled or
triggered shared workflow, and :mod:`backend.uefn_plugins.sync_scheduler` keeps the
rest in step (after writes, every minute while the view is on screen, else every 15).
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
        from frontend.ui_web.agent_turns.push import push_ui_event

        push_ui_event({"type": "graphs_changed"})
    except Exception:
        pass


def owners() -> dict[str, Any]:
    """Folders for the list: Local, then each shared space, with its sync line."""
    from backend.uefn_plugins.spaces import provider

    kind_label = provider().kind_label
    if not store.use_db("automations"):
        return {"ok": True, "owners": [{**store._LOCAL_OWNER, "folders": store.folders_of(store.LOCAL, [])}],
                "signedIn": False, "spacesEnabled": False, "kindLabel": kind_label, "localImport": 0}
    from backend.automations import owned
    from backend.store.repos import plugin_data as data
    from backend.store.repos import workflows as runtime
    from backend.uefn_plugins.scopes import LOCAL

    aid = owned.account()
    out = []
    for scope in owned.owner_scopes(aid):
        view = owned.owner_view(scope)
        # The owner's folder list (empty folders too; a space's syncs with its workflows).
        # The list adds the folders its workflows are filed in.
        view["folders"] = store.folders_of(view["id"], [])
        if scope["kind"] == "shared":
            st = data.sync_get(aid, scope["id"])
            view["sync"] = {
                "state": scope["state"],
                "error": st["error"],
                "syncedAt": float(st["synced_at"]) or None,
                "pending": data.count_dirty(aid, scope["id"]),
                "members": int(st["members"]),
            }
            view["slug"] = runtime.web_slug(aid, scope["id"])
            if scope["state"] == "reset":
                # What Upload my copy / Clear my copy act on: the space's whole copy here.
                from backend.uefn_plugins.spaces import space_contents

                view["resetContents"] = space_contents(aid, scope["id"])
        out.append(view)
    return {"ok": True, "owners": out, "signedIn": aid != LOCAL, "spacesEnabled": provider().enabled(),
            "kindLabel": kind_label, "localImport": owned.local_import_count(aid)}


def refresh_spaces() -> dict[str, Any]:
    """Ask the provider which spaces this account has (only when the view opens, never
    on a timer); it records labels and who may change each space's workflows."""
    if store.use_db("automations"):
        from backend.uefn_plugins.spaces import provider

        try:
            provider().refresh_workflow_spaces()
        except Exception:
            pass
    return owners()


def _space_ids() -> list[tuple[str, str]]:
    from backend.automations import owned

    return [(s["account"], s["id"]) for s in owned.owner_scopes() if s["kind"] == "shared"]


def _sync_one(account: str, space: str, force: bool) -> dict[str, Any]:
    """One provider round. One that raises shows on its space (state ``error`` and why,
    via ``spaces._round_failed``) and the next space still syncs; before, the thread died
    and the list kept its old "Synced"."""
    from backend.uefn_plugins.sync_scheduler import run_round

    return run_round(account, space, force=force)


def _round(targets: list[tuple[str, str]], force: bool) -> None:
    changed = False
    for account, space in targets:
        out = _sync_one(account, space, force)
        changed = changed or bool(out.get("changed")) or not out.get("skipped")
    if changed:
        _announce()


def sync(*, force: bool = False, space_id: str = "", upload: bool = False) -> dict[str, Any]:
    """One round per listed space. A named space (Save, Update online) runs now and,
    with ``upload``, queues that space's workflows first so a saved copy goes up even
    when an earlier round had given up on it."""
    if not store.use_db("automations"):
        return {"ok": True, "started": False}
    targets = _space_ids()
    want = (space_id or "").strip()
    if want:
        targets = [pair for pair in targets if pair[1] == want]
        if not targets:
            return {"ok": False, "started": False, "error": "That space isn't on this PC."}
    if not targets:
        return {"ok": True, "started": False}
    if want or upload:
        return _sync_now(targets, upload=upload or bool(want))
    threading.Thread(target=_round, args=(targets, force), name="workflow-space-sync", daemon=True).start()
    return {"ok": True, "started": True}


def _sync_now(targets: list[tuple[str, str]], *, upload: bool) -> dict[str, Any]:
    from backend.automations.owned import DOC_PLUGIN
    from backend.store.repos import plugin_data as data

    errors: list[str] = []
    for account, space in targets:
        if upload and data.sync_get(account, space)["state"] != "reset":
            # A reset copy stays as it is until the person picks Upload or Clear my copy.
            data.mark_plugin_dirty(account, space, DOC_PLUGIN)
        out = _sync_one(account, space, True)
        err = str(out.get("error") or "")
        if err:
            errors.append(err)
    _announce()
    message = "; ".join(errors)
    return {"ok": not message, "started": True, "error": message}


def background(workflows: list[dict[str, Any]], now: float | None = None) -> list[str]:
    """Scheduler hook: a round every 15 minutes for spaces this PC runs schedules or
    triggers of, so an edit made on another PC arrives without opening the view."""
    now = time.time() if now is None else now
    spaces = sorted({
        str((wf.get("owner") or {}).get("id") or "")
        for wf in workflows
        if (wf.get("owner") or {}).get("kind") == "shared" and wf.get("enabled") and wf.get("run_here")
        and store.trigger_of((wf.get("graph") or {}).get("nodes") or [])["kind"] in ("schedule", "event")
    } - {""})
    due = [s for s in spaces if now - _LAST_BACKGROUND.get(s, 0.0) >= BACKGROUND_EVERY_S]
    if not due:
        return []
    for space in due:
        _LAST_BACKGROUND[space] = now
    targets = [(a, s) for a, s in _space_ids() if s in due]
    if targets:
        threading.Thread(target=_round, args=(targets, False), name="workflow-space-sync-bg", daemon=True).start()
    return due


def open_web(space: str) -> dict[str, Any]:
    """The space's Workflows page on the web (the provider knows where)."""
    from backend.uefn_plugins.spaces import provider

    return provider().open_web(space_id=str(space or ""), page="workflows")


def import_local() -> dict[str, Any]:
    """Bring the workflows made while signed out on this PC into the account."""
    from backend.automations import owned

    moved = owned.import_local(owned.account())
    if moved:
        _announce()
    return {"ok": True, "moved": moved}
