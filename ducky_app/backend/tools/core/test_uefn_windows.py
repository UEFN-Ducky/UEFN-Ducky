"""Island-code parse and cached Copy click, no live UEFN window."""

from backend.tools.core.uefn_windows import _pick_copy, island_code_in


def test_island_code_in_accepts_private_version_code():
    assert island_code_in("Private version 1234-5678-9012") == "1234-5678-9012"
    assert island_code_in("code 1234-5678-9012?v=3 done") == "1234-5678-9012?v=3"
    assert island_code_in("no code here") == ""


def test_pick_copy_prefers_explicit_then_same_size_cache():
    rect = {"width": 480, "height": 260}
    cached = {"w": 480, "h": 260, "copy": [0.72, 0.81]}
    assert _pick_copy(rect, cached, 0.5, 0.4) == (0.5, 0.4)
    assert _pick_copy(rect, cached, None, None) == (0.72, 0.81)
    assert _pick_copy({"width": 800, "height": 600}, cached, None, None) == (None, None)


def _windows(monkeypatch, rows):
    from backend.tools.core import uefn_windows

    monkeypatch.setattr(uefn_windows, "list_uefn_windows", lambda title_regex="": {"ok": True, "windows": rows})
    return uefn_windows


_RECT = {"left": 0, "top": 0, "right": 4, "bottom": 4, "width": 4, "height": 4}


def _grab(monkeypatch):
    """ImageGrab returns a tiny real image; no screen is read."""
    from PIL import Image, ImageGrab

    monkeypatch.setattr(ImageGrab, "grab", lambda *a, **k: Image.new("RGB", (4, 4)))


def test_capture_with_no_arguments_takes_the_main_editor_window(monkeypatch):
    """Nov 17-19 2026: 69 of 108 uefn_window_capture calls failed, many with no arguments."""
    uw = _windows(monkeypatch, [
        {"hwnd": 7, "title": "Save Content", "rect": _RECT, "owned": True},
        {"hwnd": 3, "title": "Unreal Editor for Fortnite", "rect": _RECT, "owned": False},
    ])
    assert uw._resolve(0, "")["hwnd"] == 3


def test_capture_with_no_window_says_not_found(monkeypatch):
    uw = _windows(monkeypatch, [])
    out = uw._resolve(0, "")
    assert out["ok"] is False and out["fail"] == "not_found"
    assert uw._resolve(99, "")["fail"] == "not_found"


def test_capture_with_no_chat_saves_to_the_captures_folder(monkeypatch, tmp_path):
    from frontend.ui_web import conversation_attachments, tool_captures

    uw = _windows(monkeypatch, [{"hwnd": 3, "title": "Unreal Editor for Fortnite", "rect": _RECT, "owned": False}])
    _grab(monkeypatch)
    monkeypatch.setattr(conversation_attachments, "active_chat_id", lambda: "")
    monkeypatch.setattr(tool_captures, "tool_captures_dir", lambda for_write=False: tmp_path)
    monkeypatch.setattr(tool_captures, "_record_capture", lambda *a, **k: None)
    monkeypatch.setattr(tool_captures, "_prune_old_captures", lambda d: None)
    monkeypatch.setattr(tool_captures, "build_tool_capture_url", lambda name: f"http://127.0.0.1:1/tool-captures/{name}")
    out = uw.capture_uefn_window()
    assert out["ok"] is True and out["hwnd"] == 3
    assert out["path"].startswith(str(tmp_path))


def test_a_minimized_window_says_why(monkeypatch):
    uw = _windows(monkeypatch, [{"hwnd": 3, "title": "Unreal Editor for Fortnite", "rect": {}, "owned": False}])
    out = uw.capture_uefn_window(hwnd=3)
    assert out["ok"] is False and out["fail"] == "not_found" and "minimized" in out["error"]
