"""Project files, Verse, tester, URC, deploy/IDE. Mixin for PanelApi — methods stay on the PyWebView JS object."""

from __future__ import annotations

from typing import Any

import frontend.ui_web.panel_api as _pa


class PanelApiProjectMixin:
    def get_project_info(self) -> dict[str, str]:
        return _pa.get_panel_project_info()

    def list_recent_projects(self) -> list[dict[str, str | bool]]:
        return _pa.list_panel_projects()

    def set_project_root(self, path: str) -> dict[str, str]:
        self._listener_project_cache = None
        self._ping_fail_streak = 0
        # Central switch (shared with the ducky_set_project MCP tool). background_deploy
        # keeps the one-time deploy off the UI thread so the switch returns instantly.
        return _pa.switch_panel_project(
            path=path, push_ui=True, background_deploy=True, on_deploy_log=_pa._log
        )

    def delete_recent_project(self, path: str) -> dict[str, str]:
        from frontend.ui_web.project_switch import delete_panel_project

        self._listener_project_cache = None
        self._ping_fail_streak = 0
        return delete_panel_project(path, push_ui=True)

    def list_project_files(self, relative_path: str = "") -> dict[str, object]:
        return _pa.list_project_files(relative_path)

    def list_workspace_roots(self) -> list[dict[str, object]]:
        return _pa.list_workspace_roots()

    def list_project_file_paths(self) -> list[dict[str, str]]:
        return _pa.list_project_file_paths()

    def search_workspace(
        self,
        query: str,
        scope: str = "both",
        case_sensitive: bool = False,
        whole_word: bool = False,
        max_results: int = 500,
    ) -> dict[str, object]:
        scope_val = scope if scope in ("files", "chats", "both") else "both"
        return _pa._search_workspace(
            query,
            scope=scope_val,  # type: ignore[arg-type]
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            max_results=max_results,
        )

    def replace_workspace(
        self,
        query: str,
        replacement: str,
        case_sensitive: bool = False,
        whole_word: bool = False,
    ) -> dict[str, object]:
        """Replace in Verse files only; never modifies duckies or chat history."""
        return _pa._replace_workspace(
            query,
            replacement,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
        )

    def open_project_file(self, relative_path: str) -> None:
        _pa.open_project_file(relative_path)

    def open_asset_in_uefn(self, relative_path: str) -> dict[str, object]:
        """Reveal a project asset in UEFN (Content Browser). Rich preview UI is the Store plugin."""
        from frontend.settings import PANEL_LISTENER_PORT

        content_rel = _pa.content_package_rel(relative_path)
        asset_path = f"/Game/{content_rel}"
        try:
            from backend.bridge import post_command_to_listener

            info = post_command_to_listener(_pa.PANEL_LISTENER_PORT, "get_project_info", {}, timeout=4.0)
            root = str((info or {}).get("content_root") or "").strip().rstrip("/")
            if root:
                asset_path = f"{root}/{content_rel}"
            res = post_command_to_listener(
                _pa.PANEL_LISTENER_PORT,
                "open_asset_in_uefn",
                {"asset_path": asset_path},
                timeout=15.0,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "asset_path": asset_path}
        ok = bool(res.get("success", True)) if isinstance(res, dict) else True
        opened = bool(res.get("opened")) if isinstance(res, dict) else None
        return {"ok": ok, "asset_path": asset_path, "opened": opened}

    def _uefn_asset_path_for_content(self, relative_path: str) -> str:
        """Map ``Content/...`` to ``/{Project}/...`` package/folder path for EditorAssetLibrary."""
        from frontend.settings import PANEL_LISTENER_PORT

        from backend.bridge import post_command_to_listener

        content_rel = _pa.content_package_rel(relative_path)
        asset_path = f"/Game/{content_rel}"
        info = post_command_to_listener(_pa.PANEL_LISTENER_PORT, "get_project_info", {}, timeout=4.0)
        root = str((info or {}).get("content_root") or "").strip().rstrip("/")
        if root:
            asset_path = f"{root}/{content_rel}"
        return asset_path

    def _uefn_delete_content_entry(self, relative_path: str) -> None:
        """Delete uassets/umaps (or a folder of them) through UEFN — disk-only delete leaves ghosts."""
        from frontend.settings import PANEL_LISTENER_PORT

        from backend.bridge import post_command_to_listener

        rel = (relative_path or "").strip().replace("\\", "/").strip("/")
        asset_path = self._uefn_asset_path_for_content(rel)
        is_file = rel.lower().endswith((".uasset", ".umap"))
        try:
            if is_file:
                res = post_command_to_listener(
                    _pa.PANEL_LISTENER_PORT,
                    "delete_asset",
                    {"asset_path": asset_path},
                    timeout=60.0,
                )
            else:
                res = post_command_to_listener(
                    _pa.PANEL_LISTENER_PORT,
                    "delete_directory",
                    {"directory": asset_path},
                    timeout=120.0,
                )
        except ConnectionError as exc:
            raise ValueError(
                "UEFN listener offline — open this island in UEFN to delete Content assets. "
                "Disk-only delete leaves them in the Content Browser."
            ) from exc
        if isinstance(res, dict) and res.get("success") is False:
            kind = "asset" if is_file else "folder"
            raise ValueError(f"UEFN refused to delete {kind}: {asset_path}")

    def delete_project_entry(self, relative_path: str) -> dict[str, str]:
        rel = (relative_path or "").strip().replace("\\", "/")
        needs_uefn = _pa.content_entry_needs_uefn_delete(rel)
        if needs_uefn:
            self._uefn_delete_content_entry(rel)
        if _pa.content_entry_exists(rel):
            # Soft-trash leftovers (verse/text, or non-asset deletes). After a full UEFN
            # folder delete the path is usually gone — skip soft-trash then.
            result = _pa.delete_project_entry(rel)
        else:
            from pathlib import Path

            result = {"path": rel.strip("/"), "name": _pa.Path(rel).name}
        # All windows purge diagnostics for the dead path and close the LSP document.
        self._push({"type": "file_deleted", "old_path": rel.replace("\\", "/")})
        return result

    def read_project_file(self, relative_path: str) -> dict[str, str]:
        return _pa.read_project_file(relative_path)

    def classify_project_file(self, relative_path: str) -> dict[str, object]:
        return _pa.classify_project_file(relative_path)

    def project_file_media_url(self, relative_path: str) -> dict[str, str]:
        """Return a loopback URL for rendering an image/model/audio/video file in an editor tab."""
        from frontend.ui_web.project_media import build_model_media_urls, build_project_media_url

        result = _pa.read_project_file(relative_path)
        kind = result.get("kind") or ""
        url = result.get("media_url") or ""
        if not url and kind == "image":
            url = build_project_media_url(result.get("path") or relative_path)
        out: dict[str, str] = {
            "path": result.get("path") or relative_path,
            "media_url": url,
            "mime": result.get("mime") or "",
            "kind": kind,
        }
        if kind == "model":
            if not url:
                urls = build_model_media_urls(result.get("path") or relative_path)
                out.update(urls)
            else:
                out["media_base_url"] = result.get("media_base_url") or ""
                out["media_filename"] = result.get("media_filename") or ""
        return out

    def stat_project_file(self, relative_path: str) -> dict[str, int | str | bool]:
        return _pa.stat_project_file(relative_path)

    def fingerprint_project_dirs(self, relative_paths: list[str]) -> dict[str, object]:
        return _pa.fingerprint_project_dirs(relative_paths)

    def create_project_folder(self, parent_relative: str, name: str) -> dict[str, str]:
        return _pa.create_project_folder(parent_relative, name)

    def copy_project_entry(self, source_relative: str, dest_parent_relative: str) -> dict[str, str]:
        return _pa.copy_project_entry(source_relative, dest_parent_relative)

    def move_project_entry(self, source_relative: str, dest_parent_relative: str) -> dict[str, str]:
        result = _pa.move_project_entry(source_relative, dest_parent_relative)
        _pa.remap_conversation_file_paths(source_relative, result["path"])
        # Moves are renames as far as tabs/diagnostics are concerned.
        self._push(
            {
                "type": "file_renamed",
                "old_path": source_relative.replace("\\", "/"),
                "new_path": str(result.get("path") or "").replace("\\", "/"),
            }
        )
        return result

    def restore_trashed_entry(self, trash_token: str) -> dict[str, str]:
        """Ctrl+Z undo of a delete — move the trashed file/folder back into Content."""
        return _pa.restore_trashed_entry(trash_token)

    def rename_project_entry(self, source_relative: str, new_name: str) -> dict[str, str]:
        result = _pa.rename_project_entry(source_relative, new_name)
        _pa.remap_conversation_file_paths(source_relative, result["path"])
        # Open tabs must follow the rename (VS Code renames the tab in place) — every
        # window remaps its tab id/path/name instead of orphaning the old tab.
        self._push(
            {
                "type": "file_renamed",
                "old_path": source_relative.replace("\\", "/"),
                "new_path": str(result.get("path") or "").replace("\\", "/"),
            }
        )
        return result

    def create_project_verse_file(self, parent_relative: str, name: str, content: str = "") -> dict[str, str]:
        return _pa.create_project_verse_file(parent_relative, name, content)

    def create_project_file(self, parent_relative: str, name: str, content: str = "") -> dict[str, str]:
        return _pa.create_project_file(parent_relative, name, content)

    def set_import_drop_target(self, dest_parent_relative: str = "") -> None:
        """Sidebar reports the folder an external OS-file drag is currently over ("" = none)."""
        with self._import_drop_lock:
            self._import_drop_target = (dest_parent_relative or "").strip()

    def read_import_drop_target(self) -> str:
        with self._import_drop_lock:
            return self._import_drop_target

    def consume_pending_open_files(self) -> list[str]:
        """Absolute paths from Open-with / CLI / second-instance handoff (cold start)."""
        from frontend.open_files import take_pending_open_paths

        return take_pending_open_paths()

    def consume_pending_deep_links(self) -> list[str]:
        """``uefn-ducky://…`` URLs from a browser protocol launch (cold start)."""
        from frontend.open_files import take_pending_deep_links

        return take_pending_deep_links()

    def import_external_entries(
        self, dest_parent_relative: str, source_paths: list[str]
    ) -> dict[str, object]:
        return _pa.import_external_entries(dest_parent_relative, source_paths)

    def write_project_file(self, relative_path: str, content: str) -> dict[str, object]:
        # ext: = a dragged-in external file edited in place; write straight to its real
        # location, bypassing the project-scoped Content-only write path.
        if relative_path.strip().replace("\\", "/").lower().startswith(_pa.EXT_PATH_PREFIX):
            return _pa.write_external_file(relative_path, content)
        return self._verse_editor.write_file(relative_path, content)

    def record_external_file_change(
        self,
        relative_path: str,
        previous_content: str,
        new_content: str,
    ) -> dict[str, object]:
        return self._verse_editor.record_external_file_change(relative_path, previous_content, new_content)

    def list_file_history(self, relative_path: str) -> list[dict[str, Any]]:
        from frontend.ui_web.verse_editor import file_history

        return file_history.list_entries(relative_path)

    def read_file_history_entry(self, relative_path: str, entry_id: str) -> dict[str, str]:
        from frontend.ui_web.verse_editor import file_history

        return file_history.read_entry(relative_path, entry_id)

    def snapshot_file_history(self, relative_path: str, content: str) -> dict[str, str]:
        from frontend.ui_web.verse_editor import file_history

        return file_history.snapshot_editor_content(relative_path, content)

    def get_verse_lsp_status(self, client_id: str = "") -> dict[str, object]:
        return self._verse_editor.get_lsp_status(client_id or None)

    def start_verse_lsp(self, project_root: str = "", client_id: str = "") -> dict[str, object]:
        return self._verse_editor.start_lsp(project_root or None, client_id or None)

    def stop_verse_lsp(self, client_id: str = "") -> None:
        self._verse_editor.stop_lsp(client_id or None)

    def load_verse_diagnostics_cache(self, project_root: str = "") -> dict[str, object]:
        return self._verse_editor.load_verse_diagnostics_cache(project_root or None)

    def save_verse_file_cache(
        self,
        relative_path: str,
        errors: int,
        warnings: int,
        items: list | None = None,
        project_root: str = "",
    ) -> dict[str, object]:
        return self._verse_editor.save_verse_file_cache(
            relative_path,
            errors,
            warnings,
            items,
            project_root or None,
        )

    def scan_verse_diagnostics(self, project_root: str = "", full: bool = False) -> dict[str, object]:
        return self._verse_editor.scan_verse_diagnostics(project_root or None, full=full)

    def stop_verse_diagnostics_scan(self, project_root: str = "") -> dict[str, object]:
        return self._verse_editor.stop_verse_diagnostics_scan(project_root or None)

    def connect_verse_workflow(self) -> dict[str, object]:
        return self._verse_editor.connect_verse_workflow()

    def disconnect_verse_workflow(self) -> dict[str, object]:
        return self._verse_editor.disconnect_verse_workflow()

    def get_verse_workflow_status(self) -> dict[str, object]:
        return self._verse_editor.get_verse_workflow_status()

    def compile_verse_project(self) -> dict[str, object]:
        return self._verse_editor.compile_verse_project()

    def push_verse_changes(self, verse_only: bool = True) -> dict[str, object]:
        return self._verse_editor.push_verse_changes(verse_only)

    def _tester_project_root(self) -> str:
        from frontend.ui_web.verse_editor.lsp.project_root import normalize_verse_lsp_project_root

        return normalize_verse_lsp_project_root(_pa.PanelSettings.load().uefn_project_root.strip())

    def _require_tester_plugin(self) -> dict[str, Any] | None:
        """Tester dock + bridge require the Store tester plugin to be enabled."""
        try:
            from backend.uefn_plugins.host import is_plugin_enabled

            if is_plugin_enabled("tester"):
                return None
        except Exception:
            pass
        return {"ok": False, "error": "Tester plugin is disabled — enable it in Settings → Store"}

    def tester_list_devices(self) -> dict[str, Any]:
        """Device outliner for the Tester dock: live graph + workspace Verse sources."""
        blocked = self._require_tester_plugin()
        if blocked:
            return blocked
        from backend.testing.device_sim import device_graph_audit, scan_verse_devices_from_files

        root = self._tester_project_root()
        live: dict[str, Any] | None = None
        err: str | None = None
        try:
            from backend.bridge import send_command

            # Short timeout: offline probe must not block Sim / refresh for 30s.
            live = send_command(
                "device_graph_snapshot",
                {
                    "limit": 100,
                    "include_editables": True,
                    "include_events": True,
                },
                timeout=2.0,
            )
        except Exception as exc:
            err = str(exc)
        workspace = scan_verse_devices_from_files(root) if root else {"nodes": [], "count": 0}
        audit = device_graph_audit(live) if live else None
        return {
            "ok": True,
            "listener_online": live is not None,
            "live": live,
            "workspace": workspace,
            "audit": audit,
            "error": err,
        }

    def tester_simulate(
        self,
        device: str,
        event: str = "InteractedWithEvent",
        snapshot_json: str = "",
    ) -> dict[str, Any]:
        """Run offline event simulation. Prefer client-supplied snapshot (no listener hop)."""
        blocked = self._require_tester_plugin()
        if blocked:
            return {**blocked, "trace": [], "effects": []}
        import json

        from backend.testing.device_sim import simulate_device_event

        listener_online = False
        snapshot: dict[str, Any] | None = None
        raw = str(snapshot_json or "").strip()
        if raw:
            try:
                parsed = _pa.json.loads(raw)
                if isinstance(parsed, dict):
                    snapshot = parsed
                    listener_online = bool(parsed.get("listener_online"))
            except _pa.json.JSONDecodeError as exc:
                return {"ok": False, "error": f"invalid snapshot_json: {exc}", "trace": [], "effects": []}
        if snapshot is None:
            listed = self.tester_list_devices()
            snapshot = listed.get("live") or {"nodes": [], "edges": []}
            if not (snapshot.get("nodes") or []):
                snapshot = listed.get("workspace") or snapshot
            listener_online = bool(listed.get("listener_online"))
        # Normalize: UI may send {live, workspace} or a flat {nodes, edges}.
        if not (snapshot.get("nodes") or []) and (
            snapshot.get("live") is not None or snapshot.get("workspace") is not None
        ):
            live = snapshot.get("live") if isinstance(snapshot.get("live"), dict) else None
            workspace = (
                snapshot.get("workspace") if isinstance(snapshot.get("workspace"), dict) else None
            )
            listener_online = bool(snapshot.get("listener_online") or live)
            snapshot = live if live and (live.get("nodes") or []) else (workspace or {"nodes": [], "edges": []})
        try:
            result = simulate_device_event(
                snapshot, str(device or ""), str(event or "InteractedWithEvent")
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "trace": [], "effects": []}
        result["listener_online"] = listener_online
        return result

    def tester_list_tests(self) -> dict[str, Any]:
        blocked = self._require_tester_plugin()
        if blocked:
            return {**blocked, "tests": []}
        from backend.testing.verse_harness import list_harness_tests

        root = self._tester_project_root()
        if not root:
            return {"ok": False, "error": "No UEFN project root configured", "tests": []}
        tests = list_harness_tests(root)
        return {"ok": True, "tests": tests, "count": len(tests)}

    def tester_scaffold(self, overwrite: bool = False) -> dict[str, Any]:
        """Write the Verse/DuckyTests harness scaffold into the project."""
        blocked = self._require_tester_plugin()
        if blocked:
            return blocked
        import os

        from backend.bridge import resolve_workspace_path
        from backend.testing.verse_harness import scaffold_content

        rel = "Verse/DuckyTests/ducky_test_device.verse"
        try:
            path = resolve_workspace_path(rel)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if _pa.os.path.isfile(path) and not overwrite:
            return {
                "ok": True,
                "path": rel,
                "created": False,
                "note": "already exists — pass overwrite to replace",
            }
        parent = _pa.os.path.dirname(path)
        if parent:
            _pa.os.makedirs(parent, exist_ok=True)
        content = scaffold_content()
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return {"ok": True, "path": rel, "created": True}

    def tester_run_tests(self, start_session: bool = False) -> dict[str, Any]:
        """Compile + push Verse harness; optionally start a play session."""
        blocked = self._require_tester_plugin()
        if blocked:
            return blocked
        compile_data = self.compile_verse_project()
        push_data: dict[str, Any]
        try:
            push_data = dict(self.push_verse_changes(True))
        except Exception as exc:
            push_data = {"ok": False, "error": str(exc)}
        session: dict[str, Any] = {"started": False}
        if start_session:
            try:
                from backend.bridge import send_command

                session = send_command("play_in_editor", {})
                session["started"] = True
            except Exception as exc:
                session = {"started": False, "error": str(exc)}
        return {"ok": True, "compile": compile_data, "push": push_data, "session": session}

    def tester_results(self, last_n: int = 500, since_offset: int = 0) -> dict[str, Any]:
        """Parse [DUCKY-TEST] lines from the editor log."""
        blocked = self._require_tester_plugin()
        if blocked:
            return {**blocked, "results": []}
        from backend.testing.verse_harness import parse_test_results

        try:
            from backend.bridge import send_command

            log = send_command(
                "get_editor_log",
                {
                    "last_n": int(last_n or 500),
                    "since_offset": int(since_offset or 0),
                    "regex": r"\[DUCKY-TEST\]",
                },
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc), "results": []}
        parsed = parse_test_results(list(log.get("lines") or []))
        parsed["log_offset"] = log.get("offset")
        parsed["log_file"] = log.get("file")
        if log.get("error"):
            parsed["log_error"] = log["error"]
        return parsed

    def tester_create_chat(self, device_label: str = "") -> dict[str, Any]:
        """Spawn a Tester Ducky chat pre-prompted for the selected device."""
        blocked = self._require_tester_plugin()
        if blocked:
            return blocked
        from frontend.agent_profiles import list_bundled_agent_profile_templates

        profile = next(
            (p for p in _pa.list_bundled_agent_profile_templates() if str(p.get("id") or "") == "tester"),
            {},
        )
        label = str(device_label or "").strip()
        title = f"Test {label}" if label else "Tester"
        prompt = (
            f"Create and run tests for device `{label}`. "
            "Start with device_graph_snapshot + device_graph_audit, then simulate_device_event, "
            "then verse_test_scaffold / verse_test_run as needed."
            if label
            else (
                "Audit the level device graph, simulate key interaction chains, "
                "and scaffold/run Verse harness tests for leveling and movement."
            )
        )
        conv = self.create_conversation(
            "",
            str(profile.get("ducky_style") or "hacker"),
            None,
            {
                "title": title,
                "ducky_name": str(profile.get("name") or "Tester"),
                "ducky_personality": str(profile.get("ducky_personality") or ""),
                "ducky_style": str(profile.get("ducky_style") or "hacker"),
                "disabled_packs": list(profile.get("disabled_packs") or []),
                "disabled_tool_ids": list(profile.get("disabled_tool_ids") or []),
            },
        )
        return {"ok": True, "conversation": conv, "prompt": prompt, "device": label}

    def get_urc_status(self, project_root: str = "") -> dict[str, object]:
        return self._verse_editor.get_urc_status(project_root or None)

    def urc_commit(self, message: str = "", project_root: str = "") -> dict[str, object]:
        return self._verse_editor.urc_commit(message, project_root or None)

    def urc_push(self, project_root: str = "") -> dict[str, object]:
        return self._verse_editor.urc_push(project_root or None)

    def is_verse_editor_enabled(self) -> bool:
        return self._verse_editor.is_enabled()

    def deploy(self, project_path: str = "") -> list[str]:
        path = (project_path or "").strip()
        if not path:
            path = _pa.PanelSettings.load().uefn_project_root.strip()
        if not path:
            picked = self.pick_project_path()
            if not picked:
                return [
                    "No project selected. Pick a project in the header (folder dropdown) "
                    "or Agent settings, then try again."
                ]
            path = picked
        try:
            root = _pa.resolve_uefn_project_root(_pa.Path(path))
        except ValueError as e:
            return [f"Invalid project: {e}"]
        except Exception as e:
            _pa.record_error("deploy", str(e))
            return [f"Deploy failed: {e}"]
        try:
            lines = _pa.deploy_listener(root, _pa.PANEL_LISTENER_PORT)
            from frontend.ui_web.recent_projects import add_recent_project

            add_recent_project(str(root))
            for ln in lines:
                _pa._log(ln)
            return lines
        except Exception as e:
            _pa.record_error("deploy", str(e))
            return [f"Deploy failed: {e}"]

    def deploy_all_projects(self) -> list[str]:
        from frontend.ui_web.project_deploy import deploy_all_recent_projects

        lines = deploy_all_recent_projects(log=_pa._log)
        if not lines:
            return ["No projects yet. Open a project from the header, then return here."]
        return lines

    def apply_ide(self, kind: str) -> str:
        # Host writes MCP+skills for every IDE (like pre-gateway peel). Gateway
        # plugins still register hookups for Settings UI grouping.
        key = (kind or "").strip().lower()
        ide = _pa.IdeKind(key)
        s = _pa.replace(_pa.PanelSettings.load(), port=_pa.PANEL_LISTENER_PORT)
        block = _pa.build_uefn_server_block(s)
        path = _pa.path_for_ide(ide, s.antigravity_config_path)
        _pa.merge_uefn_into_config(path, block, dry_run=False)
        for ln in _pa.sync_skill_for_ide(path):
            _pa._log(ln)
        _pa._log(f"Applied → {path}")
        return f"Applied → {path}"

    def test_ide(self, kind: str) -> dict[str, Any]:
        key = (kind or "").strip().lower()
        _pa.IdeKind(key)  # raises on unknown IDE kind
        s = _pa.replace(_pa.PanelSettings.load(), port=_pa.PANEL_LISTENER_PORT)
        block = _pa.build_uefn_server_block(s)
        cmd = block.get("command", "")
        args = list(block.get("args") or [])
        ok, detail = _pa.probe_stdio_mcp(cmd, args)
        _pa._log(f"Test {key}: {detail[:500]}")
        return {"ok": ok, "detail": detail}

    def get_ide_statuses(self) -> dict[str, dict[str, Any]]:
        from frontend.ide_apply import verify_all_ide_bridges

        return verify_all_ide_bridges()

    def apply_all_ides(self) -> list[str]:
        """Apply MCP+skills for every host IDE (Cursor / Claude / Antigravity)."""
        from frontend.ide_apply import ALL_IDES

        out: list[str] = []
        for ide in ALL_IDES:
            kind = ide.value
            try:
                msg = self.apply_ide(kind)
                out.append(f"{kind}: {msg}")
            except Exception as e:
                out.append(f"{kind}: error: {e}")
        try:
            for ln in _pa.sync_skill_on_mcp_update(_pa.PanelSettings.load().antigravity_config_path):
                _pa._log(ln)
                out.append(ln)
        except Exception as e:
            out.append(f"skill sync: error: {e}")
        return out
