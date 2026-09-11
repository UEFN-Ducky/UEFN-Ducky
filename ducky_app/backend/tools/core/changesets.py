"""Changeset tools: what each run wrote, export it, revert it.

Every project write is ledgered per run by the write pipeline
(``backend.workspace``). These tools read that ledger and revert through the
same pipeline, so a revert is itself an attributed write. Archive keeps the
log for later readers and locks revert until the run is unarchived.
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


def _archived_filter(value: str) -> bool | None:
    v = (value or "all").strip().lower()
    if v in ("", "all", "both"):
        return None
    if v in ("1", "true", "yes", "archived"):
        return True
    if v in ("0", "false", "no", "live", "active"):
        return False
    raise ValueError("archived must be all, live, or archived")


def _program_of(entry: dict[str, Any]) -> str:
    if entry.get("op") == "editor":
        spec = entry.get("editor") if isinstance(entry.get("editor"), dict) else {}
        prog = str((spec or {}).get("program") or "")
        if prog:
            return prog
        from backend.workspace.plugin_revert import program_of_slot

        return program_of_slot(str(entry.get("path") or ""))
    return "file"


def _summary(run: dict[str, Any]) -> dict[str, Any]:
    entries = run.get("entries", [])
    applied = [e for e in entries if e.get("outcome", "ok") == "ok"]
    files = [e for e in applied if e.get("op") != "editor"]
    editor = [e for e in applied if e.get("op") == "editor"]
    # Counted on their own: an attempt that changed nothing is not a change.
    blocked = [e for e in entries if e.get("outcome", "ok") != "ok"]
    archived = bool(run.get("archived"))
    programs: dict[str, int] = {}
    for entry in applied:
        prog = _program_of(entry)
        programs[prog] = programs.get(prog, 0) + 1
    return {
        "run_id": run.get("run_id", ""),
        "conv_id": run.get("conv_id", ""),
        "ducky_name": run.get("ducky_name", ""),
        "model": run.get("model", ""),
        "coding_agent": run.get("coding_agent", ""),
        "group_id": run.get("group_id", ""),
        "source": run.get("source", ""),
        "reverts_run_id": run.get("reverts_run_id") or "",
        "status": run.get("status", ""),
        "archived": archived,
        "revert_locked": archived,
        "started": run.get("started", 0.0),
        "ended": run.get("ended"),
        "lane": run.get("lane"),
        "programs": programs,
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
def changeset_list(
    conv_id: str = "",
    group_id: str = "",
    limit: int = 20,
    archived: str = "all",
    pretty: bool = False,
) -> str:
    """List recent runs that changed the project, including archived ones.

    Covers file writes, UEFN listener / Epic MCP editor mutations, Store plugin
    programs (``blender://`` slots), and human file edits (``source=user``,
    ``run_id`` like ``human:YYYY-MM-DD``, ``ducky_name`` You, ``tool=external``
    for Explorer / VS Code / UEFN). Each summary has ``source`` and ``programs``
    ({file, uefn, blender, …}). Archived runs stay readable (revert_locked=true)
    — use changeset_export / changeset_contents, not revert.
    Filter archived=all|live|archived. Then changeset_revert to undo a live run.
    Agents cannot revert human runs (no matching conv_id); only the user can.
    """
    runs = _journal().list_runs(
        project_root=_project_root(),
        conv_id=conv_id.strip(),
        group_id=group_id.strip(),
        limit=max(1, min(200, int(limit))),
        archived=_archived_filter(archived),
    )
    return tool_json({"ok": True, "runs": [_summary(r) for r in runs]}, pretty=pretty)


@mcp.tool()
def changeset_export(run_id: str, pretty: bool = False) -> str:
    """Export one run as a ducky.changeset/1 manifest: author, lane, files (with seq), editor slots.

    Editor rows include program, summary, reason, and target label. Works on
    archived runs. Use seq with changeset_contents to read before/after text.
    """
    return tool_json(_journal().export_run(run_id.strip(), project_root=_project_root()), pretty=pretty)


@mcp.tool()
def changeset_contents(run_id: str, seq: int, pretty: bool = False) -> str:
    """Read the recorded before/after text for one entry. Works on archived runs. Does not change the island."""
    return tool_json(
        _journal().entry_contents(run_id.strip(), int(seq), project_root=_project_root()),
        pretty=pretty,
    )


@mcp.tool()
def changeset_archive(
    run_id: str = "",
    conv_id: str = "",
    group_id: str = "",
    archived: bool = True,
    pretty: bool = False,
) -> str:
    """Archive (or unarchive) ledger rows. The island is untouched. Archived runs stay readable; revert is locked.

    Pass run_id for one run, or conv_id / group_id to archive that chat/group. archived=false unarchives.
    """
    root = _project_root()
    journal = _journal()
    rid = run_id.strip()
    if rid:
        require_revert_allowed(journal.get_run(rid, project_root=root))
    elif identity.resolve_context() is not None:
        raise ValueError("Agents may archive one run_id they own. Bulk archive is for the user.")
    result = journal.set_archived(
        project_root=root,
        conv_id=conv_id.strip(),
        group_id=group_id.strip(),
        run_id=rid,
        archived=bool(archived),
    )
    return tool_json({"ok": True, **result}, pretty=pretty)


@mcp.tool()
def changeset_revert(
    run_id: str, seq: int = 0, force: bool = False, step: bool = False, program: str = "", pretty: bool = False
) -> str:
    """Undo a live run's writes (or one file: pass seq). Archived runs refuse — unarchive first.

    Files changed by someone else since are skipped unless force=true. Allowed for the
    chat that made the changes, the leader of its group, or the user. The revert is
    journaled as its own run (source "revert"); Redo is reverting that run.

    step=true with seq set undoes that one write only. A later unreverted write on
    the same path blocks — revert newest first.

    program (uefn / blender / file / …) limits a full-run undo to that program only.
    """
    root = _project_root()
    journal = _journal()
    run = journal.get_run(run_id.strip(), project_root=root)
    require_revert_allowed(run)
    actor = identity.current_writer(tool="changeset_revert")
    if int(seq) > 0:
        result = journal.revert_entry(
            run_id.strip(), int(seq), project_root=root, force=bool(force), actor=actor, step=bool(step),
        )
    else:
        result = journal.revert_run(
            run_id.strip(), project_root=root, force=bool(force), actor=actor, program=(program or "").strip(),
        )
    if result.get("skipped_modified") and not force:
        result["next"] = "Some files changed since this run; re-run with force=true to restore them anyway."
    return tool_json(result, pretty=pretty)
