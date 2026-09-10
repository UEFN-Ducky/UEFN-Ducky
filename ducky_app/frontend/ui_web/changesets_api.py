"""Panel-side view of the changeset journal (per-run ledger of project writes).

Thin adapter between the PanelApi JS object and ``backend.workspace.journal``:
resolves the active project root, sorts out the enabled/disabled state, and
performs reverts as the user.
"""

from __future__ import annotations

from typing import Any

from backend.workspace import identity
from backend.workspace.journal import FileChangeJournal
from backend.workspace.runtime import get_writer

def _project_root() -> str:
    from frontend.ui_web.project_files import _project_root as panel_root

    return str(panel_root().resolve())


def _journal() -> FileChangeJournal | None:
    journal = get_writer().journal
    return journal if isinstance(journal, FileChangeJournal) else None


def _close_orphans(journal: FileChangeJournal, root: str) -> None:
    """Close dead 'running' rows before the list is returned.

    Live ids come from ``live_agent_runs`` so this stays cheap on the UI thread
    (importing ``agent_modes`` here used to stall the panel).
    """
    from frontend.ui_web.live_agent_runs import get_live_run_ids
    from frontend.ui_web.project_chats import heal_killed_coding_turn

    try:
        closed = journal.close_orphan_runs(project_root=root, live_run_ids=get_live_run_ids())
    except Exception:  # noqa: BLE001 - listing must still return
        return
    for row in closed:
        try:
            heal_killed_coding_turn(str(row.get("conv_id") or ""), str(row.get("run_id") or ""))
        except Exception:  # noqa: BLE001 - ledger close already landed
            pass


def heal_orphans_on_panel_boot() -> None:
    """Panel process just started — no agent threads exist. Unstick leftover RUNNING rows."""
    journal = _journal()
    if journal is None:
        return
    try:
        root = _project_root()
    except Exception:
        return
    if not root:
        return
    _close_orphans(journal, root)


def list_changesets(*, conv_id: str = "", group_id: str = "", limit: int = 50, archived: bool = False) -> list[dict[str, Any]]:
    journal = _journal()
    if journal is None:
        return []
    root = _project_root()
    _close_orphans(journal, root)
    return journal.list_runs(
        project_root=root,
        conv_id=(conv_id or "").strip(),
        group_id=(group_id or "").strip(),
        limit=max(1, min(500, int(limit))),
        archived=bool(archived),
    )


def get_changeset(run_id: str) -> dict[str, Any]:
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    return journal.get_run((run_id or "").strip(), project_root=_project_root())


def entry_contents(run_id: str, seq: int) -> dict[str, str]:
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    return journal.entry_contents((run_id or "").strip(), int(seq), project_root=_project_root())


_LIVE_REVERT = "Stop the agent first — this run is still going."


def _refuse_live_run(run_id: str) -> dict[str, Any] | None:
    """Block island revert while the agent thread for this run (or its chat) is alive."""
    rid = (run_id or "").strip()
    if not rid:
        return None
    journal = _journal()
    if journal is None:
        return None
    try:
        run = journal.get_run(rid, project_root=_project_root())
    except Exception:
        return None
    conv = str(run.get("conv_id") or "").strip()
    try:
        from frontend.ui_web.agent_modes import is_agent_running, live_changeset_run_ids

        live = rid in live_changeset_run_ids() or (bool(conv) and is_agent_running(conv))
    except Exception:
        live = str(run.get("status") or "") == "running"
    if not live:
        return None
    return {
        "ok": False,
        "run_id": rid,
        "reverted": [],
        "skipped_modified": [],
        "errors": [_LIVE_REVERT],
    }


def revert_entry(run_id: str, seq: int, *, force: bool = False, step: bool = False) -> dict[str, Any]:
    blocked = _refuse_live_run(run_id)
    if blocked:
        return blocked
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    actor = identity.user_writer(tool="changeset_revert")
    return journal.revert_entry(
        (run_id or "").strip(),
        int(seq),
        project_root=_project_root(),
        force=bool(force),
        actor=actor,
        step=bool(step),
    )


def revert_run(run_id: str, *, force: bool = False) -> dict[str, Any]:
    blocked = _refuse_live_run(run_id)
    if blocked:
        return blocked
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    actor = identity.user_writer(tool="changeset_revert")
    return journal.revert_run((run_id or "").strip(), project_root=_project_root(), force=bool(force), actor=actor)


def export_run(run_id: str) -> dict[str, Any]:
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    return journal.export_run((run_id or "").strip(), project_root=_project_root())


def archive_changesets(
    *, conv_id: str = "", group_id: str = "", run_id: str = "", archived: bool = True
) -> dict[str, int]:
    """Move ledger rows to/from Archived. The island is untouched."""
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    return journal.set_archived(
        project_root=_project_root(),
        conv_id=(conv_id or "").strip(),
        group_id=(group_id or "").strip(),
        run_id=(run_id or "").strip(),
        archived=bool(archived),
    )


def delete_changeset_entries(run_id: str, seqs: list[int]) -> dict[str, int]:
    """Drop blocked/failed rows from a run's ledger. The island is untouched."""
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    return journal.delete_entries(run_id, [int(s) for s in seqs], project_root=_project_root())


def clear_changesets(
    *, conv_id: str = "", group_id: str = "", run_id: str = "", archived_only: bool = False
) -> dict[str, int]:
    """Drop ledger rows. The island is untouched — use revert to undo work."""
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    return journal.delete_runs(
        project_root=_project_root(),
        conv_id=(conv_id or "").strip(),
        group_id=(group_id or "").strip(),
        run_id=(run_id or "").strip(),
        archived_only=bool(archived_only),
    )
