"""Scene Graph component classes named without a path (no Unreal runtime).

Regression: every add/get/set/remove-component call that named its class
(``my_component``) walked every UClass in the editor with ``ObjectIterator`` —
tens of thousands of Python wrappers on the game thread — although the same
walk was already cached for list_scene_component_classes.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_MODULE = (
    Path(__file__).resolve().parents[1]
    / "ducky_app"
    / "uefn_listener"
    / "listener"
    / "registry"
    / "scene_graph.py"
)

_COMPONENT = "/EntityFramework/_Verse/VNI/Entity.component"


class _Meta:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


class _Class:
    def __init__(self, path: str, parent: "_Class | None" = None, verse: bool = True) -> None:
        self.path = path
        self.parent = parent
        self.verse = verse

    def get_class(self) -> _Meta:
        return _Meta("VerseClass" if self.verse else "Class")

    def get_name(self) -> str:
        return self.path.rsplit(".", 1)[-1]

    def get_path_name(self) -> str:
        return self.path

    def get_editor_property(self, name: str):
        assert name == "super_struct"
        return self.parent


_base = _Class(_COMPONENT)
_classes = [
    _base,
    _Class("/Script/Engine.Actor", verse=False),
    _Class("/EntityFramework/_Verse/VNI/Component.mesh_component", _base),
    _Class("/Proj/_Verse/VNI/Proj.my_component", _base),
    _Class("/Proj/_Verse/VNI/Proj.not_a_component"),
]
_walks = {"count": 0}


def _object_iterator(_cls):
    _walks["count"] += 1
    return iter(list(_classes))


def _load_object(_outer, path: str):
    return next((c for c in _classes if c.path == path), None)


_STUBS = {
    "unreal": {"Class": object, "ObjectIterator": _object_iterator, "load_object": _load_object},
    "listener": {},
    "listener.dispatch": {"register": lambda name: (lambda fn: fn)},
    "listener.project_paths": {"pin_project_folder": lambda folder="", default_leaf="": folder},
    "listener.registry": {},
    "listener.registry.scene_graph_filters": {
        "is_junk_entity_name": lambda name: False,
        "is_proxy_shadow_path": lambda path: False,
        "spatial_to_unreal_xyz": lambda xyz: tuple(xyz),
    },
}


def _load_module():
    """Load the registry module in isolation, then put sys.modules back."""
    saved = {name: sys.modules.get(name) for name in _STUBS}
    try:
        for name, attrs in _STUBS.items():
            mod = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(mod, key, value)
            sys.modules[name] = mod
        spec = importlib.util.spec_from_file_location("listener_scene_graph_under_test", _MODULE)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


scene_graph = _load_module()


@pytest.fixture(autouse=True)
def _fresh_session():
    scene_graph._component_classes_cache = None
    _walks["count"] = 0
    yield
    del _classes[5:]


def test_a_named_component_does_not_walk_every_class_on_each_call() -> None:
    for _ in range(5):
        cls = scene_graph._resolve_component_class("my_component")
        assert cls.get_path_name() == "/Proj/_Verse/VNI/Proj.my_component"
    assert _walks["count"] <= 1


def test_a_component_added_by_a_later_verse_build_is_still_found() -> None:
    scene_graph._resolve_component_class("my_component")
    _classes.append(_Class("/Proj/_Verse/VNI/Proj.new_component", _base))

    cls = scene_graph._resolve_component_class("new_component")

    assert cls.get_path_name() == "/Proj/_Verse/VNI/Proj.new_component"
    assert _walks["count"] == 2


def test_only_component_classes_resolve() -> None:
    with pytest.raises(ValueError, match="No Verse component class named 'not_a_component'"):
        scene_graph._resolve_component_class("not_a_component")
