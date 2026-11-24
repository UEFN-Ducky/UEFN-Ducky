"""One detection test per install kind, with fake installs."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.tools.apps import finders

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Linux app finders")


def _exe(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def bare(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(finders, "OPT", tmp_path / "opt")
    monkeypatch.setattr(finders, "_flatpak_info", lambda app_id: "")
    return tmp_path


def test_path(bare):
    exe = _exe(bare / "bin" / "blender")
    hit = finders.find("blender", bare / "home")
    assert hit["kind"] == "path" and hit["argv"] == [str(exe)]


def test_path_blender_asks_its_version(bare):
    exe = _exe(bare / "bin" / "blender")
    exe.write_text("#!/bin/sh\necho 'Blender 4.0.2'\necho 'build date: 2024-01-01'\n")
    assert finders.find("blender", bare / "home")["version"] == "4.0.2"


def test_flatpak_blender_42(bare, monkeypatch):
    info = "Blender - Free and open source 3D creation suite\n\n          ID: org.blender.Blender\n     Version: 4.2.1\n"
    monkeypatch.setattr(finders, "_flatpak_info", lambda app_id: info if app_id == "org.blender.Blender" else "")
    hit = finders.find("blender", bare / "home")
    assert hit == {"app": "blender", "kind": "flatpak", "argv": ["flatpak", "run", "org.blender.Blender"],
                   "path": "flatpak:org.blender.Blender", "version": "4.2.1"}


def test_steam(bare):
    exe = _exe(bare / "home/.local/share/Steam/steamapps/common/Blender/blender")
    hit = finders.find("blender", bare / "home")
    assert hit["kind"] == "steam" and hit["argv"] == [str(exe)]


def test_opt_newest_first(bare):
    _exe(bare / "opt/blender-3.6.0-linux-x64/blender")
    new = _exe(bare / "opt/blender-4.2.1-linux-x64/blender")
    hit = finders.find("blender", bare / "home")
    assert hit["kind"] == "opt" and hit["path"] == str(new) and hit["version"] == "4.2.1"


def test_godot_path_and_flatpak(bare, monkeypatch):
    monkeypatch.setattr(finders, "_flatpak_info",
                        lambda app_id: "Version: 4.3\n" if app_id == "org.godotengine.Godot" else "")
    assert finders.find("godot", bare / "home")["argv"] == ["flatpak", "run", "org.godotengine.Godot"]
    _exe(bare / "bin" / "godot4")
    kinds = [h["kind"] for h in finders.find_all("godot", bare / "home")]
    assert kinds == ["path", "flatpak"]


def test_unreal_source_build(bare):
    exe = _exe(bare / "home/UnrealEngine-5.4/Engine/Binaries/Linux/UnrealEditor")
    hit = finders.find("unreal", bare / "home")
    assert hit["kind"] == "home" and hit["argv"] == [str(exe)] and hit["version"] == "5.4"


def test_nothing_installed(bare):
    assert finders.find("blender", bare / "home") is None
