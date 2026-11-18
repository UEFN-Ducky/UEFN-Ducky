"""The four "this call does not run" answers in a turn share ``AgentRunner._refuse``; each
gives the model, the transcript and the event stream what the inline copies gave."""

from __future__ import annotations

import json

import pytest

from backend.agent.providers.base import ProviderMessage
from backend.agent.runner import AgentEvent, AgentRunner, RunConfig
from backend.agent.serialization import format_rejected_tool_result, format_tool_result_for_llm
from backend.agent.tools import ToolCallRecord, ToolCallResult

NUDGE = "Stop: same call again."


def _old(runner: AgentRunner, rec: ToolCallRecord, case: str) -> tuple[list, list, list]:
    """The inline blocks as they were, one per refusal reason."""
    fmt = runner.config.tool_result_format
    blocks: list = []
    messages: list = []
    if case == "rejected":
        llm = format_rejected_tool_result(fmt=fmt)
    else:
        if case == "blocked":
            result = ToolCallResult(ok=False, tool=rec.name, error="Plan mode: read only")
            llm = format_tool_result_for_llm(rec.name, result.to_json_str(), fmt=fmt)
            rec.result = {"ok": False, "data": result.error, "hint": ""}
        else:
            payload = json.dumps({"ok": False, "tool": rec.name, "error": NUDGE}, ensure_ascii=False)
            llm = format_tool_result_for_llm(rec.name, payload, fmt=fmt)
            rec.duration_ms = 0
            rec.result = {"ok": False, "data": NUDGE, "hint": case}
        rec.status = "error"
    runner._attach_llm_payload(rec, llm)
    blocks.append(runner._record_to_block(rec))
    events = [AgentEvent(kind="tool_end", tool=rec)]
    messages.append(ProviderMessage(role="tool", tool_call_id=rec.id, content=llm))
    return blocks, messages, events


def _new(runner: AgentRunner, rec: ToolCallRecord, case: str) -> tuple[list, list, list]:
    blocks: list = []
    messages: list = []
    if case == "rejected":
        event = runner._refuse(rec, None, "", "", blocks, messages)
    elif case == "blocked":
        payload = ToolCallResult(ok=False, tool=rec.name, error="Plan mode: read only").to_json_str()
        event = runner._refuse(rec, payload, "Plan mode: read only", "", blocks, messages)
    else:
        payload = json.dumps({"ok": False, "tool": rec.name, "error": NUDGE}, ensure_ascii=False)
        event = runner._refuse(rec, payload, NUDGE, case, blocks, messages)
    return blocks, messages, [event]


def _rec(status: str) -> ToolCallRecord:
    return ToolCallRecord(id="call-1", name="ducky_call_tool", status=status,
                          arguments={"name": "workspace_write_file", "arguments": {"path": "a.txt"}})


@pytest.mark.parametrize("case", ["blocked", "rejected", "repeat-call guard", "one-heavy-per-step"])
@pytest.mark.parametrize("fmt", ["toon", "json"])
def test_each_refusal_reason_yields_the_same_events_as_before(case: str, fmt: str) -> None:
    runner = AgentRunner(RunConfig(conv_id="c1", tool_result_format=fmt))
    status = "rejected" if case == "rejected" else "pending"
    old_rec, new_rec = _rec(status), _rec(status)
    old = _old(runner, old_rec, case)
    new = _new(runner, new_rec, case)
    assert new[0] == old[0]  # transcript block
    assert new[1] == old[1]  # what the model reads next
    assert [(e.kind, e.tool) for e in new[2]] == [(e.kind, e.tool) for e in old[2]]
    assert new_rec == old_rec
