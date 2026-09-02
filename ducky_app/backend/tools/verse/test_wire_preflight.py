"""run_with_build_retry: stale reflection → compile + reload + one retry."""

from __future__ import annotations

import json
import sys
import types

import pytest

from backend.tools.verse import wire_preflight as wp

STALE = "RuntimeError: STALE REFLECTION — field has no compiled hash"


def _install_fakes(monkeypatch, *, compile_payload=None, compile_raises=None):
    calls: dict[str, int] = {"compile": 0, "reload": 0}

    def _compile(pretty: bool = False) -> str:
        calls["compile"] += 1
        if compile_raises is not None:
            raise compile_raises
        return json.dumps(compile_payload or {"compile": {"numErrors": 0}})

    def _reload(pretty: bool = False) -> str:
        calls["reload"] += 1
        return '{"ok": true}'

    diag = types.ModuleType("backend.tools.verse.verse_diagnostics")
    diag.workspace_compile_verse = _compile  # type: ignore[attr-defined]
    system = types.ModuleType("backend.tools.core.system")
    system.reload_listener = _reload  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.tools.verse.verse_diagnostics", diag)
    monkeypatch.setitem(sys.modules, "backend.tools.core.system", system)
    return calls


def _flaky(fail_times: int, error: str = STALE):
    state = {"n": 0}

    def _call():
        state["n"] += 1
        if state["n"] <= fail_times:
            raise RuntimeError(error)
        return {"ok": True, "linked": "Button_1"}

    return _call, state


def test_fails_once_then_succeeds_after_compile_and_reload(monkeypatch):
    calls = _install_fakes(monkeypatch)
    call, state = _flaky(1)
    out = wp.run_with_build_retry(call, tool_name="wire_verse_device_ref")
    assert out["ok"] is True
    assert out["auto_recovered"] == "compiled + reloaded listener"
    assert state["n"] == 2
    assert calls == {"compile": 1, "reload": 1}


def test_string_result_gets_auto_recovered_field(monkeypatch):
    _install_fakes(monkeypatch)
    state = {"n": 0}

    def _call():
        state["n"] += 1
        if state["n"] == 1:
            return json.dumps({"ok": False, "error": "Verse class not found for MyDevice"})
        return json.dumps({"ok": True})

    out = wp.run_with_build_retry(_call, tool_name="set_verse_editable")
    assert json.loads(out) == {"ok": True, "auto_recovered": "compiled + reloaded listener"}


def test_compile_with_errors_returns_next_without_retry(monkeypatch):
    calls = _install_fakes(
        monkeypatch,
        compile_payload={"compile": {"numErrors": 2, "message": "2 errors in npc.verse"}},
    )
    call, state = _flaky(5)
    out = wp.run_with_build_retry(call, tool_name="wire_verse_device_array")
    assert out["ok"] is False
    assert out["next"] == "fix the Verse errors, then retry"
    assert out["compile"]["numErrors"] == 2
    assert "2 errors in npc.verse" in json.dumps(out)
    assert STALE in out["error"]
    assert state["n"] == 1  # no retry
    assert calls == {"compile": 1, "reload": 0}


def test_compile_unavailable_returns_original_error_and_next(monkeypatch):
    calls = _install_fakes(
        monkeypatch,
        compile_raises=ValueError("Verse Workflow Server not connected (UEFN not open)"),
    )
    call, state = _flaky(5)
    out = wp.run_with_build_retry(call, tool_name="set_npc_definition_behavior")
    assert out["ok"] is False
    assert STALE in out["error"]
    assert "not connected" in out["compile_error"]
    assert out["next"] == "Open the project in UEFN and run workspace_compile_verse, then retry once"
    assert state["n"] == 1
    assert calls == {"compile": 1, "reload": 0}


def test_non_stale_error_propagates_unchanged(monkeypatch):
    calls = _install_fakes(monkeypatch)
    call, _ = _flaky(5, error="Actor not found: Button_9")
    with pytest.raises(RuntimeError, match="Actor not found"):
        wp.run_with_build_retry(call, tool_name="wire_verse_device_ref")
    assert calls == {"compile": 0, "reload": 0}


def test_only_one_retry_then_second_failure_raises(monkeypatch):
    calls = _install_fakes(monkeypatch)
    call, state = _flaky(5)
    with pytest.raises(RuntimeError, match="STALE REFLECTION"):
        wp.run_with_build_retry(call, tool_name="wire_verse_device_ref")
    assert state["n"] == 2
    assert calls == {"compile": 1, "reload": 1}


def test_failure_recorded_in_verse_stats_when_importable(monkeypatch):
    _install_fakes(monkeypatch)
    seen: list[tuple[str, str]] = []
    stats = types.ModuleType("backend.tools.verse.verse_stats")
    stats.record_tool_failure = lambda tool, msg: seen.append((tool, msg))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "backend.tools.verse.verse_stats", stats)
    call, _ = _flaky(1)
    wp.run_with_build_retry(call, tool_name="wire_verse_device_ref")
    assert seen and seen[0][0] == "wire_verse_device_ref" and "STALE" in seen[0][1]


def test_host_wrapper_wire_array_accepts_many_targets(monkeypatch):
    import backend.tools.verse.verse_editable as ve

    sent: list[tuple[str, dict]] = []

    def _send(command, params=None, timeout=None):
        sent.append((command, params or {}))
        return {"ok": True, "wired": list(params["target_paths"]), "count": len(params["target_paths"])}

    monkeypatch.setattr(ve, "send_command", _send)
    out = json.loads(ve.wire_verse_device_array("Dev", "Markers", target_paths=["M1", "M2", "M3"]))
    assert out["count"] == 3
    assert sent[0][1]["target_paths"] == ["M1", "M2", "M3"]
    with pytest.raises(ValueError, match="at least one"):
        ve.wire_verse_device_array("Dev", "Markers")
