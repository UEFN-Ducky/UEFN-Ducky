"""Locate Epic urc.exe on Windows."""

from __future__ import annotations

import os
from pathlib import Path

_DRIVE_LETTERS = "CDEFGHIJKLMNOPQRSTUVWXYZ"
_SUFFIX = Path("Epic Games") / "Fortnite" / "FortniteGame" / "Binaries" / "Win64" / "urc.exe"


def detect_urc_executable() -> dict[str, object]:
    """Return {available, path, source, error}."""
    env_keys = ("URC_CLI_LOCATION", "URC_PATH")
    for key in env_keys:
        val = os.environ.get(key, "").strip()
        if val:
            p = Path(val)
            if p.is_file():
                return {"available": True, "path": str(p), "source": key.lower(), "error": ""}

    for env_name in ("ProgramW6432", "ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env_name)
        if base:
            candidate = Path(base) / _SUFFIX
            if candidate.is_file():
                return {"available": True, "path": str(candidate), "source": env_name, "error": ""}

    for letter in _DRIVE_LETTERS:
        candidate = Path(f"{letter}:/Program Files") / _SUFFIX
        if candidate.is_file():
            return {"available": True, "path": str(candidate), "source": "program_files_scan", "error": ""}

    return {
        "available": False,
        "path": "",
        "source": "",
        "error": "urc.exe not found — set URC_CLI_LOCATION or install Fortnite/UEFN",
    }
