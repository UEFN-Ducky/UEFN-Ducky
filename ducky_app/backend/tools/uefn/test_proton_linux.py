"""UEFN under Proton/Wine: fake Heroic and Steam prefixes, the command built, /proc pids.

Pure file work, so it runs on the Windows CI too."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.tools.uefn import proton

SHIP = "FortniteGame/Binaries/Win64/UnrealEditorFortnite-Win64-Shipping.exe"


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x", encoding="utf-8")
    return p


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    return tmp_path


def _heroic(home: Path, runner: str = "proton") -> tuple[Path, Path]:
    heroic = home / ".config/heroic"
    install = home / "Games/Heroic/Fortnite"
    _touch(install / SHIP)
    prefix = home / "Games/Heroic/Prefixes/default/Fortnite"
    (heroic / "legendaryConfig/legendary").mkdir(parents=True)
    (heroic / "legendaryConfig/legendary/installed.json").write_text(
        json.dumps({"Fortnite": {"app_name": "Fortnite", "install_path": str(install)}}), encoding="utf-8"
    )
    wine_bin = str(home / "proton/proton") if runner == "proton" else "/usr/bin/wine"
    (heroic / "GamesConfig").mkdir(parents=True)
    (heroic / "GamesConfig/Fortnite.json").write_text(
        json.dumps({"Fortnite": {"winePrefix": str(prefix), "wineVersion": {"bin": wine_bin, "type": runner}}}),
        encoding="utf-8",
    )
    return install, prefix


def test_heroic_proton_prefix_and_launch_command(home: Path) -> None:
    install, prefix = _heroic(home)
    pre = proton.find_prefix(home)
    assert (pre.source, pre.runner) == ("heroic", "proton")
    assert pre.pfx == prefix / "pfx" and pre.compat == prefix
    assert pre.exe == install / SHIP
    cmd, env = proton.launch_command(pre, "/home/me/Fortnite Projects/Isle/Isle.uefnproject")
    assert cmd == [
        str(home / "proton/proton"), "run", str(install / SHIP),
        r"-ValkyrieProject=Z:\home\me\Fortnite Projects\Isle\Isle.uefnproject",
    ]
    assert env["STEAM_COMPAT_DATA_PATH"] == str(prefix)


def test_heroic_wine_runner_sets_wineprefix(home: Path) -> None:
    _install, prefix = _heroic(home, runner="wine")
    pre = proton.find_prefix(home)
    cmd, env = proton.launch_command(pre)
    assert cmd[0] == "/usr/bin/wine" and cmd[1] == str(pre.exe)
    assert env == {"WINEPREFIX": str(prefix)}


def test_steam_compatdata_prefix_from_epic_manifest(home: Path) -> None:
    steam = home / ".local/share/Steam"
    compat = steam / "steamapps/compatdata/123456"
    pfx = compat / "pfx"
    _touch(pfx / "drive_c/Program Files/Epic Games/Fortnite" / SHIP)
    proton_dir = steam / "steamapps/common/Proton 9.0"
    _touch(proton_dir / "proton")
    (compat / "config_info").write_text(f"9.0-200\n{proton_dir}/files/share/fonts/\n", encoding="utf-8")
    item = {
        "AppName": "Fortnite_Studio",
        "InstallLocation": r"C:\Program Files\Epic Games\Fortnite",
        "LaunchExecutable": SHIP.replace("/", "\\"),
        "LaunchCommand": "-disableplugins=ValkyrieFortnite",
    }
    man = pfx / proton._MANIFESTS
    man.mkdir(parents=True)
    (man / "a.item").write_text(json.dumps(item), encoding="utf-8")

    pre = proton.find_prefix(home)
    assert (pre.source, pre.runner, pre.pfx, pre.compat) == ("steam", "proton", pfx, compat)
    assert pre.bin == str(proton_dir / "proton")
    assert pre.exe.is_file()
    cmd, env = proton.launch_command(pre, pfx / "drive_c/users/steamuser/Documents/Isle/Isle.uefnproject")
    assert cmd[-2:] == [
        "-disableplugins=ValkyrieFortnite",
        r"-ValkyrieProject=C:\users\steamuser\Documents\Isle\Isle.uefnproject",
    ]
    assert env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] == str(steam)


def test_no_prefix_says_how_to_install(home: Path) -> None:
    with pytest.raises(RuntimeError, match="Heroic"):
        proton.find_prefix(home)


def test_path_mapping_round_trip(tmp_path: Path) -> None:
    pfx = tmp_path / "pfx"
    assert proton.to_unix(pfx, r"C:\users\steamuser") == pfx / "drive_c/users/steamuser"
    assert proton.to_unix(pfx, r"Z:\home\me") == Path("/home/me")
    assert proton.to_windows(pfx, pfx / "drive_c" / "x" / "y.txt") == r"C:\x\y.txt"
    assert proton.to_windows(pfx, "/home/me/a b") == r"Z:\home\me\a b"
    with pytest.raises(ValueError):
        proton.to_unix(pfx, r"Q:\nope")


def test_uefn_pids_match_the_windows_exe_not_the_wrapper(tmp_path: Path) -> None:
    def proc(pid: int, *argv: str) -> None:
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")

    proc(10, r"C:\Program Files\Epic Games\Fortnite\FortniteGame\Binaries\Win64\UnrealEditorFortnite-Win64-Shipping.exe")
    proc(11, "/home/me/proton/proton", "run", "/x/UnrealEditorFortnite-Win64-Shipping.exe")
    proc(12, "/usr/bin/python3")
    (tmp_path / "self").mkdir()
    assert proton.uefn_pids(tmp_path) == [10]
