"""The page's "startup logo went" report lands in Ducky's boot record (the launch race reads it)."""

from __future__ import annotations

from frontend import boot_report
from frontend.ui_web.panel_api import PanelApi


def test_ui_drawn_becomes_a_boot_stage(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(boot_report, "stage", seen.append)
    api = PanelApi.__new__(PanelApi)
    assert api.report_ui_perf([{"kind": "boot", "name": "ui_drawn", "duration_ms": 1234}])
    assert seen == ["ui_drawn"]


def test_other_perf_samples_are_not_boot_stages(monkeypatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(boot_report, "stage", seen.append)
    api = PanelApi.__new__(PanelApi)
    assert api.report_ui_perf([{"kind": "ui_stall", "name": "frame", "duration_ms": 80}])
    assert seen == []
