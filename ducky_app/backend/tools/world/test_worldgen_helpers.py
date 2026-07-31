"""Pure helpers for worldgen heightfields and scatter (no unreal)."""

from __future__ import annotations

from pathlib import Path


def _extract_helpers() -> dict:
    source = (
        Path(__file__).resolve().parents[3]
        / "uefn_listener"
        / "listener"
        / "registry"
        / "worldgen.py"
    ).read_text(encoding="utf-8")
    ns: dict = {
        "math": __import__("math"),
        "random": __import__("random"),
        "_MAX_INSTANCES": 2500,
        "Any": object,
        "Dict": dict,
        "List": list,
        "Optional": type(None),
        "Sequence": list,
        "Tuple": tuple,
    }
    start = source.index("def _clamp(")
    end = source.index("# ---------------------------------------------------------------------------\n# Capability probe")
    exec(source[start:end], ns)
    return ns


def test_heightfield_is_deterministic_and_sized():
    ns = _extract_helpers()
    a = ns["build_heightfield"](17, 1000.0, 200.0, 400.0, 42, None)
    b = ns["build_heightfield"](17, 1000.0, 200.0, 400.0, 42, None)
    c = ns["build_heightfield"](17, 1000.0, 200.0, 400.0, 43, None)
    assert len(a) == 17 and len(a[0]) == 17
    assert a == b
    assert a != c


def test_hill_stamp_raises_center():
    ns = _extract_helpers()
    base = ns["build_heightfield"](21, 2000.0, 0.0, 1000.0, 1, None)
    stamped = ns["build_heightfield"](
        21,
        2000.0,
        0.0,
        1000.0,
        1,
        [{"type": "hill", "x": 0, "y": 0, "radius": 800, "height": 500, "strength": 1}],
    )
    mid = stamped[10][10]
    corner = stamped[0][0]
    assert mid > base[10][10]
    assert mid > corner


def test_scatter_respects_budget_and_seed():
    ns = _extract_helpers()
    pts = ns["scatter_points"]([0, 0], [1000, 1000], 50.0, 200.0, 9, max_instances=25)
    pts2 = ns["scatter_points"]([0, 0], [1000, 1000], 50.0, 200.0, 9, max_instances=25)
    assert pts == pts2
    assert len(pts) <= 25
    # Min-distance roughly honored
    for i, (x1, y1) in enumerate(pts):
        for x2, y2 in pts[i + 1 :]:
            assert (x1 - x2) ** 2 + (y1 - y2) ** 2 >= 200.0 ** 2 - 1e-3


def test_budget_rejection_zero_density():
    ns = _extract_helpers()
    assert ns["scatter_points"]([0, 0], [1000, 1000], 0.0, 100.0, 1, 100) == []
