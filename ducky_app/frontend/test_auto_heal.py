"""Auto-heal: each healer fixes its stuck state and nothing else, and the loop keeps what it fixed."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from frontend import auto_heal, settings


@pytest.fixture
def app_dir(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "default_app_data_dir", lambda: tmp_path)
    return tmp_path


def _on(monkeypatch, *ui_ports: int) -> None:
    monkeypatch.setattr(auto_heal, "_answers", lambda port: port in ui_ports)


def _saved(app_dir: Path) -> int | None:
    path = app_dir / settings.PANEL_PORT_FILE
    return json.loads(path.read_text(encoding="utf-8"))["port"] if path.is_file() else None


def test_a_port_file_a_second_copy_moved_points_at_the_app_again(app_dir: Path, monkeypatch) -> None:
    """Nov 11 2026: the app ran on 4199/4200, a second copy saved 4300, and every tool server
    started after it talked to 4299, where nothing listened."""
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4200)
    (app_dir / settings.PANEL_PORT_FILE).write_text(json.dumps({"port": 4300}), encoding="utf-8")
    _on(monkeypatch, 4199)
    fixed = auto_heal.heal_port_file()
    assert "4300" in fixed and "4200" in fixed
    assert _saved(app_dir) is None  # the default needs no file
    assert auto_heal.heal_port_file() == ""  # once


def test_an_app_on_a_moved_port_gets_its_file_back(app_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4400)
    _on(monkeypatch, 4399)
    assert auto_heal.heal_port_file()
    assert _saved(app_dir) == 4400


def test_the_port_file_stays_when_both_ports_answer_or_the_app_doesnt(app_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4200)
    (app_dir / settings.PANEL_PORT_FILE).write_text(json.dumps({"port": 4300}), encoding="utf-8")
    _on(monkeypatch, 4199, 4299)  # can't tell which is the app
    assert auto_heal.heal_port_file() == "" and _saved(app_dir) == 4300
    _on(monkeypatch)  # the app's own port doesn't answer (yet)
    assert auto_heal.heal_port_file() == "" and _saved(app_dir) == 4300


def test_a_port_file_that_agrees_is_left_alone(app_dir: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4200)
    _on(monkeypatch, 4199)
    assert auto_heal.heal_port_file() == ""
    monkeypatch.setattr(settings, "PANEL_LISTENER_PORT", 4300)
    settings._save_panel_port(4300)
    _on(monkeypatch, 4299)
    assert auto_heal.heal_port_file() == ""


def test_plugins_another_process_installed_reload_in_the_app(monkeypatch) -> None:
    from backend.uefn_plugins.plugin_host import lifecycle

    monkeypatch.setattr(lifecycle, "reload_plugins_changed_on_disk", lambda: ["road-network"])
    assert "road-network" in auto_heal.heal_stale_plugins()
    monkeypatch.setattr(lifecycle, "reload_plugins_changed_on_disk", lambda: [])
    assert auto_heal.heal_stale_plugins() == ""


def test_one_healer_failing_never_stops_the_others_and_fixes_are_kept(monkeypatch) -> None:
    def broken() -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(auto_heal, "HEALERS", (("broken", broken), ("ok", lambda: "fixed a thing"), ("quiet", lambda: "")))
    monkeypatch.setattr(auto_heal, "_healed", auto_heal.deque(maxlen=50))
    out = auto_heal.run_once(now=100.0)
    assert out == [{"at": 100.0, "healer": "ok", "fixed": "fixed a thing"}]
    assert auto_heal.recent() == out


def test_ducky_health_says_what_auto_heal_fixed_in_the_last_hour() -> None:
    from frontend import self_health

    now = time.time()
    snap = {"auto_heal": [{"at": now - 7200, "healer": "port_file", "fixed": "old"},
                          {"at": now - 60, "healer": "stale_plugins", "fixed": "reloaded road-network in the app"}]}
    rows = [f for f in self_health.findings(snap) if "by itself" in f["what"]]
    assert len(rows) == 1 and "1 stuck thing" in rows[0]["what"] and "road-network" in rows[0]["why"]


def test_the_loop_runs_only_in_the_app(monkeypatch) -> None:
    from backend import process_role

    started = []
    monkeypatch.setattr(auto_heal, "_started", False)
    monkeypatch.setattr(auto_heal.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(kw["name"])})())
    monkeypatch.setattr(process_role, "is_app", lambda: False)
    auto_heal.start()
    assert started == []
    monkeypatch.setattr(process_role, "is_app", lambda: True)
    auto_heal.start()
    auto_heal.start()  # once
    assert started == ["auto-heal"]
