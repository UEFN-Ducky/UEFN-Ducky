"""host.X still reads and patches the moved plugin host code (compat.install)."""

from __future__ import annotations

import sys
import types
from unittest import mock

import pytest

from backend.uefn_plugins.plugin_host import compat


@pytest.fixture
def modules():
    owner = types.ModuleType("fake_owner")
    owner.FLAG = False
    owner.shared = "owner"

    def set_flag() -> None:
        owner.FLAG = True

    owner.set_flag = set_flag
    reader = types.ModuleType("fake_reader")
    reader.shared = "owner"
    facade = types.ModuleType("fake_facade")
    facade.local_only = 1
    sys.modules["fake_facade"] = facade
    compat.install("fake_facade", [owner, reader])
    yield facade, owner, reader
    sys.modules.pop("fake_facade", None)


def test_reads_follow_the_owning_module(modules) -> None:
    facade, owner, _ = modules
    assert facade.FLAG is False
    owner.set_flag()
    assert facade.FLAG is True  # never a stale copy
    with pytest.raises(AttributeError):
        facade.missing  # noqa: B018


def test_from_import_reads_through() -> None:
    owner = types.ModuleType("fake_owner2")
    owner.value = 7
    sys.modules["fake_facade2"] = types.ModuleType("fake_facade2")
    try:
        compat.install("fake_facade2", [owner])
        from fake_facade2 import value  # type: ignore[import-not-found]

        assert value == 7
    finally:
        sys.modules.pop("fake_facade2", None)


def test_setting_patches_every_module_that_binds_the_name(modules) -> None:
    facade, owner, reader = modules
    facade.shared = "patched"
    assert owner.shared == reader.shared == facade.shared == "patched"
    assert "shared" not in facade.__dict__


def test_a_name_nobody_binds_stays_on_the_facade(modules) -> None:
    facade, owner, reader = modules
    facade.new_name = 3
    assert facade.__dict__["new_name"] == 3
    assert not hasattr(owner, "new_name") and not hasattr(reader, "new_name")
    facade.local_only = 2
    assert facade.__dict__["local_only"] == 2


def test_monkeypatch_restores_the_live_value(modules, monkeypatch) -> None:
    facade, owner, _ = modules
    owner.FLAG = True
    with monkeypatch.context() as m:
        m.setattr(facade, "FLAG", "x")
        assert owner.FLAG == "x"
    assert owner.FLAG is True and facade.FLAG is True


def test_mock_patch_object_restores_the_live_value(modules) -> None:
    facade, owner, reader = modules
    owner.FLAG = True  # changed after install: the facade holds no copy of it
    with mock.patch.object(facade, "FLAG", False):
        assert owner.FLAG is False
    assert owner.FLAG is True and facade.FLAG is True
    with mock.patch.object(facade, "shared", "p"):
        assert reader.shared == "p"
    assert owner.shared == reader.shared == "owner"


def test_delete_hides_until_set_again(modules) -> None:
    facade, owner, _ = modules
    del facade.FLAG
    assert not hasattr(facade, "FLAG")
    assert owner.FLAG is False  # the owner keeps its value
    facade.FLAG = 5
    assert facade.FLAG == 5 == owner.FLAG
    with pytest.raises(AttributeError):
        del facade.missing


def test_dir_lists_forwarded_names(modules) -> None:
    facade, _, _ = modules
    names = dir(facade)
    assert {"FLAG", "set_flag", "shared", "local_only"} <= set(names)
