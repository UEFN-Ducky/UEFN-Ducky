"""A tool call's spill, sidecar and receipt writes run off the shared event loop."""

from __future__ import annotations

import asyncio
import contextvars
import threading

from backend.agent import tool_writes

_who = contextvars.ContextVar("who", default="")


def test_the_spill_write_runs_off_the_loop(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def spill(name, text, limit=0):
        seen["spill"] = threading.get_ident()
        seen["who"] = _who.get()
        return text

    monkeypatch.setattr("backend.agent.tool_spills.inline_or_spill", spill)
    monkeypatch.setattr("backend.workspace.tool_record.record_tool_result",
                        lambda *a, **k: seen.setdefault("record", threading.get_ident()))

    async def main() -> str:
        _who.set("run-7")
        seen["loop"] = threading.get_ident()
        return await tool_writes.spill_and_record("demo__ping", {}, "pong")

    assert asyncio.run(main()) == "pong"
    assert seen["spill"] != seen["loop"] and seen["record"] != seen["loop"]
    assert seen["who"] == "run-7"  # the caller's identity went with it


def test_the_result_is_the_same_as_before(monkeypatch, tmp_path) -> None:
    import json

    from backend.agent import tool_spills

    monkeypatch.setattr("backend.workspace.tool_record.record_tool_result", lambda *a, **k: None)
    monkeypatch.setattr(tool_spills, "tool_spills_dir", lambda for_write=False: tmp_path)
    assert asyncio.run(tool_writes.spill_and_record("demo__big", {}, "small result")) == "small result"
    big = "x" * 400_000
    direct = json.loads(tool_spills.inline_or_spill("demo__big", big))
    via = json.loads(asyncio.run(tool_writes.spill_and_record("demo__big", {}, big)))
    # Each spill names its own file; everything else in the stub is the same.
    for stub in (direct, via):
        stub.pop("path"), stub.pop("name")
    assert via == direct and via["chars"] == 400_000
    assert len(list(tmp_path.glob("demo__big_*.txt"))) == 2


def test_a_failing_journal_never_fails_the_tool(monkeypatch) -> None:
    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("backend.workspace.tool_record.record_tool_result", boom)
    asyncio.run(tool_writes.off_loop(tool_writes.record_sidecar, "demo__ping", {}, "x", ok=False))
