"""MCP tools for where a plugin's data lives (Local or a shared space) and its team copy.

``ducky_plugin_data_scope`` shows or switches the copy; switching asks the user first,
following the app's inline Allow/Deny pattern (``ducky_ask_user``).
``ducky_team_status`` says who else is in the plugin's team copy, what is locked, what
waits to go out and what conflicts; ``ducky_team_lock`` takes or releases a lock, so a
ducky can hold an item through a long edit.
"""

from __future__ import annotations

import json
from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json


def _team_summary(pid: str) -> dict[str, Any]:
    """The scope bar's status, trimmed for an agent: the copy, its state and reason, and for
    a shared copy who is in it, locks, what waits to go out or come in, and conflicts."""
    from backend.uefn_plugins.spaces import scope_status

    st = scope_status(pid)
    out: dict[str, Any] = {"scope": st.get("scope") or {}, "state": st.get("state") or "ok"}
    if (st.get("scope") or {}).get("kind") != "shared":
        return out
    if st.get("error"):
        out["reason"] = st["error"]
    changes = st.get("changes") or {}
    people = []
    for p in st.get("people") or []:
        if p.get("me"):
            continue
        here = (p.get("plugins") or {}).get(pid) or {}
        doing = [f"{'ducky ' + e['ducky'] + ' ' if e.get('actor') == 'agent' else ''}editing {e.get('unitName')}"
                 for e in here.get("editing") or []] or (["viewing"] if here.get("viewing") else [])
        people.append({"name": p.get("name"), "device": p.get("device") or "", "doing": doing})
    out.update(
        mode=st.get("mode") or "auto",
        head=st.get("head") or 0,
        pending=st.get("pending") or 0,
        incoming=changes.get("incoming") or 0,
        people=people,
        locks=[{"unit": lk["unit"], "name": lk.get("name"), "holder": "you" if lk.get("mine") else lk.get("holder"),
                "leaseLeft": round(float(lk.get("leaseLeft") or 0))} for lk in st.get("locks") or []],
        conflicts=[{"kind": c["kind"], "key": c["key"], "reason": c.get("reason"), "by": c.get("by")}
                   for c in st.get("conflicts") or []],
        notSent=[{"kind": o["kind"], "key": o["key"], "state": o["state"], "reason": o.get("reason") or ""}
                 for o in changes.get("outgoing") or [] if o["state"] not in ("queued", "sending")],
    )
    live = st.get("live") or {}
    if live.get("on"):  # who this PC reaches peer to peer: pointers, follow and "pull now" need it
        out["live"] = {"room": live.get("room") or "", "state": live.get("state") or "",
                       "peers": [{k: p.get(k) or "" for k in ("name", "label", "device", "path")}
                                 for p in live.get("peers") or []],
                       "connecting": list(live.get("connecting") or []), "unlinked": list(live.get("unlinked") or [])}
    if changes.get("blocked"):
        out["incomingBlocked"] = [{"kind": b["kind"], "key": b["key"], "error": b.get("error")}
                                  for b in changes["blocked"]]
    if changes.get("held"):  # waits behind the bar's Update now; lands before this ducky's next call
        out["updatesWaitingFrom"] = list(changes.get("incomingBy") or [])
    if st.get("folders"):  # a plugin that keeps its own folders: each unit and who holds it
        out["folders"] = [{"unit": f"{f['folder_id']}/{f['unit']}", "name": f["name"], "state": f["state"],
                           **({"holder": f["holder"]} if f.get("holder") else {})} for f in st["folders"]]
    return out


def _confirm(prompt: str, title: str = "Confirm") -> bool:
    """Ask one Yes/No question inline; True only on an explicit Yes."""
    from backend.tools.panel.panel_ui import ducky_ask_user

    raw = ducky_ask_user(
        [
            {
                "id": "ok",
                "prompt": prompt,
                "options": [
                    {"id": "yes", "label": "Yes"},
                    {"id": "no", "label": "No"},
                ],
                "required": True,
            }
        ],
        title=title,
    )
    try:
        out = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if out.get("skipped_all"):
        return False
    ans = (out.get("answers") or {}).get("ok") or {}
    return "yes" in (ans.get("selected") or [])


