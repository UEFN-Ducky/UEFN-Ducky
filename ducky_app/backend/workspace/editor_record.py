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

import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

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
    with _opaque_lock:
        _opaque_counts.clear()


def _outcome(ok: bool, error: str) -> str:
    if ok:
        return OUTCOME_OK
    lowered = (error or "").lower()
    if any(marker in lowered for marker in _REFUSAL_MARKERS):
        return OUTCOME_BLOCKED
    return OUTCOME_FAILED


#: Per-run numbering for opaque calls, so two scripts in one run read as two rows.
_opaque_counts: dict[str, int] = {}
_opaque_lock = threading.Lock()
_opaque_fallback = itertools.count(1)
#: Runs remembered for numbering. Old ones are dropped; the number only has to be
#: unique within the run it appears in.
_OPAQUE_RUNS_KEPT = 256


def _opaque_slot(command: str, run_id: str) -> str:
    """A slot for one opaque call.

    Every other slot is a target and a facet, so repeated edits to one actor
    collapse into a single row and a single restore. An opaque command has no
    target it can name — running a script twice is two events, not one target
    edited twice — so each call gets its own slot and its own row.
    """
    if not run_id:
        return slot_path("opaque", f"{command}/user-{next(_opaque_fallback)}")
    with _opaque_lock:
        count = _opaque_counts.get(run_id, 0) + 1
        _opaque_counts[run_id] = count
        if len(_opaque_counts) > _OPAQUE_RUNS_KEPT:
            for stale in list(_opaque_counts)[: len(_opaque_counts) - _OPAQUE_RUNS_KEPT]:
                _opaque_counts.pop(stale, None)
    return slot_path("opaque", f"{command}/{run_id}-{count}")


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
    writer = identity.current_writer(tool=command)
    slot = (
        _opaque_slot(command, str(writer.get("run_id") or ""))
        if spec.mutates == MUT_OPAQUE
        else slot_path(kind, _target_id(targets, params), facet)
    )
    return EditorChange(
        command=command,
        kind=kind,
        slot=slot,
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
        writer=writer,
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


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-safe copy; unknown objects become their repr."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return repr(value)


def _dumps(value: Any) -> str:
    try:
        return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)
    except Exception:  # noqa: BLE001 - a blob is never worth failing a record over
        return json.dumps({"unserializable": repr(value)[:2000]})


class JournalEditorObserver:
    """Write each editor change into the run's changeset ledger.

    Goes straight to the journal rather than through ``ProjectWriter``: an editor
    slot is not a file, and the pipeline's observers (file history, follow-code
    editor sync) must never be handed a ``uefn://`` pseudo-path.

    Blocked and failed attempts are recorded too. They carry no after-blob and
    the journal never reverts or indexes them, but they are what turns the
    history from a list of successes into an account of what actually happened.
    """

    def on_editor_change(self, change: EditorChange) -> None:
        from backend.workspace import events
        from backend.workspace.journal import OP_EDITOR, FileChangeJournal
        from backend.workspace.paths import content_hash
        from backend.workspace.policy import ALLOW
        from backend.workspace.runtime import get_writer
        from backend.workspace.writer import WriteRecord

        if not change.writer.get("run_id"):
            # Not part of an agent run — the panel's own editor calls are the user.
            return
        writer = get_writer()
        journal = writer.journal
        if not isinstance(journal, FileChangeJournal):
            return
        try:
            root = writer.root()
        except Exception:
            return

        before_json = _dumps(change.before) if change.before is not None else ""
        after_json = _dumps(
            {"params": _jsonable(change.params), "after": _jsonable(change.after),
             "created": _jsonable(list(change.created))}
        ) if change.applied else ""

        record = WriteRecord(
            op=OP_EDITOR,
            path=change.slot,
            from_path="",
            before=before_json,
            after=after_json,
            before_hash=content_hash(before_json) if before_json else "",
            after_hash=content_hash(after_json) if after_json else "",
            existed_before=change.before is not None,
            tool=change.command,
            writer=change.writer,
            ctx=identity.resolve_context(),
            ts=time.time(),
            lines_added=0,
            lines_removed=0,
            decision=ALLOW,          # lanes do not cover editor targets (ADR 0002)
            project_root=root,
            abs_path="",
            editor=self._payload(change),
            outcome=change.outcome,
            reason=change.reason,
        )
        journal.record(record)

        target_label = ""
        if change.targets:
            first = change.targets[0]
            target_label = str(first.get("label") or first.get("path") or first.get("id") or "")
        events.emit(
            events.EditorOpEvent(
                command=change.command,
                kind=change.kind,
                slot=change.slot,
                summary=change.summary,
                revertable=change.revertable,
                outcome=change.outcome,
                target_label=target_label,
                conv_id=str(change.writer.get("conv_id") or ""),
                run_id=str(change.writer.get("run_id") or ""),
                tool=change.command,
            ).to_dict()
        )

    @staticmethod
    def _payload(change: EditorChange) -> dict[str, Any]:
        return {
            "command": change.command,
            "kind": change.kind,
            "facet": change.spec.slot,
            "targets": _jsonable(list(change.targets)),
            "inverse": _jsonable(list(change.inverse)),
            "created": _jsonable(list(change.created)),
            "revertable": change.revertable,
            "reason": change.reason,
            "summary": change.summary,
        }
