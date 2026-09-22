"""Island-code parse and cached Copy click, no live UEFN window."""

from backend.tools.core.uefn_windows import _pick_copy, island_code_in


def test_island_code_in_accepts_private_version_code():
    assert island_code_in("Private version 1234-5678-9012") == "1234-5678-9012"
    assert island_code_in("code 1234-5678-9012?v=3 done") == "1234-5678-9012?v=3"
    assert island_code_in("no code here") == ""


def test_pick_copy_prefers_explicit_then_same_size_cache():
    rect = {"width": 480, "height": 260}
    cached = {"w": 480, "h": 260, "copy": [0.72, 0.81]}
    assert _pick_copy(rect, cached, 0.5, 0.4) == (0.5, 0.4)
    assert _pick_copy(rect, cached, None, None) == (0.72, 0.81)
    assert _pick_copy({"width": 800, "height": 600}, cached, None, None) == (None, None)
