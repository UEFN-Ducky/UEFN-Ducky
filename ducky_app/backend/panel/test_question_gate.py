"""MCP dispatch must stop before executing another tool with unanswered questions."""
import asyncio
import threading

from backend.panel import rpc
from backend.server import ProtectedFastMCP
from backend.workspace import ai_ignore, identity
from frontend.ui_web import ui_rpc


def test_dispatch_waits_before_tool_body(monkeypatch):
    server = ProtectedFastMCP("question-gate-test")
    entered = threading.Event()

    @server.tool()
    def task_write() -> str:
        entered.set()
        return "done"

    monkeypatch.setattr(ai_ignore, "require_safe_tool", lambda _name: None)
    monkeypatch.setattr(rpc, "wait_for_question_answers", ui_rpc.wait_for_answers)
    from backend.agent import chat_title
    monkeypatch.setattr(chat_title, "require_self_name", lambda *_args: None)
    request_id, _ = ui_rpc.submit("ask_user", {"conv_id": "dispatch-gate", "questions": [{"id": "q"}]})

    async def run():
        token = identity.bind(identity.RunContext(run_id="run", conv_id="dispatch-gate"))
        try:
            dispatch = asyncio.create_task(server.call_tool("task_write", {}))
            await asyncio.sleep(0.05)
            assert not entered.is_set()
            assert ui_rpc.respond(request_id, {"ok": True, "answers": {"q": {"text": "yes"}}})
            await asyncio.wait_for(dispatch, 2)
            assert entered.is_set()
        finally:
            identity.reset(token)

    try:
        asyncio.run(run())
    finally:
        ui_rpc.cancel(request_id)


def test_dispatch_fails_closed_when_panel_cannot_confirm_answers(monkeypatch):
    import pytest

    server = ProtectedFastMCP("offline-question-gate")
    entered = []

    @server.tool()
    def task_write() -> str:
        entered.append(True)
        return "done"

    monkeypatch.setattr(ai_ignore, "require_safe_tool", lambda _name: None)
    monkeypatch.setattr(rpc, "panel_rpc", lambda *args, **kwargs: {"error": "panel not open"})
    token = identity.bind(identity.RunContext(run_id="run", conv_id="offline"))
    try:
        with pytest.raises(RuntimeError, match="remain blocked"):
            asyncio.run(server.call_tool("task_write", {}))
        assert not entered
    finally:
        identity.reset(token)
