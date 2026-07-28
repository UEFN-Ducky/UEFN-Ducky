"""Native SC_SIZE edges must be WMSZ_* codes (1..8), not HT* (10..17)."""

from frontend.ui_web.win_frameless import _RESIZE_EDGE_HT


def test_resize_edge_wmsz_codes():
    assert set(_RESIZE_EDGE_HT) == {"n", "s", "e", "w", "nw", "ne", "sw", "se"}
    assert _RESIZE_EDGE_HT["w"] == 1
    assert _RESIZE_EDGE_HT["se"] == 8
    for edge, wmsz in _RESIZE_EDGE_HT.items():
        assert 1 <= wmsz <= 8, edge
