from __future__ import annotations

import json

from backend.agent.serialization import format_tool_result_for_llm


def test_cap_envelope_shrinks_structured_plan():
    payload = {
        "ok": True,
        "tool": "ducky_create_plan",
        "data": {
            "nodes": [{"id": f"n{i}", "title": "step " * 40} for i in range(80)],
        },
    }
    out = format_tool_result_for_llm(
        "ducky_create_plan", json.dumps(payload), fmt="toon"
    )
    assert isinstance(out, str)
    assert out
