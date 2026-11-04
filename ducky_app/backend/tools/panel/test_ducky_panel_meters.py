"""ducky_perf_report shows the per-process blocks; ducky_agent_list rows carry agent states."""
import json
from types import SimpleNamespace

from backend.tools.panel import ducky_panel


def test_perf_report_has_the_per_process_blocks(monkeypatch):
    from backend import bridge
    from frontend import perf_trace

    block = {"ok": True, "ui_js_big_top": [["list_messages", 3, 900_000]],
             "processes": [{"pid": 1, "role": "app window"}, {"pid": 2, "role": "tool server"},
                           {"pid": 3, "role": "other"}]}
    monkeypatch.setattr(perf_trace, "write_report", lambda: {"ring_size": 5, "hints": []})
    monkeypatch.setattr(perf_trace, "per_process_report", lambda: block)
    monkeypatch.setattr(bridge, "listener_get_health", lambda *a, **k: None)
    out = json.loads(ducky_panel.ducky_perf_report())
    assert out["ring_size"] == 5 and out["per_process"] == block

    def broken():
        raise RuntimeError("no events table")

    monkeypatch.setattr(perf_trace, "per_process_report", broken)
    out = json.loads(ducky_panel.ducky_perf_report())
    assert out["ring_size"] == 5 and out["per_process"] == {"ok": False, "error": "no events table"}


def test_agent_list_rows_carry_state_since_and_detail(monkeypatch):
    from frontend.ui_web import agent_state, group_orchestrator
    from frontend.ui_web.agent_turns.testing import patch_turns

    chats = [SimpleNamespace(id=cid, title=title, folder_id="", parent_conv_id="", ducky_name="",
                             coding_agent="claude", messages=[]) for cid, title in
             (("lead", "Lead: Google Workspace plugin"), ("idle", "Idle chat"))]
    monkeypatch.setattr(ducky_panel, "list_conversations", lambda project_root=None: chats)
    monkeypatch.setattr(ducky_panel, "_project_root", lambda: "")
    monkeypatch.setattr(ducky_panel, "_resolve_sender", lambda s: "")
    patch_turns(monkeypatch, "is_agent_running", lambda cid: cid == "lead")
    patch_turns(monkeypatch, "linked_parent_of", lambda cid: "")
    monkeypatch.setattr(group_orchestrator, "is_group_conversation", lambda conv: False)
    asked = []
    monkeypatch.setattr(agent_state, "agent_state", lambda cid: asked.append(cid) or
                        {"state": "needs_answer", "since_s": 720, "detail": "Which repo?"})
    rows = {r["id"]: r for r in json.loads(ducky_panel.ducky_agent_list())["agents"]}
    assert rows["lead"]["state"] == "needs_answer" and rows["lead"]["since_s"] == 720
    assert rows["lead"]["detail"] == "Which repo?" and rows["lead"]["title"] == "Lead: Google Workspace plugin"
    assert "state" not in rows["idle"] and asked == ["lead"]  # only running agents are classified
