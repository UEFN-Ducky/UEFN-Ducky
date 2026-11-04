"""Crash guard: a marker left by a dead process skips that plugin version next launch."""

from __future__ import annotations

import json

import pytest

from backend.uefn_plugins.plugin_host import crash_guard


@pytest.fixture
def markers(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_guard, "_markers_dir", lambda: tmp_path)
    monkeypatch.setattr(crash_guard, "_crash_checked", False)
    return tmp_path


def test_a_dead_process_marker_becomes_a_crash_record(markers, monkeypatch) -> None:
    monkeypatch.setattr(crash_guard, "_process_alive", lambda pid: False)
    (markers / "boom.4242.json").write_text(json.dumps({"plugin": "boom", "version": "1.2", "process": 4242}))
    assert crash_guard.plugin_crashed_ducky("boom", "1.2") is True
    assert crash_guard.plugin_crashed_ducky("boom", "1.3") is False
    assert not (markers / "boom.4242.json").exists()
    crash_guard.clear_plugin_crash("boom")
    assert crash_guard._read_crashed() == {}


def test_a_live_process_marker_is_a_load_in_flight(markers, monkeypatch) -> None:
    monkeypatch.setattr(crash_guard, "_process_alive", lambda pid: True)
    (markers / "busy.4242.json").write_text(json.dumps({"plugin": "busy", "version": "1", "process": 4242}))
    assert crash_guard.plugin_crashed_ducky("busy", "1") is False
    assert (markers / "busy.4242.json").exists()


def test_a_marker_ends_with_its_load(markers) -> None:
    path = crash_guard._load_marker_begin("ok", "1")
    assert path is not None and path.exists()
    crash_guard._load_marker_end(path)
    assert not path.exists()
