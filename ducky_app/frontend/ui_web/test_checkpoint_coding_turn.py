"""In-flight coding-agent turns must land on disk before launch() returns."""

from __future__ import annotations

import tempfile
from pathlib import Path

import time

from backend.agent.coding_agents import runner
from backend.agent.coding_agents.runner import _CHECKPOINT_EVERY_S, _TurnCheckpoint, checkpoint_coding_turn
from frontend.settings import PanelSettings
from frontend.ui_web.chats.conversations import create_conversation, load_conversation
from frontend.ui_web.chats.saves import (
    KILLED_TURN_ERROR,
    heal_killed_coding_turn,
    heal_usage_orphaned_turn,
    upsert_in_flight_assistant,
)


def test_checkpoint_then_upsert_replaces_same_run():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Snake", project_root=root)
        conv.messages = [{"role": "user", "text": "make snake", "content": "make snake", "ts": 1}]
        from frontend.ui_web.chats.saves import save_conversation

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
        from frontend.ui_web.chats.saves import save_conversation

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
        time.sleep(_CHECKPOINT_EVERY_S + 0.4)  # the done lands with the burst's trailing save
        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        last = fresh.messages[-1]
        assert last["incomplete"] is True
        assert last["blocks"][-1]["status"] == "success"
        assert last["blocks"][-1]["result"]["data"] == "ok"
        assert [e["type"] for e in events] == ["tool", "tool_done"]


def _counting(monkeypatch) -> list[int]:
    saves: list[int] = []
    real = runner.checkpoint_coding_turn

    def counted(conv, **kw):
        saves.append(sum(1 for b in kw.get("blocks") or [] if b.get("type") == "tool_call"))
        real(conv, **kw)

    monkeypatch.setattr(runner, "checkpoint_coding_turn", counted)
    return saves


def test_a_tool_burst_saves_twice_and_the_last_save_has_every_tool(tmp_path, monkeypatch):
    root = str(tmp_path)
    conv = create_conversation(PanelSettings.load(), "", title="Burst", project_root=root)
    saves = _counting(monkeypatch)
    push = _TurnCheckpoint(conv, "claude", "burst", project_root=root).wrap(lambda _ev: None)
    for i in range(10):
        push({"type": "tool", "tool": {"id": f"t{i}", "name": "Grep", "status": "pending"}})
        push({"type": "tool_done", "tool": {"id": f"t{i}", "name": "Grep", "status": "success", "result": "x"}})
    time.sleep(_CHECKPOINT_EVERY_S + 0.4)
    assert 1 <= len(saves) <= 2 and saves[-1] == 10  # the first tool soon, the rest together
    stored = load_conversation(conv.id, project_root=root).messages[-1]["blocks"]
    assert [b["status"] for b in stored] == ["success"] * 10


def test_the_thread_reading_the_agent_never_saves(tmp_path, monkeypatch):
    import threading

    root = str(tmp_path)
    conv = create_conversation(PanelSettings.load(), "", title="Reader", project_root=root)
    saved_on: list[str] = []
    monkeypatch.setattr(runner, "checkpoint_coding_turn",
                        lambda *a, **kw: saved_on.append(threading.current_thread().name))
    push = _TurnCheckpoint(conv, "claude", "reader", project_root=root).wrap(lambda _ev: None)
    for i in range(5):
        push({"type": "tool", "tool": {"id": f"t{i}", "name": "Read", "status": "pending"}})
    time.sleep(_CHECKPOINT_EVERY_S + 0.4)
    assert saved_on and threading.current_thread().name not in saved_on


def test_close_drops_a_pending_trailing_save(tmp_path, monkeypatch):
    root = str(tmp_path)
    conv = create_conversation(PanelSettings.load(), "", title="Final", project_root=root)
    saves = _counting(monkeypatch)
    ckpt = _TurnCheckpoint(conv, "claude", "final", project_root=root)
    push = ckpt.wrap(lambda _ev: None)
    push({"type": "tool", "tool": {"id": "a", "name": "Read", "status": "pending"}})
    push({"type": "tool", "tool": {"id": "b", "name": "Read", "status": "pending"}})
    ckpt.close()  # the turn's final message is written next; nothing may land after it
    landed = len(saves)
    time.sleep(_CHECKPOINT_EVERY_S + 0.4)
    assert len(saves) == landed


def test_same_named_tools_complete_by_call_id(tmp_path):
    root = str(tmp_path)
    conv = create_conversation(PanelSettings.load(), "", title="Tools", project_root=root)
    ckpt = _TurnCheckpoint(conv, "cursor", "ordered", project_root=root)
    push = ckpt.wrap(lambda _ev: None)
    for call_id in ("first", "second"):
        push({"type": "tool", "tool": {"id": call_id, "name": "Read", "status": "pending"}})
    push({"type": "tool_done", "tool": {"id": "first", "name": "Read", "status": "success", "result": "a"}})
    tools = {b["id"]: b for b in ckpt.blocks if b["type"] == "tool_call"}
    assert tools["first"]["status"] == "success"
    assert tools["second"]["status"] == "pending"
    push({"type": "tool_done", "tool": {"id": "second", "name": "Read", "status": "cancelled"}})
    assert tools["second"]["status"] == "cancelled"
    assert tools["second"]["result"]["ok"] is False


