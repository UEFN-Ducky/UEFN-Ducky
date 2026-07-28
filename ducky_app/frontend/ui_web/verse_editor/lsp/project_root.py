"""Canonical UEFN project root — same rules as file I/O."""

from __future__ import annotations

from pathlib import Path

from frontend.deploy import resolve_uefn_project_root


def normalize_verse_lsp_project_root(project_root: str) -> str:
    """Resolve settings path to the project directory (handles .uefnproject file selection)."""
    raw = (project_root or "").strip()
    if not raw:
        return ""
    try:
        return str(resolve_uefn_project_root(Path(raw)))
    except (OSError, ValueError):
        return str(Path(raw).resolve())
