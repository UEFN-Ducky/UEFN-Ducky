"""In-flight coding-agent turns must land on disk before launch() returns."""

from __future__ import annotations

import tempfile
from pathlib import Path

from backend.agent.coding_agents.runner import _TurnCheckpoint, checkpoint_coding_turn
from frontend.settings import PanelSettings
from frontend.ui_web.project_chats import (
    KILLED_TURN_ERROR,
    create_conversation,
    heal_killed_coding_turn,
    load_conversation,
    upsert_in_flight_assistant,
)


def test_checkpoint_then_upsert_replaces_same_run():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Snake", project_root=root)
        conv.messages = [{"role": "user", "text": "make snake", "content": "make snake", "ts": 1}]
        from frontend.ui_web.project_chats import save_conversation

        save_conversation(conv, root)

        checkpoint_coding_turn(
            conv,
            agent_id="cursor",
            run_id="run-1",
            blocks=[{"type": "tool_call", "name": "Read", "status": "pending"}],
            project_root=root,
        )
        mid = load_conversation(conv.id, project_root=root)
        assert mid is not None
        assert mid.messages[-1]["incomplete"] is True
        assert mid.messages[-1]["blocks"][0]["name"] == "Read"

        upsert_in_flight_assistant(
            conv,
            {
                "role": "assistant",
                "content": "done",
                "text": "done",
                "run_id": "run-1",
                "blocks": [{"type": "tool_call", "name": "Read", "status": "success"}],
            },
            run_id="run-1",
            project_root=root,
        )
        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        asst = [m for m in fresh.messages if m.get("role") == "assistant"]
        assert len(asst) == 1
        assert asst[0]["content"] == "done"
        assert asst[0].get("incomplete") is None


def test_turn_checkpoint_flushes_on_tool_done():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Snake", project_root=root)
        conv.messages = [{"role": "user", "text": "go", "content": "go", "ts": 1}]
        from frontend.ui_web.project_chats import save_conversation

        save_conversation(conv, root)
        events: list[dict] = []
        ckpt = _TurnCheckpoint(conv, "cursor", "run-9", project_root=root)
        push = ckpt.wrap(events.append)
        ckpt.seed()
        push({"type": "tool", "tool": {"name": "Read", "arguments": {"path": "a"}, "status": "pending"}})
        push(
            {
                "type": "tool_done",
                "success": True,
                "tool": {"name": "Read", "status": "success", "durationMs": 12, "result": "ok"},
            }
        )
        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        last = fresh.messages[-1]
        assert last["incomplete"] is True
        assert last["blocks"][-1]["status"] == "success"
        assert last["blocks"][-1]["result"]["data"] == "ok"
        assert [e["type"] for e in events] == ["tool", "tool_done"]


def test_heal_killed_turn_adds_stub_when_only_user_left():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Gone", project_root=root)
        conv.messages = [{"role": "user", "text": "hello", "content": "hello", "ts": 1}]
        from frontend.ui_web.project_chats import save_conversation

        save_conversation(conv, root)
        assert heal_killed_coding_turn(conv.id, "dead-run", project_root=root) is True
        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        last = fresh.messages[-1]
        assert last["incomplete"] is True
        assert last["error"] == KILLED_TURN_ERROR
        assert last["run_id"] == "dead-run"
