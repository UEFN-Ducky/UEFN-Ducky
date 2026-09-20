"""PanelApi surface for Automations and Pipelines (graphs, catalog, test run)."""

from __future__ import annotations

from typing import Any

from backend.automations.store import KIND_AUTOMATION, KIND_PIPELINE, normalize_kind


class PanelApiAutomationsMixin:
    def list_automation_nodes(self) -> dict[str, Any]:
        from backend.automations.catalog import list_nodes

        return {"ok": True, "nodes": list_nodes(system=KIND_AUTOMATION)}

    def list_pipeline_nodes(self) -> dict[str, Any]:
        from backend.automations.catalog import list_nodes

        return {"ok": True, "nodes": list_nodes(system=KIND_PIPELINE)}

    def list_automations(self) -> dict[str, Any]:
        from backend.automations.store import list_automations

        return {"ok": True, "automations": list_automations(kind=KIND_AUTOMATION)}

    def list_pipelines(self) -> dict[str, Any]:
        from backend.automations.store import list_automations

        return {"ok": True, "pipelines": list_automations(kind=KIND_PIPELINE)}

    def get_automation(self, workflow_id: str) -> dict[str, Any]:
        from backend.automations.store import get_automation

        wf = get_automation(workflow_id)
        if wf is None:
            return {"ok": False, "error": "automation not found"}
        return {"ok": True, "automation": wf}

    def get_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        from backend.automations.store import get_automation

        wf = get_automation(pipeline_id)
        if wf is None or normalize_kind(wf.get("kind")) != KIND_PIPELINE:
            return {"ok": False, "error": "pipeline not found"}
        return {"ok": True, "pipeline": wf, "automation": wf}

    def save_automation(self, doc: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        from backend.automations.store import save_automation

        payload = dict(doc or {})
        payload.update(extra)
        payload.setdefault("kind", KIND_AUTOMATION)
        return {"ok": True, "automation": save_automation(payload)}

    def save_pipeline(self, doc: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        from backend.automations.store import save_automation

        payload = dict(doc or {})
        payload.update(extra)
        payload["kind"] = KIND_PIPELINE
        saved = save_automation(payload)
        return {"ok": True, "pipeline": saved, "automation": saved}

    def delete_automation(self, workflow_id: str) -> dict[str, Any]:
        from backend.automations.store import delete_automation

        if not delete_automation(workflow_id):
            return {"ok": False, "error": "automation not found"}
        return {"ok": True}

    def delete_pipeline(self, pipeline_id: str) -> dict[str, Any]:
        return self.delete_automation(pipeline_id)

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

    def run_pipeline(
        self,
        pipeline_id: str,
        prompt: str = "",
        files: list[Any] | None = None,
        caller_conv_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from backend.automations.runner import run_pipeline

        return run_pipeline(
            pipeline_id,
            prompt=prompt,
            files=files,
            caller_conv_id=caller_conv_id,
            payload=payload or {},
        )

    def emit_automation(self, trigger_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        from backend.automations.runner import emit_automation

        return emit_automation(trigger_id, payload or {})

    def list_automation_templates(self, system: str = KIND_AUTOMATION) -> dict[str, Any]:
        from backend.automations.templates import list_templates

        return {"ok": True, "templates": list_templates(system=system)}

    def list_pipeline_templates(self) -> dict[str, Any]:
        from backend.automations.templates import list_templates

        return {"ok": True, "templates": list_templates(system=KIND_PIPELINE)}

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
