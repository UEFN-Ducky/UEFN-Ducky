"""Typed UI/log events raised by the write pipeline, fanned out to registered sinks."""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], None]

FILE_GUARD = "file_guard"
FILES_REVERTED = "files_reverted"
LANE_CHANGED = "lane_changed"

GUARD_LANE_DENIED = "lane_denied"
GUARD_SHADOW_VIOLATION = "shadow_violation"
GUARD_CONFLICT = "conflict"
GUARD_POLICY_DENIED = "policy_denied"


@dataclass(frozen=True)
class FileGuardEvent:
    kind: str
    path: str
    text: str
    conv_id: str = ""
    run_id: str = ""
    tool: str = ""
    lane: tuple[str, ...] = ()
    leader_conv_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["lane"] = list(self.lane)
        d["type"] = FILE_GUARD
        return d


@dataclass(frozen=True)
class FilesRevertedEvent:
    paths: tuple[str, ...]
    run_id: str = ""
    conv_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": FILES_REVERTED,
            "paths": list(self.paths),
            "run_id": self.run_id,
            "conv_id": self.conv_id,
        }


@dataclass(frozen=True)
class LaneChangedEvent:
    group_id: str
    member_conv_id: str
    write_allowed: tuple[str, ...] | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": LANE_CHANGED,
            "group_id": self.group_id,
            "member_conv_id": self.member_conv_id,
            "write_allowed": None if self.write_allowed is None else list(self.write_allowed),
        }


_sinks: list[EventSink] = []
_lock = threading.Lock()


def register_sink(sink: EventSink) -> None:
    with _lock:
        if sink not in _sinks:
            _sinks.append(sink)


def unregister_sink(sink: EventSink) -> None:
    with _lock:
        if sink in _sinks:
            _sinks.remove(sink)


def emit(event: dict[str, Any]) -> None:
    """Deliver to every sink; a failing sink is logged and never blocks a write."""
    with _lock:
        sinks = list(_sinks)
    for sink in sinks:
        try:
            sink(event)
        except Exception:  # noqa: BLE001 - sinks are UI glue, never fatal
            log.warning("workspace event sink failed", exc_info=True)


def reset_for_tests() -> None:
    with _lock:
        _sinks.clear()
