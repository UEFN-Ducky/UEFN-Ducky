"""A chat save that fails during a turn leaves a warning; the turn goes on as before."""

from __future__ import annotations

import ast
import inspect
import logging
import textwrap
from types import SimpleNamespace

from backend.agent import runner as agent_runner
from frontend.ui_web.agent_turns import pending, routing

# (module, function): how many guarded saves it has.
SITES = [(routing, "run_message", 3), (pending, "_take_chat_model", 1), (agent_runner.AgentRunner, "_run_turn_inner", 1)]


def _guarded_saves(fn) -> list[ast.Try]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Try) and any(
        isinstance(c, ast.Call) and getattr(c.func, "id", "") == "save_conversation"
        for stmt in node.body for c in ast.walk(stmt))]


def _logs_warning(handler: ast.ExceptHandler) -> bool:
    return any(isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "warning"
               and getattr(c.func.value, "id", "") == "_log"
               and any(k.arg == "exc_info" for k in c.keywords)
               for c in ast.walk(handler))


def test_every_guarded_save_logs_its_failure() -> None:
    for owner, name, count in SITES:
        tries = _guarded_saves(inspect.unwrap(getattr(owner, name)))
        assert len(tries) == count, (name, len(tries))
        for node in tries:
            assert all(_logs_warning(h) for h in node.handlers), (name, node.lineno)


def test_a_failed_save_leaves_a_log_line(monkeypatch, caplog) -> None:
    from frontend import favorite_models

    choice = SimpleNamespace(coding_agent="ducky", model="m1", provider="anthropic")
    monkeypatch.setattr(favorite_models, "chat_model_choice", lambda settings: (choice, ""))
    monkeypatch.setattr(pending, "notify_chats_changed", lambda *a, **k: None)

    def disk_full(conv):
        raise OSError("disk full")

    monkeypatch.setattr(pending, "save_conversation", disk_full)
    conv = SimpleNamespace(id="chat-7", title="t", folder_id="", coding_agent="", model="", provider="")
    pushed: list = []
    with caplog.at_level(logging.WARNING, logger=pending.__name__):
        assert pending._take_chat_model(conv, object(), pushed.append) == ("m1", "ducky", "")
    lines = [r for r in caplog.records if r.getMessage() == "chat save failed: chat-7"]
    assert len(lines) == 1 and lines[0].exc_info
    assert pushed and pushed[0]["type"] == "model_adopted"  # the turn goes on
