"""ducky_update_plan / ducky_create_plan replies stay small: no step bodies unless full=true."""

from __future__ import annotations

import json

from backend.agent.coding_agents import plans
from backend.tools.panel import ducky_panel

_BODY = "**Why:** " + "a long step description that explains the work in detail. " * 25


def _big_plan(**kw):
    nodes = [{"id": f"s{i}", "content": f"Step {i}", "status": "pending", "body_markdown": _BODY, "children": []}
             for i in range(50)]
    return {"id": "p1", "chat_id": "lead", "title": "Big plan", "status": "open", "nodes": nodes}


def _stub(monkeypatch):
    monkeypatch.setattr(ducky_panel, "_resolve_plan_chat_id", lambda chat_id: "lead")
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(plans, "update_plan", lambda cid, **kw: _big_plan())
    monkeypatch.setattr(plans, "create_plan", lambda cid, **kw: _big_plan())
    monkeypatch.setattr(plans, "push_plan_updated", lambda plan: None)
    monkeypatch.setattr("backend.tools.panel.ducky_swarm.suggest_swarm", lambda plan: None)


def test_a_50_step_plan_reply_stays_under_8k_chars(monkeypatch):
    _stub(monkeypatch)
    assert len(json.dumps(_big_plan())) > 60_000  # what the reply used to echo
    for reply in (ducky_panel.ducky_update_plan(title="Big plan"), ducky_panel.ducky_create_plan(title="Big plan")):
        assert len(reply) < 8_000, len(reply)
        out = json.loads(reply)
        assert out["plan"] == {"id": "p1", "chat_id": "lead", "title": "Big plan", "status": "open"}
        assert len(out["outline"]) == 50 and out["outline"][0]["id"] == "s0"
        assert _BODY not in reply


def test_full_true_returns_the_step_bodies(monkeypatch):
    _stub(monkeypatch)
    for reply in (ducky_panel.ducky_update_plan(title="Big plan", full=True),
                  ducky_panel.ducky_create_plan(title="Big plan", full=True)):
        out = json.loads(reply)
        assert out["plan"]["nodes"][0]["body_markdown"] == _BODY
