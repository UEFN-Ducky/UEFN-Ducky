from __future__ import annotations

import io

from frontend.window_view import jpeg_bytes, kind_for


def test_kind_for_uefn_and_blender() -> None:
    assert kind_for("Unreal Editor for Fortnite", "UnrealEditorFortnite.exe") == "uefn"
    assert kind_for("ExampleProject1 - Unreal Editor", "UnrealEditorFortnite.exe") == "uefn"
    assert kind_for("Blender", "blender.exe") == "blender"
    assert kind_for("Notes", "notepad.exe") == "app"


def test_jpeg_bytes_shrinks_wide_image() -> None:
    from PIL import Image

    img = Image.new("RGB", (3200, 200), color=(10, 20, 30))
    raw = jpeg_bytes(img, max_edge=800)
    out = Image.open(io.BytesIO(raw))
    assert out.format == "JPEG"
    assert max(out.size) == 800