@mcp.tool()
def ducky_plugin_data_scope(id: str, scope: str = "", pretty: bool = False) -> str:
    """Show or switch where a plugin's data lives (Local or a shared space). Switching asks first.

    ``scope`` empty → report the current scope, its state (ok, offline, error, paused,
    waiting, locked, lost, unavailable, reset, no_provider) and, for a shared copy, who is
    in it, locks, what waits to go out or come in, and conflicts (as ``ducky_team_status``).
    ``scope`` of 'local' or a space id → the whole app switches there: every plugin,
    Workflows and General storage show that copy (nothing is copied) and restart on it.
    """
    from backend.uefn_plugins import scopes
    from backend.uefn_plugins.spaces import provider

    pid = (id or "").strip()
    if not scopes.valid_plugin_id(pid):
        return tool_json({"ok": False, "error": f"invalid plugin id: {id!r}"}, pretty=pretty)
    want = str(scope or "").strip()
    if not want:
        try:
            return tool_json({"ok": True, **_team_summary(pid)}, pretty=pretty)
        except Exception as exc:  # noqa: BLE001 — the scope alone still answers
            return tool_json({"ok": True, "scope": scopes.scope_view(scopes.active_scope(pid)),
                              "note": f"status unavailable: {exc}"}, pretty=pretty)
    scope_id = scopes.PERSONAL if want.lower() in ("local", "personal") else want
    if scope_id != scopes.PERSONAL and not scopes.sharing_allowed(pid):
        return tool_json({"ok": False, "error": f"'{pid}' keeps its data on this PC only. It can't be shared."},
                         pretty=pretty)
    label = "Local" if scope_id == scopes.PERSONAL else f"{provider().kind_label} {want}"
    if not _confirm(f"Switch the whole app (every plugin, Workflows and General storage) to {label}? "
                    "Nothing is copied.", title="Switch data"):
        return tool_json({"ok": False, "error": "Cancelled — data scope unchanged."}, pretty=pretty)
    # The app is on one copy: the same switch as the scope bar and the Team picker.
    import frontend.ui_web.panel_api  # noqa: F401 — panel_api_store is imported through it
    from frontend.ui_web.agent_modes import push_ui_event
    from frontend.ui_web.panel_api_store import PanelApiStoreMixin

    class _Switch(PanelApiStoreMixin):
        def _push_panel(self, event: dict) -> None:
            push_ui_event(event)

    out = _Switch().plugin_scope_set(pid, scope_id)
    if out.get("needs") == "first_share":
        return tool_json({"ok": False, "needs": "first_share", "units": out.get("units") or [],
                          "error": f"{out.get('team') or 'The team'} has none of this plugin's files yet. Choose "
                                   "which to copy in the plugin's bar (Copy Local into the team)."}, pretty=pretty)
    if out.get("ok") is False:
        return tool_json({"ok": False, "error": out.get("error") or "The switch didn't go through."}, pretty=pretty)
    return tool_json({"ok": True, "scope": out.get("scope"), "switchedAll": out.get("switchedAll", []),
                      "pendingAll": out.get("pendingAll", [])}, pretty=pretty)


@mcp.tool()
def ducky_team_status(plugin: str, pretty: bool = False) -> str:
    """Who else is in a plugin's team copy, what is locked, what waits to sync, and conflicts.

    Answers the copy (``scope``) and its ``state`` (``reason`` when not ok); for a shared
    copy also ``people`` (each one's PC and what they do in this plugin: viewing, editing
    an item, or their ducky editing it), ``locks`` (unit, name, holder, seconds left),
    ``pending`` (changes of this PC to send), ``incoming`` (changes waiting to come
    down), ``notSent`` (changes held back, each with why) and ``conflicts``. Check it
    before a long edit of a shared item, and lock with ``ducky_team_lock``.
    """
    from backend.uefn_plugins import scopes

    pid = (plugin or "").strip()
    if not scopes.valid_plugin_id(pid):
        return tool_json({"ok": False, "error": f"invalid plugin id: {plugin!r}"}, pretty=pretty)
    try:
        return tool_json({"ok": True, "plugin": pid, **_team_summary(pid)}, pretty=pretty)
    except Exception as exc:  # noqa: BLE001
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)


@mcp.tool()
def ducky_team_lock(plugin: str, unit: str, action: str = "acquire", pretty: bool = False) -> str:
    """Lock or release one item of a plugin's team copy, so nobody else changes it meanwhile.

    ``unit``: ``doc:<key>`` for a doc, ``asset:<path>`` for a file, or just the key or
    path; for a plugin that keeps its own folders, ``<folder>/<unit>`` as
    ``ducky_team_status`` lists it (e.g. ``scenes/MEGA_Duel_Stadium``).
    ``action``: ``acquire`` (kept until you release it; renewed while the app runs)
    or ``release`` (as soon as your edit is saved). Someone else's lock answers who holds
    it; a copy you're behind on is synced first. Only for a shared copy on the new sync.
    """
    from backend.uefn_plugins import scopes
    from backend.uefn_plugins.spaces import team_lock

    pid = (plugin or "").strip()
    if not scopes.valid_plugin_id(pid):
        return tool_json({"ok": False, "error": f"invalid plugin id: {plugin!r}"}, pretty=pretty)
    if action not in ("acquire", "release"):
        return tool_json({"ok": False, "error": "action must be acquire or release"}, pretty=pretty)
    try:
        return tool_json(team_lock(pid, unit, action), pretty=pretty)
    except (ValueError, PermissionError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
