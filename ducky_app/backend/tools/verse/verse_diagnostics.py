"""MCP tools for Verse diagnostics and workflow — host-side panel integration."""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from frontend.settings import PanelSettings
from frontend.ui_web.verse_editor.feature_flag import verse_editor_enabled
from frontend.ui_web.verse_editor.lsp.project_root import normalize_verse_lsp_project_root
from backend.bridge import resolve_workspace_path
from backend.tools.verse.compile_hints import hints_for
from backend.tools.verse.verse_stats import record_compile, record_tool_failure
from backend.util.json_util import tool_json
from backend.server import mcp
from backend.tools.support.plugin_gate import plugin_mcp_tool


OFFLINE_SCAN_NOTE = (
    "Offline LSP scan. It does NOT detect effect (no_rollback/transacts), module-access, "
    "or ambiguous-identifier errors. Run workspace_compile_verse before "
    "wire_*/set_verse_editable/set_npc_definition_behavior."
)
WORKFLOW_NOT_CONNECTED = (
    "UEFN Workflow Server not connected — open the project in UEFN, then retry once"
)
_VERSE_FILE_RE = re.compile(r"[\w\-./\\]+\.verse\b")


# An agent's check answers within this; a slow or stuck verse-lsp never holds its turn.
_AGENT_SCAN_SECONDS = 90.0

# project root -> (time.time() of the last real UEFN build, its error count).
# ponytail: per process; a build from another Ducky process isn't seen, persist it if that matters.
_LAST_COMPILE: dict[str, tuple[float, int]] = {}


def _defer_to_clean_build(root: str, payload: dict[str, Any]) -> None:
    """A clean UEFN build newer than every flagged file outranks verse-lsp: report its 0 errors."""
    built = _LAST_COMPILE.get(root)
    flagged = [f for f in payload.get("files") or [] if f.get("errors")]
    if not built or built[1] or not flagged:
        return
    try:
        newest = max(Path(root, f["path"]).stat().st_mtime for f in flagged)
    except (OSError, KeyError, ValueError):
        return
    if newest >= built[0]:
        return
    payload["files"] = [f for f in payload["files"] if not f.get("errors")]
    payload["lsp_only"] = flagged
    payload["note"] = (
        f"UEFN's last Verse build ({time.strftime('%H:%M:%S', time.localtime(built[0]))}) is newer than "
        f"every flagged file and had 0 errors: the {len(flagged)} file(s) in lsp_only are verse-lsp "
        "disagreements, not errors. Do not edit code for them."
    )


def _project_root() -> str:
    """The calling agent's project: the one its write tools write into. Oct 17 2026: a chat
    working in Roguelike wrote its .verse there while this checked the project open in
    Settings and answered "0 errors" for a file it never saw."""
    from backend.bridge.client import workspace_roots

    roots = workspace_roots()
    raw = roots[0] if roots else PanelSettings.load().uefn_project_root.strip()
    return normalize_verse_lsp_project_root(raw)


def _scan_within_limit(root: str, full: bool) -> bool:
    """Run the scan for an agent, at most _AGENT_SCAN_SECONDS; True when it finished. A scan
    that runs over is stopped (each checked batch is already saved, newest files first)."""
    from frontend.ui_web.verse_editor.lsp.diagnostics_scan import (
        abort_active_scan,
        scan_project_verse_diagnostics,
    )

    done = threading.Event()

    def run() -> None:
        try:
            scan_project_verse_diagnostics(root, full=full, on_progress=_ui_progress(root))
        except Exception:
            pass
        finally:
            done.set()

    threading.Thread(target=run, name="verse-check", daemon=True).start()
    if done.wait(_AGENT_SCAN_SECONDS):
        return True
    abort_active_scan()
    done.wait(5.0)
    return False


def _ui_progress(root: str):
    """Stream agent-triggered scan progress to the problems dropdown in every window."""
    from frontend.ui_web.verse_editor.panel_events import push_verse_diagnostics_progress

    def on_progress(**kwargs: object) -> None:
        push_verse_diagnostics_progress({**kwargs, "project_root": root})

    return on_progress


