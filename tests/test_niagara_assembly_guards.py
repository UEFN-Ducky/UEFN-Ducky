"""Unit tests for Niagara assembly guardrails (no Unreal runtime).

These cover the rules that were each paid for by a UEFN crash or by junk assets
left in a user's project: throwaway asset paths are refused, mesh particles may
not reference Engine content, and dynamic-input trees stay shallow.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path

_MODULE = (
    Path(__file__).resolve().parents[1]
    / "ducky_app"
    / "uefn_listener"
    / "listener"
    / "registry"
    / "niagara.py"
)


_STUBS = {
    "unreal": {},
    "listener": {},
    "listener.lookup": {"require_actor": lambda path: None},
    "listener.dispatch": {"register": lambda name: (lambda fn: fn)},
    "listener.project_paths": {"pin_project_folder": lambda folder="", default_leaf="": folder},
    "listener.serialize": {"serialize": lambda value: value},
    "listener.registry": {},
    "listener.registry.asset_registry": {"assets_by_class": lambda *a, **k: []},
}


def _load_module():
    """Load the registry module in isolation.

    The real listener package pulls in the whole editor surface (and a live
    ``unreal``), so stub its imports for the load and put sys.modules back
    afterwards — other suites import the real listener modules.
    """
    saved = {name: sys.modules.get(name) for name in _STUBS}
    try:
        for name, attrs in _STUBS.items():
            mod = types.ModuleType(name)
            for key, value in attrs.items():
                setattr(mod, key, value)
            sys.modules[name] = mod
        spec = importlib.util.spec_from_file_location("listener_niagara_under_test", _MODULE)
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


niagara = _load_module()


class JunkPathTests(unittest.TestCase):
    def test_real_effect_paths_pass(self):
        for path in (
            "/VideoTest/SolarSystem/Niagara/NS_SolarSystem",
            "/VideoTest/Fx/Meshes/SM_Planet_Rocky",
            "/VideoTest/Fx/Niagara/NS_Waterfall.NS_Waterfall",
        ):
            self.assertTrue(niagara._reject_junk_path(path))

    def test_probe_folder_refused(self):
        with self.assertRaises(ValueError):
            niagara._reject_junk_path("/VideoTest/__probe/NS_Sun")
        with self.assertRaises(ValueError):
            niagara._reject_junk_path("/VideoTest/Temp/NS_Sun")

    def test_throwaway_names_refused(self):
        for name in ("NS_MeshTest", "NS_SpriteTest", "NS_Probe", "NS_Scratch"):
            with self.assertRaises(ValueError):
                niagara._reject_junk_path(f"/VideoTest/Fx/Niagara/{name}")

    def test_object_suffix_stripped(self):
        self.assertEqual(niagara._package_path("/P/Fx/NS_X.NS_X"), "/P/Fx/NS_X")


class RendererGuardTests(unittest.TestCase):
    def test_engine_mesh_refused(self):
        with self.assertRaises(ValueError) as ctx:
            niagara._project_mesh("/Engine/BasicShapes/Sphere")
        self.assertIn("create_niagara_mesh", str(ctx.exception))

    def test_mesh_path_required(self):
        with self.assertRaises(ValueError):
            niagara._project_mesh("")


class SessionTests(unittest.TestCase):
    def test_closed_emitter_explains_the_two_ways_out(self):
        niagara._SESSIONS.clear()
        with self.assertRaises(ValueError) as ctx:
            niagara._open_emitter("/P/Fx/NS_X", "Planet_Earth")
        message = str(ctx.exception)
        self.assertIn("add_niagara_emitter", message)
        self.assertIn("finalize_niagara_system", message)

    def test_component_renderer_refused(self):
        with self.assertRaises(ValueError) as ctx:
            niagara._make_renderer(None, {"type": "component"})
        self.assertIn("publish", str(ctx.exception))


class DynamicInputBudgetTests(unittest.TestCase):
    def test_depth_cap_rejects_deep_trees(self):
        budget = {"nodes": 0}
        with self.assertRaises(ValueError) as ctx:
            niagara._build_input(
                {"name": "Normalized Angle", "value": 1.0},
                system_key="/P/Fx/NS_X",
                where="deep",
                budget=budget,
                depth=niagara._MAX_DYNAMIC_DEPTH + 1,
            )
        self.assertIn("RotateAroundPoint", str(ctx.exception))

    def test_parameter_needs_a_source(self):
        with self.assertRaises(ValueError):
            niagara._build_input(
                {"name": "Radius"},
                system_key="/P/Fx/NS_X",
                where="module",
                budget={"nodes": 0},
                depth=0,
            )

    def test_parameter_cap(self):
        with self.assertRaises(ValueError):
            niagara._apply_parameters(
                object(),
                [{"name": f"P{i}", "value": 1.0} for i in range(niagara._MAX_PARAMS_PER_MODULE + 1)],
                system_key="/P/Fx/NS_X",
                where="module",
                budget={"nodes": 0},
            )


class ExecutionCategoryTests(unittest.TestCase):
    def test_unknown_category_lists_valid_ones(self):
        with self.assertRaises(ValueError) as ctx:
            niagara._exec_category("particle_tick")
        self.assertIn("particle_update", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
