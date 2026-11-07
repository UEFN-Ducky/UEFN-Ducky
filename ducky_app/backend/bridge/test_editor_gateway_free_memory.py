"""After a heavy call on a grown UEFN, its idle RAM is given back once, in the background."""

from __future__ import annotations

import asyncio
import threading

import pytest

from backend.bridge import editor_gateway

GB = 1024 ** 3


@pytest.fixture
def uefn(monkeypatch: pytest.MonkeyPatch, tmp_path) -> dict:
    """A fake UEFN (baseline 21 GB, 8 GB in RAM); ``trims`` and ``queued`` record the frees."""
    state = {"proc": (100, 5.0, 21 * GB), "ws": 8 * GB, "trims": [], "queued": [], "now": 1000.0}
    monkeypatch.setattr(editor_gateway, "_uefn_memory", lambda: state["proc"])
    monkeypatch.setattr(editor_gateway, "_baseline_path", lambda: tmp_path / "baseline.json")
    monkeypatch.setattr(editor_gateway, "_freed_path", lambda: tmp_path / "freed.json")
    monkeypatch.setattr(editor_gateway, "_lease_path", lambda: tmp_path / "lease.json")
    monkeypatch.setattr(editor_gateway, "_growth_limit_gb", lambda: 4.0)
    monkeypatch.setattr(editor_gateway, "_now", lambda: state["now"])
    monkeypatch.setattr(editor_gateway, "_working_set", lambda pid: state["ws"])

    def _trim(pid: int) -> bool:
        state["trims"].append(pid)
        state["ws"] = int(0.4 * GB)
        return True

    monkeypatch.setattr(editor_gateway, "_empty_working_set", _trim)
    monkeypatch.setattr(editor_gateway, "_spawn", state["queued"].append)
    monkeypatch.setattr(editor_gateway, "_free_lock", threading.Lock())
    editor_gateway.uefn_growth()  # anchors the 21 GB baseline
    return state


def _grow(uefn: dict, gb: float) -> None:
    uefn["proc"] = (100, 5.0, int((21 + gb) * GB))


def test_a_heavy_call_past_the_limit_queues_one_free_after_it_answers(uefn) -> None:
    _grow(uefn, 6)
    order: list[str] = []
    assert editor_gateway.call("", "execute_python", True, lambda: order.append("call") or "ran") == "ran"
    assert order == ["call"] and len(uefn["queued"]) == 1 and uefn["trims"] == []  # queued, not run inline
    uefn["queued"][0]()
    assert uefn["trims"] == [100]
    assert editor_gateway.last_freed()["freed_gb"] == 7.6


def test_under_the_limit_or_a_light_call_frees_nothing(uefn) -> None:
    _grow(uefn, 2)
    editor_gateway.call("", "execute_python", True, lambda: None)
    _grow(uefn, 9)
    editor_gateway.call("", "spawn_actor", True, lambda: None)
    assert uefn["queued"] == []


def test_only_one_free_at_a_time(uefn) -> None:
    _grow(uefn, 6)
    editor_gateway.call("", "execute_python", True, lambda: None)
    editor_gateway.call("", "execute_python", True, lambda: None)  # first one still queued
    assert len(uefn["queued"]) == 1
    uefn["queued"][0]()
    uefn["now"] += editor_gateway.FREE_GAP_SEC + 1
    editor_gateway.call("", "execute_python", True, lambda: None)
    assert len(uefn["queued"]) == 2


def test_no_second_free_within_the_gap(uefn) -> None:
    _grow(uefn, 6)
    editor_gateway.call("", "execute_python", True, lambda: None)
    uefn["queued"][0]()
    uefn["now"] += 10
    editor_gateway.call("", "execute_python", True, lambda: None)
    assert len(uefn["queued"]) == 1


def test_an_epic_heavy_acall_queues_the_free_too(uefn) -> None:
    _grow(uefn, 6)

    async def _fn():
        return "built"

    assert asyncio.run(editor_gateway.acall("", "unreal__call_tool", True, _fn, heavy=True)) == "built"
    assert len(uefn["queued"]) == 1


def test_a_failed_call_frees_nothing(uefn) -> None:
    _grow(uefn, 6)
    with pytest.raises(RuntimeError):
        editor_gateway.call("", "execute_python", True, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert uefn["queued"] == []


def test_on_request_free_reports_gb_and_keeps_the_undo_history(uefn) -> None:
    out = editor_gateway.free_uefn_memory()
    assert out["ok"] is True and out["freed_gb"] == 7.6 and out["why"] == "on request"
    assert out["working_set_gb"] == {"before": 8.0, "after": 0.4}
    assert "undo history is left as it is" in out["note"]


def test_free_with_uefn_closed_says_so(uefn) -> None:
    uefn["proc"] = None
    assert editor_gateway.free_uefn_memory() == {"ok": False, "error": "UEFN isn't running."}


def test_status_shows_growth_and_the_last_free(uefn) -> None:
    _grow(uefn, 6.2)
    assert editor_gateway.uefn_memory_status()["line"] == "UEFN +6.2 GB since load"
    editor_gateway.call("", "execute_python", True, lambda: None)
    uefn["queued"][0]()
    assert editor_gateway.uefn_memory_status()["line"] == (
        "UEFN +6.2 GB since load, freed 7.6 GB RAM after the last heavy call"
    )


def test_self_care_runs_free_uefn_memory(uefn) -> None:
    from frontend import self_health

    out = self_health.self_care("free_uefn_memory")
    assert out["ok"] is True and out["action"] == "free_uefn_memory" and out["freed_gb"] == 7.6
