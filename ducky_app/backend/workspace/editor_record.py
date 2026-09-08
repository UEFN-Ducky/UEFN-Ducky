"""Receive what the listener reports about an editor command, and pass it on.

``backend.bridge.send_command`` is the one place every Ducky editor command
crosses the process boundary, in both the panel and the MCP bridge. It calls
:func:`record` there with the command, its parameters, and the ``_ducky``
sidecar the listener attached (see ``uefn_listener/listener/ducky_capture.py``).

This module decides whether the call was a change worth recording, normalises it,
and hands it to whatever observers are registered — the change journal, once it
is wired up. It does not itself write anything.

The contract is the same as the write pipeline's: **recording can never fail a
command**. Every entry point swallows exceptions, and a command that already ran
must not be reported as an error because bookkeeping went wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from backend.workspace import identity
from backend.workspace.editor_ops import (
    MUT_OPAQUE,
    MUT_READ,
    REVERT_AUTO,
    REVERT_MANUAL,
    REVERT_NONE,
    OpSpec,
    classify,
    slot_path,
)

log = logging.getLogger(__name__)

OUTCOME_OK = "ok"
OUTCOME_BLOCKED = "blocked"
OUTCOME_FAILED = "failed"

#: Errors the listener raises when it refuses on principle rather than failing.
_REFUSAL_MARKERS = ("refused:", "never delete")


@dataclass(frozen=True)
class EditorChange:
    """One editor command that changed, or tried to change, the project."""

    command: str
    kind: str
    slot: str
    outcome: str
    params: Mapping[str, Any]
    spec: OpSpec
    targets: tuple[Mapping[str, Any], ...] = ()
    before: Any = None
    after: Any = None
    inverse: tuple[Mapping[str, Any], ...] = ()
    created: tuple[Mapping[str, Any], ...] = ()
    revertable: str = REVERT_MANUAL
    reason: str = ""
    summary: str = ""
    writer: Mapping[str, Any] = field(default_factory=dict)

    @property
    def applied(self) -> bool:
        return self.outcome == OUTCOME_OK


@runtime_checkable
class EditorObserver(Protocol):
    def on_editor_change(self, change: EditorChange) -> None: ...


_observers: list[EditorObserver] = []


def add_observer(observer: EditorObserver) -> None:
    if observer not in _observers:
        _observers.append(observer)


def remove_observer(observer: EditorObserver) -> None:
    if observer in _observers:
        _observers.remove(observer)


def reset_for_tests() -> None:
    _observers.clear()


def _outcome(ok: bool, error: str) -> str:
    if ok:
        return OUTCOME_OK
    lowered = (error or "").lower()
    if any(marker in lowered for marker in _REFUSAL_MARKERS):
        return OUTCOME_BLOCKED
    return OUTCOME_FAILED


def _target_id(targets: list[Mapping[str, Any]], params: Mapping[str, Any]) -> str:
    """Best stable id for the slot: the listener's guid, else a path from the params."""
    for target in targets:
        ident = str(target.get("guid") or target.get("id") or target.get("path") or "")
        if ident:
            return ident
    for key in ("actor_path", "child_path", "asset_path", "material_path", "data_table_path",
                "path", "source_path", "entity_path"):
        value = params.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _tuple_of_maps(value: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(v for v in value if isinstance(v, Mapping))
    return ()


def build(
    command: str,
    params: Mapping[str, Any] | None,
    sidecar: Mapping[str, Any] | None,
    *,
    ok: bool,
    error: str = "",
) -> EditorChange | None:
    """Normalise one dispatched command into an :class:`EditorChange`, or None.

    None means "not a change": a read, or a read that failed. Everything else is
    recorded, including commands the classifier does not know — an unknown
    command is treated as opaque rather than assumed harmless.
    """
    spec = classify(command)
    if spec.mutates == MUT_READ:
        return None

    params = dict(params or {})
    side = dict(sidecar or {})
    targets = list(_tuple_of_maps(side.get("targets")))
    outcome = _outcome(ok, error)

    inverse = _tuple_of_maps(side.get("inverse"))
    created = _tuple_of_maps(side.get("created"))
    revertable = str(side.get("revertable") or spec.revertable)
    reason = str(side.get("reason") or "")
    if not ok:
        # Nothing changed, so there is nothing to undo — but it is still recorded.
        revertable, reason = REVERT_NONE, error.strip() or reason
    elif revertable == REVERT_AUTO and not inverse and not created:
        # The spec's ceiling says this is undoable in principle, but this call
        # produced neither an inverse nor an identified creation — so it is not.
        # Older listeners send no sidecar at all and land here.
        revertable = REVERT_MANUAL
        reason = reason or "the editor did not report how to undo this"
    elif spec.mutates == MUT_OPAQUE and not side:
        reason = reason or spec.note or "this command's effects cannot be modelled"

    kind = str(side.get("kind") or spec.kind)
    facet = str(side.get("facet") or spec.slot)
    return EditorChange(
        command=command,
        kind=kind,
        slot=slot_path(kind, _target_id(targets, params), facet),
        outcome=outcome,
        params=params,
        spec=spec,
        targets=tuple(targets),
        before=side.get("before"),
        after=side.get("after"),
        inverse=inverse,
        created=created,
        revertable=revertable,
        reason=reason,
        summary=str(side.get("summary") or command.replace("_", " ")),
        writer=identity.current_writer(tool=command),
    )


def record(
    command: str,
    params: Mapping[str, Any] | None,
    body: Mapping[str, Any] | None,
    *,
    ok: bool,
    error: str = "",
) -> EditorChange | None:
    """Called by the bridge for every listener command. Never raises."""
    try:
        change = build(command, params, (body or {}).get("_ducky"), ok=ok, error=error)
        if change is None:
            return None
        log.debug(
            "workspace.editor.%s command=%s slot=%s revertable=%s run=%s",
            change.outcome, change.command, change.slot, change.revertable,
            change.writer.get("run_id", ""),
        )
        for observer in list(_observers):
            try:
                observer.on_editor_change(change)
            except Exception:  # noqa: BLE001 - an observer must not break the command
                log.warning("editor observer %r failed", observer, exc_info=True)
        return change
    except Exception:  # noqa: BLE001 - the command already ran; never fail it now
        log.debug("editor change recording failed for %s", command, exc_info=True)
        return None


def sidecar_from(body: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """The listener's capture payload, if this build of the listener sends one."""
    side = (body or {}).get("_ducky")
    return side if isinstance(side, Mapping) else None


_ObserverFactory = Callable[[], EditorObserver]
