"""Early splash helpers — no GUI required."""

from __future__ import annotations

import frontend.early_splash as early_splash
from frontend.open_files import try_handoff_to_running


def test_logo_png_resolves_in_dev_tree() -> None:
    path = early_splash._logo_png()
    assert path is not None
    assert path.is_file()
    assert path.name == "OnlineMCPIcon.png"


def test_the_first_frame_opens_where_the_window_will() -> None:
    """The outline sits on the main window's saved bounds, so the real window replaces it in place."""
    saved = {"x": 100, "y": 50, "width": 1400, "height": 900}
    assert early_splash.frame_rect(saved, 2560, 1440) == (100, 50, 1400, 900)
    # First run (or junk bounds): webview_app's 920x640, centred.
    assert early_splash.frame_rect(None, 1920, 1080) == (500, 220, 920, 640)
    assert early_splash.frame_rect({"x": 0, "y": 0, "width": 10, "height": 10}, 1920, 1080) == (500, 220, 920, 640)
    # A screen smaller than the window never puts it off the top-left.
    assert early_splash.frame_rect(None, 800, 600)[:2] == (0, 0)


def test_open_png_skips_avif_codec() -> None:
    """PNG decode must not load Pillow's AVIF native codec."""
    from frontend.tray_icon import open_png

    path = early_splash._logo_png()
    assert path is not None
    import sys

    for name in ("PIL.AvifImagePlugin", "PIL._avif"):
        sys.modules.pop(name, None)
    img = open_png(path)
    assert img.size[0] > 0
    assert "PIL.AvifImagePlugin" not in sys.modules
    assert "PIL._avif" not in sys.modules


def test_handoff_defaults_are_short() -> None:
    """Stale panel.pid must not burn ~20s of connect timeouts on cold start."""
    import inspect

    sig = inspect.signature(try_handoff_to_running)
    assert sig.parameters["retries"].default <= 4
    assert float(sig.parameters["timeout_s"].default) <= 0.5
    assert float(sig.parameters["delay_s"].default) <= 0.25


if __name__ == "__main__":
    test_logo_png_resolves_in_dev_tree()
    test_the_first_frame_opens_where_the_window_will()
    test_open_png_skips_avif_codec()
    test_handoff_defaults_are_short()
    print("ok")
