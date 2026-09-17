"""Stale tool-result clearing: trigger/keep/clear_at_least, pairing, captures, idempotent."""

from __future__ import annotations

from backend.agent.context_memory import estimate_tokens
from backend.agent.context_trim import (
    CLEARED_MARKER,
    EXCLUDE_TOOLS,
    IMAGE_CLEARED,
    KEEP_RECENT_TOOL_RESULTS,
    clear_stale_tool_results,
    high_water_thresholds,
    plan_clears,
    should_force_clear,
)
from backend.agent.message_attachment import MessageAttachment
from backend.agent.providers.base import ProviderMessage, ToolCallRequest


def test_plan_clears_under_trigger_is_empty() -> None:
    sizes = [100] * 10
    chosen = plan_clears(sizes, keep_recent=5, trigger=5000, target=1000)
    assert chosen == set()


def test_plan_clears_over_trigger_keeps_recent() -> None:
    sizes = [200] * 12  # 2400; trigger 1000; target 800; keep 5
    chosen = plan_clears(sizes, keep_recent=5, trigger=1000, target=800)
    # 7 clearable. After those, remaining last 5 = 1000; freed 1400 >= clear_at_least 200.
    assert chosen == set(range(7))
    assert max(chosen) < 12 - 5


def test_plan_clears_skips_when_cannot_meet_clear_at_least() -> None:
    # trigger-target = 400; only 20 tokens are clearable
    sizes = [10, 10, 1000]
    chosen = plan_clears(sizes, keep_recent=1, trigger=500, target=100)
    assert chosen == set()


def test_plan_clears_force_ignores_trigger_and_floor() -> None:
    sizes = [80] * 8
    assert plan_clears(sizes, keep_recent=3, trigger=10_000, target=200) == set()
    chosen = plan_clears(sizes, keep_recent=3, trigger=10_000, target=200, force=True)
    assert chosen == set(range(5))


def test_plan_clears_budget_can_trigger_when_sizes_are_small() -> None:
    sizes = [300] * 8  # 2400 < trigger 4000
    assert plan_clears(sizes, keep_recent=3, trigger=4000, target=1000) == set()
    chosen = plan_clears(
        sizes, keep_recent=3, trigger=4000, target=1000, budget=5000
    )
    # remaining after 5 clears: 900; freed=1500 < clear_at_least 3000 → empty
    assert chosen == set()
    chosen = plan_clears(
        sizes, keep_recent=3, trigger=2000, target=1000, budget=5000
    )
    assert chosen == set(range(5))


def _pair(i: int, name: str, body: str) -> list[ProviderMessage]:
    tid = f"call-{i}"
    return [
        ProviderMessage(
            role="assistant",
            content=f"step {i}",
            tool_calls=[ToolCallRequest(id=tid, name=name, arguments={"i": i})],
            thinking_blocks=[{"type": "thinking", "thinking": f"sig-{i}"}],
        ),
        ProviderMessage(role="tool", tool_call_id=tid, content=body),
    ]


def test_clear_stale_tool_results_round_trip() -> None:
    body = "x" * 800
    msgs: list[ProviderMessage] = []
    for i in range(30):
        msgs.extend(_pair(i, "workspace_read_file", body))
    msgs.append(
        ProviderMessage(
            role="user",
            content="[capture attached: shot.png — image follows; use tool result path for file work]",
            attachments=[
                MessageAttachment(kind="image", name="shot.png", mime="image/png", data_base64="abc")
            ],
        )
    )
    # Capture is newest clearable — keep_recent=5 protects it plus last 4 tools.
    ids_before = [m.tool_call_id for m in msgs if m.role == "tool"]
    thinking_before = [m.thinking_blocks for m in msgs if m.role == "assistant"]

    hw = 4_000  # trigger 2000, target 1000
    # 30 * ~200 tok + 1600 image. Over trigger. Keep last 5 clearable.
    freed = clear_stale_tool_results(msgs, high_water=hw, conv_id="chat-1")
    assert freed > 0

    tool_msgs = [m for m in msgs if m.role == "tool"]
    assert [m.tool_call_id for m in tool_msgs] == ids_before
    assert [m.thinking_blocks for m in msgs if m.role == "assistant"] == thinking_before
    assert all(m.tool_calls for m in msgs if m.role == "assistant")

    # 31 clearable (30 tools + capture). keep 5 → last 4 tools + capture stay; first 26 tools stubbed.
    live_tools = KEEP_RECENT_TOOL_RESULTS - 1
    assert all(CLEARED_MARKER in m.content for m in tool_msgs[:-live_tools])
    assert all(CLEARED_MARKER not in m.content for m in tool_msgs[-live_tools:])
    assert "ducky_read_chat('chat-1')" in tool_msgs[0].content
    capture = msgs[-1]
    assert capture.attachments  # kept — inside keep-recent
    assert IMAGE_CLEARED not in capture.content

    assert clear_stale_tool_results(msgs, high_water=hw, conv_id="chat-1") == 0


