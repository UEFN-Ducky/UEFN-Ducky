"""The temp sweep has to actually be wired into startup, for both modes.

The leak is worst for the MCP bridge, which is the same entry point, so the
sweep must run before the `bridge` branch and not only in the GUI path.
"""

from __future__ import annotations

import pytest

from frontend import launcher


def test_sweep_runs_before_the_bridge_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(launcher.sys, "argv", ["UEFN-Ducky.exe", "bridge", "--port", "4200"])
    monkeypatch.setattr(launcher, "scrub_pyinstaller_boot_env", lambda: calls.append("scrub"))
    monkeypatch.setattr(launcher, "_sweep_stale_extracts", lambda: calls.append("sweep"))
    monkeypatch.setattr(launcher, "run_bridge", lambda: calls.append("bridge"))
    launcher.main()
    assert calls == ["scrub", "sweep", "bridge"]


def test_sweep_is_skipped_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    # From source there is no extraction dir to reclaim.
    started: list[object] = []
    monkeypatch.setattr(launcher.sys, "frozen", False, raising=False)
    monkeypatch.setattr(
        "frontend.temp_cleanup.start_background_sweep",
        lambda **kw: started.append(kw),
    )
    launcher._sweep_stale_extracts()
    assert started == []


def test_sweep_starts_in_the_background_when_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[object] = []
    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        "frontend.temp_cleanup.start_background_sweep",
        lambda **kw: started.append(kw),
    )
    launcher._sweep_stale_extracts()
    assert len(started) == 1


def test_a_broken_sweep_never_stops_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_kw: object) -> None:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr("frontend.temp_cleanup.start_background_sweep", boom)
    launcher._sweep_stale_extracts()  # must not raise
