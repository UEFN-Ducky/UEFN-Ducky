"""workspace_dock AppData round-trip."""

from __future__ import annotations

from pathlib import Path

import frontend.ui_web.workspace_dock as wd


def test_save_and_load_window(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "workspace_dock.json"
    monkeypatch.setattr(wd, "_PATH", path)

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
    assert path.is_file()
    loaded = wd.load_window("main")
    assert loaded == snap
    assert wd.load_window("missing") is None


def test_save_window_ignores_non_dict(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "workspace_dock.json"
    monkeypatch.setattr(wd, "_PATH", path)
    wd.save_window("main", "nope")  # type: ignore[arg-type]
    assert not path.exists()
