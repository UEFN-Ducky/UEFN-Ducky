"""device_graph_snapshot stops at its budget and says how much it read (anp3j8r5t2v6c)."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

MODULE = Path(__file__).resolve().parent / "registry" / "device_graph.py"


class _Actor:
    def __init__(self, i: int) -> None:
        self.i = i

    def get_class(self):
        return types.SimpleNamespace(get_name=lambda: "BP_Creative_Trigger_C")

    def get_actor_label(self) -> str:
        return f"D{self.i}"

    def get_path_name(self) -> str:
        return f"/Game/Map.Map:PersistentLevel.D{self.i}"


def _load(monkeypatch, n_devices, clock, *, read_cost=1.0, check_cost=0.0):
    """The module on a fake level of ``n_devices``; the fake clock advances ``check_cost``
    per device checked while listing and ``read_cost`` per device read."""
    unreal = types.ModuleType("unreal")
    pkg = types.ModuleType("listener")
    pkg.__path__ = []
    lookup = types.ModuleType("listener.lookup")
    actors = [_Actor(i) for i in range(n_devices)]
    lookup.actor_list = lambda: actors

    def require_actor(path):
        clock[0] += read_cost  # a slow registry: every device read costs time
        raise RuntimeError("no editor")

    def is_creative_device(_actor):
        clock[0] += check_cost
        return True

    lookup.require_actor = require_actor
    device_editor = types.ModuleType("listener.device_editor")
    device_editor.get_device_settings = lambda *a, **k: {}
    device_editor.is_creative_device = is_creative_device
    device_editor._device_kind = lambda _a: "actor"
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


def test_slow_registry_returns_partial_graph_at_budget(monkeypatch):
    clock = [0.0]
    graph = _load(monkeypatch, 20, clock)
    snap = graph.device_graph_snapshot(limit=100)
    assert snap["partial"] is True and snap["truncated"] is True
    assert snap["read"] == 6 and snap["total"] == 20
    assert snap["count"] == 6 and clock[0] <= graph._DEEP_BUDGET_S


def test_fast_registry_reads_everything(monkeypatch):
    clock = [0.0]
    graph = _load(monkeypatch, 3, clock)
    snap = graph.device_graph_snapshot(limit=100)
    assert snap["partial"] is False and snap["truncated"] is False
    assert snap["read"] == 3 and snap["total"] == 3


def test_a_5000_device_level_answers_under_10s_flagged_truncated(monkeypatch):
    # Listing a device costs 0.05 s: the walk alone would take 250 s.
    clock = [0.0]
    graph = _load(monkeypatch, 5000, clock, read_cost=0.01, check_cost=0.05)
    snap = graph.device_graph_snapshot(limit=150)
    assert clock[0] < 10.0
    assert snap["truncated"] is True
    assert 0 < snap["total"] < 5000 and snap["read"] == snap["total"]
