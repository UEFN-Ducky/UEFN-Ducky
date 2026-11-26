"""macOS compiled builds: the manifest tag carries the arch, the file name doesn't."""

from __future__ import annotations

from backend.uefn_plugins import manifest_platform as mp


def test_mac_tags_per_arch() -> None:
    assert mp.python_abi("macos", "arm64", (3, 13)) == "cpython-313-darwin-arm64"
    assert mp.python_abi("macos", "aarch64", (3, 13)) == "cpython-313-darwin-arm64"
    assert mp.python_abi("macos", "x64", (3, 13)) == "cpython-313-darwin-x86_64"
    for arch in ("arm64", "x86_64"):
        assert mp.module_abi("macos", arch, (3, 13)) == "cpython-313-darwin"
    assert mp.module_abi("linux", "x86_64", (3, 13)) == "cpython-313-x86_64-linux-gnu"
    assert mp.module_suffix("macos") == ".so"


def test_mac_tags_parse_back() -> None:
    assert mp.abi_target("cpython-313-darwin-arm64") == ("macos", "aarch64")
    assert mp.abi_target("cpython-313-darwin-x86_64") == ("macos", "x86_64")
    assert mp.abi_target("cpython-313-darwin") is None  # no arch: not a target


def test_mac_label_and_problem() -> None:
    arm = {"compiled": True, "python_abi": "cpython-313-darwin-arm64"}
    assert mp.platform_label(arm) == "macOS aarch64 only"
    assert mp.platform_problem(arm, "macos", "arm64") == ""
    assert mp.platform_problem(arm, "macos", "x86_64") == "This plugin has no macOS x86_64 build yet"
    assert mp.platform_problem(arm, "linux", "aarch64") == "This plugin has no Linux build yet"


def test_host_os_darwin(monkeypatch) -> None:
    monkeypatch.setattr(mp.sys, "platform", "darwin")
    assert mp.host_os() == "macos"
