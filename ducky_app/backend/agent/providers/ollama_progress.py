"""Read-only Ollama prompt-eval progress for the host activity footer.

Does not touch the inference request. Used so Waiting… N% works even when the
Ollama plugin is older than the progress-emitting build.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from backend.agent.providers.wait_status import clamp_percent, format_wait_status

_PROGRESS_RE = re.compile(r"progress\s*=\s*(0\.\d+|1\.0+)")


def ollama_prompt_eval_fraction() -> float | None:
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if not local:
        return None
    path = Path(local) / "Ollama" / "server.log"
    if not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 24576))
            tail = fh.read().decode("utf-8", "replace")
    except Exception:
        return None
    matches = _PROGRESS_RE.findall(tail)
    if not matches:
        return None
    try:
        return max(0.0, min(1.0, float(matches[-1])))
    except ValueError:
        return None


def ollama_wait_status(*, step: int | None = None) -> tuple[str, float | None]:
    """Return (status_text, percent_0_100) for the chat footer."""
    frac = ollama_prompt_eval_fraction()
    pct = clamp_percent(frac)
    detail = f"step {step}" if step and step > 0 else ""
    return format_wait_status(label="Waiting", percent=pct, detail=detail), pct
