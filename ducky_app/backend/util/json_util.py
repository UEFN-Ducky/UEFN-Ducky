"""Compact JSON serialization for MCP tool responses."""

from __future__ import annotations

import json
from typing import Any


def tool_json(obj: Any, *, pretty: bool = False) -> str:
    """Serialize tool output; compact by default to reduce token usage."""
    return json.dumps(
        obj,
        separators=(",", ":"),
        ensure_ascii=False,
        indent=2 if pretty else None,
    )
