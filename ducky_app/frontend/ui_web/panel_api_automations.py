"""PanelApi surface for Workflows (graphs, owners, catalog, test run)."""

from __future__ import annotations

from typing import Any


def _refused(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "error": str(exc) or "Not allowed"}


def _approve_typed_code(before: dict[str, Any] | None, saved: dict[str, Any]) -> None:
    """The editor sends the whole workflow on any save (On/off, a rename, a moved card),
    so only code this save changed was typed by the person: a new Custom code node, or
    one whose code differs from what was stored. Code an agent saved stays unreviewed.
    A new workflow (Duplicate, Make reusable) keeps only approvals its code already had."""
    from backend.automations.code_approval import approve, is_local, node_sha
    from backend.automations.store import CODE_TYPE, all_workflows, carry_approvals

    def code_nodes(wf: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        nodes = ((wf or {}).get("graph") or {}).get("nodes") or []
        return {str(n.get("id")): n for n in nodes if isinstance(n, dict) and n.get("type") == CODE_TYPE}

    after = code_nodes(saved)
    if not after or not is_local(saved):
        return
    wid = str(saved.get("id") or "")
    if before is None:
        carry_approvals(saved, [w["id"] for w in all_workflows() if code_nodes(w).keys() & after.keys()])
        return
    stored = {nid: node_sha(node) for nid, node in code_nodes(before).items()}
    for nid, node in after.items():
        sha = node_sha(node)
        if sha and stored.get(nid) != sha:
            approve(wid, nid, sha, "person")


class PanelApiAutomationsMixin:
    def list_workflow_nodes(self) -> dict[str, Any]:
        from backend.automations.catalog import list_nodes

        return {"ok": True, "nodes": list_nodes()}

    def get_workflow_tools_catalog(self) -> dict[str, Any]:
        """Host tools a Call tool node can run. Never connects nested MCP plugins
        (that is the Settings catalog); run it as a bridge job all the same."""
        from frontend.ui_web.mcp_catalog import build_workflow_tool_catalog

        return build_workflow_tool_catalog()

    def list_workflows(self) -> dict[str, Any]:
        from backend.automations.store import list_workflows

        return {"ok": True, "workflows": list_workflows()}

    def workflow_owners(self, refresh: bool = False) -> dict[str, Any]:
        """Folders for the list. ``refresh`` asks the Store for the account's teams
        (one hub call, when the view opens)."""
        from backend.automations import team

        try:
            return team.refresh_teams() if refresh else team.owners()
        except Exception as exc:
            return _refused(exc)

    def workflow_sync(self, force: bool = False, team_id: str = "", upload: bool = False) -> dict[str, Any]:
        """Team rounds while the Workflows view is open. A team id (Save, Update
        online) pushes that team's workflows and waits for the round."""
        from backend.automations import team

        try:
            return team.sync(force=bool(force), team_id=str(team_id or ""), upload=bool(upload))
        except Exception as exc:
            return _refused(exc)

    def workflow_open_web(self, team_id: str) -> dict[str, Any]:
        from backend.automations import team

        try:
            return team.open_web(str(team_id or ""))
        except Exception as exc:
            return _refused(exc)

    def import_local_workflows(self) -> dict[str, Any]:
        from backend.automations import team

        try:
            return team.import_local()
        except (PermissionError, ValueError) as exc:
            return _refused(exc)

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        from backend.automations.store import get_workflow

        from backend.automations.media import refresh_links

        wf = get_workflow(workflow_id)
        if wf is None:
            return {"ok": False, "error": "workflow not found"}
        return {"ok": True, "workflow": refresh_links(wf)}

    def list_workflow_versions(self, workflow_id: str) -> dict[str, Any]:
        from backend.automations.versions import list_versions
        return {"ok": True, "versions": list_versions(workflow_id)}

    def get_workflow_version(self, workflow_id: str, version_id: str) -> dict[str, Any]:
        from backend.automations.versions import get_version
        doc = get_version(workflow_id, version_id)
        return {"ok": True, "workflow": doc} if doc else {"ok": False, "error": "Version not found"}

    def save_workflow(self, doc: dict[str, Any] | None = None, owner: str = "", **extra: Any) -> dict[str, Any]:
        from backend.automations.store import get_workflow, save_workflow

        payload = dict(doc or {})
        payload.update(extra)
        wid = str(payload.get("id") or "").strip()
        before = get_workflow(wid) if wid else None
        try:
            saved = save_workflow(payload, owner=owner)
        except (PermissionError, ValueError) as exc:
            return _refused(exc)
        _approve_typed_code(before, saved)
        return {"ok": True, "workflow": saved}

    def copy_workflow(self, workflow_id: str, owner: str, move: bool = False) -> dict[str, Any]:
        from backend.automations.store import copy_workflow

        try:
            return {"ok": True, "workflow": copy_workflow(workflow_id, owner, move=bool(move))}
        except KeyError:
            return {"ok": False, "error": "workflow not found"}
        except (PermissionError, ValueError) as exc:
            return _refused(exc)

    def set_workflow_folder(self, workflow_id: str, folder: str = "") -> dict[str, Any]:
        """File a workflow in a folder of its owner (drag in the list)."""
        from backend.automations.store import set_folder

        try:
            wf = set_folder(workflow_id, folder)
        except (PermissionError, ValueError) as exc:
            return _refused(exc)
        return {"ok": True, "workflow": wf} if wf else {"ok": False, "error": "workflow not found"}

    def move_workflow_folder(self, owner: str, path: str, new_path: str = "") -> dict[str, Any]:
        """Rename or move a folder; ``new_path`` = its parent deletes it, keeping the workflows."""
        from backend.automations.store import move_folder

        try:
            return {"ok": True, "moved": move_folder(owner, path, new_path)}
        except (PermissionError, ValueError) as exc:
            return _refused(exc)

    def add_workflow_folder(self, owner: str, path: str) -> dict[str, Any]:
        """Make a folder that may hold nothing yet (a team's syncs to every member)."""
        from backend.automations.store import add_folder

        try:
            return {"ok": True, "folders": add_folder(owner, path)}
        except (PermissionError, ValueError) as exc:
            return _refused(exc)

    def export_workflow_folder(self, owner: str, path: str) -> dict[str, Any]:
        """A folder with everything in it (nested folders, empty ones too) as one bundle."""
        from backend.automations.bundles import export_folder

        try:
            return {"ok": True, "bundle": export_folder(owner, path)}
        except KeyError:
            return {"ok": False, "error": "folder not found"}
        except (PermissionError, ValueError) as exc:
            return _refused(exc)

    def import_workflow_bundle(self, bundle: dict[str, Any] | None = None, owner: str = "local", parent_path: str = "",
                               name: str = "") -> dict[str, Any]:
        """Make a bundle's folder tree under ``owner``/``parent_path`` with new ids."""
        from backend.automations.bundles import import_bundle

        try:
            return {"ok": True, **import_bundle(bundle, owner, parent_path, name=name or None)}
        except (PermissionError, ValueError) as exc:
            return _refused(exc)

    def copy_workflow_folder(self, owner_from: str, path: str, owner_to: str, parent_path: str = "",
                             move: bool = False) -> dict[str, Any]:
        """Copy or move a whole folder tree to Local or a team, Run workflow steps intact."""
        from backend.automations.bundles import copy_folder

        try:
            return {"ok": True, **copy_folder(owner_from, path, owner_to, parent_path, move=bool(move))}
        except KeyError:
            return {"ok": False, "error": "folder not found"}
        except (PermissionError, ValueError) as exc:
            return _refused(exc)

    def set_workflow_run_here(self, workflow_id: str, on: bool) -> dict[str, Any]:
        from backend.automations.store import set_run_here

        wf = set_run_here(workflow_id, bool(on))
        return {"ok": True, "workflow": wf} if wf else {"ok": False, "error": "workflow not found"}

    def clear_workflow_runs(self, workflow_id: str) -> dict[str, Any]:
        """Run log → Clear log: forget this PC's past runs of one workflow."""
        from backend.automations.store import clear_runs

        return {"ok": True} if clear_runs(workflow_id) else {"ok": False, "error": "workflow not found"}

    def delete_workflow(self, workflow_id: str) -> dict[str, Any]:
        from backend.automations.store import delete_workflow

        try:
            ok = delete_workflow(workflow_id)
        except PermissionError as exc:
            return _refused(exc)
        return {"ok": True} if ok else {"ok": False, "error": "workflow not found"}

    def run_workflow(
        self,
        workflow_id: str,
        prompt: str = "",
        files: list[Any] | None = None,
        caller_conv_id: str = "",
        payload: dict[str, Any] | None = None,
        trigger_id: str = "",
        starter_id: str = "",
    ) -> dict[str, Any]:
        from backend.automations.runner import run_workflow

        # Test in the editor: a person started it, so its Custom code may run.
        return run_workflow(
            workflow_id,
            trigger_id=trigger_id,
            payload={**(payload or {}), "_person_started": True},
            starter_id=starter_id,
            prompt=prompt,
            files=files,
            caller_conv_id=caller_conv_id,
        )

    def run_workflow_node(self, workflow_id: str, node_id: str, approve_spend: bool = False) -> dict[str, Any]:
        """Run this node only: what feeds it is reused from the last run (no paid repeats).
        approve_spend: the person pressed play, so paid steps this run needs may spend."""
        from backend.automations.runner import run_node

        return run_node(workflow_id, node_id, approve_spend=bool(approve_spend), person=True)

    def get_workflow_node_code(self, workflow_id: str, node_id: str, node: dict[str, Any] | None = None) -> dict[str, Any]:
        """Code tab: the JavaScript a node runs (a built-in's as generated code). ``node``:
        the editor's unsaved node, so a built-in's code shows the settings on screen."""
        from backend.tools.panel.panel_automations import node_code

        return node_code(workflow_id, node_id, draft=node if isinstance(node, dict) else None,
                         missing="Save the workflow first to see its code.")

    def edit_workflow_node_code(
        self,
        workflow_id: str,
        node_id: str,
        code: str | None = None,
        edits: list[Any] | None = None,
        revert: bool = False,
        expected_sha: str | None = None,
    ) -> dict[str, Any]:
        """Write a node's code (or revert it) and save; a person's save approves it."""
        from backend.tools.panel.panel_automations import edit_node_code

        return edit_node_code(workflow_id, node_id, code=code, edits=edits, revert=bool(revert),
                              expected_sha=expected_sha, person=True)

    def test_workflow_node(
        self,
        workflow_id: str,
        node_id: str,
        code: str | None = None,
        inputs: dict[str, Any] | None = None,
        settings: dict[str, Any] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Code tab's Test: run one Custom code node with draft code, nothing saved."""
        from backend.automations.runner import run_code_draft

        return run_code_draft(workflow_id, node_id, code=code, inputs=inputs, settings=settings,
                              dry_run=bool(dry_run), person=True)

    def workflow_code_api(self, tools: list[str] | None = None) -> dict[str, Any]:
        """The ducky API's types and docs for the Code tab's editor."""
        from backend.tools.panel.panel_automations import code_api_reference

        return code_api_reference(tools)

    def check_workflow_node_code(self, code: str = "") -> dict[str, Any]:
        """Problems in draft code as it is typed (nothing runs, nothing saved)."""
        from backend.automations.code_check import check

        return check(str(code or ""))

    def approve_workflow_node_code(self, workflow_id: str, node_id: str, code_sha: str) -> dict[str, Any]:
        """Review: the person read this exact code and lets it run on its own here."""
        from backend.automations.code_approval import approve, is_local, node_sha
        from backend.automations.store import get_workflow

        wf = get_workflow(workflow_id)
        if wf is None:
            return {"ok": False, "error": "workflow not found"}
        node = next((n for n in (wf.get("graph") or {}).get("nodes") or [] if str(n.get("id")) == str(node_id)), None)
        if node is None or node.get("type") != "code.js":
            return {"ok": False, "error": "That node isn't Custom code in the saved workflow; save first."}
        if not is_local(wf):
            from backend.automations.code_approval import TEAM_REFUSAL

            return {"ok": False, "error": TEAM_REFUSAL}
        sha = node_sha(node)
        if str(code_sha or "").strip() != sha:
            return {"ok": False, "error": "The code changed since you opened it; review it again.", "code_sha": sha}
        approve(str(wf["id"]), str(node["id"]), sha, "person")
        return {"ok": True, "approved": True, "code_sha": sha}

    def keep_workflow_preview(self, workflow_id: str, node_id: str) -> dict[str, Any]:
        """A Preview's "Use this": run the steps that take what it shows, with that value."""
        from backend.automations.runner import keep_preview

        return keep_preview(workflow_id, node_id)

    def pick_workflow_folder(self) -> dict[str, Any]:
        """Save file nodes: the Windows folder picker."""
        win = getattr(self, "_window", None)
        if win is None:
            return {"ok": False, "error": "No window to open the picker from", "folder": ""}
        try:
            import webview

            try:
                folder_type = webview.FileDialog.FOLDER
            except AttributeError:
                folder_type = getattr(webview, "FOLDER_DIALOG", 20)
            picked = win.create_file_dialog(folder_type)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "folder": ""}
        folder = picked[0] if isinstance(picked, (list, tuple)) and picked else picked or ""
        return {"ok": True, "folder": str(folder or "")}

    def check_workflow_expression(self, expression: str = "") -> dict[str, Any]:
        """If / Expression nodes: '' when the condition parses, else what is wrong."""
        from backend.automations.expr import check

        error = check(str(expression or ""))
        return {"ok": not error, "error": error}

    def pick_workflow_files(self, accept: str = "any", multiple: bool = False) -> dict[str, Any]:
        """Input nodes: the Windows file picker, filtered to what the node takes."""
        from backend.automations.files import FILE_FILTERS, file_ref, kind_of, with_url

        win = getattr(self, "_window", None)
        if win is None:
            return {"ok": False, "error": "No window to open the picker from", "files": []}
        try:
            import webview

            try:
                open_type = webview.FileDialog.OPEN
            except AttributeError:
                open_type = getattr(webview, "OPEN_DIALOG", 10)
            picked = win.create_file_dialog(open_type, allow_multiple=bool(multiple), file_types=FILE_FILTERS.get(str(accept or "any"), FILE_FILTERS["any"]))
        except Exception as exc:
            return {"ok": False, "error": str(exc), "files": []}
        kind = str(accept or "any")
        return {"ok": True, "files": [with_url(file_ref(path, kind if kind != "any" else kind_of(path))) for path in (picked or [])]}

    def workflow_editor_prefs(self) -> dict[str, Any]:
        """Grid, snap, tool, panel sizes and zoom of the Workflows editor on this PC."""
        from backend.automations.editor_prefs import load

        return {"ok": True, "prefs": load()}

    def set_workflow_editor_prefs(self, prefs: dict[str, Any] | None = None) -> dict[str, Any]:
        from backend.automations.editor_prefs import save

        try:
            return {"ok": True, "prefs": save(dict(prefs or {}))}
        except ValueError as exc:
            return _refused(exc)

    def stop_workflow(self, workflow_id: str) -> dict[str, Any]:
        """Stop button: end every run of this workflow on this PC now."""
        from backend.automations.runner import stop_workflow

        return {"ok": True, "stopped": stop_workflow(workflow_id)}

    def emit_workflow_trigger(self, trigger_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from backend.automations.runner import emit_trigger

        return emit_trigger(trigger_id, payload or {})

    def list_workflow_templates(self) -> dict[str, Any]:
        from backend.automations.templates import list_templates

        return {"ok": True, "templates": list_templates()}

    def save_workflow_template(
        self,
        name: str,
        description: str = "",
        icon: str = "⚡",
        graph_json: str = "",
        template_id: str = "",
        category: str = "",
    ) -> dict[str, Any]:
        from backend.automations.templates import save_custom

        graph: Any = {}
        raw = (graph_json or "").strip()
        if raw:
            import json

            try:
                graph = json.loads(raw)
            except json.JSONDecodeError as exc:
                return {"ok": False, "error": f"bad graph_json: {exc}"}
        try:
            row = save_custom(
                name,
                description=description,
                icon=icon,
                graph=graph,
                template_id=template_id,
                category=category,
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "template": row}

    def delete_workflow_template(self, template_id: str) -> dict[str, Any]:
        from backend.automations.templates import delete_custom

        if not delete_custom(template_id):
            return {"ok": False, "error": "template not found"}
        return {"ok": True}

    def list_generated_images(self) -> dict[str, Any]:
        from frontend.ui_web.generated_images import list_generated_images

        return {"ok": True, "images": list_generated_images()}

    def get_generated_image_attachment(self, name: str) -> dict[str, Any]:
        from frontend.ui_web.generated_images import attachment_from_path, resolve_generated_image_path

        try:
            att = attachment_from_path(resolve_generated_image_path(name))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        if not att:
            return {"ok": False, "error": "not an image"}
        return {"ok": True, "attachment": att}
