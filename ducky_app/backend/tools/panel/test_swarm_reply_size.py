"""ducky_swarm_create replies stay small: a big plan's reply is the seat list, detail=true the tree."""

from __future__ import annotations

import json

from backend.tools.panel import ducky_swarm as sw
from backend.tools.panel.test_swarm_build import Api

BODY = "**Why:** " + "a long step body that explains everything in detail. " * 40


def _big_plan():
    secs = []
    for i in range(1, 13):
        steps = [{"id": f"s{i}-{k}", "content": f"Seat {i} step {k}", "body_markdown": BODY} for k in range(3)]
        secs.append({"id": f"s{i}", "content": f"Seat {i}: a section with a title",
                     "body_markdown": f"**Files:** `src/area{i}/**`\nDucky: developer\nModel: claude_code:claude-opus-5-5\n{BODY}",
                     "children": steps})
    return {"id": "p", "title": "Twelve seats", "nodes": secs}


def _call(monkeypatch, tmp_path, **kw):
    plan = _big_plan()
    monkeypatch.setattr(sw, "PanelSwarmApi", lambda: Api(tmp_path, plan=plan))
    return json.loads(sw.ducky_swarm_create(from_plan=True, **kw))


def test_a_twelve_section_preview_reply_is_under_8k(monkeypatch, tmp_path):
    full = sw.swarm_create("", None, from_plan=True, api=Api(tmp_path, plan=_big_plan()))
    assert len(json.dumps(full)) > 60_000  # the old echo
    out = _call(monkeypatch, tmp_path)
    text = json.dumps(out)
    assert len(text) < 8_000 and "long step body" not in text
    assert out["status"] == "needs_confirm" and out["agents"] == 12 and len(out["seats"]) == 12
    assert out["seats"][0] == {"name": "Seat 1: a section with a title", "profile": "developer",
                               "model": "claude-opus-5-5", "coding_agent": "claude_code", "lane_count": 1, "steps": 3}


def test_detail_true_gives_the_bodies(monkeypatch, tmp_path):
    out = _call(monkeypatch, tmp_path, detail=1)
    assert "long step body" in json.dumps(out) and out["preview"]["tree"]


def test_an_error_reply_puts_error_first_and_drops_the_tree():
    out = sw.reply_for({"status": "error", "goal": "G", "error": "boom", "tree": [{"name": "x"}]})
    assert list(out)[0] == "error" and out == {"error": "boom", "status": "error", "goal": "G"}


def test_a_small_reply_keeps_the_card():
    small = {"ui": "swarm_builder", "status": "needs_confirm", "goal": "G", "preview": {"tree": []}}
    assert sw.reply_for(small) is small
