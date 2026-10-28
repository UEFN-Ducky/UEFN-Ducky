"""Project metadata, level save/info, viewport camera, revision control push/pull."""

from __future__ import annotations

import time
from typing import Any, Optional

from backend.bridge import send_command
from backend.util.json_util import tool_json
from backend.tools.support.plugin_gate import plugin_mcp_tool


@plugin_mcp_tool("uefn")
def get_project_info(pretty: bool = False) -> str:
    """Get project name and content root (often /{ProjectName}/, not /Game/)."""
    result = send_command("get_project_info")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def save_current_level(pretty: bool = False) -> str:
    """Save the current level. Never behind an open Save/Checkout popup (dismiss_uefn_modal)."""
    result = send_command("save_current_level")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_level_info(pretty: bool = False) -> str:
    """Get the current level name and actor count. Not an actor list (get_all_actors)."""
    result = send_command("get_level_info")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def get_viewport_camera(pretty: bool = False) -> str:
    """Get the viewport camera position and rotation. Does not move it."""
    result = send_command("get_viewport_camera")
    return tool_json(result, pretty=pretty)


@plugin_mcp_tool("uefn")
def set_viewport_camera(
    location: Optional[list[float]] = None,
    rotation: Optional[list[float]] = None,
    pretty: bool = False,
) -> str:
    """Move the viewport camera. Never moves actors."""
    params: dict[str, Any] = {}
    if location is not None:
        params["location"] = location
    if rotation is not None:
        params["rotation"] = rotation
    result = send_command("set_viewport_camera", params)
    return tool_json(result, pretty=pretty)


# Saving, checking in and syncing run on the editor thread; a big island takes a while.
_REVISION_TIMEOUT = 180.0
_CHECK_IN_RETRIES = 2
_CHECK_IN_RETRY_WAIT_S = 3.0


def _lore_cli():
    from frontend.ui_web.verse_editor.urc import lore_cli

    return lore_cli


def _island_root() -> str:
    # The island UEFN has open, not the project picked in Ducky: they can differ, and
    # Lore must read the repository the editor will check in to.
    island = str(send_command("revision_status").get("island") or "")
    return island.replace("/", "\\").rstrip("\\") if island else ""


def _remote_state(state: dict) -> dict:
    return {k: state.get(k) for k in ("in_sync", "behind", "ahead")}


def _rel(root: str, path: str) -> str:
    base = root.replace("\\", "/").rstrip("/") + "/"
    return path[len(base):] if path.lower().startswith(base.lower()) else path.rsplit("/", 1)[-1]


# UEFN's own names for its conflict panel (French UI seen live on Oct 27; English guessed).
_RESOLVE_STEPS = (
    'in the "Conflict Resolution" tab ("Résolution des conflits", next to Details) the person picks '
    "\"Keep All Current\" (\"Tout ce qui est actuel\": this PC's version) or \"Take All Incoming\" "
    "(\"Tout ce qui est entrant\": the other PC's), or \"Resolve in VS Code\" (\"Résoudre dans VS Code\") "
    'per file, then clicks "Resolve Conflict" ("Résoudre conflit"); then push (revision_push).'
)
CONFLICT_DIALOG_HINT = (
    'UEFN is showing its "Conflicting Changes" dialog ("Modifications en conflit") and is blocked until '
    "the person acts; never press it. The person clicks \"Review Conflicts\" (\"Examiner les conflits\"), "
    "then " + _RESOLVE_STEPS
)


def _conflict_dialog() -> dict | None:
    from backend.tools.core.uefn_popups import find_conflict_dialog

    return find_conflict_dialog()


@plugin_mcp_tool("uefn")
def revision_push(description: str, pretty: bool = False) -> str:
    """Save the island and push every change not yet on the remote (Unreal Revision Control). Never without a description."""
    if not (description or "").strip():
        return tool_json({"ok": False, "error": "description is required (the check-in message)"}, pretty=pretty)
    root = _island_root()
    if not root:
        return tool_json({"ok": False, "error": "UEFN has no island open"}, pretty=pretty)
    lore = _lore_cli()
    # Lore refuses a check-in (with no message) while main has revisions this island lacks.
    before = lore.repo_state(root)
    if before.get("ok") and before.get("behind"):
        return tool_json(
            {
                "ok": False,
                **_remote_state(before),
                "error": "the remote has changes this island doesn't have: pull them first (revision_pull), then push",
            },
            pretty=pretty,
        )
    pending = lore.pending_paths(root)
    if not pending.get("ok"):
        return tool_json({"ok": False, "error": pending.get("error") or "could not list pending files"}, pretty=pretty)
    # A text file Lore merged keeps its <<<<<<< / >>>>>>> markers until someone resolves it.
    marked = [_rel(root, p) for p in lore.conflict_marked(pending.get("paths") or [])]
    if marked:
        return tool_json(
            {
                "ok": False,
                "conflict_markers": marked,
                "error": f"{marked[0]} still has conflict markers from a merge: resolve it "
                "(UEFN's Conflict resolution tab or VS Code) before pushing",
            },
            pretty=pretty,
        )
    # A file changed here while the other PC holds its lock (edited by hand past UEFN's
    # warning, or by a script) can't be checked in: say which, instead of a failed check-in.
    from backend.bridge.island_locks import locked_files

    locked = locked_files(root, pending.get("paths") or [])
    if locked:
        first = locked[0]
        return tool_json(
            {
                "ok": False,
                "locked_by_other": locked,
                "error": f"{first['file'].rsplit('/', 1)[-1]} is being edited on the other PC by "
                f"{first['owner'] or 'someone else'}: wait until they push it, pull, then push again",
            },
            pretty=pretty,
        )
    result = send_command(
        "revision_check_in",
        {"files": list(pending.get("paths") or []), "description": description},
        timeout=_REVISION_TIMEOUT,
    )
    saved = result.get("saved") or []
    # Right after a save UEFN is still backing it up on its personal branch, and a
    # check-in made then fails with no message; the same check-in passes seconds later.
    for _ in range(_CHECK_IN_RETRIES):
        if result.get("ok") or not saved or not result.get("targets"):
            break
        time.sleep(_CHECK_IN_RETRY_WAIT_S)
        result = send_command(
            "revision_check_in",
            {"files": list(result["targets"]), "description": description},
            timeout=_REVISION_TIMEOUT,
        )
    state = lore.repo_state(root)
    return tool_json(
        {
            "ok": bool(result.get("ok")),
            "checked_in": len(result.get("checked_in") or []),
            "saved": len(saved),
            **_remote_state(state),
            "error": result.get("error") or state.get("error") or "",
        },
        pretty=pretty,
    )


