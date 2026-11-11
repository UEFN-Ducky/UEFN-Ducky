"""UEFN closed and the window idle: nothing in the background asks the listener anything."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.uefn_plugins.plugin_host.testing import patch_host
from frontend.ui_web.verse_editor.urc import island_watch as iw

TEN_MIN = 600.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def wait(self, delay: float) -> bool:
        self.now += delay
        return False


@pytest.fixture
def health_gets(monkeypatch) -> list[int]:
    gets: list[int] = []

    def _closed(port: int, *a, **k):
        gets.append(port)
        return None  # UEFN closed

    monkeypatch.setattr("backend.bridge.listener_get_health", _closed)
    monkeypatch.setattr(iw, "_write_doc", lambda doc: None)
    monkeypatch.setattr(iw, "_write_locks", lambda doc: None)
    iw.reset_for_tests()
    yield gets
    iw.reset_for_tests()


def test_window_boot_does_not_start_the_island_watch() -> None:
    source = (Path(__file__).resolve().parents[3] / "frontend" / "ui_web" / "webview_app.py").read_text("utf-8")
    assert "island_watch.start()" not in source


def test_uefn_closed_ten_idle_minutes_make_zero_listener_requests(health_gets) -> None:
    # Nobody called a UEFN tool: the watch never started, so 10 idle minutes ask nothing.
    clock = FakeClock()
    while clock.now < TEN_MIN:
        clock.wait(iw.NO_ISLAND_S)
    assert not iw.running()
    assert health_gets == []


def test_watch_stops_itself_after_two_rounds_with_uefn_closed(health_gets) -> None:
    # Started by a UEFN call, then UEFN closed: two health GETs, then it stops for good.
    clock = FakeClock()
    iw.run_rounds(wait=clock.wait)
    assert len(health_gets) == iw.STOP_AFTER_MISSES == 2
    assert clock.now <= iw.NO_ISLAND_S * iw.STOP_AFTER_MISSES
    before = len(health_gets)
    while clock.now < TEN_MIN:  # the rest of the 10 minutes: nothing runs
        clock.wait(iw.NO_ISLAND_S)
    assert len(health_gets) == before


def test_a_stop_left_by_an_earlier_test_does_not_skip_the_rounds(health_gets) -> None:
    # Full suite, same worker: test_island_watch sets _stop and never clears it; the reset must.
    iw.stop()
    iw.reset_for_tests()
    iw.run_rounds(wait=FakeClock().wait)
    assert len(health_gets) == iw.STOP_AFTER_MISSES


def test_a_working_uefn_tool_call_starts_the_watch(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import backend.agent.tools as tools

    started: list[str] = []
    monkeypatch.setattr(iw, "on_uefn_call_ok", lambda: started.append("watch"))
    patch_host(monkeypatch, "is_plugin_host_only_tool", lambda _n: False)

    async def _ok(name: str, args: dict):
        return [SimpleNamespace(text='{"ok": true}')]

    mcp = MagicMock()
    mcp.call_tool = _ok
    monkeypatch.setattr(tools, "_ensure_mcp", lambda: mcp)

    assert asyncio.run(tools.execute_tool("workspace_write_file", {})).ok
    assert started == []  # not a UEFN tool
    assert asyncio.run(tools.execute_tool("spawn_actor", {})).ok
    assert started == ["watch"]


def test_status_sweep_runs_only_inside_a_status_read(monkeypatch, tmp_path) -> None:
    # No timer: bridge/status.py starts no thread; the sweep happens only when a caller asks
    # for status, and that read makes exactly its one health GET.
    import ast

    from backend.bridge import status

    tree = ast.parse(Path(status.__file__).read_text("utf-8"))
    called = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"Thread", "Timer", "start_new_thread"}

    gets: list[int] = []
    monkeypatch.setattr(status, "listener_get_health", lambda port, *a, **k: gets.append(port))
    monkeypatch.setattr("backend.mcp_plugins.epic.probe_epic_mcp", lambda **k: {})
    monkeypatch.setattr("frontend.deploy.refresh_inits", lambda root: None)
    monkeypatch.setattr(status, "_python_sweep_at", {})

    out = status.fetch_listener_status(4200, selected_project_root=str(tmp_path))
    assert out["online"] is False
    assert gets == [4200]
