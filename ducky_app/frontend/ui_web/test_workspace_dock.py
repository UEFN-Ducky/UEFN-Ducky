"""workspace_dock round-trip on both store backends (ADR 0003, phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

import frontend.ui_web.workspace_dock as wd


@pytest.fixture(params=["db", "files"])
def backend(request, monkeypatch) -> str:
    monkeypatch.setenv("DUCKY_STORE_BACKEND", request.param)
    return request.param


def _file(tmp_path: Path) -> Path:
    return tmp_path / ".ducky-appdata" / "UEFN-Ducky" / "workspace_dock.json"


def test_save_and_load_window(backend: str, tmp_path: Path) -> None:
    snap = {
        "version": 1,
        "leftWidth": 280,
        "rightWidth": 320,
        "leftRailOpen": True,
        "rightRailOpen": False,
        "leftPanelMode": "stacked",
        "rightPanelMode": "tabs",
    }
    wd.save_window("main", snap)
    assert wd.load_window("main") == snap
    assert wd.load_window("missing") is None
    assert _file(tmp_path).is_file() == (backend == "files")


def test_save_window_ignores_non_dict(backend: str, tmp_path: Path) -> None:
    wd.save_window("main", "nope")  # type: ignore[arg-type]
    assert wd.load_window("main") is None
    assert not _file(tmp_path).exists()
