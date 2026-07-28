"""Read user-written ducky details (personality / when_to_use) — the only role signal."""

from __future__ import annotations

from typing import Any, Iterable


def profile_detail_text(conv: Any) -> str:
    """Concat personality + when_to_use from a conversation or profile-like object."""
    parts = [
        str(getattr(conv, "ducky_personality", "") or ""),
        str(getattr(conv, "when_to_use", "") or ""),
    ]
    return "\n".join(p.strip() for p in parts if str(p).strip())


def tools_named_in_profile(conv: Any, catalog: Iterable[str]) -> set[str]:
    """Exact tool names from ``catalog`` that appear in the profile details text."""
    text = profile_detail_text(conv)
    if not text:
        return set()
    return {name for name in catalog if name and name in text}
