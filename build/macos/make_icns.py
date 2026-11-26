#!/usr/bin/env python3
"""App icon (PNG or ICO) -> ``.icns`` with Pillow, so it also runs on Windows (no iconutil).

Usage: py build/macos/make_icns.py [src] dest.icns   (src defaults to the panel's online PNG)
"""

from __future__ import annotations

import sys
from pathlib import Path


def make_icns(src: Path, dest: Path) -> Path:
    """Write ``dest``: Pillow's ICNS writer resizes one 1024 px image to every size (16 pt..512@2x)."""
    from PIL import Image

    img = Image.open(src)
    if getattr(img, "format", "") == "ICO" and img.info.get("sizes"):
        img.size = max(img.info["sizes"])  # biggest ICO frame
    img = img.convert("RGBA")
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    square.resize((1024, 1024), Image.Resampling.LANCZOS).save(dest, format="ICNS")
    return dest


def _default_src() -> Path:
    root = Path(__file__).resolve().parents[2]
    for p in (root, root / "ducky_app"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    from frontend.tray_icon import resolve_status_png_path

    png = resolve_status_png_path("online")
    if png is None:
        raise SystemExit("ERROR: no app icon PNG (build the panel first)")
    return png


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) not in (1, 2):
        raise SystemExit(__doc__)
    src, dest = (Path(args[0]), Path(args[1])) if len(args) == 2 else (_default_src(), Path(args[0]))
    print(f"Wrote {make_icns(src, dest)}")
