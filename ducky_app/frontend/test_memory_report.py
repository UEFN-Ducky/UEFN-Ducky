"""``ducky_memory_report``: the sections it answers, a big module global named among the
containers, and tracemalloc's diff showing an allocation made between two calls."""

from __future__ import annotations

import json
import sys

from frontend import memory_report

BIG: list[bytes] = []  # a module global the report must find


def test_the_report_has_every_section_and_names_a_big_global() -> None:
    BIG[:] = [bytes(1024) + i.to_bytes(4, "little") for i in range(4096)]  # ~4 MB, each its own object
    try:
        got = memory_report.report(top=200, walk_budget_s=30)
    finally:
        BIG.clear()
    assert got["process"]["pid"] > 0 and got["process"]["python_threads"] >= 1
    assert "rss_mb" in got["process"] and "private_mb" in got["process"]
    assert got["gc"]["tracked"] > 0 and got["gc"]["types"][0]["count"] > 0
    row = next(r for r in got["containers"]["top"] if r["name"].endswith("test_memory_report.BIG"))
    assert row["len"] == 4096 and row["mb"] >= 4
    assert "tracemalloc" not in got  # only once asked for
    if sys.platform == "win32":  # native memory no Python object accounts for (DPAPI, C libraries)
        native = got["native"]
        assert native["heaps_allocated_mb"] > 0 and native["private_committed_mb"] >= native["heaps_committed_mb"] * 0.5
        assert 0 <= native["heap_free_mb"] <= native["heaps_committed_mb"]
    json.dumps(got)  # the tool answers it as JSON


def test_tracemalloc_diff_shows_what_grew_between_two_calls() -> None:
    kept: list[bytes] = []
    try:
        first = memory_report.report(start_trace=True, collect=False, walk_budget_s=1)
        assert first["tracemalloc"]["tracing"] and "grew" not in first["tracemalloc"]
        kept.extend(bytes(1024) for _ in range(8192))  # ~8 MB on this line
        second = memory_report.report(collect=False, walk_budget_s=1)
        grew = second["tracemalloc"]["grew"]
        assert any("test_memory_report.py:" in g["site"] and g["mb"] >= 7 for g in grew), grew[:5]
    finally:
        assert memory_report.report(stop_trace=True, collect=False, walk_budget_s=1)["tracemalloc"] == {"tracing": False}
        kept.clear()


def test_the_tool_answers_in_the_app_process(monkeypatch) -> None:
    from backend.tools.panel import ducky_panel
    from frontend.ui_web.agent_turns.testing import patch_turns

    patch_turns(monkeypatch, "_in_bridge_process", lambda: False)
    got = json.loads(ducky_panel.ducky_memory_report(collect=False, top=5))
    assert {"process", "gc", "containers"} <= set(got)