def test_clear_stale_strips_old_capture_and_skips_excluded() -> None:
    body = "y" * 800
    msgs: list[ProviderMessage] = []
    msgs.extend(_pair(0, "ducky_ask_user", body))
    msgs.append(
        ProviderMessage(
            role="user",
            content="[capture attached: old.png — image follows; use tool result path for file work]",
            attachments=[
                MessageAttachment(kind="image", name="old.png", mime="image/png", data_base64="zz")
            ],
        )
    )
    for i in range(1, 12):
        msgs.extend(_pair(i, "workspace_read_file", body))

    assert "ducky_ask_user" in EXCLUDE_TOOLS
    freed = clear_stale_tool_results(msgs, high_water=3_000, force=True)
    assert freed > 0
    ask = [m for m in msgs if m.role == "tool"][0]
    assert CLEARED_MARKER not in ask.content
    capture = [m for m in msgs if m.role == "user"][0]
    assert capture.attachments == []
    assert capture.content == IMAGE_CLEARED


def test_under_trigger_does_not_mutate() -> None:
    msgs = []
    for i in range(3):
        msgs.extend(_pair(i, "workspace_read_file", "z" * 800))
    snapshot = [m.content for m in msgs]
    assert clear_stale_tool_results(msgs, high_water=80_000) == 0
    assert [m.content for m in msgs] == snapshot


def test_should_force_clear_from_registration(monkeypatch) -> None:
    from backend.uefn_plugins import host

    monkeypatch.setattr(host, "get_llm_provider_registration", lambda _p: None)
    assert should_force_clear("ollama", 1.0) is True

    monkeypatch.setattr(
        host, "get_llm_provider_registration", lambda _p: {"cache_mode": "cached"}
    )
    assert should_force_clear("anthropic", 1.0) is False

    monkeypatch.setattr(
        host,
        "get_llm_provider_registration",
        lambda _p: {"cache_mode": "cached", "cache_ttl_s": 60},
    )
    assert should_force_clear("anthropic", 0) is False
    import time

    monkeypatch.setattr(
        host,
        "get_llm_provider_registration",
        lambda _p: {"cache_mode": "cached", "cache_ttl_s": 60},
    )
    assert should_force_clear("anthropic", time.time()) is False
    assert should_force_clear("anthropic", time.time() - 120) is True


def test_context_memory_head_points_at_chat() -> None:
    from backend.agent.context_memory import CONTEXT_MEMORY_PREFIX, context_memory_head

    bare = context_memory_head("digest")
    assert bare["content"].startswith(CONTEXT_MEMORY_PREFIX)
    assert "ducky_read_chat" not in bare["content"]
    headed = context_memory_head("digest", conv_id="abc")
    assert "ducky_read_chat('abc')" in headed["content"]


def test_high_water_thresholds() -> None:
    trigger, target = high_water_thresholds(8_000)
    assert trigger == 4_000
    assert target == 2_000
    assert estimate_tokens("abcd") == 1


def test_toon_error_result_stubs_ok_false() -> None:
    import json

    from backend.agent.serialization import (
        format_tool_result_for_llm,
        parse_tool_result_envelope,
        set_tool_result_format,
    )

    payload = json.dumps(
        {"ok": False, "tool": "workspace_read_file", "error": "missing", "data": "x" * 800},
        ensure_ascii=False,
    )
    body = format_tool_result_for_llm("workspace_read_file", payload, fmt="toon")
    assert not body.strip().startswith("{")
    set_tool_result_format("toon")
    try:
        msgs: list[ProviderMessage] = []
        for i in range(12):
            msgs.extend(_pair(i, "workspace_read_file", body))
        assert clear_stale_tool_results(msgs, high_water=3_000, force=True, conv_id="c1") > 0
        stubbed = [m for m in msgs if m.role == "tool" and CLEARED_MARKER in m.content]
        assert stubbed
        env = parse_tool_result_envelope(stubbed[0].content)
        assert env is not None
        assert env.get("ok") is False
    finally:
        set_tool_result_format(None)


def test_conversation_report_skip_stale_clear(monkeypatch) -> None:
    from frontend.ui_web.context_tokens import _conversation_report

    monkeypatch.setattr(
        "backend.agent.context_memory.token_high_water",
        lambda *a, **k: 2_000,
    )
    body = "z" * 800
    messages = [
        {
            "role": "assistant",
            "content": "",
            "blocks": [
                {
                    "type": "tool_call",
                    "id": f"c{i}",
                    "name": "workspace_read_file",
                    "status": "success",
                    "result": {"ok": True, "data": body},
                    "llm_content": body,
                }
            ],
        }
        for i in range(20)
    ]
    on = _conversation_report(messages, model="gpt-4o", provider="openai", apply_stale_clear=True)
    off = _conversation_report(messages, model="gpt-4o", provider="openai", apply_stale_clear=False)
    assert off[1] > on[1]