@mcp.tool()
def workspace_list_verse_errors(pretty: bool = False, full: bool = False, rescan: bool = True) -> str:
    """List current Verse errors from the Problems panel (verse-lsp on host disk).

    OFFLINE — does NOT use the UEFN listener. This is the FIRST tool for fix-errors /
    Verse code turns. Never call ping, get_project_info, ducky_get_errors, compile, or
    execute_python to discover compile errors.

    The result lists EVERY file with errors (path, line, column, message). It is the
    single source of truth for what is broken — read it once and act; do not call this
    repeatedly to "make sure".

    Modes (cheapest first):
    - rescan=False: return the Problems-panel state as-is, no scanning at all. Instant.
      Use to re-read errors you were already told about.
    - default (rescan=True, full=False): auto-incremental — re-checks only files that
      changed since the last scan; if nothing is stale, returns the cache with no scan.
      Use this after you edit a .verse file to see if your fix worked.
    - full=True: force a fresh re-scan of ALL .verse files. SLOW — can take a minute on
      a large project. Only when the user explicitly asks for a full rescan or there is
      no cached scan yet. Never pass full=True just to double-check known errors.
    """
    if not verse_editor_enabled():
        raise ValueError("Verse editor is disabled")
    root = _project_root()
    from frontend.project_kind import is_folder_project

    if root and is_folder_project(root):
        # No verse-lsp run over a whole repo: Verse only exists on UEFN islands.
        return tool_json(
            {"ok": True, "folder_project": True, "files": [], "note": "Folder project: no Verse to check."},
            pretty=pretty,
        )
    if not root:
        raise ValueError("No UEFN project root configured in panel settings")
    from frontend.ui_web.verse_editor.lsp.diagnostics_cache import load_for_ui, stale_keys
    from frontend.ui_web.verse_editor.panel_events import push_verse_diagnostics_sync

    finished = True
    if rescan and (full or stale_keys(root)):
        finished = _scan_within_limit(root, full)
    # load_for_ui reconciles against disk (deleted/moved files pruned), so the
    # payload is authoritative — always replace the UI registry with it.
    merged = load_for_ui(root)
    push_verse_diagnostics_sync(list(merged.get("files") or []), root, replace=True)
    payload = dict(merged)
    payload["project_root"] = root
    payload["note"] = OFFLINE_SCAN_NOTE
    _defer_to_clean_build(root, payload)
    if not finished:
        unchecked = sorted(stale_keys(root))
        payload["complete"] = False
        payload["unchecked"] = unchecked[:50]
        payload["note"] = (
            f"The check stopped after {int(_AGENT_SCAN_SECONDS)} s with {len(unchecked)} file(s) not checked "
            "yet (listed in unchecked; recently changed files go first). A file in unchecked has NOT been "
            "checked: call this once more to continue, and never report such a file as clean. "
            + OFFLINE_SCAN_NOTE
        )
    return tool_json(payload, pretty=pretty)


@plugin_mcp_tool("uefn")
def workspace_open_verse_file(
    relative_path: str,
    line: int = 1,
    column: int = 1,
    pretty: bool = False,
) -> str:
    """Open a .verse file in the Ducky editor at line/column (does not activate tab)."""
    from frontend.ui_web.verse_editor.agent_sync import build_open_verse_file_batch
    from frontend.ui_web.verse_editor.panel_events import push_agent_event

    if not verse_editor_enabled():
        raise ValueError("Verse editor is disabled")
    rel = relative_path.strip().replace("\\", "/")
    if not rel.lower().endswith(".verse"):
        raise ValueError("Only .verse files are supported")
    file_path = resolve_workspace_path(rel)
    batch = build_open_verse_file_batch(rel, line=max(1, int(line)), column=max(1, int(column)))
    if batch:
        push_agent_event(
            {
                "type": "editor_batch",
                "editor_batch": batch.to_dict(),
            }
        )
    return tool_json({"path": rel, "absolute": file_path, "line": line, "column": column}, pretty=pretty)


