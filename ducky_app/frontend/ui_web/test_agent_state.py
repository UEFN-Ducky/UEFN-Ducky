"""agent_state: one plain state per running agent, from seeded fakes only."""

from __future__ import annotations

import time

import pytest

from frontend.ui_web import agent_state, ui_rpc
from frontend.ui_web.agent_turns.testing import patch_turns


class _Terminals:
    def __init__(self, rows):
        self.rows = rows

    def list_pending(self):
        return self.rows


@pytest.fixture
def seed(monkeypatch):
    facts = {"question": None, "approval": [], "tool": None, "output_age_s": None}
    monkeypatch.setattr(ui_rpc, "pending_question", lambda cid: facts["question"])
    monkeypatch.setattr(ui_rpc, "has_pending_for_conv", lambda cid: facts["question"] is not None)
    monkeypatch.setattr("frontend.ui_web.terminal.get_terminal_manager", lambda: _Terminals(facts["approval"]))
    patch_turns(monkeypatch, "running_tool", lambda cid: facts["tool"])
    patch_turns(monkeypatch, "last_output_age_s", lambda cid: facts["output_age_s"])
    patch_turns(monkeypatch, "list_running_agents", lambda: ["c1"])
    return facts


def test_pending_question_needs_an_answer(seed):
    seed.update(question={"request_id": "r1", "text": "Which folder?", "since": time.time() - 62}, output_age_s=5)
    assert agent_state.agent_state("c1") == {"state": "needs_answer", "since_s": 62, "detail": "Which folder?"}


def test_pending_approval_needs_approval(seed):
    seed.update(approval=[{"conv_id": "c1", "command": "pytest -q", "created_at": time.time() - 20}], output_age_s=5)
    out = agent_state.agent_state("c1")
    assert out["state"] == "needs_approval" and out["detail"] == "pytest -q" and 19 <= out["since_s"] <= 21


def test_a_tool_running_40_s_is_a_running_command(seed):
    seed.update(tool={"name": "workspace_search", "started_at": time.time() - 40}, output_age_s=40)
    assert agent_state.agent_state("c1") == {"state": "running_command", "since_s": 40, "detail": "workspace_search"}


def test_output_10_s_ago_is_working(seed):
    seed.update(tool={"name": "workspace_read_file", "started_at": time.time() - 2}, output_age_s=10)
    assert agent_state.agent_state("c1")["state"] == "working"


def test_output_15_min_ago_is_silent(seed):
    seed.update(output_age_s=900)
    assert agent_state.agent_state("c1") == {"state": "silent", "since_s": 900, "detail": ""}


def test_running_agents_lists_each_with_its_state(seed):
    seed.update(question={"request_id": "r1", "text": "Which folder?", "since": time.time() - 3})
    assert agent_state.running_agents() == [{"id": "c1", "state": "needs_answer", "since_s": 3, "detail": "Which folder?"}]
