"""ducky_update_plan passes reason= to the plan close, so open steps say why they were X'd."""

from __future__ import annotations

import json

from backend.agent.coding_agents import plans
from backend.tools.panel import ducky_panel


def _stub(monkeypatch, update):
    monkeypatch.setattr(ducky_panel, "_resolve_plan_chat_id", lambda chat_id: "lead")
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(plans, "update_plan", update)
    monkeypatch.setattr(plans, "push_plan_updated", lambda plan: None)
    monkeypatch.setattr(plans, "todo_progress", lambda plan: {"total": 1})
    monkeypatch.setattr(plans, "outline_numbers", lambda nodes: [])
    monkeypatch.setattr(plans, "attach_next_tick", lambda out, plan: out)


def test_the_close_reason_reaches_update_plan(monkeypatch):
    seen: dict = {}

    def update(chat_id, **kw):
        seen.update(kw, chat_id=chat_id)
        return {"id": "p", "nodes": [], "status": kw.get("status")}

    _stub(monkeypatch, update)
    out = json.loads(ducky_panel.ducky_update_plan(status="finished", reason="moved to 1.2.471"))
    assert out["ok"] and seen["chat_id"] == "lead"
    assert seen["status"] == "finished" and seen["reason"] == "moved to 1.2.471"


def test_a_plans_module_without_reason_still_closes_the_plan(monkeypatch):
    calls: list = []

    def update(chat_id, *, title=None, overview=None, body_markdown=None, nodes=None, todos=None,
               merge=True, status=None, project_root=None):
        calls.append(status)
        return {"id": "p", "nodes": [], "status": status}

    _stub(monkeypatch, update)
    out = json.loads(ducky_panel.ducky_update_plan(status="finished", reason="moved"))
    assert out["ok"] and calls == ["finished"]
