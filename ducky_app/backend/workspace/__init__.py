"""Governed project writes: one pipeline every UEFN project mutation goes through.

Layering rule: this package imports nothing from ``frontend``. Frontend-owned
concerns (file history, editor sync, the group roster that stores lanes) plug
in through the protocols defined here and are registered by the composition
root at app start (see :mod:`backend.workspace.runtime`).

See ``docs/architecture/write-pipeline.md`` and ADR 0001.
"""

from backend.workspace.identity import RunContext, current_writer, resolve_context
from backend.workspace.policy import Decision, WritePolicy, WriteRequest
from backend.workspace.writer import (
    ProjectWriter,
    StaleWrite,
    WriteDenied,
    WriteObserver,
    WriteRecord,
    WriteResult,
)

__all__ = [
    "Decision",
    "ProjectWriter",
    "RunContext",
    "StaleWrite",
    "WriteDenied",
    "WriteObserver",
    "WritePolicy",
    "WriteRecord",
    "WriteRequest",
    "WriteResult",
    "current_writer",
    "resolve_context",
]
