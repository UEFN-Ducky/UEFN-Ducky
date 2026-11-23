"""The loader takes Linux .so builds and source plugins, and explains a Windows-only build."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.uefn_plugins import loader_platform as lp

SO = "uefn_plugin_demo.v1.cpython-313-x86_64-linux-gnu.so"
PYD = "uefn_plugin_demo.v1.cp313-win_amd64.pyd"
LINUX_BUILD = {"compiled": True, "python_abi": "cpython-313-x86_64-linux-gnu", "backend": {"compiled": SO}}
WINDOWS_BUILD = {"compiled": True, "python_abi": "cp313-win_amd64", "backend": {"compiled": PYD}}
SOURCE = {"id": "demo", "backend": {"entry": "backend"}}


def test_linux_so_build_loads_on_linux(tmp_path: Path) -> None:
    (tmp_path / SO).write_bytes(b"")
    (tmp_path / PYD).write_bytes(b"")
    lp.check_loadable(LINUX_BUILD, "linux", "x86_64")
    assert lp.find_compiled(tmp_path, LINUX_BUILD, "uefn_plugin_demo", "linux").name == SO
    # backend.compiled missing: the .so is still found by name, never the .pyd
    assert lp.find_compiled(tmp_path, {"compiled": True}, "uefn_plugin_demo", "linux").name == SO
    assert lp.card_badge(LINUX_BUILD, "linux", "x86_64") == ""


def test_source_plugin_loads_everywhere() -> None:
    for os_name, arch in (("linux", "x86_64"), ("linux", "aarch64"), ("windows", "AMD64")):
        lp.check_loadable(SOURCE, os_name, arch)
        assert lp.card_badge(SOURCE, os_name, arch) == ""


def test_windows_only_build_is_explained_on_linux(tmp_path: Path) -> None:
    with pytest.raises(lp.PlatformError, match="This plugin has no Linux build yet"):
        lp.check_loadable(WINDOWS_BUILD, "linux", "x86_64")
    # old compiled builds name no python_abi: they are Windows builds
    with pytest.raises(lp.PlatformError, match="no Linux build yet"):
        lp.check_loadable({"backend": {"compiled": "x.pyd"}}, "linux", "x86_64")
    assert lp.card_badge(WINDOWS_BUILD, "linux", "x86_64") == "Windows only"
    (tmp_path / PYD).write_bytes(b"")
    assert lp.find_compiled(tmp_path, WINDOWS_BUILD, "uefn_plugin_demo", "linux") is None


def test_windows_still_loads_its_pyd(tmp_path: Path) -> None:
    (tmp_path / PYD).write_bytes(b"")
    lp.check_loadable(WINDOWS_BUILD, "windows", "x86_64")
    assert lp.find_compiled(tmp_path, WINDOWS_BUILD, "uefn_plugin_demo", "windows").name == PYD
    with pytest.raises(lp.PlatformError, match="no Windows build yet"):
        lp.check_loadable(LINUX_BUILD, "windows", "x86_64")


def test_wrong_cpu_is_named() -> None:
    with pytest.raises(lp.PlatformError, match="no Linux aarch64 build yet"):
        lp.check_loadable(LINUX_BUILD, "linux", "arm64")
