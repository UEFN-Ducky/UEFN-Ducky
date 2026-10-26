from __future__ import annotations

import json

from backend.agent.serialization import format_tool_result_for_llm


def test_json_result_preserves_values_without_structural_whitespace():
    from backend.agent.tools import ToolCallResult, compact_tool_result_for_api

    data = {"label": "Énergie 中文", "text": "  keep : spaces , and\\n", "items": [1, False, None]}
    payload = {"ok": True, "tool": "demo_read", "data": data}
    out = format_tool_result_for_llm("demo_read", json.dumps(payload), fmt="json")
    expected = {**payload, "data": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}
    assert json.loads(json.loads(out)["data"]) == data
    assert out == json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    assert len(out) < len(json.dumps(expected, ensure_ascii=False))

    envelope = ToolCallResult(ok=True, tool="demo_read", data=json.dumps(data)).to_json_str()
    assert json.loads(json.loads(envelope)["data"]) == data
    legacy = compact_tool_result_for_api("demo_read", json.dumps(payload))
    assert json.loads(json.loads(legacy)["data"]) == data


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
