"""Resolve the active UEFN project slug for AppData cache isolation."""

from __future__ import annotations


def current_project_cache_slug() -> str:
    """Return ``project_slug`` for the panel's active project (or ``_no_project``)."""
    try:
        from frontend.settings import PanelSettings
        from frontend.ui_web.project_chats import project_slug

        root = (PanelSettings.load().uefn_project_root or "").strip()
        return project_slug(root)
    except Exception:
        return "_no_project"
