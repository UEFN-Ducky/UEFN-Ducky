"""Central AppData / project paths for UEFN Ducky."""

from __future__ import annotations

import os
from pathlib import Path

APP_DATA_DIR_NAME = "UEFN-Ducky"
PROJECT_DOT_DIR = ".uefn-ducky"
EXE_PREFIX = "UEFN-Ducky"


def local_app_data_base() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if base:
        return Path(base)
    return Path.home()


def resolve_app_data_dir(*, for_write: bool = False) -> Path:
    """Return ``%LOCALAPPDATA%/UEFN-Ducky``."""
    path = local_app_data_base() / APP_DATA_DIR_NAME
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_project_dot_dir(project_root: str, *, for_write: bool = False) -> Path:
    """Per-project agent folder: ``<project>/.uefn-ducky``."""
    path = Path(project_root) / PROJECT_DOT_DIR
    if for_write:
        path.mkdir(parents=True, exist_ok=True)
    return path


def exe_name_matches(name: str) -> bool:
    """True if filename is ``UEFN-Ducky*.exe`` but not the Setup host."""
    lower = name.lower()
    if not lower.endswith(".exe") or not lower.startswith(EXE_PREFIX.lower()):
        return False
    stem = lower[:-4]
    # Custom Setup host is published as UEFN-Ducky-Setup-<ver>.exe — the panel
    # sweep must not kill it (or Setup-engine.exe, which does not match anyway).
    if stem == "uefn-ducky-setup" or stem.startswith("uefn-ducky-setup-"):
        return False
    return True


def process_name_matches(process_name: str) -> bool:
    """Windows ``ProcessName`` without ``.exe`` suffix."""
    n = process_name.lower()
    pl = EXE_PREFIX.lower()
    if n == "uefn-ducky-setup" or n.startswith("uefn-ducky-setup-"):
        return False
    return n == pl or n.startswith(f"{pl}-") or n.startswith(f"{pl}_")


def kill_process_ps_filter() -> str:
    """PowerShell ``Where-Object`` filter for UEFN Ducky processes."""
    return (
        f"($_.ProcessName -like '{EXE_PREFIX}*' "
        f"-and $_.ProcessName -notlike '{EXE_PREFIX}-Setup*')"
    )
