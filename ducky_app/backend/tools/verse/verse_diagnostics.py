"""MCP tools for Verse diagnostics and workflow — host-side panel integration."""

from __future__ import annotations

import json
import re
from typing import Any

from frontend.settings import PanelSettings
from frontend.ui_web.verse_editor.agent_sync import build_open_verse_file_batch
from frontend.ui_web.verse_editor.feature_flag import verse_editor_enabled
from frontend.ui_web.verse_editor.lsp.diagnostics_cache import load_for_ui
from frontend.ui_web.verse_editor.lsp.diagnostics_scan import scan_project_verse_diagnostics
from frontend.ui_web.verse_editor.lsp.project_root import normalize_verse_lsp_project_root
from frontend.ui_web.verse_editor.panel_events import (
    push_agent_event,
    push_verse_diagnostics_progress,
    push_verse_diagnostics_sync,
)
from frontend.ui_web.verse_editor.workflow.client import get_workflow_client
from backend.bridge import resolve_workspace_path
from backend.tools.verse.compile_hints import hints_for
from backend.tools.verse.verse_stats import record_compile, record_tool_failure
from backend.util.json_util import tool_json
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


def _project_root() -> str:
    return normalize_verse_lsp_project_root(PanelSettings.load().uefn_project_root.strip())


def _ui_progress(root: str):
    """Stream agent-triggered scan progress to the problems dropdown in every window."""

    def on_progress(**kwargs: object) -> None:
        push_verse_diagnostics_progress({**kwargs, "project_root": root})

    return on_progress


@plugin_mcp_tool("verse")
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
    if not root:
        raise ValueError("No UEFN project root configured in panel settings")
    if rescan:
        from frontend.ui_web.verse_editor.lsp import diagnostics_cache

        if full:
            scan_project_verse_diagnostics(root, full=True, on_progress=_ui_progress(root))
        elif diagnostics_cache.stale_keys(root):
            scan_project_verse_diagnostics(root, full=False, on_progress=_ui_progress(root))
    # load_for_ui reconciles against disk (deleted/moved files pruned), so the
    # payload is authoritative — always replace the UI registry with it.
    merged = load_for_ui(root)
    push_verse_diagnostics_sync(list(merged.get("files") or []), root, replace=True)
    payload = dict(merged)
    payload["note"] = OFFLINE_SCAN_NOTE
    return tool_json(payload, pretty=pretty)


@plugin_mcp_tool("verse")
def workspace_open_verse_file(
    relative_path: str,
    line: int = 1,
    column: int = 1,
    pretty: bool = False,
) -> str:
    """Open a .verse file in the Ducky editor at line/column (does not activate tab)."""
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


def _spawnable_verse_classes(limit: int = 40) -> Any:
    """Best-effort compiled /_Verse asset paths via the listener (may be offline)."""
    from backend.bridge import send_command

    try:
        found = send_command(
            "search_assets",
            {"directory": "/_Verse", "recursive": True, "limit": limit, "fields": ["path"]},
            timeout=15.0,
        )
        return found.get("assets") or []
    except Exception as exc:  # noqa: BLE001 — listener down must not fail the compile tool
        return {"unavailable": str(exc)[:160]}


def _compile_text(payload: dict[str, Any], last_log: str) -> str:
    """Everything the compiler said, flattened, so `Script error NNNN` codes can be counted."""
    try:
        body = json.dumps(payload, default=str)
    except Exception:  # noqa: BLE001
        body = str(payload)
    return body + "\n" + str(last_log or "")


@plugin_mcp_tool("verse")
def workspace_compile_verse(pretty: bool = False) -> str:
    """Compile the Verse project inside UEFN via Verse Workflow Server (requires UEFN running).

    Fail-fast when the Workflow Server is unreachable — use workspace_list_verse_errors
    for Problems-panel diagnostics (offline OK) instead of retrying compile.
    """
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
    result = client.compile_project()
    root = _project_root()
    scan: dict[str, Any] = {}
    if root:
        from frontend.ui_web.verse_editor.lsp import diagnostics_cache

        # Builds only re-check changed/stale files — never a full-project rescan.
        if diagnostics_cache.stale_keys(root):
            scan = scan_project_verse_diagnostics(root, full=False, on_progress=_ui_progress(root))
        else:
            scan = {
                "files": diagnostics_cache.files_for_ui(diagnostics_cache.load(root)),
                "scanned": 0,
                "with_errors": 0,
                "with_warnings": 0,
            }
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
        payload["verse_classes"] = _spawnable_verse_classes()
        payload["next"] = (
            "Compile OK — Verse device classes are spawnable now. If you were placing a "
            "device, continue: pick the asset_path from verse_classes (or "
            "search_assets(search='<class_name>', directory='/_Verse')), then "
            "spawn_actor(asset_path=..., location=...) → set_actor_label → wire_verse_device_ref per field. "
            "Do not stop after compiling and do not ask the user to build."
        )
    return tool_json(payload, pretty=pretty)


@plugin_mcp_tool("verse")
def workspace_push_verse_changes(verse_only: bool = True, pretty: bool = False) -> str:
    """Push Verse changes to the running UEFN session (hot reload)."""
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
        raise ValueError("UEFN is not ready to push Verse changes")
    message = client.push_changes(bool(verse_only))
    return tool_json({"message": message, "status": client.get_status()}, pretty=pretty)
