"""Old-version bridges exit after an update: only the running app's version connects."""

from __future__ import annotations

import contextlib
import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

import backend.bridge.bridge_version as bv
import backend.bridge.client as bridge

OLD, NEW = "1.2.467", "1.2.469"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_path):
    bv.reset_for_tests()
    monkeypatch.setenv(bv.DIR_ENV, str(tmp_path / "bridges"))
    yield
    bv.reset_for_tests()


@pytest.fixture
def pids(monkeypatch):
    """Fake live processes: pid -> create_time. This test process is always alive."""
    table: dict[int, float] = {os.getpid(): 500.0}
    monkeypatch.setattr(bv, "_create_time", lambda pid: table.get(int(pid), 0.0))
    return table


def _app_running(root: Path, version: str = NEW) -> None:
    bv.note_running_app(version, root=root)


def _bridge(root: Path, pid: int, version: str, create_time: float = 1000.0) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{pid}.json").write_text(
        json.dumps({"pid": pid, "version": version, "role": "tool_server", "create_time": create_time}),
        encoding="utf-8",
    )


def test_versions_compare_numerically() -> None:
    assert bv.is_older("1.2.467", "1.2.469")
    assert bv.is_older("1.2.9", "1.2.10")
    assert not bv.is_older("1.2.469", "1.2.469")
    assert not bv.is_older("1.3.0", "1.2.469")
    assert not bv.is_older("", "1.2.469") and not bv.is_older("1.2.467", "")


def test_an_older_bridge_refuses_its_call_and_exits(tmp_path, pids) -> None:
    root = tmp_path / "bridges"
    _app_running(root)
    exits: list[str] = []

    with pytest.raises(bv.OutdatedBridge, match=f"{OLD}.*{NEW}"):
        bv.ensure_current(role="tool_server", version=OLD, root=root, exit_fn=lambda: exits.append("exit"))

    assert exits == ["exit"]
    assert not (root / f"{os.getpid()}.json").exists()


def test_a_current_bridge_registers_and_stays(tmp_path, pids) -> None:
    root = tmp_path / "bridges"
    _app_running(root)

    bv.ensure_current(role="tool_server", version=NEW, root=root, exit_fn=lambda: pytest.fail("must not exit"))

    record = json.loads((root / f"{os.getpid()}.json").read_text(encoding="utf-8"))
    assert record["version"] == NEW


def test_a_bridge_with_no_app_running_stays_up(tmp_path, pids) -> None:
    root = tmp_path / "bridges"
    root.mkdir(parents=True)
    (root / bv.APP_FILE).write_text(json.dumps({"pid": 999_999, "version": NEW}), encoding="utf-8")

    bv.ensure_current(role="tool_server", version=OLD, root=root, exit_fn=lambda: pytest.fail("must not exit"))


def test_the_app_stops_older_registered_bridges_only(tmp_path, pids) -> None:
    root = tmp_path / "bridges"
    pids.update({101: 1000.0, 102: 1000.0})
    _app_running(root)
    _bridge(root, 101, OLD)
    _bridge(root, 102, NEW)
    _bridge(root, 103, OLD)  # already gone: its record is just dropped
    killed: list[int] = []

    stopped = bv.stop_old_bridges(version=NEW, root=root, kill=lambda pid: killed.append(pid) or True, exe="")

    assert stopped == killed == [101]
    assert sorted(p.name for p in root.glob("*.json")) == sorted([bv.APP_FILE, "102.json"])


def test_a_reused_pid_is_not_mistaken_for_the_old_bridge(tmp_path, pids) -> None:
    root = tmp_path / "bridges"
    pids[101] = 5000.0  # a new, unrelated process now has pid 101
    _app_running(root)
    _bridge(root, 101, OLD, create_time=1000.0)

    assert bv.stop_old_bridges(version=NEW, root=root, kill=lambda pid: pytest.fail("killed"), exe="") == []


def test_bridges_from_before_the_registry_are_stopped_by_exe(tmp_path, pids, monkeypatch) -> None:
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    root = tmp_path / "bridges"
    _app_running(root)
    exe = tmp_path / "install" / "UEFN-Ducky.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    installed = bv._installed_at(str(exe))
    old_exe = str(tmp_path / "old" / "UEFN-Ducky-1.2.467.exe")
    procs = [
        {"pid": 201, "exe": old_exe, "cmdline": [old_exe, "bridge"], "create_time": installed + 60},
        {"pid": 202, "exe": str(exe), "cmdline": [str(exe), "bridge"], "create_time": installed + 60},  # current
        {"pid": 203, "exe": str(exe), "cmdline": [str(exe), "bridge"], "create_time": installed - 3600},  # old binary
        {"pid": 204, "name": "UEFN-Ducky-Bridge.exe", "exe": str(tmp_path / "old" / "UEFN-Ducky-Bridge.exe"),
         "cmdline": [], "create_time": installed + 60},  # old bridge EXE
        {"pid": os.getpid(), "exe": str(tmp_path / "x" / "UEFN-Ducky.exe"), "create_time": 0.0},  # the app
    ]
    killed: list[int] = []

    stopped = bv.stop_old_bridges(
        version=NEW, root=root, processes=lambda: procs, kill=lambda pid: killed.append(pid) or True, exe=str(exe)
    )

    assert sorted(stopped) == [201, 203, 204]


