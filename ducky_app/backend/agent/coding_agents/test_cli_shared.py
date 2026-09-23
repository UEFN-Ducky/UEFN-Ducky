"""Tool-result trimming for coding-agent cards."""

from backend.agent.coding_agents.cli_shared import truncate_tool_result


def test_json_web_result_keeps_the_picture_list() -> None:
    payload = '{"ok":true,"images":[' + ",".join(
        '{"thumb":"https://example.com/%d"}' % i for i in range(200)
    ) + "]}"
    assert len(payload) > 4000
    kept = truncate_tool_result(payload)
    assert kept == payload
    assert "…(truncated)" not in kept


def test_plain_text_still_truncates() -> None:
    text = "x" * 5000
    kept = truncate_tool_result(text)
    assert kept.endswith("…(truncated)")
    assert len(kept) < len(text)
