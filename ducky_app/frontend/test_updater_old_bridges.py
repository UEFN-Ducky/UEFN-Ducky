"""After an update the new app stops tool servers (bridges) left over from the old install."""

from __future__ import annotations

import json
import os

import pytest

import backend.bridge.bridge_version as bv
import frontend.updater as updater


@pytest.fixture
def registry(monkeypatch, tmp_path):
    root = tmp_path / "bridges"
    monkeypatch.setenv(bv.DIR_ENV, str(root))
    table = {os.getpid(): 500.0, 4467: 1000.0, 4469: 1000.0}
    monkeypatch.setattr(bv, "_create_time", lambda pid: table.get(int(pid), 0.0))
    root.mkdir(parents=True)
    for pid, version in ((4467, "1.2.467"), (4469, updater.__version__)):
        (root / f"{pid}.json").write_text(
            json.dumps({"pid": pid, "version": version, "create_time": 1000.0}), encoding="utf-8"
        )
    return root


def test_the_new_app_stops_bridges_from_the_old_install(registry, monkeypatch) -> None:
    if not bv.is_older("1.2.467", updater.__version__):
        pytest.skip("app version is not newer than the fake old bridge")
    killed: list[int] = []
    real = bv.stop_old_bridges
    monkeypatch.setattr(
        bv, "stop_old_bridges", lambda **kw: real(kill=lambda pid: killed.append(pid) or True, exe="", **kw)
    )

    assert updater.stop_old_bridges_after_update() == [4467]

    assert killed == [4467]
    app = json.loads((registry / bv.APP_FILE).read_text(encoding="utf-8"))
    assert app["version"] == updater.__version__ and app["pid"] == os.getpid()
    assert sorted(p.name for p in registry.glob("*.json")) == sorted([bv.APP_FILE, "4469.json"])


def test_startup_sweep_stops_old_bridges_but_the_old_apps_own_sweep_does_not(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    monkeypatch.setattr(updater, "stop_old_bridges_after_update", lambda: calls.append("stop") or [])
    monkeypatch.setattr(updater, "installer_cache_dir", lambda: tmp_path / "no-cache")

    updater.sweep_installer_cache()
    assert calls == ["stop"]

    updater.sweep_installer_cache(keep_newer_than="1.2.469")  # apply_update, in the old app
    assert calls == ["stop"]


def test_source_runs_stop_nothing(monkeypatch) -> None:
    monkeypatch.delenv(bv.DIR_ENV, raising=False)
    monkeypatch.setattr(bv, "_packaged", lambda: False)
    monkeypatch.setattr(bv, "stop_old_bridges", lambda **kw: pytest.fail("stopped bridges from a source run"))

    assert updater.stop_old_bridges_after_update() == []


def test_a_broken_registry_never_breaks_the_update(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(bv.DIR_ENV, str(tmp_path / "bridges"))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(bv, "note_running_app", boom)
    assert updater.stop_old_bridges_after_update() == []
