"""Messaging another agent never kills it: a wait that runs out only stops waiting."""

from __future__ import annotations

import json
from types import SimpleNamespace

from backend.tools.panel import ducky_panel
from frontend.ui_web import agent_modes


def test_waiting_on_an_agent_never_cancels_it_when_the_wait_runs_out(monkeypatch) -> None:
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: SimpleNamespace(id=cid, folder_id=""))
    seen: list[dict] = []

    def fake_wait(conv_id, text, mode="agent", model="", **kwargs):
        seen.append(kwargs)
        return {"status": "timeout", "conv_id": conv_id}

    monkeypatch.setattr(agent_modes, "run_message_and_wait", fake_wait)
    json.loads(ducky_panel.ducky_send_chat_message("builder", "status?", timeout_sec=5))
    assert seen and seen[0]["cancel_on_timeout"] is False


def test_messaging_a_busy_agent_without_waiting_queues_instead_of_stopping_it(monkeypatch) -> None:
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "load_conversation", lambda cid, project_root=None: SimpleNamespace(id=cid, folder_id=""))
    seen: list[dict] = []
    monkeypatch.setattr(agent_modes, "run_message", lambda cid, text, mode, model, **kw: seen.append(kw) or "queued")
    out = json.loads(ducky_panel.ducky_send_chat_message("builder", "next task", wait_for_reply=False))
    assert seen and seen[0]["queue_if_busy"] is True
    assert out["status"] == "queued"
