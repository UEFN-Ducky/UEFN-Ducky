"""Changeset tools: what each run wrote, export it, revert it.

Every project write is ledgered per run by the write pipeline
(``backend.workspace``). These tools read that ledger and revert through the
same pipeline, so a revert is itself an attributed write.
"""

from __future__ import annotations

from typing import Any

from backend.bridge import workspace_roots
from backend.server import mcp
from backend.util.json_util import tool_json
from backend.workspace import identity
from backend.workspace.journal import FileChangeJournal
from backend.workspace.runtime import get_writer


def _project_root() -> str:
    roots = workspace_roots()
    if not roots:
        raise ValueError("No UEFN project root configured.")
    return roots[0]


def _journal() -> FileChangeJournal:
    journal = get_writer().journal
    if not isinstance(journal, FileChangeJournal):
        raise ValueError("The change journal is disabled (Settings → Agent → Change journal).")
    return journal


def _summary(run: dict[str, Any]) -> dict[str, Any]:
    entries = run.get("entries", [])
    applied = [e for e in entries if e.get("outcome", "ok") == "ok"]
    files = [e for e in applied if e.get("op") != "editor"]
    editor = [e for e in applied if e.get("op") == "editor"]
    # Counted on their own: an attempt that changed nothing is not a change.
    blocked = [e for e in entries if e.get("outcome", "ok") != "ok"]
    return {
        "run_id": run.get("run_id", ""),
        "conv_id": run.get("conv_id", ""),
        "ducky_name": run.get("ducky_name", ""),
        "model": run.get("model", ""),
        "coding_agent": run.get("coding_agent", ""),
        "group_id": run.get("group_id", ""),
        "status": run.get("status", ""),
        "started": run.get("started", 0.0),
        "ended": run.get("ended"),
        "lane": run.get("lane"),
        "files": sorted({e["path"] for e in files}),
        "entries": len(entries),
        "editor_changes": len(editor),
        "editor_manual": sum(
            1 for e in editor if (e.get("editor") or {}).get("revertable") != "auto"
        ),
        "blocked": len(blocked),
        "conflicts": sum(1 for e in entries if e.get("conflict")),
        "out_of_lane": sum(1 for e in entries if e.get("in_lane") is False),
    }


def require_revert_allowed(run: dict[str, Any]) -> None:
    """The user, the run's own chat, or the leader of the run's group may revert it."""
    ctx = identity.resolve_context()
    if ctx is None:
        return
    if ctx.conv_id and ctx.conv_id == run.get("conv_id"):
        return
    if ctx.is_leader and ctx.group_id and ctx.group_id == run.get("group_id"):
        return
    raise ValueError(
        "Only the chat that made these changes, its group leader, or the user can revert "
        f"run {run.get('run_id', '')!r}. Ask the leader in the group chat."
    )


@mcp.tool()
def changeset_list(conv_id: str = "", group_id: str = "", limit: int = 20, pretty: bool = False) -> str:
    """List recent runs that changed the project: who, which files, which editor changes, what was blocked.

    Covers both file writes and editor changes (actors, assets, devices, Verse
    wiring), plus attempts that were refused or failed. Filter by conv_id (one
    chat) or group_id (every member of a group). Use before compiling a group's
    work to see what each ducky touched; then changeset_export for the full
    per-file record or changeset_revert to undo a run.
    """
    runs = _journal().list_runs(
        project_root=_project_root(),
        conv_id=conv_id.strip(),
        group_id=group_id.strip(),
        limit=max(1, min(200, int(limit))),
    )
    return tool_json({"ok": True, "runs": [_summary(r) for r in runs]}, pretty=pretty)


@mcp.tool()
def changeset_export(run_id: str, pretty: bool = False) -> str:
    """Export one run as a ducky.changeset/1 manifest: author, write lane, and every file with hashes.

    Stable interop shape for external orchestrators that need per-worker attribution.
    """
    return tool_json(_journal().export_run(run_id.strip(), project_root=_project_root()), pretty=pretty)


@mcp.tool()
def changeset_revert(run_id: str, seq: int = 0, force: bool = False, pretty: bool = False) -> str:
    """Undo a run's project writes (or one file: pass the seq of any entry for it), restoring the pre-run content.

    Files changed by someone else since are skipped unless force=true. Allowed for the
    chat that made the changes, the leader of its group, or the user. The revert is
    journaled as its own run (source "revert"); compile afterwards to confirm the project builds.
    """
    root = _project_root()
    journal = _journal()
    run = journal.get_run(run_id.strip(), project_root=root)
    require_revert_allowed(run)
    actor = identity.current_writer(tool="changeset_revert")
    if int(seq) > 0:
        result = journal.revert_entry(run_id.strip(), int(seq), project_root=root, force=bool(force), actor=actor)
    else:
        result = journal.revert_run(run_id.strip(), project_root=root, force=bool(force), actor=actor)
    if result.get("skipped_modified") and not force:
        result["next"] = "Some files changed since this run; re-run with force=true to restore them anyway."
    return tool_json(result, pretty=pretty)
