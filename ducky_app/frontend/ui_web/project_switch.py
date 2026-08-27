"""Panel project root — shared by UI API and ducky MCP tools.

`switch_panel_project` is the ONE entry point for changing the active project. Both the
header dropdown (PanelApi.set_project_root) and the ducky_set_project MCP tool go through
it so the UI and the AI take the exact same path — settings + recent + project_changed
push + a deduplicated one-time deploy.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from frontend.deploy import resolve_uefn_project_root
from frontend.settings import PanelSettings, apply_workspace_env
from frontend.ui_web.project_chats import project_display_name, project_slug
from frontend.ui_web.recent_projects import (
    add_recent_project,
    load_recent_projects,
    remove_recent_project,
)

# Roots whose one-time deploy (Python quarantine + skill sync) already ran in this
# process — so switching back and forth, from either the UI or MCP, never redoes it.
_deployed_roots: set[str] = set()
_deploy_lock = threading.Lock()


def normalize_project_path(path: str) -> str:
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


def get_panel_project_info() -> dict[str, str]:
    root = normalize_project_path(PanelSettings.load().uefn_project_root.strip())
    return {
        "path": root,
        "name": project_display_name(root),
        "slug": project_slug(root),
    }


def list_panel_projects() -> list[dict[str, str | bool]]:
    current = normalize_project_path(PanelSettings.load().uefn_project_root)
    out: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    for path in load_recent_projects():
        norm = normalize_project_path(path)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(
            {
                "path": norm,
                "name": project_display_name(norm),
                "slug": project_slug(norm),
                "active": norm == current,
            }
        )
    if current and current not in seen:
        out.insert(
            0,
            {
                "path": current,
                "name": project_display_name(current),
                "slug": project_slug(current),
                "active": True,
            },
        )
    return out


def _push_project_changed(info: dict[str, str]) -> None:
    try:
        from frontend.ui_web.verse_editor.panel_events import push_agent_event

        push_agent_event({"type": "project_changed", "project": info})
    except Exception:
        pass


def set_panel_project_root(path: str, *, push_ui: bool = True) -> dict[str, str]:
    """Switch the panel's active UEFN project (same as the header project dropdown)."""
    s = PanelSettings.load()
    raw = (path or "").strip()
    if raw:
        s.uefn_project_root = str(resolve_uefn_project_root(Path(raw)))
    else:
        s.uefn_project_root = ""
    s.validate()
    s.save()
    apply_workspace_env(s.uefn_project_root)
    try:
        from frontend.ui_web.project_files import _invalidate_file_paths_cache

        _invalidate_file_paths_cache()
    except Exception:
        pass
    try:
        from frontend.ui_web.verse_editor.lsp.diagnostics_cache import clear as clear_diag_cache

        clear_diag_cache(s.uefn_project_root or None)
    except Exception:
        pass
    if s.uefn_project_root:
        add_recent_project(s.uefn_project_root)
    info = get_panel_project_info()
    if push_ui:
        _push_project_changed(info)
    return info


def _run_project_deploy(root: str, on_log: Callable[[str], None] | None = None) -> None:
    from frontend.deploy import deploy_listener
    from frontend.error_log import record_error
    from frontend.settings import PANEL_LISTENER_PORT

    try:
        lines = deploy_listener(Path(root), PANEL_LISTENER_PORT)
        if on_log:
            for ln in lines:
                on_log(ln)
    except Exception as e:  # noqa: BLE001 — deploy is best-effort; surface via error log
        record_error("deploy", str(e))
        # Failed deploy: drop the flag so the next switch retries instead of skipping.
        with _deploy_lock:
            _deployed_roots.discard(root)


def _deploy_project_once(
    root: str, *, background: bool, on_log: Callable[[str], None] | None = None
) -> None:
    with _deploy_lock:
        if root in _deployed_roots:
            return
        _deployed_roots.add(root)
    if background:
        threading.Thread(
            target=_run_project_deploy, args=(root, on_log), daemon=True, name="deploy-listener"
        ).start()
    else:
        _run_project_deploy(root, on_log)


def switch_panel_project(
    *,
    path: str = "",
    name: str = "",
    push_ui: bool = True,
    deploy: bool = True,
    background_deploy: bool = False,
    on_deploy_log: Callable[[str], None] | None = None,
) -> dict[str, str]:
    """The one project-switch entry point (header dropdown + ducky_set_project MCP tool).

    Resolves a `path` or recent `name` (empty → clear the active project), updates settings
    + recent list, pushes the ``project_changed`` UI event, and runs the one-time-per-project
    deploy (deduplicated). ``background_deploy=True`` keeps the deploy off the caller's thread
    so the UI switch returns instantly; MCP callers run it inline so the listener
    hooks and Python quarantine finish before the tool returns. Returns the new
    project info (path/name/slug).
    """
    if path.strip() or name.strip():
        resolved = resolve_panel_project_ref(path=path, name=name)
    else:
        resolved = ""  # clear the active project
    info = set_panel_project_root(resolved, push_ui=push_ui)
    root = info.get("path", "")
    if deploy and root:
        _deploy_project_once(root, background=background_deploy, on_log=on_deploy_log)
    return info


def resolve_panel_project_ref(*, path: str = "", name: str = "") -> str:
    """Resolve a project path or recent display name to a normalized root."""
    raw_path = (path or "").strip()
    if raw_path:
        norm = normalize_project_path(raw_path)
        if not norm:
            raise ValueError(f"Invalid project path: {path!r}")
        return norm

    raw_name = (name or "").strip()
    if not raw_name:
        raise ValueError("Provide path or name")

    key = raw_name.casefold()
    for item in list_panel_projects():
        candidates = (
            str(item.get("path", "")),
            str(item.get("name", "")),
            str(item.get("slug", "")),
        )
        for candidate in candidates:
            if not candidate:
                continue
            if candidate.casefold() == key or Path(candidate).name.casefold() == key:
                return str(item["path"])
    raise ValueError(f"Unknown project {name!r} — call ducky_list_projects for recent paths")


def delete_panel_project(path: str, *, push_ui: bool = True) -> dict[str, str]:
    """Remove a project from recents, AppData, and the Ducky init script (not the project folder)."""
    norm = normalize_project_path(path)
    if not norm:
        raise ValueError("Invalid project path")

    slug = project_slug(norm)
    current = normalize_project_path(PanelSettings.load().uefn_project_root)
    was_active = norm == current

    remove_recent_project(norm)
    with _deploy_lock:
        _deployed_roots.discard(norm)

    from frontend.appdata_maintenance import delete_project_appdata
    from frontend.deploy import quarantine_project_python, remove_project_init

    delete_project_appdata(slug)
    quarantine_project_python(Path(norm), deep=True)
    remove_project_init(Path(norm))

    if was_active:
        next_path = load_recent_projects()[0] if load_recent_projects() else ""
        info = set_panel_project_root(next_path, push_ui=push_ui)
    else:
        info = get_panel_project_info()

    return info
