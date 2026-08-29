"""Follow-ups must replay assistant → tools → assistant → next user."""

from backend.agent.runner import AgentRunner, RunConfig


def test_followup_comes_after_previous_reply_not_after_tools():
    runner = AgentRunner(RunConfig())
    msgs = runner._history_to_provider(
        [
            {"role": "user", "content": "first question"},
            {
                "role": "assistant",
                "content": "Here is the answer.",
                "blocks": [
                    {"type": "text", "text": "Checking…"},
                    {
                        "type": "tool_call",
                        "id": "t1",
                        "name": "ping",
                        "arguments": {},
                        "status": "success",
                        "llm_content": '{"ok":true}',
                    },
                ],
            },
            {"role": "user", "content": "follow up"},
        ]
    )
    roles = [m.role for m in msgs]
    assert roles == ["user", "assistant", "tool", "assistant", "user"]
    assert "Checking" in (msgs[1].content or "")
    assert msgs[1].tool_calls
    assert msgs[3].content == "Here is the answer."
    assert msgs[4].content == "follow up"


def test_plain_assistant_stays_one_message():
    runner = AgentRunner(RunConfig())
    msgs = runner._history_to_provider(
        [
            {"role": "user", "text": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[0].content == "hi"
    assert msgs[1].content == "hello"
