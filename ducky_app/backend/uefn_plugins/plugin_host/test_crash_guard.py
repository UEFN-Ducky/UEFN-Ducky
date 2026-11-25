"""Crash guard: a window that died loading a plugin version twice in a row skips it next launch."""

from __future__ import annotations

import json

import pytest

from backend.uefn_plugins.plugin_host import crash_guard


@pytest.fixture
def markers(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_guard, "_markers_dir", lambda: tmp_path)
    monkeypatch.setattr(crash_guard, "_crash_checked", False)
    monkeypatch.setattr(crash_guard, "_closing", False)
    return tmp_path


def _died_loading(markers, monkeypatch, plugin: str, version: str, process: int) -> None:
    """A window that ended while loading it; the next launch reads the marker."""
    monkeypatch.setattr(crash_guard, "_crash_checked", False)
    (markers / f"{plugin}.{process}.json").write_text(
        json.dumps({"plugin": plugin, "version": version, "process": process, "role": "app"}))


def test_a_dead_process_marker_twice_in_a_row_becomes_a_crash_record(markers, monkeypatch) -> None:
    monkeypatch.setattr(crash_guard, "_process_alive", lambda pid: False)
    _died_loading(markers, monkeypatch, "boom", "1.2", 4241)
    assert crash_guard.plugin_crashed_ducky("boom", "1.2") is False  # one strike: loads once more
    _died_loading(markers, monkeypatch, "boom", "1.2", 4242)
    assert crash_guard.plugin_crashed_ducky("boom", "1.2") is True
    assert crash_guard.plugin_crashed_ducky("boom", "1.3") is False
    assert not (markers / "boom.4242.json").exists()
    crash_guard.clear_plugin_crash("boom")
    assert crash_guard._read_crashed() == {}


def test_one_window_ended_mid_load_then_a_clean_load_skips_nothing(markers, monkeypatch) -> None:
    # An installer or Task Manager ending Ducky while Ducky Account 1.1.88 loaded (Nov 24 2026).
    monkeypatch.setattr(crash_guard, "_process_alive", lambda pid: False)
    _died_loading(markers, monkeypatch, "account", "1.1.88", 4241)
    assert crash_guard.plugin_crashed_ducky("account", "1.1.88") is False
    path = crash_guard._load_marker_begin("account", "1.1.88")
    crash_guard._load_marker_end(path)  # this launch loaded it fine: the strike goes
    _died_loading(markers, monkeypatch, "account", "1.1.88", 4242)
    assert crash_guard.plugin_crashed_ducky("account", "1.1.88") is False


def test_a_live_process_marker_is_a_load_in_flight(markers, monkeypatch) -> None:
    monkeypatch.setattr(crash_guard, "_process_alive", lambda pid: True)
    (markers / "busy.4242.json").write_text(json.dumps({"plugin": "busy", "version": "1", "process": 4242, "role": "app"}))
    assert crash_guard.plugin_crashed_ducky("busy", "1") is False
    assert (markers / "busy.4242.json").exists()


def test_a_marker_ends_with_its_load(markers) -> None:
    path = crash_guard._load_marker_begin("ok", "1")
    assert path is not None and path.exists()
    assert json.loads(path.read_text())["role"] == "app"
    crash_guard._load_marker_end(path)
    assert not path.exists()


def test_a_tool_server_or_agent_host_leaves_no_marker(markers, monkeypatch) -> None:
    # Whoever started it ends it at any moment (a chat that stops): that is not Ducky closing.
    monkeypatch.setattr(crash_guard, "is_app", lambda: False)
    assert crash_guard._load_marker_begin("account", "1.1.82") is None
    assert list(markers.glob("*.json")) == []
    crash_guard._load_marker_end(None)


def test_a_marker_a_tool_server_left_blames_no_plugin(markers, monkeypatch) -> None:
    # What versions before Nov 12 2026 left behind: no role in it, its process gone.
    monkeypatch.setattr(crash_guard, "_process_alive", lambda pid: False)
    (markers / "account.4242.json").write_text(json.dumps({"plugin": "account", "version": "1.1.82", "process": 4242}))
    assert crash_guard.plugin_crashed_ducky("account", "1.1.82") is False
    assert not (markers / "account.4242.json").exists() and crash_guard._read_crashed() == {}


@pytest.mark.parametrize("old", ["crashed.json", "crashed-app.json"])
def test_records_made_by_older_rules_are_dropped_once(markers, monkeypatch, old) -> None:
    monkeypatch.setattr(crash_guard, "_process_alive", lambda pid: False)
    (markers / old).write_text(json.dumps({"account": "1.1.88", "kanban": "1.0.3"}))
    assert crash_guard.plugin_crashed_ducky("account", "1.1.88") is False
    assert not (markers / old).exists()
    # A window that really dies while loading it, twice, is found again in the new record.
    _died_loading(markers, monkeypatch, "account", "1.1.88", 4243)
    _died_loading(markers, monkeypatch, "account", "1.1.88", 4244)
    monkeypatch.setattr(crash_guard, "_crash_checked", False)
    crash_guard.plugin_crashed_ducky("account", "1.1.88")
    assert json.loads((markers / "crashed-twice.json").read_text()) == {"account": "1.1.88"}


def test_quitting_or_updating_while_a_plugin_loads_is_no_crash(markers, monkeypatch) -> None:
    # Quit, an update and a newer version taking over end the process at once (TerminateProcess),
    # with the start's loads still running: boot_report.mark("closed") says so first.
    from frontend import boot_report

    path = crash_guard._load_marker_begin("account", "1.1.82")
    other = markers / "account.4242.json"  # another window's load: not this exit's to end
    other.write_text(json.dumps({"plugin": "account", "version": "1.1.82", "process": 4242, "role": "app"}))
    assert path is not None and path.exists()
    boot_report.mark("closed")
    assert not path.exists() and other.exists()
    assert crash_guard._load_marker_begin("kanban", "1.0.4") is None  # the exit is running: nothing new is left
    # The next start: this process is gone, and nothing blames the plugin it was loading.
    other.unlink()
    monkeypatch.setattr(crash_guard, "_closing", False)
    monkeypatch.setattr(crash_guard, "_process_alive", lambda pid: False)
    assert crash_guard.plugin_crashed_ducky("account", "1.1.82") is False
