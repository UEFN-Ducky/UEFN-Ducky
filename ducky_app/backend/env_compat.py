"""Read UEFN Ducky environment variables."""

from __future__ import annotations

import os


def env_str(ducky_suffix: str, default: str = "") -> str:
    val = os.environ.get(f"UEFN_DUCKY_{ducky_suffix}", "").strip()
    return val if val else default


def env_flag(ducky_suffix: str) -> bool:
    return env_str(ducky_suffix).lower() in ("1", "true", "yes")


def env_int(ducky_suffix: str, default: int) -> int:
    raw = env_str(ducky_suffix)
    if not raw:
        return default
    return int(raw)
