"""A fresh agent session for a team member keeps its chat history."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from frontend.ui_web import agent_modes, context_control


@pytest.fixture()
def member(monkeypatch):
    conv = SimpleNamespace(
        id="builder",
        title="B - Builder",
        model="gpt-6-astra",
        messages=[{"role": "user", "content": "task 1"}, {"role": "assistant", "content": "Committed abc1234"}],
        upstream_session_id="codex-thread-1",
        coding_agent_stats={"turns": 40},
        context_omit=None,
    )
    saved: list[str] = []
    monkeypatch.setattr(context_control, "load_conversation", lambda cid, root=None: conv if cid == "builder" else None)
    monkeypatch.setattr(context_control, "save_conversation", lambda c, root=None: saved.append(c.id))
    monkeypatch.setattr(context_control, "notify_context_changed", lambda cid: None)
    monkeypatch.setattr(context_control, "_usage_after", lambda *a, **k: {})
    monkeypatch.setattr(context_control, "_project_root", lambda: "")
    monkeypatch.setattr(agent_modes, "is_agent_running", lambda cid: False)
    cancelled: list[str] = []
    monkeypatch.setattr(agent_modes, "cancel_agent", lambda cid: cancelled.append(cid))
    import backend.agent.prompt_cache as prompt_cache

    monkeypatch.setattr(prompt_cache, "invalidate_conv_cache", lambda c: None)
    return SimpleNamespace(conv=conv, saved=saved, cancelled=cancelled)


def test_a_fresh_session_keeps_every_message(member) -> None:
    out = context_control.reset_context("builder", ["session"])
    assert out["cleared"] == ["session"]
    assert member.conv.upstream_session_id == ""  # the next task starts a new Codex thread
    assert member.conv.coding_agent_stats is None
    assert [m["content"] for m in member.conv.messages] == ["task 1", "Committed abc1234"]
    assert member.saved == ["builder"] and member.cancelled == []


def test_a_working_agent_is_never_stopped_for_a_new_session(member, monkeypatch) -> None:
    monkeypatch.setattr(agent_modes, "is_agent_running", lambda cid: True)
    with pytest.raises(ValueError, match="working"):
        context_control.reset_context("builder", ["session"])
    assert member.conv.upstream_session_id == "codex-thread-1"
    assert member.cancelled == []


def test_unknown_segments_are_still_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown context segment"):
        context_control._normalize_segments(["sesion"])
