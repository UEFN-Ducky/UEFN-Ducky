"""Composition root for the write pipeline.

One ``ProjectWriter`` per process. Its default resolvers come from
``backend.bridge`` (the same root the ``workspace_*`` tools use). The panel
process registers its adapters (file history, editor sync, event push, lane
provider) through :func:`configure`; the MCP bridge process used by external
coding agents runs with the defaults and reads identity from ``DUCKY_*`` env.
"""

from __future__ import annotations

import threading
from typing import Iterable

from backend.workspace.journal import ChangeJournal
from backend.workspace.policy import WritePolicy
from backend.workspace.writer import ProjectWriter, WriteObserver

_writer: ProjectWriter | None = None
_lock = threading.Lock()


def _default_writer() -> ProjectWriter:
    from backend.bridge import resolve_workspace_path, workspace_roots

    def root() -> str:
        roots = workspace_roots()
        if not roots:
            raise ValueError(
                "No workspace root configured. Set the project in UEFN-Ducky Settings → Agent, "
                "or set UEFN_VSCODE_WORKSPACE_FOLDERS / UEFN_DUCKY_PROJECT_ROOT."
            )
        return roots[0]

    return ProjectWriter(root_resolver=root, path_resolver=resolve_workspace_path)


def get_writer() -> ProjectWriter:
    global _writer
    with _lock:
        if _writer is None:
            _writer = _default_writer()
        return _writer


def configure(
    *,
    observers: Iterable[WriteObserver] = (),
    policies: Iterable[WritePolicy] = (),
    journal: ChangeJournal | None = None,
) -> ProjectWriter:
    """Attach adapters to the process writer. Idempotent for the same objects."""
    writer = get_writer()
    for observer in observers:
        writer.add_observer(observer)
    for policy in policies:
        writer.add_policy(policy)
    if journal is not None:
        writer.set_journal(journal)
    return writer


def reset_for_tests(writer: ProjectWriter | None = None) -> None:
    global _writer
    with _lock:
        _writer = writer
