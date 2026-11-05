"""landscape_sculpt releases its float render targets and refuses the no-op modes."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


class _Rendering:
    def __init__(self) -> None:
        self.created: list[object] = []
        self.released: list[object] = []

    def create_render_target2d(self, _world, _x, _y, _fmt):
        rt = object()
        self.created.append(rt)
        return rt

    def release_render_target2d(self, rt):
        self.released.append(rt)

    def begin_draw_canvas_to_render_target(self, _world, _rt):
        canvas = types.SimpleNamespace(draw_texture=lambda *a, **k: None)
        return (canvas, types.SimpleNamespace(x=8.0, y=8.0), object())

    def end_draw_canvas_to_render_target(self, _world, _ctx):
        pass


class _Land:
    def __init__(self, import_raises: bool = False) -> None:
        self.import_raises = import_raises

    def get_path_name(self):
        return "/Game/Map.Landscape"

    def landscape_export_heightmap_to_render_target(self, _rt, _a, _b):
        return True

    def landscape_import_heightmap_from_render_target(self, _rt, _rg, _layer):
        if self.import_raises:
            raise RuntimeError("import blew up")
        return True


@pytest.fixture
def env(monkeypatch):
    rendering = _Rendering()
    trims: list[int] = []
    unreal = types.ModuleType("unreal")
    unreal.RenderingLibrary = rendering
    unreal.EditorLevelLibrary = types.SimpleNamespace(get_editor_world=lambda: object())
    unreal.TextureRenderTargetFormat = types.SimpleNamespace(RTF_RGBA32F="RTF_RGBA32F")
    unreal.Vector2D = lambda *a: a
    unreal.LinearColor = lambda *a: a
    unreal.BlendMode = types.SimpleNamespace(BLEND_OPAQUE=0, BLEND_TRANSLUCENT=1)
    monkeypatch.setitem(sys.modules, "unreal", unreal)

    listener = types.ModuleType("listener")
    listener.__path__ = []
    stubs = {
        "listener": listener,
        "listener.lookup": types.ModuleType("listener.lookup"),
        "listener.dispatch": types.ModuleType("listener.dispatch"),
        "listener.island_placeable": types.ModuleType("listener.island_placeable"),
        "listener.project_paths": types.ModuleType("listener.project_paths"),
        "listener.serialize": types.ModuleType("listener.serialize"),
        "listener.render_trim": types.ModuleType("listener.render_trim"),
    }
    stubs["listener.dispatch"].register = lambda _name: (lambda fn: fn)
    ip = stubs["listener.island_placeable"]
    ip.FOLIAGE_SOURCE_FOLDERS = ()
    ip.is_foliage_bake_mesh = lambda *a, **k: False
    ip.resolve_content_drawer_placeable = lambda *a, **k: None
    stubs["listener.project_paths"].content_root = lambda: "/Game/"
    stubs["listener.project_paths"].pin_project_folder = lambda *a, **k: None
    stubs["listener.serialize"].is_live = lambda *a, **k: True
    stubs["listener.serialize"].serialize = lambda v, *a, **k: v
    stubs["listener.render_trim"].request_trim = lambda *a, **k: trims.append(1)
    listener.lookup = stubs["listener.lookup"]
    listener.render_trim = stubs["listener.render_trim"]
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)

    path = Path(__file__).resolve().parent / "registry" / "landscape_heightmap.py"
    spec = importlib.util.spec_from_file_location("landscape_heightmap_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    land = _Land()
    monkeypatch.setattr(
        mod, "landscape_get_info", lambda **_k: {"has_panels": True, "path": land.get_path_name(), "label": "L"}
    )
    monkeypatch.setattr(mod, "_iter_landscape_roots", lambda: [land])
    monkeypatch.setattr(mod, "_landscape_heightmap_resolution", lambda _l: {"res_x": 8, "res_y": 8})
    monkeypatch.setattr(mod, "_load_white_texture", lambda: object())
    return mod, rendering, land, trims


def test_non_verify_mode_is_refused_before_any_target(env):
    mod, rendering, _land, trims = env
    for mode in ("add", "set"):
        out = mod.landscape_sculpt(mode=mode, confirm_overwrite=True)
        assert out["ok"] is False and out["refused"] is True
        assert out["use_instead"] == "terrain_generate"
    assert rendering.created == []
    assert trims == []


def test_verify_releases_its_target_on_success(env):
    mod, rendering, _land, trims = env
    out = mod.landscape_sculpt(mode="verify", confirm_overwrite=True)
    assert out["ok"] is True
    assert len(rendering.created) == 1
    assert rendering.released == rendering.created
    assert trims == [1]


def test_both_targets_released_on_success(env, monkeypatch):
    mod, rendering, _land, _trims = env
    monkeypatch.setattr(mod, "_SCULPT_MODES_ENABLED", True)
    out = mod.landscape_sculpt(mode="add", confirm_overwrite=True)
    assert out["ok"] is True
    assert len(rendering.created) == 2
    assert sorted(map(id, rendering.released)) == sorted(map(id, rendering.created))


def test_both_targets_released_when_import_raises(env, monkeypatch):
    mod, rendering, land, trims = env
    monkeypatch.setattr(mod, "_SCULPT_MODES_ENABLED", True)
    land.import_raises = True
    out = mod.landscape_sculpt(mode="set", confirm_overwrite=True)
    assert out["ok"] is False and "import blew up" in out["error"]
    assert len(rendering.created) == 2
    assert sorted(map(id, rendering.released)) == sorted(map(id, rendering.created))
    assert trims == [1]
