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

from frontend.project_kind import (
    UEFN,
    content_root_for,
    forget_cached_kind,
    has_uefnproject,
    is_island_identity,
    island_mount_name,
    project_flavor,
    project_kind,
    project_root_path,
    resolve_project_root,
)
from frontend.settings import PanelSettings, apply_workspace_env
from frontend.ui_web.chats.store import project_display_name, project_slug
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
        return str(project_root_path(raw))
    except (OSError, ValueError):
        try:
            return str(Path(raw).resolve())
        except OSError:
            return raw


def _mount_for(root: str, kind: str) -> str:
    """The island's content mount (root plugin name) — what UEFN reports as the live
    project. Empty for a folder project or an empty root."""
    if not root or kind != UEFN:
        return ""
    try:
        return island_mount_name(root)
    except Exception:
        return ""


def get_panel_project_info() -> dict[str, str]:
    root = normalize_project_path(PanelSettings.load().uefn_project_root.strip())
    kind = project_kind(root)
    return {
        "path": root,
        "name": project_display_name(root),
        "slug": project_slug(root),
        # 'uefn' island or plain 'folder'; content_root is the Content pane's tree root.
        "kind": kind,
        "content_root": content_root_for(kind),
        "mount": _mount_for(root, kind),
        # What it is, for its icon: uefn, roblox, unreal, unity or folder.
        "flavor": project_flavor(root),
    }


def list_panel_projects() -> list[dict[str, str | bool]]:
    """Every known project, opened ones newest first, then islands found on disk."""
    from frontend.ui_web.known_projects import known_project_roots

    current = normalize_project_path(PanelSettings.load().uefn_project_root)
    out: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    for path in known_project_roots():
        norm = normalize_project_path(path)
        if not norm or norm in seen:
            continue
        seen.add(norm)
        kind = project_kind(norm)
        out.append(
            {
                "path": norm,
                "name": project_display_name(norm),
                "slug": project_slug(norm),
                "active": norm == current,
                "kind": kind,
                "mount": _mount_for(norm, kind),
                "flavor": project_flavor(norm),
            }
        )
    if current and current not in seen:
        kind = project_kind(current)
        out.insert(
            0,
            {
                "path": current,
                "name": project_display_name(current),
                "slug": project_slug(current),
                "active": True,
                "kind": kind,
                "mount": _mount_for(current, kind),
                "flavor": project_flavor(current),
            },
        )
    return out


def _push_project_changed(info: dict[str, str]) -> None:
    try:
        from frontend.ui_web.verse_editor.panel_events import push_agent_event

        push_agent_event({"type": "project_changed", "project": info})
    except Exception:
        pass


def _rebind_project_folders(root: str) -> threading.Thread | None:
    """Plugins that keep a folder in the project (a ``{PROJECT}`` team folder) point it at
    the copy they read for this project, in the background: a team's copy of it may come
    down first. None when no plugin on keeps one."""
    try:
        from backend.uefn_plugins.spaces import project_folder_plugins, rebind_project_folders

        plugins = project_folder_plugins()
    except Exception:  # noqa: BLE001 — each plugin's bind before its next load does it
        return None
    if not plugins:
        return None

    def work() -> None:
        try:
            rebind_project_folders(root, plugins)
        except Exception:  # noqa: BLE001 — each plugin's bind before its next load tries again
            pass

    t = threading.Thread(target=work, daemon=True, name="project-folders")
    t.start()
    return t


def set_panel_project_root(path: str, *, push_ui: bool = True) -> dict[str, str]:
    """Switch the panel's active project, island or folder (same as the header dropdown)."""
    s = PanelSettings.load()
    before = str(s.uefn_project_root or "").strip()
    raw = (path or "").strip()
    kind = ""
    if raw:
        root, kind = resolve_project_root(raw)
        s.uefn_project_root = str(root)
    else:
        s.uefn_project_root = ""
    s.validate()
    s.save()
    # Save the kind before anything asks for it: a new repo with a content/ folder must
    # never be read as an island by the fallback rule.
    if s.uefn_project_root:
        add_recent_project(s.uefn_project_root, kind=kind)
    forget_cached_kind()
    apply_workspace_env(s.uefn_project_root)
    try:
        # Status must not answer for the previous island (name, match flag) after a switch.
        from backend.bridge.status import forget_listener_project_caches

        forget_listener_project_caches()
    except Exception:
        pass
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
    if s.uefn_project_root and s.uefn_project_root != before:
        _rebind_project_folders(s.uefn_project_root)
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


def mark_deployed(root: str) -> None:
    """Boot's all-islands deploy did ``root``: a switch or rescan this process skips it."""
    with _deploy_lock:
        _deployed_roots.add(root)


def deploy_new_islands(roots: list[str]) -> None:
    """Islands that showed up since boot (a rescan found them) get the listener now."""
    for root in roots:
        _deploy_project_once(root, background=True)


def _deploy_project_once(
    root: str, *, background: bool, on_log: Callable[[str], None] | None = None
) -> None:
    if not has_uefnproject(root):
        return  # folder projects get no listener init or Python quarantine
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
    projects = list_panel_projects()
    for item in projects:
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
    # The live UEFN name is the island's content mount (root plugin), which may be a
    # GUID that no folder is called: match it against each island's own descriptors.
    for item in projects:
        if item.get("kind") == UEFN and is_island_identity(str(item.get("path", "")), raw_name):
            return str(item["path"])
    raise ValueError(f"Unknown project {name!r} — call ducky_list_projects for recent paths")


def delete_panel_project(path: str, *, push_ui: bool = True) -> dict[str, str]:
    """Remove a project from recents, AppData, and the Ducky init script (not the project folder).

    Folder projects only leave the list: the island cleanup below checks for a real
    ``*.uefnproject`` and does nothing to a plain folder.
    """
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
    from frontend.deploy import remove_project_init

    delete_project_appdata(slug)
    remove_project_init(Path(norm))

    if was_active:
        next_path = load_recent_projects()[0] if load_recent_projects() else ""
        info = set_panel_project_root(next_path, push_ui=push_ui)
    else:
        info = get_panel_project_info()

    return info
