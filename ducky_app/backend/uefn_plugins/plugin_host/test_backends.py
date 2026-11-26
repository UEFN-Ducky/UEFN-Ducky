"""Importing a plugin backend: source packages, plain files, and compiled .pyd detection."""

from __future__ import annotations

import sys

import pytest

from backend.uefn_plugins.plugin_host import backends, state


def test_compiled_is_read_from_the_manifest() -> None:
    assert backends._manifest_is_compiled({}) is False
    assert backends._manifest_is_compiled({"compiled": True}) is True
    assert backends._manifest_is_compiled({"python_abi": "cp313-win_amd64"}) is True
    assert backends._manifest_is_compiled({"backend": {"compiled": "x.pyd"}}) is True


def test_a_source_package_imports_under_its_module_name(tmp_path) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "__init__.py").write_text("VALUE = 7\n", encoding="utf-8")
    sys.modules.pop("uefn_plugin_src_probe", None)
    try:
        mod = backends._import_backend("src-probe", tmp_path, "backend")
        assert mod.VALUE == 7 and sys.modules["uefn_plugin_src_probe"] is mod
    finally:
        sys.modules.pop("uefn_plugin_src_probe", None)


def test_a_missing_entry_imports_nothing(tmp_path) -> None:
    assert backends._import_backend("none-probe", tmp_path, "backend") is None


_WIN_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="Windows .pyd behaviour")


@_WIN_ONLY
def test_a_compiled_backend_for_another_abi_is_refused(tmp_path) -> None:
    sys.modules.pop("uefn_plugin_abi_probe", None)
    with pytest.raises(RuntimeError, match="built for cp27"):
        backends._import_compiled_backend("abi-probe", tmp_path, {"python_abi": "cp27-win_amd64"})
    assert state.PY_ABI.startswith("cp3")


@_WIN_ONLY
def test_the_newest_pyd_is_picked(tmp_path) -> None:
    old = tmp_path / "uefn_plugin_p.cp313-win_amd64.pyd"
    old.write_bytes(b"")
    assert backends._compiled_backend_pyd(tmp_path, {}, "uefn_plugin_p") == old
    assert backends._compiled_backend_pyd(tmp_path, {"backend": {"compiled": old.name}}, "uefn_plugin_p") == old


@pytest.fixture
def as_linux(monkeypatch):
    from backend.uefn_plugins import manifest_platform as mp

    monkeypatch.setattr(mp, "host_os", lambda: "linux")
    monkeypatch.setattr(mp, "host_arch", lambda: "x86_64")
    monkeypatch.setattr(backends, "_WINDOWS", False)
    monkeypatch.setattr(backends, "_COMPILED_SUFFIXES", (".pyd", ".so"))
    monkeypatch.setattr(backends, "PY_ABI", "cpython-313-x86_64-linux-gnu")
    monkeypatch.setattr(backends, "_restore_compiled_from_quarantine", lambda pid, root: None)


def test_a_linux_so_build_loads_on_linux(tmp_path, monkeypatch, as_linux) -> None:
    import importlib.machinery

    name = "uefn_plugin_so_probe_v1"
    so = tmp_path / f"{name}.cpython-313-x86_64-linux-gnu.so"
    so.write_text("def register():\n    return 'so'\n", encoding="utf-8")
    (tmp_path / "uefn_plugin_so_probe.cp313-win_amd64.pyd").write_bytes(b"")
    # A fake .so: Python source, loaded the way the host loads an extension.
    monkeypatch.setattr(importlib.machinery, "ExtensionFileLoader", importlib.machinery.SourceFileLoader)
    manifest = {"compiled": True, "python_abi": "cpython-313-x86_64-linux-gnu",
                "backend": {"compiled": so.name, "module": name}}
    assert backends._compiled_backend_pyd(tmp_path, manifest, "uefn_plugin_so_probe") == so
    try:
        mod = backends._import_compiled_backend("so-probe", tmp_path, manifest)
        assert mod.register() == "so"
    finally:
        for key in (name, "uefn_plugin_so_probe"):
            sys.modules.pop(key, None)
            state._COMPILED_LOADED.pop(key, None)


