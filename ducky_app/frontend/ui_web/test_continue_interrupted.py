"""Continue strips the interrupt and does not invent a user message."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web.agent_modes import clear_last_interrupt, last_user_payload


def test_clear_keeps_partial_and_ts():
    conv = SimpleNamespace(
        messages=[
            {"role": "user", "content": "build it", "text": "build it", "ts": 1},
            {
                "role": "assistant",
                "content": "halfway",
                "thinking": "plan",
                "ts": 99,
                "usage": {"input_tokens": 3},
                "incomplete": True,
                "error": "Reached max turns (25)",
            },
        ]
    )
    assert clear_last_interrupt(conv) is True
    last = conv.messages[-1]
    assert last["content"] == "halfway"
    assert last["thinking"] == "plan"
    assert last["ts"] == 99
    assert last["usage"] == {"input_tokens": 3}
    assert "incomplete" not in last
    assert "error" not in last
    assert last_user_payload(conv)[0] == "build it"


def test_clear_drops_empty_error_only():
    conv = SimpleNamespace(
        messages=[
            {"role": "user", "content": "hi", "text": "hi"},
            {"role": "assistant", "content": "", "incomplete": True, "error": "Reached max turns (25)"},
        ]
    )
    assert clear_last_interrupt(conv) is True
    assert [m["role"] for m in conv.messages] == ["user"]


def test_clear_drops_standalone_error_row():
    conv = SimpleNamespace(
        messages=[
            {"role": "user", "content": "hi", "text": "hi"},
            {"role": "error", "content": "boom"},
        ]
    )
    assert clear_last_interrupt(conv) is True
    assert [m["role"] for m in conv.messages] == ["user"]


def test_clear_keeps_blocks():
    conv = SimpleNamespace(
        messages=[
            {"role": "user", "content": "hi", "text": "hi"},
            {
                "role": "assistant",
                "content": "",
                "blocks": [{"type": "tool_call", "name": "x"}],
                "incomplete": True,
                "error": "Stopped",
            },
        ]
    )
    assert clear_last_interrupt(conv) is True
    assert conv.messages[-1]["blocks"]
    assert "incomplete" not in conv.messages[-1]
