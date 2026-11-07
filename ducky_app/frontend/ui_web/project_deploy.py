"""Deploy listener hooks and quarantine island .py on every UEFN island Ducky knows.

That is every island the person opened and every one found on disk
(``known_projects``), so a new island is ready on the next boot without anyone adding
it. Folder projects (no ``*.uefnproject``) are skipped outright: they get no listener
init and keep every Python file they have.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from frontend.deploy import deploy_listener, resolve_uefn_project_root
from frontend.error_log import record_error
from frontend.project_kind import has_uefnproject
from frontend.settings import PANEL_LISTENER_PORT, PanelSettings
from frontend.ui_web.known_projects import known_project_roots
from frontend.ui_web.project_switch import mark_deployed


def deploy_all_recent_projects(*, log: Callable[[str], None] | None = None) -> list[str]:
    """Refresh listener hooks and quarantine island .py on every known island.

    Only the selected island gets the deep sweep (every folder); the others get the shallow
    one (root, Content/Python, .ducky) and their deep sweep when the person switches to them.
    """
    lines: list[str] = []
    paths: list[str] = []
    seen: set[str] = set()

    for path in known_project_roots(rescan=True):
        norm = _safe_normalize(path)
        if norm and norm not in seen:
            seen.add(norm)
            paths.append(norm)

    current = _safe_normalize(PanelSettings.load().uefn_project_root)
    if current and current not in seen:
        paths.insert(0, current)

    paths = [path for path in paths if has_uefnproject(path)]
    for i, path in enumerate(paths):
        try:
            root = resolve_uefn_project_root(Path(path))
            project_lines = deploy_listener(root, PANEL_LISTENER_PORT, shared=(i == 0), deep_sweep=(path == current))
            mark_deployed(path)
            for ln in project_lines:
                lines.append(ln)
                if log:
                    log(ln)
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
