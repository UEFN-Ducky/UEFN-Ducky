"""PanelApi surface for Automations (graphs, catalog, test run)."""

from __future__ import annotations

from typing import Any


class PanelApiAutomationsMixin:
    def list_automation_nodes(self) -> dict[str, Any]:
        from backend.automations.catalog import list_nodes

        return {"ok": True, "nodes": list_nodes()}

    def list_automations(self) -> dict[str, Any]:
        from backend.automations.store import list_automations

        return {"ok": True, "automations": list_automations()}

    def get_automation(self, workflow_id: str) -> dict[str, Any]:
        from backend.automations.store import get_automation

        wf = get_automation(workflow_id)
        if wf is None:
            return {"ok": False, "error": "automation not found"}
        return {"ok": True, "automation": wf}

    def save_automation(self, doc: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        from backend.automations.store import save_automation

        payload = dict(doc or {})
        payload.update(extra)
        return {"ok": True, "automation": save_automation(payload)}

    def delete_automation(self, workflow_id: str) -> dict[str, Any]:
        from backend.automations.store import delete_automation

        if not delete_automation(workflow_id):
            return {"ok": False, "error": "automation not found"}
        return {"ok": True}

    def run_automation(
        self,
        workflow_id: str,
        trigger_id: str = "",
        payload: dict[str, Any] | None = None,
        starter_id: str = "",
    ) -> dict[str, Any]:
        from backend.automations.runner import run_automation

        return run_automation(
            workflow_id,
            trigger_id=trigger_id,
            payload=payload or {},
            starter_id=starter_id,
        )

    def emit_automation(self, trigger_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from backend.automations.runner import emit_automation

        return emit_automation(trigger_id, payload or {})

    def list_automation_templates(self) -> dict[str, Any]:
        from backend.automations.templates import list_templates

        return {"ok": True, "templates": list_templates()}

    def save_custom_automation_template(
        self,
        name: str,
        description: str = "",
        icon: str = "⚡",
        graph_json: str = "",
        template_id: str = "",
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
            )
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "template": row}

    def delete_custom_automation_template(self, template_id: str) -> dict[str, Any]:
        from backend.automations.templates import delete_custom

        if not delete_custom(template_id):
            return {"ok": False, "error": "template not found"}
        return {"ok": True}
