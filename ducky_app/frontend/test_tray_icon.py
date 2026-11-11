"""Tray icon helpers — no GUI required."""

from __future__ import annotations

import sys
from pathlib import Path

from frontend.tray_icon import open_png

LOGO = Path(__file__).resolve().parent / "ui_web" / "web" / "public" / "OnlineMCPIcon.png"


def test_open_png_skips_avif_codec() -> None:
    """PNG decode must not load Pillow's AVIF native codec."""
    for name in ("PIL.AvifImagePlugin", "PIL._avif"):
        sys.modules.pop(name, None)
    img = open_png(LOGO)
    assert img.size[0] > 0
    assert "PIL.AvifImagePlugin" not in sys.modules
    assert "PIL._avif" not in sys.modules
