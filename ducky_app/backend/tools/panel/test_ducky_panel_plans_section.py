"""ducky_get_plan(section=) finds a step by its id, outline number or title; a miss shows the outline."""
import json

import pytest

from backend.agent.coding_agents import plans
from backend.tools.panel import ducky_panel_plans as dpp


def _step(sid, title, kids=()):
    return {"id": sid, "content": title, "status": "pending", "children": list(kids)}


PLAN = {"id": "p1", "chat_id": "c1", "title": "Fixes", "status": "open", "nodes": [
    _step("s1", "Stop hung Epic calls", [_step("s1-a", "Prove the hang"), _step("s1-b", "Bound the ping")]),
    _step("s2", "Make get_plan forgiving", [_step("s2-a", "Match by number")]),
]}


@pytest.fixture(autouse=True)
def _plan(monkeypatch):
    monkeypatch.setattr(dpp, "_resolve_plan_chat_id", lambda chat_id="": "c1")
    monkeypatch.setattr(dpp, "_project_root", lambda: "")
    monkeypatch.setattr(plans, "load_plan_view", lambda cid, project_root=None: PLAN)
    monkeypatch.setattr(plans, "attach_next_tick", lambda out, plan, caller="": out)


def _get(section):
    return json.loads(dpp.ducky_get_plan(section=section))


@pytest.mark.parametrize("section, want_id, want_n", [
    ("s1-b", "s1-b", "1.2"),
    ("1.2", "s1-b", "1.2"),
    ("1.2.", "s1-b", "1.2"),
    ("2", "s2", "2"),
    ("bound THE ping", "s1-b", "1.2"),
    ("Make get_plan", "s2", "2"),
])
def test_a_step_is_found_by_id_number_or_title(section, want_id, want_n):
    got = _get(section)
    assert got["ok"] and got["section"]["id"] == want_id and got["n"] == want_n


@pytest.mark.parametrize("section", ["nope", "9.9"])
def test_a_miss_answers_with_the_outline(section):
    got = _get(section)
    assert not got["ok"] and section in got["error"]
    assert [r["n"] for r in got["outline"]] == ["1", "1.1", "1.2", "2", "2.1"]


def test_an_ambiguous_title_start_is_a_miss():
    got = _get("Ma")  # "Make get_plan forgiving" and "Match by number"
    assert not got["ok"] and len(got["outline"]) == 5
