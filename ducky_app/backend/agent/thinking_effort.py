"""Shared thinking-effort levels for API / coding-agent turns.

Host-owned so runners can normalize UI values without importing a vendor plugin.
"""

from __future__ import annotations

# Map UI effort levels → provider budget tokens (Anthropic-style).
EFFORT_BUDGET: dict[str, int] = {
    "low": 2048,
    "medium": 8192,
    "high": 16384,
}


def normalize_thinking_effort(value: str | None) -> str:
    v = (value or "").strip().lower()
    if v in ("", "off", "none", "0"):
        return "off"
    if v in EFFORT_BUDGET:
        return v
    return "off"
