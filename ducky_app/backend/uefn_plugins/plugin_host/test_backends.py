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


def test_a_compiled_backend_for_another_abi_is_refused(tmp_path) -> None:
    sys.modules.pop("uefn_plugin_abi_probe", None)
    with pytest.raises(RuntimeError, match="built for cp27"):
        backends._import_compiled_backend("abi-probe", tmp_path, {"python_abi": "cp27-win_amd64"})
    assert state.PY_ABI.startswith("cp3")


def test_the_newest_pyd_is_picked(tmp_path) -> None:
    old = tmp_path / "uefn_plugin_p.cp313-win_amd64.pyd"
    old.write_bytes(b"")
    assert backends._compiled_backend_pyd(tmp_path, {}, "uefn_plugin_p") == old
    assert backends._compiled_backend_pyd(tmp_path, {"backend": {"compiled": old.name}}, "uefn_plugin_p") == old