@plugin_mcp_tool("uefn")
def revision_pull(force: bool = False, allow_both_changed: bool = False, pretty: bool = False) -> str:
    """Pull the remote's changes and reload the map. Never force over unsaved work or files changed on both PCs unasked."""
    root = _island_root()
    if not root:
        return tool_json({"ok": False, "error": "UEFN has no island open"}, pretty=pretty)
    lore = _lore_cli()
    before = lore.repo_state(root)
    if not before.get("ok"):
        return tool_json({"ok": False, "error": before.get("error") or "could not read the repository"}, pretty=pretty)
    if not before.get("behind"):
        return tool_json({"ok": True, "up_to_date": True, **_remote_state(before)}, pretty=pretty)
    # A file changed both here and on the other PC: never let the pull pick a side unasked.
    # Its own flag: forcing past unsaved work must not also wave this through.
    both = lore.overlap(root, state=before)
    both_changed = [{k: f[k] for k in ("rel", "local", "remote")} for f in both.get("files") or []]
    if not allow_both_changed and not both.get("ok"):
        return tool_json(
            {
                "ok": False,
                "error": f"could not check which files changed on both PCs ({both.get('error')}): "
                "tell the person, pull with allow_both_changed=true only if they agree",
            },
            pretty=pretty,
        )
    if not allow_both_changed and both_changed:
        names = ", ".join(f["rel"] for f in both_changed[:3]) + (" …" if len(both_changed) > 3 else "")
        return tool_json(
            {
                "ok": False,
                "both_changed": both_changed,
                "error": f"{len(both_changed)} file{'s' if len(both_changed) != 1 else ''} changed on both PCs "
                f"({names}): nothing was pulled. Ask the person what to do with each: keep this PC's version, "
                "take the other PC's, or merge by hand; then pull with allow_both_changed=true",
            },
            pretty=pretty,
        )
    try:
        result = send_command("revision_sync", {"force": bool(force)}, timeout=_REVISION_TIMEOUT)
    except Exception as exc:  # the editor thread is held (a dialog) or the listener is gone
        result = {"ok": False, "error": str(exc)}
    # A merged file makes UEFN ask the person in a modal dialog; the sync waits on it.
    if not result.get("ok") and not result.get("needs_save") and _conflict_dialog():
        return tool_json(
            {"ok": False, "conflict_dialog": True, "both_changed": both_changed, "hint": CONFLICT_DIALOG_HINT},
            pretty=pretty,
        )
    if result.get("needs_save"):
        return tool_json(
            {
                "ok": False,
                "needs_save": result["needs_save"],
                "error": "UEFN has unsaved changes: ask the person to push them first (revision_push) "
                "or to pull anyway (force=true, the map is then not reloaded)",
            },
            pretty=pretty,
        )
    after = lore.repo_state(root)
    out = {
        "ok": bool(result.get("ok")),
        "reloaded": bool(result.get("reloaded")),
        "seconds": result.get("seconds"),
        **_remote_state(after),
        "error": result.get("error") or after.get("error") or "",
    }
    if both_changed:
        out.update(both_changed=both_changed, **_conflicts(both.get("files") or []))
    return tool_json(out, pretty=pretty)


def _conflicts(files: list[dict]) -> dict:
    """After a forced pull over files changed on both PCs: which ones UEFN marks conflicted."""
    try:
        res = send_command("revision_conflicts", {"paths": [f["path"] for f in files]}, timeout=60.0)
        marked = {str(p).replace("\\", "/").lower() for p in res.get("conflicted") or []}
        error = str(res.get("error") or "")
    except Exception as exc:  # an older listener: no such command
        marked, error = set(), str(exc)
    conflicted = [f["rel"] for f in files if f["path"].lower() in marked]
    if conflicted:
        hint = "UEFN marks these files conflicted: " + _RESOLVE_STEPS
    else:
        hint = ("UEFN marks none of them conflicted" + (f" (could not ask: {error})" if error else "")
                + ": have the person check each file kept both PCs' changes before pushing (a text file "
                "Lore merged has <<<<<<< / >>>>>>> markers to resolve, and ~base/~mine/~theirs copies beside it).")
    return {"conflicted": conflicted, "hint": hint}
