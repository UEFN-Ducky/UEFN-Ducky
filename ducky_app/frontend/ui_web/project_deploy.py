"""Deploy listener hooks on every UEFN island Ducky knows.

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
from frontend.settings import UEFN_LISTENER_PORT, PanelSettings
from frontend.ui_web.known_projects import known_project_roots
from frontend.ui_web.project_switch import mark_deployed

# Island path -> the failure last reported for it. A deploy that keeps failing the same way
# is reported once, not on every pass (Nov 11 2026: one PC sent 115 deploy errors in a day,
# 64% of that day's error rows). A new error, or a pass that works, clears it.
_reported: dict[str, str] = {}


def _report_once(path: str, msg: str) -> None:
    """Error-log row plus one alert for a failure this island has not shown before."""
    if _reported.get(path) == msg:
        return
    _reported[path] = msg
    record_error("deploy", msg)
    try:
        from frontend import notifications

        notifications.alert(
            "UEFN Ducky", "Ducky can't set up an island's UEFN link.", level="error", detail=msg,
        )
    except Exception:
        pass


def deploy_all_recent_projects(*, log: Callable[[str], None] | None = None) -> list[str]:
    """Refresh the listener hooks on every known island (every start; a write refused before
    lands now). A failure is reported once per island and error (``_report_once``)."""
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
            project_lines = deploy_listener(root, UEFN_LISTENER_PORT, shared=(i == 0))
            mark_deployed(path)
            _reported.pop(path, None)
            for ln in project_lines:
                lines.append(ln)
                if log:
                    log(ln)
        except Exception as e:
            msg = f"Deploy skipped for {path}: {e}"
            if isinstance(e, PermissionError):
                from frontend.folder_access import refusal_hint

                msg = f"{msg} {refusal_hint(getattr(e, 'filename', None) or path)}".rstrip()
            lines.append(msg)
            _report_once(path, msg)
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
