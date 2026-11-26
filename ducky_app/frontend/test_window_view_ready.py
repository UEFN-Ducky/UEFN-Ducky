"""wait_uefn_ready against a GUID-mounted island: ready at once, and timeouts say why."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontend import project_kind as pk
from frontend import window_view

GUID = "86ef2167-1111-2222-3333-444444444444"
pytestmark = pytest.mark.pc_only("UEFN runs on the PC; a Mac answers at once (test_window_view.py)")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    pk.forget_cached_kind()
    pk.forget_cached_identity()
    monkeypatch.setattr("time.sleep", lambda _s: None)
    yield
    pk.forget_cached_kind()
    pk.forget_cached_identity()


def _island(tmp_path: Path, name: str = "MyProjectBJJJ", plugin: str = GUID) -> Path:
    root = tmp_path / name
    (root / "Content").mkdir(parents=True)
    manifest = {"title": name, "plugins": [{"name": plugin, "bIsRoot": True}]}
    (root / f"{name}.uefnproject").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _health(monkeypatch, name: str) -> None:
    monkeypatch.setattr(
        "backend.bridge.client.listener_get_health",
        lambda _port, timeout=0.8: {"status": "ok", "project_name": name, "project_dir": "x"},
    )


def test_guid_mount_is_ready_on_the_first_poll(tmp_path, monkeypatch):
    island = _island(tmp_path)
    _health(monkeypatch, GUID)
    out = window_view.wait_uefn_ready(timeout=5, root=str(island))
    assert out["ok"] is True
    assert out["project_match"] is True
    assert out["project_name"] == GUID


def test_timeout_names_the_open_island_and_the_expected_mount(tmp_path, monkeypatch):
    island = _island(tmp_path)
    _health(monkeypatch, "Tycoony")
    out = window_view.wait_uefn_ready(timeout=1, root=str(island))
    assert out["ok"] is False
    assert out["project_match"] is False
    assert "Tycoony" in out["error"]
    assert GUID in out["error"]
    assert "MyProjectBJJJ" in out["error"]


def test_unknown_project_name_fails_at_once_instead_of_reporting_ready(tmp_path, monkeypatch):
    _health(monkeypatch, GUID)
    out = window_view.wait_uefn_ready(timeout=5, root="not-a-folder-0000")
    assert out["ok"] is False
    assert out["project_match"] is False
    assert "Not a UEFN island" in out["error"]
