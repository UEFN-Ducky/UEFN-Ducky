"""The Flatpak manifest: runtime, portals and D-Bus names, host commands, voice modules."""

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")
HERE = Path(__file__).resolve().parent
APP = "online.uefnducky.Ducky"


def _manifest() -> dict:
    return yaml.safe_load((HERE / f"{APP}.yml").read_text(encoding="utf-8"))


def test_gnome_runtime_and_app_id():
    m = _manifest()
    assert m["app-id"] == APP and m["command"] == "uefn-ducky"
    assert m["runtime"] == "org.gnome.Platform" and m["sdk"] == "org.gnome.Sdk"


def test_finish_args_talk_to_notifications_tray_keyring_and_host():
    args = set(_manifest()["finish-args"])
    for name in ("org.freedesktop.Notifications", "org.kde.StatusNotifierWatcher",
                 "org.freedesktop.secrets", "org.freedesktop.Flatpak"):
        assert f"--talk-name={name}" in args
    assert {"--socket=wayland", "--socket=fallback-x11", "--share=network", "--socket=pulseaudio"} <= args


def test_voice_modules_are_bundled_and_pinned():
    modules = {mod["name"]: mod for mod in _manifest()["modules"]}
    for name in ("portaudio", "espeak-ng"):
        [source] = modules[name]["sources"]
        assert source["type"] == "git" and len(source["commit"]) == 40


def test_terminal_and_git_run_on_the_host():
    app = {mod["name"]: mod for mod in _manifest()["modules"]}["uefn-ducky"]
    commands = "\n".join(app["build-commands"])
    assert "/app/libexec/host-bin" in commands and "bash zsh fish git" in commands
    assert "flatpak-spawn --host" in (HERE / "host-run.sh").read_text(encoding="utf-8")
    wrapper = (HERE / "uefn-ducky.sh").read_text(encoding="utf-8")
    assert 'PATH="/app/libexec/host-bin:$PATH"' in wrapper
    assert "--self-test" in wrapper and "flatpak-spawn --host true" in wrapper


def test_metainfo_matches_the_app():
    root = ET.parse(HERE / f"{APP}.metainfo.xml").getroot()
    assert root.findtext("id") == APP
    assert root.find("launchable").text == f"{APP}.desktop"
