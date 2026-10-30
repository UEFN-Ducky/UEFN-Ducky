"""Context limit: which limit applies, and a chat past it starts fresh between turns."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend.chat_store import Conversation
from frontend.ui_web import context_limit as cl


@pytest.fixture
def settings(monkeypatch):
    from frontend import settings as settings_mod

    box = SimpleNamespace(context_limit_tokens=0)
    monkeypatch.setattr(settings_mod.PanelSettings, "load", classmethod(lambda cls: box))
    return box


@pytest.fixture
def window(monkeypatch):
    from frontend.ui_web import context_tokens

    box = {"tokens": None}
    monkeypatch.setattr(context_tokens, "context_limit_for_model", lambda model, provider="": box["tokens"])
    monkeypatch.setattr(context_tokens, "_coding_agent_context_limit", lambda agent, model: box["tokens"])
    return box


def test_parse_tokens_reads_k_m_and_commas():
    assert cl.parse_tokens("200k") == 200_000
    assert cl.parse_tokens("1.5M") == 1_500_000
    assert cl.parse_tokens("200,000") == 200_000
    assert cl.parse_tokens("lots") == 0 and cl.parse_tokens("") == 0


def test_a_plan_step_line_beats_the_chats_own_limit(settings, window):
    conv = Conversation(id="c", model="m", context_limit_tokens=800_000)
    step = {"content": "Build it", "body_markdown": "Do the thing.\nContext: 200k\n**Done when:** ok"}
    assert cl.resolve_limit(conv, step) == 200_000
    assert cl.resolve_limit(conv, "- Context: 1.2m") == 1_200_000


def test_the_chats_own_limit_beats_the_default(settings, window):
    settings.context_limit_tokens = 300_000
    conv = Conversation(id="c", model="m", context_limit_tokens=800_000)
    assert cl.resolve_limit(conv, {"content": "No limit line"}) == 800_000


def test_the_settings_default_beats_the_model_window(settings, window):
    settings.context_limit_tokens = 300_000
    window["tokens"] = 1_000_000
    assert cl.resolve_limit(Conversation(id="c", model="m")) == 300_000


def test_default_is_60_percent_of_the_model_window(settings, window):
    window["tokens"] = 1_000_000
    assert cl.resolve_limit(Conversation(id="c", model="m")) == 600_000
    window["tokens"] = 200_000
    assert cl.resolve_limit(Conversation(id="c", model="claude-x", coding_agent="claude_code")) == 120_000


def test_default_falls_back_to_600k_when_the_window_is_unknown(settings, window):
    assert cl.resolve_limit(Conversation(id="c", model="unknown")) == cl.FALLBACK_TOKENS == 600_000


def test_the_chats_limit_is_kept_on_the_chat(monkeypatch):
    from frontend.ui_web import project_chats

    conv = Conversation(id="c")
    saved = []
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None: conv)
    monkeypatch.setattr(project_chats, "save_conversation", lambda c, root=None: saved.append(c.id))
    assert cl.set_chat_limit("c", "250k") == 250_000
    assert saved == ["c"]
    assert Conversation.from_dict(conv.to_dict()).context_limit_tokens == 250_000


def test_tool_argument_is_stored_only_when_given(monkeypatch):
    from backend.tools.panel import ducky_panel as panel

    calls = []
    monkeypatch.setattr(panel, "_project_root", lambda: "project")
    monkeypatch.setattr(cl, "set_chat_limit", lambda cid, value, project_root=None: calls.append((cid, value)))
    panel._store_context_limit("m1", "")
    panel._store_context_limit("m1", "200k")
    assert calls == [("m1", "200k")]


@pytest.fixture
def chat(monkeypatch, settings, window):
    """A member chat and every call the fresh start makes, in order."""
    from backend.store.repos import kv
    from frontend.ui_web import agent_modes, context_control, project_chats

    docs: dict = {}
    monkeypatch.setattr(kv, "set_doc", lambda table, key, value: docs.__setitem__((table, key), value))
    monkeypatch.setattr(kv, "get_doc", lambda table, key: docs.get((table, key)))
    monkeypatch.setattr(kv, "delete_doc", lambda table, key: docs.pop((table, key), None))
    conv = Conversation(id="m1", model="m", context_limit_tokens=600_000, parent_conv_id="group")
    calls: list[tuple] = []
    world = SimpleNamespace(conv=conv, calls=calls, handoff={"status": "done", "assistant_text": "Built A; next B."},
                            used=612_000, reset_fails=False)
    monkeypatch.setattr(project_chats, "load_conversation", lambda cid, project_root=None: conv if cid == "m1" else None)
    monkeypatch.setattr(project_chats, "append_message",
                        lambda c, msg, project_root=None: calls.append(("line", msg["content"])))
    monkeypatch.setattr(cl, "used_tokens", lambda c: world.used)

    def wait(cid, text, mode, model="", **kw):
        calls.append(("handoff", text))
        return world.handoff

    def reset(cid, segments, project_root=None):
        if world.reset_fails:
            raise ValueError("agent is working")
        calls.append(("reset", tuple(segments)))

    monkeypatch.setattr(agent_modes, "run_message_and_wait", wait)
    monkeypatch.setattr(agent_modes, "run_message", lambda cid, text, mode, model, **kw: calls.append(("run", text)))
    monkeypatch.setattr(agent_modes, "is_agent_running", lambda cid: False)
    monkeypatch.setattr(context_control, "reset_context", reset)
    return world


def _start(world) -> bool:
    over = cl.over_limit("m1")
    if not over:
        return False
    return cl.start_fresh("m1", "Task: point B", used=over[0], limit=over[1])


def test_over_the_limit_hands_off_resets_then_continues_in_order(chat):
    assert _start(chat) is True
    kinds = [c[0] for c in chat.calls]
    assert kinds == ["handoff", "reset", "line", "run"]
    assert "HANDOFF REQUIRED" in chat.calls[0][1] and "18,000 tokens" in chat.calls[0][1]  # 3% of 600k
    assert chat.calls[1][1] == ("session",)
    assert chat.calls[2][1].startswith("Started fresh at 612k → ") and chat.calls[2][1].endswith("k tokens")
    assert "Built A; next B." in chat.calls[3][1] and "Task: point B" in chat.calls[3][1]


def test_a_prompt_bigger_than_the_limit_hands_off_once_not_every_turn(chat):
    """Oct 29 2026: a Ducky AI chat (~166k a turn, 77k limit) handed off after every reply."""
    chat.conv.context_limit_tokens = 77_000
    chat.used = 166_000
    assert _start(chat) is True  # the first time over: one handoff
    for used in (166_000, 170_000, 200_000, 240_000):  # the fresh session is as big, then grows
        chat.used = used
        assert cl.over_limit("m1") is None, used
    chat.used = 166_000 + 77_001  # real growth past the limit since the fresh start
    assert cl.over_limit("m1") == (243_001, 77_000)


def test_under_the_limit_is_untouched(chat):
    chat.used = 500_000
    assert _start(chat) is False
    assert chat.calls == []


def test_a_failed_handoff_keeps_the_old_session(chat):
    chat.handoff = {"status": "error", "error": "boom"}
    assert _start(chat) is False
    assert [c[0] for c in chat.calls] == ["handoff", "run"]
    assert chat.calls[1][1] == "Task: point B"


def test_the_agents_own_handoff_skips_the_handoff_turn(chat):
    assert cl.start_fresh("m1", "Task: point B", handoff="My own handoff.") is True
    assert [c[0] for c in chat.calls] == ["reset", "line", "run"]
    assert chat.calls[1][1].startswith("Started fresh at 612k")
    assert "My own handoff." in chat.calls[2][1] and "Task: point B" in chat.calls[2][1]


def test_a_new_sub_plan_starts_with_its_task_alone(chat):
    assert cl.start_fresh("m1", "Task: plugin docs", handoff="") is True
    assert [c[0] for c in chat.calls] == ["reset", "line", "run"]
    assert chat.calls[2][1] == "Task: plugin docs"


def test_a_fresh_session_request_is_kept_until_used(monkeypatch):
    from backend.store.repos import kv

    docs: dict = {}
    monkeypatch.setattr(kv, "set_doc", lambda table, key, value: docs.__setitem__((table, key), value))
    monkeypatch.setattr(kv, "get_doc", lambda table, key: docs.get((table, key)))
    monkeypatch.setattr(kv, "delete_doc", lambda table, key: docs.pop((table, key), None))
    assert cl.requested_handoff("m1") is None
    cl.request_fresh("m1", "Done A.")
    assert cl.requested_handoff("m1") == "Done A." and cl.requested_handoff("m2") is None
    cl.clear_request("m1")
    assert cl.requested_handoff("m1") is None


def test_fresh_session_tool_asks_for_the_calling_chat(monkeypatch):
    import json

    from backend.tools.panel import ducky_panel as panel

    asked = []
    monkeypatch.setattr(panel, "_resolve_sender", lambda sender: "m1")
    monkeypatch.setattr(cl, "request_fresh", lambda cid, text: asked.append((cid, text)))
    out = json.loads(panel.ducky_fresh_session("  Done A; next B.  "))
    assert out["status"] == "after_this_turn" and asked == [("m1", "Done A; next B.")]
    with pytest.raises(ValueError):
        panel.ducky_fresh_session("   ")


def test_a_refused_reset_keeps_the_old_session(chat):
    chat.reset_fails = True
    assert _start(chat) is False
    assert [c[0] for c in chat.calls] == ["handoff", "run"]
    assert chat.calls[1][1] == "Task: point B"
