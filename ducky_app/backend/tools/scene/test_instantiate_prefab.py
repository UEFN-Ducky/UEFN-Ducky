"""instantiate_prefab takes the translation the way agents write it (Oct 30 2026: {x, y, z}
failed validation before the call reached UEFN)."""

from __future__ import annotations

import json

import pytest

from backend.tools.scene import scene_graph


@pytest.mark.parametrize(
    "given,sent",
    [
        ([100, 20, 300], [100.0, 20.0, 300.0]),
        ({"forward": 100, "left": 20, "up": 300}, [100.0, 20.0, 300.0]),
        ({"x": 16000, "y": 16000, "z": 300}, [16000.0, -16000.0, 300.0]),  # Unreal Y is right
        ({"X": 1, "Y": -2, "Z": 3}, [1.0, 2.0, 3.0]),
    ],
)
def test_a_translation_list_or_dict_reaches_the_listener_as_spatial_math(monkeypatch, given, sent) -> None:
    calls = []
    monkeypatch.setattr(scene_graph, "send_command", lambda cmd, params: calls.append((cmd, params)) or {"ok": True})
    scene_graph.instantiate_prefab("/P/EP_Thing", translation=given)
    assert calls == [("instantiate_prefab", {"prefab_path": "/P/EP_Thing", "name": "", "parent_entity": "", "translation": sent})]


def test_a_translation_it_cannot_read_says_what_it_takes(monkeypatch) -> None:
    monkeypatch.setattr(scene_graph, "send_command", lambda *a: pytest.fail("never sent"))
    out = json.loads(scene_graph.instantiate_prefab("/P/EP_Thing", translation={"a": 1}))
    assert out["ok"] is False and "{x, y, z}" in out["error"]


def test_duplicate_prefab_sends_the_source_and_new_name(monkeypatch) -> None:
    # Oct 30 2026 feedback: a Held Item prefab (10 template components) could not be made from
    # MCP; copying one made in the Content Drawer keeps them all.
    calls = []
    monkeypatch.setattr(scene_graph, "send_command", lambda cmd, params: calls.append((cmd, params)) or {"ok": True})
    scene_graph.duplicate_prefab("/P/Prefabs/Lantern.Lantern", "Flashlight")
    assert calls == [("duplicate_prefab", {"source_prefab": "/P/Prefabs/Lantern.Lantern", "prefab_name": "Flashlight", "folder": ""})]
