"""Host clipboard write used when WebView2 clipboard API fails (Linux: wl-clipboard / xclip)."""

from __future__ import annotations

import struct
import zlib

import pytest

from frontend.ui_web import win_clipboard as clip
from frontend.ui_web.win_clipboard import get_clipboard_text, set_clipboard_text

_NO_LINUX_TOOL = clip.IS_LINUX and clip._linux_cmd(True, "image/png") is None
needs_tool = pytest.mark.skipif(_NO_LINUX_TOOL, reason="no display with wl-clipboard or xclip")


def _png() -> bytes:
    """A real 1x1 PNG."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


@needs_tool
def test_set_clipboard_text_writes() -> None:
    assert set_clipboard_text("ducky-clip-self-check") is True
    assert get_clipboard_text() == "ducky-clip-self-check"


@needs_tool
@pytest.mark.skipif(not clip.IS_LINUX, reason="PNG clipboard is the Linux host's")
def test_linux_png_and_unicode_round_trip() -> None:
    png = _png()
    assert clip.set_clipboard_png(png) is True
    assert clip.get_clipboard_png() == png
    assert set_clipboard_text("ダッキー ✓") is True
    assert get_clipboard_text() == "ダッキー ✓"
    assert clip.get_clipboard_png() == b""  # text replaced the image


def test_linux_picks_wayland_then_x11(monkeypatch) -> None:
    monkeypatch.setattr(clip.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert clip._linux_cmd(False, "image/png")[0] == "wl-paste"
    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setenv("DISPLAY", ":0")
    assert clip._linux_cmd(True, "text/plain;charset=utf-8") == [
        "xclip", "-selection", "clipboard", "-t", "UTF8_STRING", "-i"]
    monkeypatch.delenv("DISPLAY")
    assert clip._linux_cmd(True, "image/png") is None


class _FakePasteboard:
    """NSPasteboard's general board: one item of each type, cleared before each write."""

    def __init__(self) -> None:
        self.items: dict[str, object] = {}

    def clearContents(self) -> None:  # noqa: N802
        self.items.clear()

    def setString_forType_(self, s, kind):  # noqa: N802
        self.items[kind] = s
        return True

    def setData_forType_(self, data, kind):  # noqa: N802
        self.items[kind] = data
        return True

    def stringForType_(self, kind):  # noqa: N802
        return self.items.get(kind)

    def dataForType_(self, kind):  # noqa: N802
        return self.items.get(kind)


@pytest.fixture()
def mac_board(monkeypatch):
    import sys
    from types import ModuleType, SimpleNamespace

    board = _FakePasteboard()
    tiff_to_png = {b"TIFF-bytes": _png()}
    appkit = SimpleNamespace(
        NSPasteboardTypeString="public.utf8-plain-text", NSPasteboardTypePNG="public.png",
        NSPasteboardTypeTIFF="public.tiff", NSBitmapImageFileTypePNG=4,
        NSBitmapImageRep=SimpleNamespace(imageRepWithData_=lambda d: SimpleNamespace(
            representationUsingType_properties_=lambda kind, _p: tiff_to_png[d] if kind == 4 else None)))
    foundation = ModuleType("Foundation")
    foundation.NSData = SimpleNamespace(dataWithBytes_length_=lambda b, n: bytes(b[:n]))
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setattr(clip, "IS_MAC", True)
    monkeypatch.setattr(clip, "IS_LINUX", False)
    monkeypatch.setattr(clip, "_mac_pasteboard", lambda: (appkit, board))
    return board


def test_mac_text_and_png_round_trip(mac_board) -> None:
    assert set_clipboard_text("ダッキー ✓") is True
    assert get_clipboard_text() == "ダッキー ✓"
    assert clip.get_clipboard_png() == b""  # no picture on the board
    png = _png()
    assert clip.set_clipboard_png(png) is True
    assert clip.get_clipboard_png() == png
    assert get_clipboard_text() == ""  # the picture replaced the text
    assert clip.set_clipboard_png(b"") is False


def test_mac_screenshot_tiff_reads_as_png(mac_board) -> None:
    mac_board.items["public.tiff"] = b"TIFF-bytes"  # what Cmd+Ctrl+Shift+4 puts on the board
    assert clip.get_clipboard_png() == _png()


def test_mac_without_pyobjc_is_a_quiet_no(monkeypatch) -> None:
    monkeypatch.setattr(clip, "IS_MAC", True)
    monkeypatch.setattr(clip, "IS_LINUX", False)
    monkeypatch.setattr(clip, "_mac_pasteboard", lambda: (None, None))
    assert set_clipboard_text("x") is False and get_clipboard_text() == "" and clip.get_clipboard_png() == b""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
