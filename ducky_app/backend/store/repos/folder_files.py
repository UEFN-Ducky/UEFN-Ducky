"""Team folders' tables code ships with Ducky Account (the tables' migrations stay here).
This module path stays so every caller and plugin that imports it keeps working: each name
forwards to :data:`backend.uefn_plugins.team_engine.folder_files` (a safe default without
the engine).
"""

from __future__ import annotations

from typing import Any

from backend.uefn_plugins.team_engine import folder_files as _area


def __getattr__(name: str) -> Any:
    return getattr(_area, name)
