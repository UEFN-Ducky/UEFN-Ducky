"""Locate Epic urc.exe / lore.exe on Windows."""

from __future__ import annotations

import os
from pathlib import Path

_DRIVE_LETTERS = "CDEFGHIJKLMNOPQRSTUVWXYZ"
_WIN64 = Path("Epic Games") / "Fortnite" / "FortniteGame" / "Binaries" / "Win64"


def _detect(exe_name: str, env_keys: tuple[str, ...]) -> dict[str, object]:
    for key in env_keys:
        val = os.environ.get(key, "").strip()
        if val:
            p = Path(val)
            if p.is_file():
                return {"available": True, "path": str(p), "source": key.lower(), "error": ""}

    suffix = _WIN64 / exe_name
    for env_name in ("ProgramW6432", "ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env_name)
        if base:
            candidate = Path(base) / suffix
            if candidate.is_file():
                return {"available": True, "path": str(candidate), "source": env_name, "error": ""}

    for letter in _DRIVE_LETTERS:
        candidate = Path(f"{letter}:/Program Files") / suffix
        if candidate.is_file():
            return {"available": True, "path": str(candidate), "source": "program_files_scan", "error": ""}

    return {
        "available": False,
        "path": "",
        "source": "",
        "error": f"{exe_name} not found — set {env_keys[0]} or install Fortnite/UEFN",
    }


def detect_urc_executable() -> dict[str, object]:
    """Return {available, path, source, error}.

    No lore.exe fallback: callers run urc-only commands (``project status --json``, ``trigger``).
    """
    return _detect("urc.exe", ("URC_CLI_LOCATION", "URC_PATH"))


def detect_lore_executable() -> dict[str, object]:
    """Return {available, path, source, error} for the Lore CLI UEFN ships next to urc.exe."""
    return _detect("lore.exe", ("LORE_CLI_LOCATION",))
