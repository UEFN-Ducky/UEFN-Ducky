"""Deploy listener hooks and quarantine island .py on every recently opened project."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from frontend.deploy import deploy_listener, resolve_uefn_project_root
from frontend.error_log import record_error
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings
from frontend.ui_web.recent_projects import add_recent_project, load_recent_projects


def deploy_all_recent_projects(*, log: Callable[[str], None] | None = None) -> list[str]:
    """Refresh listener hooks and quarantine island .py on known project paths."""
    lines: list[str] = []
    paths: list[str] = []
    seen: set[str] = set()

    for path in load_recent_projects():
        norm = _safe_normalize(path)
        if norm and norm not in seen:
            seen.add(norm)
            paths.append(norm)

    current = _safe_normalize(PanelSettings.load().uefn_project_root)
    if current and current not in seen:
        paths.insert(0, current)

    for path in paths:
        try:
            root = resolve_uefn_project_root(Path(path))
            project_lines = deploy_listener(root, PANEL_LISTENER_PORT)
            for ln in project_lines:
                lines.append(ln)
                if log:
                    log(ln)
            add_recent_project(str(root))
        except Exception as e:
            msg = f"Deploy skipped for {path}: {e}"
            lines.append(msg)
            record_error("deploy", msg)
            if log:
                log(msg)

    return lines


def _safe_normalize(path: str) -> str:
    raw = (path or "").strip()
    if not raw:
        return ""
    try:
        return str(resolve_uefn_project_root(Path(raw)))
    except (OSError, ValueError):
        try:
            return str(Path(raw).resolve())
        except OSError:
            return raw
