"""MCP tools for building, publishing, reviewing and scoping UEFN Ducky plugins.

Thin wrappers over ``backend.uefn_plugins.publishing`` and the data-scope switch.
Anything that publishes, approves or moves data between Local and a team asks the
user first, following the app's inline Allow/Deny pattern (``ducky_ask_user``).
"""

from __future__ import annotations

import json
from typing import Any

from backend.server import mcp
from backend.util.json_util import tool_json


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
def ducky_plugin_build(
    id: str,
    visibility: str = "public",
    team_id: str = "",
    pretty: bool = False,
) -> str:
    """Compile a local plugin into a Store-ready zip (no readable Python). Does not publish.

    ``visibility`` public|team and ``team_id`` are baked into the license gate. Returns
    the build report (files, sizes, warnings, sha256).
    """
    from pathlib import Path

    from backend.uefn_plugins import compile as engine
    from backend.uefn_plugins.publishing import plugin_source_dir
    from backend.uefn_plugins.store import appdata_ai_plugins_dir, normalize_plugin_id

    try:
        pid = normalize_plugin_id(id)
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    src = plugin_source_dir(pid)
    if src is None:
        return tool_json({"ok": False, "error": f"no local source to build for {pid}"}, pretty=pretty)
    out_dir = appdata_ai_plugins_dir() / ".builds"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_zip = out_dir / f"{pid}.compiled.zip"
    try:
        report = engine.build_plugin(src, out_zip, visibility=visibility, team_id=team_id)
    except engine.CompileError as exc:
        return tool_json({"ok": False, "error": f"Build failed: {exc}"}, pretty=pretty)
    report["files"] = len(report.get("files") or [])  # compact for the tool channel
    return tool_json(report, pretty=pretty)


@mcp.tool()
def ducky_plugin_publish(
    id: str,
    target: str = "team",
    team_id: str = "",
    notes: str = "",
    pretty: bool = False,
) -> str:
    """Publish a plugin to the Store. Asks first. ``target`` is 'team' or 'public'.

    Team → live for the team at once; public → review inside Ducky. The backend ships
    compiled; the private source is uploaded only for the team / reviewers.
    """
    from backend.uefn_plugins.publishing import publish

    where = f"team {team_id}" if str(target).lower() == "team" else "the public Store (for review)"
    if not _confirm(f"Publish '{id}' to {where}?", title="Publish plugin"):
        return tool_json({"ok": False, "error": "Cancelled — not published."}, pretty=pretty)
    return tool_json(publish(id, target, team_id=team_id, notes=notes), pretty=pretty)


@mcp.tool()
def ducky_plugin_status(id: str, pretty: bool = False) -> str:
    """Where a plugin is published, its versions, and whether the latest is compiled (protected)."""
    from backend.uefn_plugins.publishing import publish_status

    return tool_json(publish_status(id), pretty=pretty)


@mcp.tool()
def ducky_plugin_open_source(id: str, team_id: str = "", pretty: bool = False) -> str:
    """Open a team plugin's private source as a local draft to edit (needs Manage plugins).

    Overwrites an existing draft of the same id, so it asks first when one exists.
    """
    from backend.uefn_plugins.publishing import open_for_edit
    from backend.uefn_plugins.store import appdata_ai_plugins_dir, normalize_plugin_id

    try:
        pid = normalize_plugin_id(id)
    except ValueError as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    draft = appdata_ai_plugins_dir() / pid
    if draft.is_dir() and not _confirm(
        f"A local draft '{pid}' already exists. Replace it with the published source?",
        title="Open source",
    ):
        return tool_json({"ok": False, "error": "Cancelled — draft kept."}, pretty=pretty)
    return tool_json(open_for_edit(pid, team_id=team_id), pretty=pretty)


@mcp.tool()
def ducky_team_plugins(team_id: str = "", pretty: bool = False) -> str:
    """List the plugins published to a team (or your own items when no team is given)."""
    from frontend.duckyos_account import _store_collect

    event = "team-items" if str(team_id or "").strip() else "my-items"
    body = {"teamId": team_id} if str(team_id or "").strip() else {}
    try:
        payload = _store_collect(event, body)
    except Exception as exc:  # noqa: BLE001
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    return tool_json({"ok": True, **payload}, pretty=pretty)


@mcp.tool()
def ducky_plugin_data_scope(id: str, scope: str = "", pretty: bool = False) -> str:
    """Show or switch where a plugin's data lives (Local or a team). Switching asks first.

    ``scope`` empty → report the current scope. ``scope`` of 'local' or a team id →
    move the plugin's data copy there (nothing is copied) and restart it on the new copy.
    """
    from backend.uefn_plugins import scopes

    pid = (id or "").strip()
    if not scopes.valid_plugin_id(pid):
        return tool_json({"ok": False, "error": f"invalid plugin id: {id!r}"}, pretty=pretty)
    want = str(scope or "").strip()
    if not want:
        return tool_json({"ok": True, "scope": scopes.scope_view(scopes.active_scope(pid))}, pretty=pretty)
    scope_id = scopes.PERSONAL if want.lower() in ("local", "personal") else want
    label = "Local" if scope_id == scopes.PERSONAL else f"team {want}"
    if not _confirm(f"Switch '{pid}' data to {label}? Nothing is copied.", title="Switch data"):
        return tool_json({"ok": False, "error": "Cancelled — data scope unchanged."}, pretty=pretty)
    try:
        result = scopes.link_plugin(pid, scope_id)
    except (ValueError, PermissionError) as exc:
        return tool_json({"ok": False, "error": str(exc)}, pretty=pretty)
    try:
        from backend.uefn_plugins.host import reload_single_plugin

        reload_single_plugin(pid)
    except Exception:
        pass
    return tool_json({"ok": True, "scope": result}, pretty=pretty)


@mcp.tool()
def ducky_store_review_queue(pretty: bool = False) -> str:
    """Public plugin submissions awaiting review (reviewer tooling inside Ducky)."""
    from backend.uefn_plugins.publishing import review_queue

    return tool_json(review_queue(), pretty=pretty)


@mcp.tool()
def ducky_store_review(
    slug: str,
    version: str = "",
    approve: bool = False,
    note: str = "",
    pretty: bool = False,
) -> str:
    """Approve or reject a public submission. Asks first.

    Approve rebuilds the submission's private source locally, uploads that build, and
    publishes it. Reject needs a note sent back to the author.
    """
    from backend.uefn_plugins.publishing import review_approve, review_reject

    if approve:
        if not _confirm(f"Approve and publish '{slug}' {version}? It will be rebuilt locally.", title="Approve"):
            return tool_json({"ok": False, "error": "Cancelled — not approved."}, pretty=pretty)
        return tool_json(review_approve(slug, version, notes=note), pretty=pretty)
    if not (note or "").strip():
        return tool_json({"ok": False, "error": "A rejection note is required."}, pretty=pretty)
    if not _confirm(f"Reject '{slug}' {version}?", title="Reject"):
        return tool_json({"ok": False, "error": "Cancelled — not rejected."}, pretty=pretty)
    return tool_json(review_reject(slug, version, note), pretty=pretty)
