"""How long agent runs took, so a run whose agent named no length still gets a progress bar.

Keyed by (tool, first 80 characters of the command with numbers stripped): the last few
real durations each, in one small JSON next to Ducky's other per-user state.
"""

from __future__ import annotations

import json
import os
import re
import statistics
import threading
from pathlib import Path

_KEEP = 5
# ponytail: oldest commands drop past this many; the file stays a few KB.
_MAX_KEYS = 200
_lock = threading.Lock()


def _path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "UEFN-Ducky" / "run_durations.json"


def _key(tool: str, command: str) -> str:
    return f"{tool}|{re.sub(r'[0-9]+', '', (command or '').strip()[:80])}"


def _load() -> dict[str, list[float]]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record(tool: str, command: str, seconds: float) -> None:
    key = _key(tool, command)
    with _lock:
        data = _load()
        runs = [*data.pop(key, []), round(float(seconds), 2)][-_KEEP:]
        data[key] = runs  # newest last: the oldest key is the first one dropped
        for old in list(data)[:-_MAX_KEYS]:
            data.pop(old, None)
        try:
            path = _path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # a progress estimate is never worth failing a run


def expected_ms(tool: str, command: str, expect_minutes: float = 0.0) -> tuple[int, bool]:
    """(expected run length in ms, estimated). (0, False) when nothing is known."""
    if expect_minutes and expect_minutes > 0:
        return int(round(float(expect_minutes) * 60_000)), False
    with _lock:
        runs = [r for r in _load().get(_key(tool, command), []) if isinstance(r, (int, float))]
    return (int(round(statistics.median(runs) * 1000)), True) if runs else (0, False)
