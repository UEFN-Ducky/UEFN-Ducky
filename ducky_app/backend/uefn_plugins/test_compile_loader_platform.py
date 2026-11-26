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


def test_store_badge_reads_the_installed_manifest(monkeypatch) -> None:
    from backend.uefn_plugins import manifest_platform, store

    manifests = {"win": WINDOWS_BUILD, "src": SOURCE}
    monkeypatch.setattr(store, "load_plugin_manifest", lambda pid: manifests.get(pid))
    monkeypatch.setattr(manifest_platform, "host_os", lambda: "linux")
    monkeypatch.setattr(manifest_platform, "host_arch", lambda: "x86_64")
    assert lp.store_badge("win") == "Windows only"
    assert lp.store_badge("src") == "" and lp.store_badge("gone") == ""
    monkeypatch.setattr(store, "load_plugin_manifest", lambda pid: 1 / 0)
    assert lp.store_badge("win") == ""  # an unreadable manifest gets no badge


def test_catalog_rows_say_windows_only_before_install(monkeypatch) -> None:
    from backend.uefn_plugins import manifest_platform

    only_win = {"slug": "win", "platforms": ["windows-x86_64"]}
    three = {"slug": "all", "platforms": ["windows-x86_64", "linux-x86_64", "linux-aarch64"]}
    monkeypatch.setattr(manifest_platform, "host_os", lambda: "linux")
    monkeypatch.setattr(manifest_platform, "host_arch", lambda: "x86_64")
    assert lp.store_badge("win", only_win) == "Windows only"
    assert lp.store_badge("all", three) == ""
    assert lp.store_badge("src", {"slug": "src"}) == "" and lp.store_badge("old", {"platforms": None}) == ""
    assert lp.catalog_badge({"platforms": ["windows-x86_64", "linux-x86_64"]}, "linux", "aarch64") == "No Linux aarch64 build"
    monkeypatch.setattr(manifest_platform, "host_os", lambda: "windows")
    assert lp.store_badge("win", only_win) == "" and lp.store_badge("all", three) == ""


ARM_PYD = "uefn_plugin_demo.v1.cp313-win_arm64.pyd"
WINDOWS_ARM_BUILD = {"compiled": True, "python_abi": "cp313-win_arm64", "backend": {"compiled": ARM_PYD}}


def test_windows_arm64_loads_only_its_own_build(tmp_path: Path) -> None:
    from backend.uefn_plugins import manifest_platform as mp

    assert mp.python_abi("windows", "ARM64", (3, 13)) == "cp313-win_arm64"
    assert mp.python_abi("windows", "AMD64", (3, 13)) == "cp313-win_amd64"
    assert mp.abi_target("cp313-win_arm64") == ("windows", "aarch64")
    (tmp_path / ARM_PYD).write_bytes(b"")
    lp.check_loadable(WINDOWS_ARM_BUILD, "windows", "ARM64")
    assert lp.find_compiled(tmp_path, WINDOWS_ARM_BUILD, "uefn_plugin_demo", "windows").name == ARM_PYD
    # an x64 app (also an emulated one on an arm PC) never imports the arm64 module, nor the reverse
    with pytest.raises(lp.PlatformError, match="no Windows aarch64 build yet"):
        lp.check_loadable(WINDOWS_BUILD, "windows", "ARM64")
    with pytest.raises(lp.PlatformError, match="no Windows x86_64 build yet"):
        lp.check_loadable(WINDOWS_ARM_BUILD, "windows", "AMD64")
    assert lp.catalog_badge({"platforms": ["windows-x86_64"]}, "windows", "ARM64") == "No Windows aarch64 build"
    assert lp.catalog_badge({"platforms": ["windows-x86_64", "windows-aarch64"]}, "windows", "ARM64") == ""


def test_pe_machine_reads_the_cpu_of_a_windows_module() -> None:
    from backend.uefn_plugins.cpu_check import PE_ARM64, pe_machine

    def pe(machine: int) -> bytes:
        head = bytearray(0x80)
        head[:2] = b"MZ"
        head[0x3C:0x40] = (0x40).to_bytes(4, "little")
        head[0x40:0x44] = b"PE\0\0"
        head[0x44:0x46] = machine.to_bytes(2, "little")
        return bytes(head)

    assert pe_machine(pe(0xAA64)) == PE_ARM64
    assert pe_machine(pe(0x8664)) == 0x8664
    assert pe_machine(b"\x7fELF" + b"\0" * 60) is None
