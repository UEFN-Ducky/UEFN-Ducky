"""Early splash helpers — no GUI required."""

from __future__ import annotations

import frontend.early_splash as early_splash
from frontend.open_files import try_handoff_to_running


def test_logo_png_resolves_in_dev_tree() -> None:
    path = early_splash._logo_png()
    assert path is not None
    assert path.is_file()
    assert path.name == "OnlineMCPIcon.png"


def test_matte_for_colorkey_kills_soft_edges() -> None:
    from PIL import Image

    img = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    img.putpixel((0, 0), (255, 200, 0, 20))  # soft fringe → key
    img.putpixel((1, 0), (255, 200, 0, 200))  # duck → opaque
    out = early_splash._matte_for_colorkey(img)
    assert out.getpixel((0, 0)) == (*early_splash._KEY_RGB, 255)
    assert out.getpixel((1, 0)) == (255, 200, 0, 255)


def test_handoff_defaults_are_short() -> None:
    """Stale panel.pid must not burn ~20s of connect timeouts on cold start."""
    import inspect

    sig = inspect.signature(try_handoff_to_running)
    assert sig.parameters["retries"].default <= 4
    assert float(sig.parameters["timeout_s"].default) <= 0.5
    assert float(sig.parameters["delay_s"].default) <= 0.25


if __name__ == "__main__":
    test_logo_png_resolves_in_dev_tree()
    test_matte_for_colorkey_kills_soft_edges()
    test_handoff_defaults_are_short()
    print("ok")
