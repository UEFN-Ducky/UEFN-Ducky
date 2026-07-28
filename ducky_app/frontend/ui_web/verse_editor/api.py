"""Facade for Verse editor — only entry point for panel_api."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from frontend.settings import PanelSettings
from frontend.ui_web.verse_editor import io
from frontend.ui_web.verse_editor.feature_flag import verse_editor_enabled
from frontend.ui_web.verse_editor.lsp.bridge import LspBridge
from frontend.ui_web.verse_editor.lsp.diagnostics_cache import (
    apply_file,
    fingerprint,
    load,
    load_for_ui,
)
from frontend.ui_web.verse_editor.lsp.diagnostics_scan import (
    abort_active_scan,
    allocate_scan_id,
    is_scan_active,
    list_verse_scan_paths,
    scan_project_verse_diagnostics,
)
from frontend.ui_web.verse_editor.lsp.project_root import normalize_verse_lsp_project_root
from frontend.ui_web.verse_editor.lsp.verse_workspace import discover_verse_workspace
from frontend.ui_web.verse_editor.panel_events import (
    push_verse_diagnostics_progress,
    push_verse_diagnostics_sync,
    push_verse_workflow_state,
)
from frontend.ui_web.verse_editor.workflow.client import get_workflow_client


# One verse-lsp per app window (VS Code runs one language server per window). A single
# shared bridge made windows steal each other's WebSocket on every editor bind.
_MAX_LSP_CLIENTS = 3
_DEFAULT_LSP_CLIENT = "default"


class VerseEditorApi:
    def __init__(self) -> None:
        self._lsp_sessions: dict[str, LspBridge] = {}
        self._lsp_lock = threading.Lock()

    def _lsp_for(self, client_id: str | None) -> LspBridge:
        cid = (client_id or "").strip() or _DEFAULT_LSP_CLIENT
        evicted: list[LspBridge] = []
        with self._lsp_lock:
            bridge = self._lsp_sessions.pop(cid, None)
            if bridge is None:
                bridge = LspBridge()
            # Re-insert at the end = most recently used (dicts preserve insertion order).
            self._lsp_sessions[cid] = bridge
            while len(self._lsp_sessions) > _MAX_LSP_CLIENTS:
                oldest = next(iter(self._lsp_sessions))
                evicted.append(self._lsp_sessions.pop(oldest))
        for stale in evicted:
            try:
                stale.stop()
            except Exception:
                pass
        return bridge

    def is_enabled(self) -> bool:
        return verse_editor_enabled()

    def read_file(self, relative_path: str) -> dict[str, str]:
        return io.read_file(relative_path)

    def write_file(self, relative_path: str, content: str) -> dict[str, object]:
        return io.write_file(relative_path, content)

    def record_external_file_change(
        self,
        relative_path: str,
        previous_content: str,
        new_content: str,
    ) -> dict[str, object]:
        return io.record_external_file_change(relative_path, previous_content, new_content)

    def _with_workspace(self, status: dict[str, Any], project_root: str) -> dict[str, Any]:
        if project_root:
            ws = discover_verse_workspace(project_root)
            status["workspace_folders"] = ws["workspace_folders"]
            status["watch_files"] = ws["watch_files"]
        return status

    def get_lsp_status(self, client_id: str | None = None) -> dict[str, Any]:
        status = self._lsp_for(client_id).get_status()
        status["enabled"] = self.is_enabled()
        root = str(status.get("project_root") or "")
        return self._with_workspace(status, root)

    def start_lsp(self, project_root: str | None = None, client_id: str | None = None) -> dict[str, Any]:
        raw = (project_root or "").strip() or PanelSettings.load().uefn_project_root.strip()
        root = normalize_verse_lsp_project_root(raw)
        bridge = self._lsp_for(client_id)
        if not root:
            return {**bridge.get_status(), "error": "No project root set"}
        # Do NOT abort scans here: editor binds/reconnects happen constantly (WS
        # auto-reconnect, extra windows) and used to kill every scan mid-flight.
        # A project-root switch aborts inside scan_project_verse_diagnostics.
        status = bridge.start(root)
        return self._with_workspace(status, root)

    def stop_lsp(self, client_id: str | None = None) -> None:
        """Stop one window's verse-lsp, or every session when no client_id is given."""
        cid = (client_id or "").strip()
        with self._lsp_lock:
            if cid:
                bridges = [b for k, b in self._lsp_sessions.items() if k == cid]
            else:
                bridges = list(self._lsp_sessions.values())
                self._lsp_sessions.clear()
        for bridge in bridges:
            try:
                bridge.stop()
            except Exception:
                pass
        if cid:
            with self._lsp_lock:
                self._lsp_sessions.pop(cid, None)

    def load_verse_diagnostics_cache(self, project_root: str | None = None) -> dict[str, Any]:
        raw = (project_root or "").strip() or PanelSettings.load().uefn_project_root.strip()
        root = normalize_verse_lsp_project_root(raw)
        if not root:
            return {"files": [], "stale_count": 0, "from_cache": False}
        return load_for_ui(root)

    def save_verse_file_cache(
        self,
        relative_path: str,
        errors: int,
        warnings: int,
        items: list[dict[str, Any]] | None = None,
        project_root: str | None = None,
    ) -> dict[str, object]:
        raw = (project_root or "").strip() or PanelSettings.load().uefn_project_root.strip()
        root = normalize_verse_lsp_project_root(raw)
        if not root:
            return {"ok": False, "error": "No project root set"}
        rel = relative_path.strip().replace("\\", "/").lstrip("/")
        key = rel.lower()
        abs_path = Path(root) / rel
        if not abs_path.is_file():
            return {"ok": False, "error": "File not found"}
        cache = load(root)
        result = {
            "path": key,
            "errors": int(errors),
            "warnings": int(warnings),
            "items": list(items or []),
        }
        apply_file(root, cache, key, fingerprint(abs_path), result)
        # Broadcast to EVERY window (main + focus): live diagnostics land in one
        # window's local registry; without this the Problems badge showed different
        # counts per window until a file was opened there.
        push_verse_diagnostics_sync([result], root, replace=False, live=True)
        return {"ok": True, "path": key}

    def scan_verse_diagnostics(
        self,
        project_root: str | None = None,
        *,
        push_ui: bool = True,
        fast: bool = False,
        full: bool = False,
    ) -> dict[str, Any]:
        raw = (project_root or "").strip() or PanelSettings.load().uefn_project_root.strip()
        root = normalize_verse_lsp_project_root(raw)
        if not root:
            return {"error": "No project root set", "files": [], "scanned": 0}

        use_fast = fast or push_ui

        # Project scans ALWAYS run in an ephemeral stdio verse-lsp (separate process).
        # Routing them through the editor's live session drowned verse-lsp in 88 didOpens
        # and starved every interactive request (completion/hover/diagnostic timeouts).
        if push_ui and not full:
            paths = list_verse_scan_paths(root, full=False)
            if not paths:
                cached = load_for_ui(root)
                files = list(cached.get("files") or [])
                push_verse_diagnostics_sync(files, root, replace=True)
                return {
                    "ok": True,
                    "pending": False,
                    "files": files,
                    "scanned": 0,
                    "with_errors": sum(1 for f in files if f.get("errors")),
                    "with_warnings": sum(1 for f in files if f.get("warnings")),
                }

        if push_ui:
            scan_id = allocate_scan_id()
            started_announced = False
            if not is_scan_active(root):
                # Instant UI feedback only when this scan will start right away.
                # When a scan is already running, its events keep feeding the UI and
                # this request queues behind it (serialized in diagnostics_scan).
                paths = list_verse_scan_paths(root, full=full)
                push_verse_diagnostics_progress(
                    {
                        "project_root": root,
                        "phase": "started",
                        "scan_id": scan_id,
                        "total": len(paths),
                        "paths": paths,
                        "index": 0,
                        "path": "",
                    }
                )
                started_announced = True

            def run_ui_scan() -> None:
                try:

                    def on_progress(**kwargs: object) -> None:
                        push_verse_diagnostics_progress({**kwargs, "project_root": root})

                    scan_project_verse_diagnostics(
                        root,
                        full=full,
                        fast=use_fast,
                        on_progress=on_progress,
                        scan_id=scan_id,
                        skip_started=started_announced,
                    )
                    # load_for_ui is the authority: session state keeps last-known
                    # results for files the scan didn't touch and prunes deleted
                    # files, so a replace can't wipe real squiggles — but it DOES
                    # finally clear files that were fixed, deleted, or moved.
                    # (The old add-only merge left ghost errors for files that no
                    # longer existed on disk.)
                    merged = load_for_ui(root)
                    push_verse_diagnostics_sync(
                        list(merged.get("files") or []),
                        root,
                        replace=True,
                    )
                except Exception as exc:
                    push_verse_diagnostics_progress(
                        {
                            "project_root": root,
                            "phase": "error",
                            "scan_id": scan_id,
                            "message": str(exc),
                        }
                    )
                    cached = load_for_ui(root)
                    push_verse_diagnostics_sync(
                        list(cached.get("files") or []),
                        root,
                        replace=True,
                    )

            threading.Thread(target=run_ui_scan, daemon=True).start()
            return {"ok": True, "pending": True, "files": [], "scanned": 0}

        result = scan_project_verse_diagnostics(root, full=full, fast=use_fast)
        return result

    def stop_verse_diagnostics_scan(self, project_root: str | None = None) -> dict[str, Any]:
        """User-requested stop: kill the ephemeral scan session and tell the UI."""
        raw = (project_root or "").strip() or PanelSettings.load().uefn_project_root.strip()
        root = normalize_verse_lsp_project_root(raw)
        abort_active_scan()
        if root:
            push_verse_diagnostics_progress(
                {"project_root": root, "phase": "error", "message": "Scan stopped"}
            )
            cached = load_for_ui(root)
            push_verse_diagnostics_sync(list(cached.get("files") or []), root, replace=True)
        return {"ok": True}

    def connect_verse_workflow(self) -> dict[str, Any]:
        client = get_workflow_client()

        def on_state(state: dict[str, object]) -> None:
            push_verse_workflow_state(state)

        client.set_state_listener(on_state)
        try:
            client.start()
        except OSError as exc:
            return {**client.get_status(), "error": str(exc)}
        return client.get_status()

    def disconnect_verse_workflow(self) -> dict[str, Any]:
        client = get_workflow_client()
        client.set_state_listener(None)
        client.stop()
        return client.get_status()

    def get_verse_workflow_status(self) -> dict[str, Any]:
        return get_workflow_client().get_status()

    def compile_verse_project(self) -> dict[str, Any]:
        client = get_workflow_client()
        if not client.connected:
            raise RuntimeError("Not connected to Verse Workflow Server — open project in UEFN")
        result = client.compile_project()
        scan = self.scan_verse_diagnostics(push_ui=True)
        return {**result, "diagnostics_scan": scan}

    def push_verse_changes(self, verse_only: bool = True) -> dict[str, Any]:
        client = get_workflow_client()
        if not client.connected:
            raise RuntimeError("Not connected to Verse Workflow Server")
        message = client.push_changes(verse_only)
        return {"message": message, "status": client.get_status()}

    def get_urc_status(self, project_root: str | None = None) -> dict[str, Any]:
        from frontend.ui_web.verse_editor.urc.cli_service import get_urc_service
        from frontend.ui_web.verse_editor.urc.detect_urc import detect_urc_executable

        raw = (project_root or "").strip() or PanelSettings.load().uefn_project_root.strip()
        svc = get_urc_service()
        cfg = svc.configure(raw)
        if not cfg.get("available"):
            return {**detect_urc_executable(), "project_root": raw}
        try:
            status = svc.get_status()
            return {**cfg, **status}
        except OSError as exc:
            return {**cfg, "error": str(exc), "staged_count": 0, "unstaged_count": 0}

    def urc_commit(self, message: str = "", project_root: str | None = None) -> dict[str, Any]:
        from frontend.ui_web.verse_editor.urc.cli_service import get_urc_service

        raw = (project_root or "").strip() or PanelSettings.load().uefn_project_root.strip()
        svc = get_urc_service()
        svc.configure(raw)
        return svc.commit(message)

    def urc_push(self, project_root: str | None = None) -> dict[str, Any]:
        from frontend.ui_web.verse_editor.urc.cli_service import get_urc_service

        raw = (project_root or "").strip() or PanelSettings.load().uefn_project_root.strip()
        svc = get_urc_service()
        svc.configure(raw)
        return svc.push()
