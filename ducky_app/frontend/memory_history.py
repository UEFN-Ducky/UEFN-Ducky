"""Local memory history: ten sessions, at most two 1 MiB files per session.

Each sample is appended once, without keeping the history in RAM. Only numeric
measurements and the session identity are saved; no chats or process arguments.
The most recent chunk and its predecessor survive a restart. This is independent
of boot.json and is never added to the crash report sent to the server.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

MAX_FILE_BYTES = 1024 * 1024
KEEP_SESSIONS = 10
_NAME = re.compile(r"^memory-(\d{20}-\d+)(?:-previous)?\.jsonl$")


def append(folder: Path, session: dict[str, Any], beat: dict[str, Any]) -> None:
    """Append a sample; filesystem errors are handled by the heartbeat caller."""
    key = f"{int(float(session['started']) * 1_000_000):020d}-{int(session['pid'])}"
    sample = {k: v for k, v in beat.items() if isinstance(v, (int, float))}
    sample.update(version=str(session.get("version", ""))[:64],
                  pid=int(session["pid"]), started=float(session["started"]))
    data = (json.dumps(sample, separators=(",", ":")) + "\n").encode("utf-8")
    if len(data) > MAX_FILE_BYTES:
        return
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"memory-{key}.jsonl"
    size = path.stat().st_size if path.exists() else 0
    rotated = size + len(data) > MAX_FILE_BYTES
    if rotated:
        path.replace(folder / f"memory-{key}-previous.jsonl")
    with path.open("ab") as stream:
        stream.write(data)
    if size == 0 or rotated:
        _prune(folder, key)


def _prune(folder: Path, current: str) -> None:
    sessions: dict[str, list[Path]] = {}
    for path in folder.iterdir():
        match = _NAME.fullmatch(path.name)
        if match and path.is_file():
            sessions.setdefault(match[1], []).append(path)
    # Always retain this writer's session, even if the system clock moved back.
    keep = {current, *sorted((key for key in sessions if key != current), reverse=True)[:KEEP_SESSIONS - 1]}
    for key, paths in sessions.items():
        if key not in keep:
            for path in paths:
                path.unlink(missing_ok=True)