def verse_asset_directory(project_name: str = "") -> str:
    """Compiled Verse devices live at /<Project>/_Verse — bare /_Verse is empty."""
    name = (project_name or "").strip().strip("/\\")
    return f"/{name}/_Verse" if name else "/_Verse"


def _project_mount_name() -> str:
    """The island's content mount (root plugin name), which UEFN may make a GUID; the
    folder name only when no descriptor says otherwise."""
    root = _project_root()
    if not root:
        return ""
    try:
        from frontend.project_kind import island_mount_name

        return island_mount_name(root) or Path(root).name
    except Exception:
        return Path(root).name


def _spawnable_verse_classes(limit: int = 40) -> Any:
    """Best-effort compiled Verse device asset paths via the listener (may be offline)."""
    from backend.bridge import send_command

    directory = verse_asset_directory(_project_mount_name())
    try:
        found = send_command(
            "search_assets",
            {"directory": directory, "recursive": True, "limit": limit, "fields": ["path"]},
            timeout=15.0,
        )
        return found.get("assets") or []
    except Exception as exc:  # noqa: BLE001 — listener down must not fail the compile tool
        return {"unavailable": str(exc)[:160], "directory": directory}


def _compile_text(payload: dict[str, Any], last_log: str) -> str:
    """Everything the compiler said, flattened, so `Script error NNNN` codes can be counted."""
    try:
        body = json.dumps(payload, default=str)
    except Exception:  # noqa: BLE001
        body = str(payload)
    return body + "\n" + str(last_log or "")


