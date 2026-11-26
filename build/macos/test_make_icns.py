import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_icns import make_icns  # noqa: E402


def _sizes(icns: Path) -> set[int]:
    """Pixel sizes stored in the ICNS (points x scale)."""
    return {w * s for w, _h, s in Image.open(icns).info["sizes"]}


def test_png_to_icns_has_16_to_1024(tmp_path):
    src = tmp_path / "icon.png"
    Image.new("RGBA", (300, 200), (255, 200, 0, 255)).save(src)
    out = make_icns(src, tmp_path / "out" / "UEFN-Ducky.icns")
    sizes = _sizes(out)
    assert min(sizes) <= 32 and max(sizes) == 1024
    assert {32, 64, 128, 256, 512, 1024} <= sizes
    assert Image.open(out).convert("RGBA").getpixel((256, 10)) == (0, 0, 0, 0)  # wide source padded to a square


def test_ico_source_uses_biggest_frame(tmp_path):
    src = tmp_path / "icon.ico"
    Image.new("RGBA", (256, 256), (0, 128, 255, 255)).save(src, sizes=[(16, 16), (256, 256)])
    assert max(_sizes(make_icns(src, tmp_path / "a.icns"))) == 1024