def test_turn_checkpoint_does_not_flush_on_text_delta():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Ollama", project_root=root)
        conv.messages = [{"role": "user", "text": "go", "content": "go", "ts": 1}]
        from frontend.ui_web.chats.saves import save_conversation

        save_conversation(conv, root)
        ckpt = _TurnCheckpoint(conv, "ducky", "run-slow", project_root=root)
        push = ckpt.wrap(lambda _ev: None)
        ckpt.seed()
        seeded = load_conversation(conv.id, project_root=root)
        assert seeded is not None
        seed_hash = (seeded.messages[-1].get("blocks") or [])[:]
        for _ in range(40):
            push({"type": "text_delta", "text": "x"})
        mid = load_conversation(conv.id, project_root=root)
        assert mid is not None
        assert (mid.messages[-1].get("blocks") or []) == seed_hash
        push({"type": "tool", "tool": {"name": "ping", "status": "pending"}})
        time.sleep(0.4)  # saved on the checkpoint's own thread
        done = load_conversation(conv.id, project_root=root)
        assert done is not None
        blocks = done.messages[-1].get("blocks") or []
        assert any(b.get("type") == "text" and "x" in (b.get("text") or "") for b in blocks)
        assert blocks[-1].get("name") == "ping"


def test_heal_killed_turn_adds_stub_when_only_user_left():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Gone", project_root=root)
        conv.messages = [{"role": "user", "text": "hello", "content": "hello", "ts": 1}]
        from frontend.ui_web.chats.saves import save_conversation

        save_conversation(conv, root)
        assert heal_killed_coding_turn(conv.id, "dead-run", project_root=root) is True
        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        last = fresh.messages[-1]
        assert last["incomplete"] is True
        assert last["error"] == KILLED_TURN_ERROR
        assert last["run_id"] == "dead-run"


def test_heal_usage_orphaned_turn_when_calls_outran_transcript():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Ollama", project_root=root)
        conv.messages = [{"role": "user", "text": "city", "content": "city", "ts": 10}]
        conv.token_usage = {"calls": [{"ts": 20, "step": 1, "input_tokens": 100, "output_tokens": 8}]}
        from frontend.ui_web.chats.saves import save_conversation

        save_conversation(conv, root)
        loaded = load_conversation(conv.id, project_root=root)
        assert loaded is not None
        assert heal_usage_orphaned_turn(loaded, project_root=root) is True
        fresh = load_conversation(conv.id, project_root=root)
        assert fresh is not None
        last = fresh.messages[-1]
        assert last["role"] == "assistant"
        assert last["incomplete"] is True
        assert last["error"] == KILLED_TURN_ERROR
        assert heal_usage_orphaned_turn(fresh, project_root=root) is False


def test_boot_heal_leaves_a_turn_sent_since_launch_alone():
    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Live", project_root=root)
        conv.messages = [{"role": "user", "text": "build it", "content": "build it", "ts": 110}]
        conv.token_usage = {"calls": [{"ts": 120, "step": 1, "input_tokens": 100, "output_tokens": 8}]}
        from frontend.ui_web.chats.saves import heal_usage_orphaned_turns_for_project, save_conversation

        save_conversation(conv, root)
        assert heal_usage_orphaned_turns_for_project(root, sent_before=100) == []
        assert load_conversation(conv.id, project_root=root).messages[-1]["role"] == "user"
        assert heal_usage_orphaned_turns_for_project(root, sent_before=200) == [conv.id]


def test_the_final_reply_is_stored_before_the_app_hears_final(monkeypatch):
    # Nov 5 2026: an agent host told the app "final" while the reply still sat in its
    # chat-writer queue. The app read the last checkpoint from the database and every
    # finished turn showed "Interrupted before finishing" with no answer.
    from frontend.ui_web.chats import store

    with tempfile.TemporaryDirectory() as tmp:
        root = str(Path(tmp))
        conv = create_conversation(PanelSettings.load(), "", title="Host turn", project_root=root)
        conv.messages = [{"role": "user", "text": "go", "content": "go", "ts": 1}]
        from frontend.ui_web.chats.saves import save_conversation

        save_conversation(conv, root, wait=True)
        seen: list[dict] = []

        def on_saved(conv_id: str, final: bool) -> None:
            if not final:
                return
            if store._use_db():  # the database as another process sees it, no flush
                rows = store._repo().messages_get(conv_id)
            else:
                rows = load_conversation(conv_id, project_root=root).messages
            seen.append(rows[-1])

        monkeypatch.setattr(runner, "on_saved", on_saved)
        checkpoint_coding_turn(conv, agent_id="claude", run_id="run-f", blocks=[], project_root=root)
        runner._emit_assistant(conv, agent_id="claude", reply="all done", push=lambda e: None,
                               run_id="run-f", streamed=True)
        assert seen, "the final save was never reported"
        assert seen[-1]["content"] == "all done"
        assert not seen[-1].get("incomplete")