def test_another_ducky_window_or_agent_host_is_never_stopped(tmp_path, pids, monkeypatch) -> None:
    # Nov 5 2026: a test copy started from the sandbox ended the person's Ducky (another file,
    # started before the copy was made) and its agent hosts, with no crash trace.
    monkeypatch.delenv("DUCKY_SANDBOX", raising=False)
    root = tmp_path / "bridges"
    _app_running(root)
    exe = tmp_path / "copy" / "UEFN-Ducky.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    installed = bv._installed_at(str(exe))
    live = str(tmp_path / "versions" / "app-1.2.470" / "UEFN-Ducky.exe")
    procs = [
        {"pid": 301, "exe": live, "cmdline": [live], "create_time": installed - 3600},  # the person's window
        {"pid": 302, "exe": live, "cmdline": [live, "--background"], "create_time": installed - 60},
        {"pid": 303, "exe": live, "cmdline": [live, "agent-host", "--conv", "x"], "create_time": installed - 60},
        {"pid": 304, "exe": live, "cmdline": [live, "C:/proj/a.verse"], "create_time": installed - 60},
        {"pid": 305, "exe": live, "cmdline": None, "create_time": installed - 60},  # cmdline unreadable
    ]

    stopped = bv.stop_old_bridges(
        version=NEW, root=root, processes=lambda: procs, kill=lambda pid: pytest.fail(f"stopped {pid}"), exe=str(exe)
    )

    assert stopped == []


def test_a_test_copy_never_sweeps_other_duckies(tmp_path, pids, monkeypatch) -> None:
    monkeypatch.setenv("DUCKY_SANDBOX", str(tmp_path / "box"))
    root = tmp_path / "bridges"
    _app_running(root)
    exe = tmp_path / "copy" / "UEFN-Ducky.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    other = str(tmp_path / "install" / "UEFN-Ducky.exe")
    procs = [{"pid": 401, "exe": other, "cmdline": [other, "bridge"], "create_time": 1.0}]

    stopped = bv.stop_old_bridges(
        version=NEW, root=root, processes=lambda: procs, kill=lambda pid: pytest.fail(f"stopped {pid}"), exe=str(exe)
    )

    assert stopped == []


def test_source_runs_never_touch_the_registry(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(bv.DIR_ENV)
    monkeypatch.setattr(bv, "_packaged", lambda: False)
    monkeypatch.setattr(bv, "note_running_app", lambda *a, **k: pytest.fail("wrote app.json"))
    monkeypatch.setattr(bv, "register_bridge", lambda *a, **k: pytest.fail("registered"))

    bv.ensure_current(role="tool_server", version=OLD)
    bv.reset_for_tests()
    bv.ensure_current(role="app", version=NEW)
    assert not bv.registry_active()


# ---------------------------------------------------------------------------
# Through the client: the old bridge never reaches the listener, the current one does
# ---------------------------------------------------------------------------


class _Resp:
    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_a) -> None:
        return None


@pytest.fixture
def listener(monkeypatch):
    """A fake listener that records each connecting client's version header."""
    import backend.tools.core.uefn_modal as uefn_modal

    seen: list[str] = []

    def urlopen(req, timeout=None):
        seen.append(str(req.get_header("X-ducky-version") or ""))
        return _Resp({"success": True, "result": {"ok": True}})

    monkeypatch.setattr(bridge, "_uefn_closed", lambda: False)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(uefn_modal, "save_modal_watchdog", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(bv, "_exit_soon", lambda: None)
    monkeypatch.setenv("UEFN_DUCKY_PROCESS_ROLE", "tool_server")
    return seen


def test_only_the_current_version_client_stays_connected(tmp_path, pids, listener, monkeypatch) -> None:
    _app_running(tmp_path / "bridges")

    monkeypatch.setattr(bv, "own_version", lambda: OLD)
    with pytest.raises(ConnectionError, match="old bridge"):
        bridge._post_json_locked(4200, "ping", {}, timeout=1.0)
    assert listener == []  # the old bridge never connected

    bv.reset_for_tests()
    monkeypatch.setattr(bv, "own_version", lambda: NEW)
    assert bridge._post_json_locked(4200, "ping", {}, timeout=1.0)["success"] is True
    assert listener == [NEW]


def test_every_request_names_its_version() -> None:
    headers = bridge._listener_headers()
    assert headers.get("X-Ducky-Version") == bv.own_version() or not bv.own_version()
    assert "X-Ducky-Client" in headers


def test_the_check_runs_once_per_process(tmp_path, pids) -> None:
    root = tmp_path / "bridges"
    _app_running(root)
    bv.ensure_current(role="tool_server", version=NEW, root=root)
    (root / f"{os.getpid()}.json").unlink()
    started = time.perf_counter()
    bv.ensure_current(role="tool_server", version=NEW, root=root)
    assert not (root / f"{os.getpid()}.json").exists()
    assert time.perf_counter() - started < 1.0
