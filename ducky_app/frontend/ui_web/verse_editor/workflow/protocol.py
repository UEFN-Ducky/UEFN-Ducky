"""Verse Workflow protocol types — matches Epic extension protocol.js."""

from __future__ import annotations

from enum import IntEnum


class MessageType(IntEnum):
    Notification = 0
    Request = 1
    Response = 2


class BuildState(IntEnum):
    Success = 0
    Warnings = 1
    Errors = 2
    Building = 3
    NoBuild = 4


class Severity(IntEnum):
    Error = 1
    Warning = 2
    Info = 3
    Log = 4


class NoParams:
    pass
