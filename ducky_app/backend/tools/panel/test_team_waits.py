"""Team and window tools return inside their bound when the thing they wait on hangs."""
from __future__ import annotations

import inspect
import json
import threading
import time
from types import SimpleNamespace

import pytest

from backend.tools.panel import ducky_panel as panel, permission_prompt, team_waits
from frontend.ui_web.agent_turns.testing import patch_turns
from frontend.ui_web.test_context_session_reset import member  # noqa: F401  (fixture)


@pytest.fixture
def release():
    gate = threading.Event()
    yield gate
    gate.set()  # let the hung worker threads end


def test_agent_send_returns_when_the_broker_hangs(monkeypatch, release) -> None:
    from backend.agent import a2a_client

    monkeypatch.setattr(team_waits, "AGENT_SEND_S", 0.3)
    hung = team_waits.bounded(0.3, team_waits.AGENT_SEND_LATE)(inspect.unwrap(panel.ducky_agent_send))
    monkeypatch.setattr(panel, "_resolve_sender", lambda _: "lead")
    monkeypatch.setattr(panel, "_project_root", lambda: "project")
    monkeypatch.setattr(panel, "load_conversation", lambda cid, project_root=None: SimpleNamespace(id=cid, folder_id="f"))
    monkeypatch.setattr(panel, "_works_alone", lambda *a: False)
    monkeypatch.setattr(panel, "_lead_uses_plan", lambda *a: False)
    monkeypatch.setattr(panel, "_owed_reply", lambda *a: "")
    monkeypatch.setattr(a2a_client, "send", lambda **k: release.wait(30) or {})
    started = time.monotonic()
    out = json.loads(hung("member", "hello", expect_reply=False))
    assert time.monotonic() - started < 5
    assert out["status"] == "timeout" and "do not send it again" in out["error"]


def test_agent_send_keeps_its_signature_and_answer() -> None:
    assert list(inspect.signature(panel.ducky_agent_send).parameters)[:2] == ["to", "message"]
    assert panel.ducky_agent_send.__doc__.startswith("Send a message")
    assert team_waits.bounded(1, "late")(lambda pretty=False: "ok")() == "ok"
    with pytest.raises(ValueError):
        team_waits.bounded(1, "late")(lambda: (_ for _ in ()).throw(ValueError("x")))()


def test_recycle_never_waits_past_its_budget(member, monkeypatch) -> None:
    # Before: 600 s hand-off + 600 s idle poll + 900 s (external CLI: unlimited) reply wait.
    conv = member.conv
    conv.parent_conv_id = "group"
    conv.folder_id = "folder"
    conv.coding_agent = "codex"
    conv.ducky_name = "Builder"
    group = SimpleNamespace(is_group=True)
    clock = [0.0]
    monkeypatch.setattr(panel.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(panel.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(panel, "_project_root", lambda: "project")
    monkeypatch.setattr(panel, "load_conversation", lambda cid, project_root=None: conv if cid == "builder" else group)
    monkeypatch.setattr(permission_prompt, "approvals_of", lambda _: {})
    waits = []

    def run(cid, text, mode, *args, timeout_sec, **kwargs):
        waits.append(timeout_sec)
        clock[0] += timeout_sec  # the member never answers in time
        return {"status": "timeout", "error": f"No reply within {timeout_sec}s"}

    patch_turns(monkeypatch, "run_message_and_wait", run)
    running = [True] * 10 + [False]  # the hand-off turn goes idle 5 s later
    patch_turns(monkeypatch, "is_agent_running", lambda _: running.pop(0) if running else False)
    out = json.loads(panel.ducky_recycle_member("builder", timeout_sec=900))
    assert waits[0] == team_waits.HANDOFF_MAX_S and waits[1] > 0
    assert clock[0] <= team_waits.TOOL_BUDGET_S + 5
    assert out["status"] == "timeout"


def test_popups_lists_a_popup_whose_capture_hangs(monkeypatch, release) -> None:
    from backend.tools.core import system, uefn_popups

    monkeypatch.setattr(team_waits, "POPUP_CAPTURE_S", 0.2)
    popup = {"hwnd": 7, "title": "Message", "rect": {}, "blocking": True}
    monkeypatch.setattr(uefn_popups, "list_popups", lambda: [popup])
    monkeypatch.setattr(uefn_popups, "describe_popup", lambda p: release.wait(30) or p)
    started = time.monotonic()
    out = json.loads(system.uefn_popups())
    assert time.monotonic() - started < 5
    assert out["count"] == 1 and out["popups"][0]["hwnd"] == 7 and "timed out" in out["popups"][0]["capture"]
