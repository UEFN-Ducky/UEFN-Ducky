"""The MCP server instructions are a short pointer; the rules travel in the agent prompt."""

from backend.agent.hard_rules import AGENT_HARD_RULES
from backend.server import mcp


def test_instructions_are_a_short_pointer():
    text = mcp.instructions or ""
    assert len(text) < 600
    assert "UEFN Ducky" in text
    assert "Store-plugin tools" in text and "ducky_call_tool" in text  # not listed: found and run through Ducky
    assert "pretty=false" in text


def test_instructions_do_not_repeat_the_rules():
    text = mcp.instructions or ""
    assert AGENT_HARD_RULES.strip()[:200] not in text
    assert "(HARD)" not in text
    assert "ducky_create_plan" not in text
