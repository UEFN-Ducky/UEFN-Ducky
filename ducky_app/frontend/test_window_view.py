from __future__ import annotations

import io

from frontend.window_view import jpeg_bytes, kind_for, map_norm_to_screen, window_fit_size, _vk_for_key


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


def test_map_norm_to_screen_corners() -> None:
    box = (100, 200, 300, 400)
    assert map_norm_to_screen(box, 0, 0) == (100, 200)
    assert map_norm_to_screen(box, 1, 1) == (299, 399)
    assert map_norm_to_screen(box, -1, 2) == (100, 399)
    x, y = map_norm_to_screen(box, 0.5, 0.5)
    assert 100 <= x <= 299 and 200 <= y <= 399


def test_window_fit_size_clamps() -> None:
    assert window_fit_size(80, 80) == (400, 300)
    assert window_fit_size(9000, 5000) == (3840, 2160)
    assert window_fit_size(1600, 900) == (1600, 900)


def test_vk_for_named_and_function_keys() -> None:
    assert _vk_for_key("Enter") == 0x0D
    assert _vk_for_key("F1") == 0x70
    assert _vk_for_key("F12") == 0x7B
    assert _vk_for_key("") == 0
