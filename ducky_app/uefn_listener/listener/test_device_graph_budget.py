"""device_graph_snapshot stops at its budget and says how much it read (anp3j8r5t2v6c)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

MODULE = Path(__file__).resolve().parent / "registry" / "device_graph.py"


def _load(monkeypatch, devices, clock):
    unreal = types.ModuleType("unreal")
    pkg = types.ModuleType("listener")
    pkg.__path__ = []
    lookup = types.ModuleType("listener.lookup")

    def require_actor(path):
        clock[0] += 1.0  # a slow registry: every device read costs a second
        raise RuntimeError("no editor")

    lookup.require_actor = require_actor
    device_editor = types.ModuleType("listener.device_editor")
    device_editor.get_device_settings = lambda *a, **k: {}
    device_editor.is_creative_device = lambda a: True
    device_editor.list_creative_devices = lambda *a, **k: {"devices": devices, "total": len(devices)}
    dispatch = types.ModuleType("listener.dispatch")
    dispatch.register = lambda name: (lambda fn: fn)
    serialize = types.ModuleType("listener.serialize")
    serialize.serialize = lambda v: v
    verse_editable = types.ModuleType("listener.verse_editable_editor")
    verse_editable.get_verse_editables = lambda *a, **k: {}
    for name, mod in {
        "unreal": unreal,
        "listener": pkg,
        "listener.lookup": lookup,
        "listener.device_editor": device_editor,
        "listener.dispatch": dispatch,
        "listener.serialize": serialize,
        "listener.verse_editable_editor": verse_editable,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("device_graph_budget_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.time = types.SimpleNamespace(monotonic=lambda: clock[0])
    return module


def _devices(n):
    return [{"path": f"/Game/Map.Map:PersistentLevel.D{i}", "label": f"D{i}", "kind": "actor"}
            for i in range(n)]


def test_slow_registry_returns_partial_graph_at_budget(monkeypatch):
    clock = [0.0]
    graph = _load(monkeypatch, _devices(20), clock)
    snap = graph.device_graph_snapshot(limit=100)
    assert snap["partial"] is True
    assert snap["read"] == 6 and snap["total"] == 20
    assert snap["count"] == 6 and clock[0] <= graph._DEEP_BUDGET_S


def test_fast_registry_reads_everything(monkeypatch):
    clock = [0.0]
    graph = _load(monkeypatch, _devices(3), clock)
    snap = graph.device_graph_snapshot(limit=100)
    assert snap["partial"] is False
    assert snap["read"] == 3 and snap["total"] == 3
