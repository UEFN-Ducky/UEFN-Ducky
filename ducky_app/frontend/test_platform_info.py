import sys

import pytest

from frontend import platform_info

_ENV = ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP")


@pytest.fixture
def clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_os_flags_match_sys_platform():
    assert platform_info.IS_WINDOWS == (sys.platform == "win32")
    assert platform_info.IS_LINUX == sys.platform.startswith("linux")
    assert not (platform_info.IS_WINDOWS and platform_info.IS_LINUX)
    assert platform_info.IS_MAC == (sys.platform == "darwin")
    assert platform_info.IS_UNIX == (not platform_info.IS_WINDOWS)


def test_flags_on_darwin(monkeypatch):
    import importlib

    monkeypatch.setattr(sys, "platform", "darwin")
    try:
        mod = importlib.reload(platform_info)
        assert mod.IS_MAC and mod.IS_UNIX
        assert not mod.IS_WINDOWS and not mod.IS_LINUX
    finally:
        monkeypatch.undo()
        importlib.reload(platform_info)


def test_session_type_quartz_on_darwin(clean_env):
    clean_env.setattr(sys, "platform", "darwin")
    clean_env.setenv("DISPLAY", ":0")
    assert platform_info.session_type() == "quartz"


@pytest.mark.parametrize(
    "env, expected",
    [
        ({}, ""),
        ({"XDG_SESSION_TYPE": "wayland"}, "wayland"),
        ({"XDG_SESSION_TYPE": "X11", "WAYLAND_DISPLAY": "wayland-0"}, "x11"),
        ({"XDG_SESSION_TYPE": "tty", "WAYLAND_DISPLAY": "wayland-0"}, "wayland"),
        ({"DISPLAY": ":0"}, "x11"),
        ({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}, "wayland"),
    ],
)
def test_session_type(clean_env, env, expected):
    clean_env.setattr(sys, "platform", "linux")
    for name, value in env.items():
        clean_env.setenv(name, value)
    assert platform_info.session_type() == expected


def test_desktop(clean_env):
    assert platform_info.desktop() == ""
    clean_env.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    assert platform_info.desktop() == "ubuntu:gnome"
    assert "gnome" in platform_info.desktop()


@pytest.mark.windows_only("marker self-test")
def test_windows_only_marker_skips_off_windows():
    # Off Windows the conftest marker must skip this before it runs.
    assert sys.platform == "win32"
