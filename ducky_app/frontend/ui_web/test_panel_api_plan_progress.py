"""plan_progress: 20 chats in one call, counts only, warm cache < 5 ms, plan events clear one chat."""

from __future__ import annotations

import json
import time

import pytest

from backend.agent.coding_agents import plans
from frontend.ui_web import panel_api_plan_progress as pp


def _plan(cid: str) -> dict:
    body = "x" * 2000
    return {
        "chat_id": cid,
        "updated_at": "2026-10-24T10:00:00Z",
        "nodes": [
            {"id": f"{cid}-a", "content": "A", "status": "completed", "body_markdown": body, "children": []},
            {"id": f"{cid}-b", "content": "B", "status": "in_progress", "body_markdown": body, "children": [
                {"id": f"{cid}-b1", "content": "B1", "status": "pending", "body_markdown": body,
                 "after": [f"{cid}-b"], "children": []},
            ]},
            {"id": f"{cid}-c", "content": "C", "status": "pending", "body_markdown": body,
             "after": [f"{cid}-a"], "children": []},
            {"id": f"{cid}-d", "content": "D", "status": "cancelled", "body_markdown": body, "children": []},
        ],
    }


@pytest.fixture
def loads(monkeypatch) -> list[str]:
    calls: list[str] = []

    def fake_view(chat_id: str, project_root: str | None = None) -> dict:
        calls.append(chat_id)
        return _plan(chat_id)

    monkeypatch.setattr(plans, "load_plan_view", fake_view)
    monkeypatch.setattr(pp, "_cache", {})
    return calls


def test_twenty_chats_one_call_counts_only_and_warm_under_5ms(loads) -> None:
    api = pp.PanelApiPlanProgressMixin()
    ids = [f"c{n}" for n in range(20)]
    cold = api.plan_progress(ids, project_root="")
    assert cold["ok"] and list(cold["progress"]) == ids
    assert cold["progress"]["c0"] == {"done": 1, "total": 4, "in_progress": 1, "blocked": 1,
                                      "updated_at": "2026-10-24T10:00:00Z", "created_at": 0.0}
    text = json.dumps(cold)
    assert "body_markdown" not in text and "nodes" not in text and "xxxx" not in text
    assert len(text) < 140 * len(ids)
    assert len(loads) == 20

    started = time.perf_counter()
    warm = api.plan_progress(ids, project_root="")
    assert (time.perf_counter() - started) * 1000 < 5
    assert warm == cold and len(loads) == 20


def test_a_plan_event_clears_only_that_chat(loads) -> None:
    api = pp.PanelApiPlanProgressMixin()
    api.plan_progress(["a", "b", "c"], project_root="")
    pp.drop_plan_progress({"type": "chats_changed", "conv_id": "a"})  # not a plan event
    pp.drop_plan_progress({"type": "plan_updated", "conv_id": "b"})
    loads.clear()
    api.plan_progress(["a", "b", "c"], project_root="")
    assert loads == ["b"]
    pp.drop_plan_progress({"type": "plan_assignment_changed", "conv_id": "c"})
    api.plan_progress(["a", "b", "c"], project_root="")
    assert loads == ["b", "c"]


def test_an_unreadable_plan_is_reported_not_cached(monkeypatch) -> None:
    monkeypatch.setattr(pp, "_cache", {})

    def two_plans(chat_id: str, project_root: str | None = None) -> dict:
        raise ValueError("open steps in 2 plans")

    monkeypatch.setattr(plans, "load_plan_view", two_plans)
    out = pp.PanelApiPlanProgressMixin().plan_progress(["m"], project_root="")
    assert out["progress"]["m"]["total"] == 0 and "2 plans" in out["progress"]["m"]["error"]
    assert pp._cache == {}
