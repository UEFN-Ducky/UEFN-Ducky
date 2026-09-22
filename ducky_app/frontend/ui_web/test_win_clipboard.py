"""Host clipboard write used when WebView2 clipboard API fails."""

from __future__ import annotations

from frontend.ui_web.win_clipboard import get_clipboard_text, set_clipboard_text


def test_set_clipboard_text_writes() -> None:
    assert set_clipboard_text("ducky-clip-self-check") is True
    assert get_clipboard_text() == "ducky-clip-self-check"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
