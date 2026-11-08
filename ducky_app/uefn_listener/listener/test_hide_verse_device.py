"""Placed Verse devices get Visible in Game off — no Unreal required."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

MODULE = Path(__file__).resolve().parent / "device_editor.py"


def _load(monkeypatch):
    unreal = types.ModuleType("unreal")
    unreal.ActorComponent = object
    unreal.Actor = object
    package = types.ModuleType("listener")
    package.lookup = types.ModuleType("listener.lookup")
    coalesce = types.ModuleType("listener.save_coalesce")
    coalesce.request_level_save = lambda: None
    monkeypatch.setitem(sys.modules, "unreal", unreal)
    monkeypatch.setitem(sys.modules, "listener", package)
    monkeypatch.setitem(sys.modules, "listener.lookup", package.lookup)
    monkeypatch.setitem(sys.modules, "listener.save_coalesce", coalesce)
    spec = importlib.util.spec_from_file_location("device_editor_under_test", MODULE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class _Class:
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name


class _Toy:
    def get_class(self):
        return _Class("ToyOptionsComponent")

    def get_property_overrides(self):
        return {}

    def refresh_property_overrides(self):
        pass


class _Actor:
    def __init__(self, cls):
        self._cls = cls
        self.props = {"VisibleInGame": True}

    def get_class(self):
        return _Class(self._cls)

    def get_components_by_class(self, _):
        return [_Toy()]

    def get_editor_property(self, key):
        return self.props[key]

    def set_editor_property(self, key, value):
        self.props[key] = value

    def modify(self):
        pass


def test_verse_device_is_hidden(monkeypatch):
    actor = _Actor("VerseDevice_C")
    assert _load(monkeypatch).hide_verse_device(actor) is True
    assert actor.props["VisibleInGame"] is False


def test_other_devices_are_left_alone(monkeypatch):
    actor = _Actor("Device_Button_V2_C")
    assert _load(monkeypatch).hide_verse_device(actor) is False
    assert actor.props["VisibleInGame"] is True
