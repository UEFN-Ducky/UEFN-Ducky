"""ctypes struct sizes must match lorelib / koffi (Win64)."""

from ctypes import sizeof

from frontend.ui_web.verse_editor.urc.lore_ffi import (
    LoreEventCallback,
    LoreFileResetArgs,
    LoreFileUnstageArgs,
    LoreGlobalArgs,
    LoreRepositoryStatusArgs,
    LoreRevisionRevertArgs,
    LoreString,
    LoreStringArray,
    encode_lore_string,
)


def test_struct_sizes_match_koffi() -> None:
    assert sizeof(LoreString) == 16
    assert sizeof(LoreStringArray) == 16
    assert sizeof(LoreGlobalArgs) == 120
    assert sizeof(LoreFileUnstageArgs) == 16
    assert sizeof(LoreFileResetArgs) == 40
    assert sizeof(LoreRepositoryStatusArgs) == 24
    assert sizeof(LoreRevisionRevertArgs) == 40
    assert sizeof(LoreEventCallback) == 16


def test_lore_string_length_excludes_nul() -> None:
    ls, buf = encode_lore_string("hello")
    assert ls.length == 5
    assert buf.raw[:6] == b"hello\0"
    assert b"\0" not in bytes(buf.raw[: ls.length])
