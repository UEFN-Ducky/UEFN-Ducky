"""Shared tool-result size limits (no MCP / serialization imports)."""
from __future__ import annotations

API_TOOL_RESULT_MAX = 2200
TOOL_RESULT_MAX_OVERRIDES = {"workspace_list_verse_errors": 9000}


def tool_result_max(tool_name: str) -> int:
    return TOOL_RESULT_MAX_OVERRIDES.get(tool_name, API_TOOL_RESULT_MAX)
