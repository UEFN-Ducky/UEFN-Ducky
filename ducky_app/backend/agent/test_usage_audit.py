"""The token audit counts each call once, prices cache tiers right and finds repeat reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.agent import usage_audit as ua

TS = "2026-11-17T10:00:00Z"
NOW = 1794996000.0  # 2026-11-17T22:00Z


def _call(mid: str, req: str, *, read: int, write: int = 0, one_hour: int = 0, out: int = 100,
          model: str = "claude-opus-5-5", tools: list | None = None) -> dict:
    usage = {"input_tokens": 10, "output_tokens": out, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": write,
             "cache_creation": {"ephemeral_1h_input_tokens": one_hour, "ephemeral_5m_input_tokens": write - one_hour}}
    return {"timestamp": TS, "requestId": req,
            "message": {"id": mid, "role": "assistant", "model": model, "usage": usage, "content": tools or []}}


def _log(tmp_path: Path, rows: list[dict], name: str = "chat.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    return path


def _read(path: str) -> dict:
    return {"type": "tool_use", "name": "mcp__uefn__workspace_read_file", "input": {"path": path}}


def test_calls_are_counted_once_and_priced_by_cache_tier(tmp_path: Path) -> None:
    rename = {"type": "tool_use", "name": "mcp__uefn__ducky_rename_self", "input": {"title": "Road network"}}
    first = _call("m1", "r1", read=0, write=1000, one_hour=1000, tools=[rename])
    log = _log(tmp_path, [first, first, _call("m2", "r2", read=500_000)])  # the same call logged twice
    out = ua.audit_logs([log])
    assert out["calls"] == 2 and out["chats"] == 1
    write_1h = (10 * 4 + 100 * 20 + 1000 * 8) / 1e6
    big = (10 * 4 + 100 * 20 + 500_000 * 0.40) / 1e6
    assert out["cost_usd"] == round(write_1h + big, 2)
    assert out["cost_by_context"]["400K+"]["calls"] == 1
    assert out["cost_by_context"]["400K+"]["share"] > 0.9
    assert out["top_chats"][0]["chat"] == "Road network" and out["top_chats"][0]["peak_context"] == 500_010


def test_repeat_reads_reset_at_a_compaction(tmp_path: Path) -> None:
    rows = [_call("m1", "r1", read=10, tools=[_read("a.verse")]),
            _call("m2", "r2", read=10, tools=[_read("a.verse"), _read("b.verse")]),
            {"isCompactSummary": True, "message": {"role": "user", "content": "summary"}},
            _call("m3", "r3", read=10, tools=[_read("a.verse")])]
    out = ua.audit_logs([_log(tmp_path, rows)])
    assert out["repeat_reads"] == {"repeats": 1, "reads": 4, "share": 0.25}


def test_request_blob_copies_and_relogged_tool_calls_are_not_repeats(tmp_path: Path) -> None:
    use = dict(_read("a.verse"), id="toolu_1")
    blob = {"type": "api-request-blob", "timestamp": TS,
            "message": {"role": "assistant", "content": [dict(use)]}}
    rows = [_call("m1", "r1", read=10, tools=[use]), blob, _call("m1", "r1", read=10, tools=[dict(use)])]
    out = ua.audit_logs([_log(tmp_path, rows)])
    assert out["repeat_reads"] == {"repeats": 0, "reads": 1, "share": 0.0}


def test_unknown_models_are_counted_but_not_priced(tmp_path: Path) -> None:
    out = ua.audit_logs([_log(tmp_path, [_call("m1", "r1", read=10, model="claude-future-9")])])
    assert out["cost_usd"] == 0 and out["unpriced_calls"] == {"claude-future-9": 1}


def test_project_logs_are_found_by_claudes_folder_name(tmp_path: Path) -> None:
    root = r"C:\Users\me\Documents\Fortnite Projects\MovieTheater"
    folder = ua.claude_log_dir(root, home=str(tmp_path))
    assert folder.name == "C--Users-me-Documents-Fortnite-Projects-MovieTheater"
    folder.mkdir(parents=True)
    _log(folder, [_call("m1", "r1", read=10)])
    out = ua.audit_project(root, days=7, home=str(tmp_path), now=NOW + 3600)
    assert out["calls"] == 1 and out["days"] == 7
    assert ua.audit_project(r"C:\Nope", home=str(tmp_path), now=NOW)["note"].startswith("No Claude Code")


@pytest.mark.parametrize("ctx,bucket", [(10, "<50K"), (150_000, "100-200K"), (399_999, "200-400K"), (400_000, "400K+")])
def test_context_buckets(ctx: int, bucket: str) -> None:
    assert ua._bucket(ctx) == bucket
