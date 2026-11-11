"""Agent and workflow ends alert: which chat, never the reply, never sub-agents, never a quick reply."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from backend.automations import runner
from frontend import notifications
from frontend.ui_web.agent_turns import state
from frontend.ui_web.agent_turns.testing import patch_turns


def test_only_top_level_failures_and_long_finishes_alert(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []
    patch_turns(monkeypatch, "_alert_finished", lambda conv_id, reason: calls.append((conv_id, reason)))
    patch_turns(monkeypatch, "close_changeset_run", lambda *_a: None)
    events: list[dict] = []

    state._push_agent_stopped(events.append, "c1", "run-1", "done")
    assert calls == [("c1", "done")]

    state._quiet_runs.add("run-2")  # started by another chat or a workflow
    state._push_agent_stopped(events.append, "c2", "run-2", "error")
    assert calls == [("c1", "done")]
    assert "run-2" not in state._quiet_runs

    state._push_agent_stopped(events.append, "c3", "run-3", "cancelled")
    assert calls == [("c1", "done")]

    # A quick reply was watched as it happened; a quick failure still alerts.
    state._run_started["run-4"] = time.monotonic()
    state._push_agent_stopped(events.append, "c4", "run-4", "done")
    assert calls == [("c1", "done")]
    state._run_started["run-5"] = time.monotonic()
    state._push_agent_stopped(events.append, "c5", "run-5", "error")
    assert calls[-1] == ("c5", "error")
    state._run_started["run-6"] = time.monotonic() - state.PHONE_PUSH_MIN_SECONDS - 1
    state._push_agent_stopped(events.append, "c6", "run-6", "done")
    assert calls[-1] == ("c6", "done")
    assert not {"run-4", "run-5", "run-6"} & set(state._run_started)


def test_runs_a_workflow_starts_are_quiet(monkeypatch) -> None:
    assert state._started_by_workflow() is False
    token = runner._LIVE.set(("wf1", "r1"))
    try:
        assert state._started_by_workflow() is True
    finally:
        runner._LIVE.reset(token)


def test_group_members_stay_quiet_and_reply_text_never_leaves(monkeypatch) -> None:
    sent: list[tuple[tuple, dict]] = []
    done = threading.Event()

    def fake_alert(*args, **kw):
        sent.append((args, kw))
        done.set()
        return True

    monkeypatch.setattr(notifications, "alert", fake_alert)
    convs = {
        "member": SimpleNamespace(title="Artist", parent_conv_id="hub"),
        "solo": SimpleNamespace(
            title="Build island",
            parent_conv_id="",
            messages=[{"role": "assistant", "content": "secret plan"}],
        ),
    }
    patch_turns(monkeypatch, "load_conversation_meta", lambda cid: convs.get(cid))

    state._alert_finished("member", "done")
    state._alert_finished("solo", "done")
    assert done.wait(5)
    for t in threading.enumerate():
        if t.name == "chat-alert":
            t.join(5)
    assert len(sent) == 1
    args, kw = sent[0]
    assert args == ("Build island", "Finished.")
    assert kw["kind"] == "chat" and kw["target_id"] == "solo" and kw["level"] == "ok"
    assert "secret" not in repr(sent)


def test_workflow_alerts(monkeypatch) -> None:
    sent: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(notifications, "alert", lambda *a, **kw: sent.append((a, kw)))
    wf = {"id": "wf1", "name": "Daily check"}

    runner._alert_run(wf, True, triggered=True, seconds=600)  # a trigger's success: its Message me talks
    runner._alert_run(wf, True, triggered=False, seconds=3)  # watched as it ran
    assert sent == []

    runner._alert_run(wf, False, triggered=True, seconds=1)
    runner._alert_run(wf, True, triggered=False, seconds=45)
    assert sent == [
        (("Daily check", "Workflow failed."), {"level": "error", "kind": "workflow", "target_id": "wf1"}),
        (("Daily check", "Workflow finished."), {"level": "ok", "kind": "workflow", "target_id": "wf1"}),
    ]