def test_a_windows_only_build_on_linux_says_so(tmp_path, as_linux) -> None:
    (tmp_path / "uefn_plugin_w.cp313-win_amd64.pyd").write_bytes(b"")
    with pytest.raises(RuntimeError, match="This plugin has no Linux build yet"):
        backends._import_compiled_backend("w", tmp_path, {"compiled": True, "python_abi": "cp313-win_amd64"})
    assert backends._compiled_backend_pyd(tmp_path, {}, "uefn_plugin_w") is None


@pytest.fixture
def as_mac_arm(monkeypatch):
    from backend.uefn_plugins import manifest_platform as mp

    monkeypatch.setattr(mp, "host_os", lambda: "macos")
    monkeypatch.setattr(mp, "host_arch", lambda: "aarch64")
    monkeypatch.setattr(backends, "_WINDOWS", False)
    monkeypatch.setattr(backends, "_COMPILED_SUFFIXES", (".pyd", ".so"))
    monkeypatch.setattr(backends, "PY_ABI", mp.python_abi("macos", "arm64"))
    monkeypatch.setattr(backends, "_restore_compiled_from_quarantine", lambda pid, root: None)


def test_a_mac_build_loads_on_its_own_chip(tmp_path, monkeypatch, as_mac_arm) -> None:
    import importlib.machinery

    from backend.uefn_plugins import manifest_platform as mp

    name = "uefn_plugin_mac_probe_v1"
    so = tmp_path / f"{name}.cpython-313-darwin.so"  # CPython's suffix: no arch in the file name
    so.write_text("def register():\n    return 'mac'\n", encoding="utf-8")
    monkeypatch.setattr(importlib.machinery, "ExtensionFileLoader", importlib.machinery.SourceFileLoader)
    manifest = {"compiled": True, "python_abi": mp.python_abi("macos", "arm64"), "backend": {"module": name}}
    assert backends._compiled_backend_pyd(tmp_path, manifest, "uefn_plugin_mac_probe") == so
    try:
        assert backends._import_compiled_backend("mac-probe", tmp_path, manifest).register() == "mac"
    finally:
        for key in (name, "uefn_plugin_mac_probe"):
            sys.modules.pop(key, None)
            state._COMPILED_LOADED.pop(key, None)


def test_an_intel_mac_build_on_apple_silicon_says_so(tmp_path, as_mac_arm) -> None:
    from backend.uefn_plugins import loader_platform

    (tmp_path / "uefn_plugin_i.cpython-313-darwin.so").write_bytes(b"")
    intel = {"compiled": True, "python_abi": "cpython-313-darwin-x86_64"}
    with pytest.raises(RuntimeError, match="This plugin has no macOS arm64 build yet"):
        backends._import_compiled_backend("i", tmp_path, intel)
    assert loader_platform.catalog_badge({"platforms": ["macos-x86_64", "linux-x86_64"]}) == "No macOS arm64 build"
    assert loader_platform.catalog_badge({"platforms": ["macos-aarch64"]}) == ""
    assert loader_platform.catalog_badge({"platforms": ["linux-x86_64"]}) == "No macOS build"


def test_quarantine_sweep_takes_so_files(tmp_path, monkeypatch, as_linux) -> None:
    monkeypatch.setattr(backends, "_compiled_quarantine_dir", lambda: tmp_path)
    (tmp_path / "p").mkdir()
    parked = tmp_path / "p" / "abc__uefn_plugin_p.cpython-313-x86_64-linux-gnu.so.bin"
    parked.write_bytes(b"")
    backends.sweep_compiled_quarantine()
    assert not parked.exists()
