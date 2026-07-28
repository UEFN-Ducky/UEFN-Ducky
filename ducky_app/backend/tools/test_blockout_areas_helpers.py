"""Pure helpers for per-area slots + blockout presets (no unreal)."""

from __future__ import annotations

from pathlib import Path


def _extract_helpers() -> dict:
    source = (
        Path(__file__).resolve().parents[2]
        / "uefn_listener"
        / "listener"
        / "registry"
        / "blockout_areas.py"
    ).read_text(encoding="utf-8")
    ns: dict = {
        "re": __import__("re"),
        "Any": object,
        "Dict": dict,
        "List": list,
        "Optional": type(None),
        "Sequence": list,
        "Tuple": tuple,
    }
    start = source.index("SLOT_SPACING_UU")
    end = source.index("# --- END PURE HELPERS ---")
    exec(source[start:end], ns)
    return ns


def test_slot_origin_spacing():
    ns = _extract_helpers()
    assert ns["slot_origin"](0) == [0.0, 0.0, 0.0]
    assert ns["slot_origin"](1) == [100_000.0, 0.0, 0.0]
    assert ns["slot_origin"](2)[0] == 200_000.0
    assert ns["origin_to_slot"]([100_000.0, 0, 0]) == 1
    assert ns["next_free_slot"]([0, 1, 3]) == 2


def test_normalize_area_id():
    ns = _extract_helpers()
    assert ns["normalize_area_id"]("Hub") == "hub"
    assert ns["normalize_area_id"]("my-store") == "my_store"
    try:
        ns["normalize_area_id"]("9bad")
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_presets_have_walls_and_openings():
    ns = _extract_helpers()
    meta = {p["id"]: p for p in ns["list_preset_meta"]()}
    for pid in ("hub", "store", "arena", "corridor", "lobby"):
        assert pid in meta
        assert meta[pid]["piece_count"] >= 5
        recipe = ns["get_preset"](pid)
        cells = recipe["footprint_cells"]
        assert cells[0] >= 2 and cells[1] >= 2
        pieces = recipe["pieces"]
        assert any(p["suffix"] == "Floor" for p in pieces)
        assert any("Wall" in p["suffix"] for p in pieces)
        # Door openings: hub/store/arena/corridor/lobby use gap segments or openings
        # Opening width constant is >= 256
        assert ns["OPENING"] >= 256


def test_pieces_world_offsets_origin():
    ns = _extract_helpers()
    recipe = ns["get_preset"]("lobby")
    world = ns["pieces_world"](recipe["pieces"], [100_000.0, 0.0, 0.0])
    assert world[0]["loc"][0] == 100_000.0 + recipe["pieces"][0]["loc"][0]
