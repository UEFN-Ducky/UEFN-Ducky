"""Sidebar context_tokens for group hover totals."""

from types import SimpleNamespace

from frontend.ui_web.panel_api import PanelApi
from frontend.ui_web.token_usage import (
    estimate_context_window_tokens,
    resolve_context_window_tokens,
)


def test_sidebar_context_tokens_prefers_coding_agent_stats():
    c = SimpleNamespace(
        coding_agent_stats={"context_tokens": 5320},
        token_usage={"calls": [{"input_tokens": 99}]},
    )
    assert PanelApi._sidebar_context_tokens(c) == 5320


def test_sidebar_context_tokens_falls_back_to_last_call_input():
    c = SimpleNamespace(
        coding_agent_stats=None,
        token_usage={"calls": [{"input_tokens": 100}, {"input_tokens": 2400}]},
    )
    assert PanelApi._sidebar_context_tokens(c) == 2400


def test_sidebar_repairs_inflated_input_plus_cache_sum():
    """Producer bug: stored 4.2M = Sent 2.1M + Cached 2.1M from one call."""
    c = SimpleNamespace(
        coding_agent_stats={"context_tokens": 4_205_545, "num_turns": 1},
        token_usage={
            "calls": [
                {
                    "input_tokens": 2_132_713,
                    "cache_read_tokens": 2_072_832,
                    "cache_write_tokens": 0,
                }
            ]
        },
    )
    assert PanelApi._sidebar_context_tokens(c) == 2_132_713


def test_estimate_context_window_near_equal_buckets():
    assert estimate_context_window_tokens(2_132_713, 2_072_832, 0) == 2_132_713


def test_estimate_context_window_divides_by_num_turns():
    assert estimate_context_window_tokens(120, 5000, 200, num_turns=3) == (
        120 + 5000 + 200 + 3 - 1
    ) // 3


def test_resolve_keeps_true_last_step_below_naive_sum():
    assert (
        resolve_context_window_tokens(
            stored_context_tokens=2200,
            input_tokens=180,
            cache_read_tokens=4000,
            cache_write_tokens=20,
            num_turns=2,
        )
        == 2200
    )


def test_conversation_sidebar_row_includes_context_tokens():
    c = SimpleNamespace(
        id="c1",
        title="Material Artist",
        sort_order=0,
        updated=1.0,
        ducky_style="artist",
        ducky_name="",
        ducky_personality="",
        tts_voice="",
        tts_speed=0,
        file_path="",
        model="claude",
        provider="anthropic",
        coding_agent="cursor",
        thinking_effort="",
        terminal_session_id="",
        folder_id="art",
        parent_conv_id="",
        is_group=False,
        leader_conv_id="",
        group_members=[],
        tool_call_count=0,
        file_count=0,
        coding_agent_stats={"context_tokens": 1800},
        token_usage=None,
    )
    row = PanelApi._conversation_sidebar_row(c)
    assert row["context_tokens"] == 1800
