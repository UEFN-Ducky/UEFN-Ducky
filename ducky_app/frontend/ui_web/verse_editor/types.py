"""Shared types for verse editor events."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EditorRange:
    start_line: int
    start_col: int
    end_line: int
    end_col: int


@dataclass
class EditorAction:
    type: str
    path: str
    range: EditorRange | None = None
    text: str | None = None
    style: str | None = None
    duration_ms: int | None = None
    activate: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "path": self.path}
        if self.range is not None:
            out["range"] = {
                "startLine": self.range.start_line,
                "startCol": self.range.start_col,
                "endLine": self.range.end_line,
                "endCol": self.range.end_col,
            }
        if self.text is not None:
            out["text"] = self.text
        if self.style is not None:
            out["style"] = self.style
        if self.duration_ms is not None:
            out["durationMs"] = self.duration_ms
        if self.activate is not None:
            out["activate"] = self.activate
        return out


@dataclass
class EditorBatch:
    actions: list[EditorAction] = field(default_factory=list)
    conv_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "actions": [a.to_dict() for a in self.actions],
            "conv_id": self.conv_id,
        }
