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


def list_changesets(*, conv_id: str = "", group_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
    journal = _journal()
    if journal is None:
        return []
    return journal.list_runs(
        project_root=_project_root(),
        conv_id=(conv_id or "").strip(),
        group_id=(group_id or "").strip(),
        limit=max(1, min(500, int(limit))),
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


def revert_entry(run_id: str, seq: int, *, force: bool = False) -> dict[str, Any]:
    journal = _journal()
    if journal is None:
        raise ValueError("The change journal is disabled.")
    actor = identity.user_writer(tool="changeset_revert")
    return journal.revert_entry((run_id or "").strip(), int(seq), project_root=_project_root(), force=bool(force), actor=actor)


def revert_run(run_id: str, *, force: bool = False) -> dict[str, Any]:
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
