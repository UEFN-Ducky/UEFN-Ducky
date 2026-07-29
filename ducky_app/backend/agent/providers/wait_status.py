"""Shared wait / prompt-eval status for every LLM gateway plugin.

Plugins yield ``StreamEvent(kind=STATUS, text=..., percent=...)`` using these
helpers. The host forwards them to the chat UI so all gateways render the same
``Waiting… 69%`` line (elapsed time is shown by the core activity timer).
"""

from __future__ import annotations


def clamp_percent(percent: float | int | None) -> float | None:
    if percent is None:
        return None
    try:
        value = float(percent)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    # Accept 0–1 fractions or 0–100 percentages.
    if 0.0 <= value <= 1.0:
        value *= 100.0
    return max(0.0, min(100.0, value))


def format_wait_status(
    *,
    label: str = "Waiting",
    percent: float | int | None = None,
    detail: str = "",
) -> str:
    """Human status line for the chat activity footer.

    Examples:
      Waiting…
      Waiting… 69%
      Waiting… 69% · 21,504 tokens
    """
    base = (label or "Waiting").strip().rstrip(".…") or "Waiting"
    parts = [f"{base}…"]
    pct = clamp_percent(percent)
    if pct is not None:
        parts.append(f"{int(round(pct))}%")
    text = " ".join(parts)
    detail_text = (detail or "").strip()
    if detail_text:
        # No parentheses — "Waiting… 69% · step 1" (or "Waiting… step 1" if no %).
        sep = " · " if pct is not None else " "
        text = f"{text}{sep}{detail_text}"
    return text