@plugin_mcp_tool("uefn")
def workspace_compile_verse(pretty: bool = False) -> str:
    """Compile the Verse project inside UEFN via Verse Workflow Server (requires UEFN running).

    Fail-fast when the Workflow Server is unreachable — use workspace_list_verse_errors
    for Problems-panel diagnostics (offline OK) instead of retrying compile.

    Verification is two steps: workspace_list_verse_errors (offline LSP, syntax only),
    then this (the real build). Never wire_verse_* / set_verse_editable /
    set_npc_definition_behavior / workspace_push_verse_changes before this succeeded.
    Build lifecycle: ``[WinError 10054]`` means the build started — wait, poll
    list_verse_types, never retry. A class_path under /Engine/Transient with
    VERSE_DEAD_* is stale: rebuild, then destroy and recreate the entity. Digest
    deadlock (Script error 9002 cascading to 9000): comment out the problem Verse →
    rebuild and wait → confirm the digest is fresh → uncomment → rebuild; never patch
    the digest. Recipe: skill_read_subskill("uefn", "verse_build_lifecycle").
    """
    from frontend.ui_web.verse_editor.workflow.client import get_workflow_client

    client = get_workflow_client()
    if not client.connected:
        try:
            client.start(timeout_s=1.0)
        except OSError as exc:
            raise ValueError(
                "Verse Workflow Server not connected (UEFN not open or Verse build unavailable). "
                "Do NOT retry compile. Use workspace_list_verse_errors for Problems diagnostics "
                f"(works offline). Detail: {exc}"
            ) from exc
    if not client.connected:
        raise ValueError(
            "Verse Workflow Server not connected. Do NOT retry. "
            "Use workspace_list_verse_errors for Problems diagnostics (works offline)."
        )
    try:
        result = client.compile_project()
        if isinstance(result, dict) and _project_root():
            _LAST_COMPILE[_project_root()] = (time.time(), int(result.get("numErrors") or 0))
    except TimeoutError as exc:
        # The build never answered: UEFN is behind a popup or busy. Say which, don't hang
        # (workspace_compile_verse past 5 min was agent.stuck 3x on Oct 31 2026).
        from backend.tools.core.uefn_popups import blocked_call_hint

        raise ValueError(f"{exc}. {blocked_call_hint()}") from exc
    try:
        from frontend.ui_web.verse_editor.api import refresh_editor_lsp_after_build

        refresh_editor_lsp_after_build()
    except Exception:
        pass
    root = _project_root()
    scan: dict[str, Any] = {}
    if root:
        from frontend.ui_web.verse_editor.lsp.diagnostics_cache import (
            files_for_ui,
            load,
            load_for_ui,
            stale_keys,
        )
        from frontend.ui_web.verse_editor.panel_events import push_verse_diagnostics_sync

        # Builds only re-check changed/stale files, within the agent's limit: an unbounded
        # scan here held the build's answer past 5 minutes.
        complete = _scan_within_limit(root, False) if stale_keys(root) else True
        scan = {"files": files_for_ui(load(root)), "with_errors": 0, "with_warnings": 0}
        if not complete:
            scan["complete"] = False
            scan["unchecked"] = sorted(stale_keys(root))[:50]
        merged = load_for_ui(root)
        push_verse_diagnostics_sync(list(merged.get("files") or []), root, replace=True)
        scan["with_errors"] = sum(1 for f in merged.get("files") or [] if f.get("errors"))
        scan["with_warnings"] = sum(1 for f in merged.get("files") or [] if f.get("warnings"))
    payload: dict[str, Any] = {"compile": result, "diagnostics": scan}
    if result.get("numErrors"):
        compile_text = _compile_text(payload, getattr(client, "last_log", ""))
        payload["hints"] = hints_for(compile_text)
        payload["next"] = (
            "Load skill_read_subskill('verse','compile_errors'), fix each reported line, rebuild."
        )
        try:
            codes = {h["code"]: h["count"] for h in payload["hints"]}
            files = sorted(f for f in set(_VERSE_FILE_RE.findall(compile_text)) if "digest" not in f.lower())
            record_compile(codes, files)
        except Exception:  # noqa: BLE001 — telemetry must never break the tool
            pass
    if not result.get("numErrors"):
        from backend.tools.core.uefn_popups import reload_level_after_clean_build

        reloaded = reload_level_after_clean_build()
        if reloaded is not None:
            # The level was opened past Verse errors (popups skipped): it is reloaded from
            # disk now, so its Verse devices work again and saving is safe again.
            payload["level_reloaded"] = reloaded
        payload["verse_classes"] = _spawnable_verse_classes()
        directory = verse_asset_directory(_project_mount_name())
        payload["verse_directory"] = directory
        payload["next"] = (
            "Compile OK — Verse device classes are spawnable now. If you were placing a "
            "device, continue: pick the asset_path from verse_classes (or "
            f"search_assets(search='<class_name>', directory='{directory}')). "
            "Never search /_Verse or /Game for a project Verse class. Then "
            "spawn_actor(asset_path=..., location=..., label=..., folder=...) → "
            "wire_verse_device_ref per field. Do not stop after compiling and do not ask the user to build."
        )
    return tool_json(payload, pretty=pretty)


@plugin_mcp_tool("uefn")
def workspace_push_verse_changes(verse_only: bool = True, pretty: bool = False) -> str:
    """Push Verse changes to the running UEFN session (hot reload)."""
    from frontend.ui_web.verse_editor.workflow.client import get_workflow_client

    client = get_workflow_client()
    if not client.connected:
        try:
            client.start(timeout_s=1.0)
        except OSError:
            pass
    if not client.connected:
        try:
            record_tool_failure("workspace_push_verse_changes", WORKFLOW_NOT_CONNECTED)
        except Exception:  # noqa: BLE001
            pass
        return tool_json({"ok": False, "error": WORKFLOW_NOT_CONNECTED}, pretty=pretty)
    if not client.can_push_verse_changes:
        raise ValueError(
            "UEFN is not ready to push Verse changes. If the Session menu's VerseVM toggle is On, "
            "Verse Reload is disabled (UEFN 42.40): build Verse and restart the session instead, "
            "or turn the toggle Off."
        )
    message = client.push_changes(bool(verse_only))
    return tool_json({"message": message, "status": client.get_status()}, pretty=pretty)
