"""Cancel on Add project must not open a second file picker."""

from __future__ import annotations

import sys
import types

from frontend.ui_web import panel_api as _panel_api  # noqa: F401 — break mixin circular import
from frontend.ui_web.panel_api_window import PanelApiWindowMixin


class _CancelWin:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def create_file_dialog(self, dialog_type: object, **kwargs: object) -> None:
        self.calls.append(dialog_type)


def test_pick_project_path_cancel_opens_one_dialog(monkeypatch) -> None:
    monkeypatch.setitem(
        sys.modules,
        "webview",
        types.SimpleNamespace(FileDialog=types.SimpleNamespace(FOLDER=20, OPEN=10)),
    )
    api = PanelApiWindowMixin()
    win = _CancelWin()
    assert api._pick_project_path_webview(win) is None
    assert win.calls == [20]


def test_pick_project_path_dialog_cancel_skips_file_picker(monkeypatch) -> None:
    calls: list[str] = []

    def askdirectory(**kwargs: object) -> str:
        calls.append("dir")
        return ""

    def askopenfilename(**kwargs: object) -> str:
        calls.append("file")
        return ""

    monkeypatch.setattr("tkinter.filedialog.askdirectory", askdirectory)
    monkeypatch.setattr("tkinter.filedialog.askopenfilename", askopenfilename)
    api = PanelApiWindowMixin()
    assert api._pick_project_path_dialog(object()) is None
    assert calls == ["dir"]
