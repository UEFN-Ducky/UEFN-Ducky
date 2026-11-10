"""Popups of any app: buttons found (filled or outlined), and only harmless message boxes
pressed by themselves.

premiere_save_project.png is Premiere Pro 2027's real "Save project" prompt (Nov 10 2026):
Save (blue outline), Don't Save, Cancel, and no window title. The first finder saw only Save
and would have pressed it.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from backend.tools.core import app_popups as ap
from backend.tools.core import uefn_popups

_FIX = Path(__file__).resolve().parent / "testdata" / "popups"


def _img(name: str) -> Image.Image:
    return Image.open(_FIX / f"{name}.png")


def _popup(title: str, *, blocking: bool = True) -> dict:
    return {"hwnd": 1, "title": title, "blocking": blocking, "enabled": True,
            "rect": {"width": 404, "height": 271}}


def test_premiere_save_prompt_has_three_buttons() -> None:
    buttons = ap.find_buttons_any(_img("premiere_save_project"))
    assert len(buttons) == 3
    assert [b["nx"] for b in buttons] == sorted(b["nx"] for b in buttons)
    assert all(0.7 < b["ny"] < 0.9 for b in buttons)
    assert buttons[0]["primary"] and not buttons[1]["primary"] and not buttons[2]["primary"]


def test_save_prompt_is_never_pressed_by_itself() -> None:
    buttons = ap.find_buttons_any(_img("premiere_save_project"))
    assert not ap.is_lone_ok(_popup(""), buttons)
    assert not ap.is_lone_ok(_popup(""), buttons[:1])  # even if only one button were seen


def test_uefn_dialogs_still_read_the_same() -> None:
    """The finder for every app keeps UEFN's own fixtures' buttons."""
    for path in sorted(_FIX.glob("*.png")):
        if path.stem.startswith("premiere"):
            continue
        img = Image.open(path)
        assert len(ap.find_buttons_any(img)) == len(uefn_popups.find_buttons(img)), path.stem


def test_lone_ok_rules() -> None:
    ok = [{"label": "OK", "nx": 0.8, "ny": 0.8}]
    assert ap.is_lone_ok(_popup("File Import Failure"), ok)
    assert not ap.is_lone_ok(_popup("File Import Failure", blocking=False), ok)
    assert not ap.is_lone_ok(_popup("Save changes?"), ok)
    assert not ap.is_lone_ok(_popup("Error"), [{"label": "Delete", "nx": 0.5, "ny": 0.8}])
    assert not ap.is_lone_ok(_popup("Error"), ok + ok)


def test_app_names() -> None:
    assert ap.exe_names("premiere") == ("adobe premiere pro.exe",)
    assert ap.exe_names("AE") == ("afterfx.exe",)
    assert ap.exe_names("Foo.exe") == ("foo.exe",)
    assert "blender.exe" in ap.exe_names("")
    assert ap.exe_names("nothing") == ()
