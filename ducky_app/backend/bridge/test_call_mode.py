"""A run's mode is read from its chat once, not on every tool call."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.bridge import call_mode


@pytest.fixture
def chats(monkeypatch):
    monkeypatch.setattr(call_mode, "_modes", {})
    store: dict[str, list[dict]] = {}
    loads: list[str] = []

    def load(conv_id):
        loads.append(conv_id)
        return SimpleNamespace(messages=list(store.get(conv_id, [])))

    monkeypatch.setattr("frontend.ui_web.chats.conversations.load_conversation", load)
    return store, loads


def _ident(run_id: str, agent: str = "claude_code"):
    return SimpleNamespace(conv_id="c1", run_id=run_id, coding_agent=agent)


def test_the_mode_is_loaded_once_per_run(chats) -> None:
    store, loads = chats
    store["c1"] = [{"role": "assistant", "run_id": "r1", "requested_mode": "plan"}]
    assert [call_mode.calling_mode(_ident("r1")) for _ in range(50)] == ["plan"] * 50
    assert loads == ["c1"]


def test_an_unknown_run_is_asked_again(chats) -> None:
    store, loads = chats
    with pytest.raises(ValueError, match="no matching host turn checkpoint"):
        call_mode.calling_mode(_ident("r2"))
    assert call_mode.calling_mode(_ident("r2", agent="ducky")) == "agent"  # not cached
    store["c1"] = [{"role": "assistant", "run_id": "r2"}]  # the checkpoint lands
    assert call_mode.calling_mode(_ident("r2")) == "agent"
    assert call_mode.calling_mode(_ident("r2")) == "agent"
    assert loads == ["c1", "c1", "c1"]


def test_the_cache_is_bounded(chats) -> None:
    store, _loads = chats
    store["c1"] = [{"role": "assistant", "run_id": f"r{i}", "requested_mode": "ask"} for i in range(300)]
    for i in range(300):
        call_mode.calling_mode(_ident(f"r{i}"))
    assert len(call_mode._modes) == call_mode._RUNS_MAX and ("c1", "r0") not in call_mode._modes
