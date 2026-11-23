"""UEFN under Wine/Proton: digests found in the prefix's %LOCALAPPDATA%, lore.exe run through the prefix."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tools.uefn import proton
from backend.tools.verse import verse_digests
from frontend.ui_web.verse_editor.urc import detect_urc, lore_cli

WIN64 = "FortniteGame/Binaries/Win64"


@pytest.fixture
def prefix(tmp_path, monkeypatch) -> dict:
    """A Heroic Proton install of UEFN with a steamuser profile holding Isle's digests."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("LORE_CLI_LOCATION", raising=False)
    install = tmp_path / "Games/Fortnite"
    for exe in ("UnrealEditorFortnite-Win64-Shipping.exe", "lore.exe"):
        (install / WIN64).mkdir(parents=True, exist_ok=True)
        (install / WIN64 / exe).write_text("x", encoding="utf-8")
    compat = tmp_path / "Prefixes/Fortnite"
    heroic = tmp_path / ".config/heroic"
    (heroic / "legendaryConfig/legendary").mkdir(parents=True)
    (heroic / "legendaryConfig/legendary/installed.json").write_text(
        json.dumps({"Fortnite": {"install_path": str(install)}}), encoding="utf-8")
    (heroic / "GamesConfig").mkdir()
    (heroic / "GamesConfig/Fortnite.json").write_text(json.dumps(
        {"Fortnite": {"winePrefix": str(compat), "wineVersion": {"bin": "/opt/proton/proton", "type": "proton"}}}),
        encoding="utf-8")
    users = compat / "pfx/drive_c/users"
    digests = users / "steamuser/AppData/Local/UnrealEditorFortnite/Saved/VerseProject/Isle/Digests/BuiltIn/Fortnite"
    digests.mkdir(parents=True)
    (digests / "Fortnite.digest.verse").write_text("fort_character<native><public> := interface:\n", encoding="utf-8")
    (users / "Public/AppData/Local").mkdir(parents=True)
    verse_digests.clear_cache()
    return {"install": install, "compat": compat, "digests": digests}


def test_digests_are_found_in_the_prefix(prefix, monkeypatch) -> None:
    monkeypatch.setattr(verse_digests, "_LINUX", True)
    monkeypatch.setattr(verse_digests, "_project_root", lambda: "")
    found = verse_digests.discover_digest_files(project_name="Isle")
    assert [Path(p) for p in found] == [prefix["digests"] / "Fortnite.digest.verse"]
    assert "steamuser" in found[0] and "drive_c" in found[0]


def test_no_prefix_means_no_saved_digests(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(verse_digests, "_LINUX", True)
    assert verse_digests._verse_project_dir("Isle") == ""


def test_lore_runs_through_the_prefix_with_a_z_drive_repository(prefix) -> None:
    cmd, env = lore_cli.lore_wine_command("/home/me/Fortnite Projects/Isle", ["status"])
    assert cmd == ["/opt/proton/proton", "run", str(prefix["install"] / WIN64 / "lore.exe"),
                   "-P", "--repository", r"Z:\home\me\Fortnite Projects\Isle", "status"]
    assert env["STEAM_COMPAT_DATA_PATH"] == str(prefix["compat"])


def test_wine_runner_lore_command(prefix, tmp_path) -> None:
    cfg = tmp_path / ".config/heroic/GamesConfig/Fortnite.json"
    cfg.write_text(json.dumps({"Fortnite": {"winePrefix": str(prefix["compat"] / "pfx"),
                                            "wineVersion": {"bin": "/usr/bin/wine", "type": "wine"}}}), encoding="utf-8")
    cmd, env = lore_cli.lore_wine_command("/srv/Isle", ["history"])
    assert cmd[:2] == ["/usr/bin/wine", str(prefix["install"] / WIN64 / "lore.exe")]
    assert env == {"WINEPREFIX": str(prefix["compat"] / "pfx")}


def test_lore_is_detected_beside_the_editor_on_linux(prefix, monkeypatch) -> None:
    monkeypatch.setattr(detect_urc.sys, "platform", "linux")
    for key in ("ProgramW6432", "ProgramFiles(x86)", "ProgramFiles"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(detect_urc, "_DRIVE_LETTERS", "")
    out = detect_urc.detect_lore_executable()
    assert out["available"] and out["source"] == "wine_prefix"
    assert Path(out["path"]) == prefix["install"] / WIN64 / "lore.exe"


def test_prefix_appdata_skips_public(prefix) -> None:
    dirs = proton.local_appdata_dirs(proton.find_prefix())
    assert [d.parent.parent.name for d in dirs] == ["steamuser"]
