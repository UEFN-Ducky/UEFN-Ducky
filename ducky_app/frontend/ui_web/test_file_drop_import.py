"""OS-file drops: pywebview sends Python a small event, never a copy of the whole page."""

from __future__ import annotations

from types import SimpleNamespace

from frontend.ui_web import file_drop_import


class _Hook(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class _Window:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.events = SimpleNamespace(loaded=_Hook())
        self.dom = SimpleNamespace(document=SimpleNamespace(on=lambda event, handler: self.calls.append(("on", event))))

    def evaluate_js(self, script: str) -> None:
        self.calls.append(("js", script))


def test_the_compact_event_script_goes_in_before_the_drop_handler_on_every_load():
    # Oct 31 2026: each drop serialized the whole page for Python (7 s, 258 MB with a
    # picture already attached), freezing the app.
    window = _Window()
    file_drop_import.install_file_drop_import(window, api=object())
    for load in range(2):
        window.events.loaded[0]()
    assert [kind for kind, _ in window.calls] == ["js", "on", "js", "on"]
    script = window.calls[0][1]
    assert "__duckyCompactEvents" in script and "dataTransfer" in script and "dt.files" in script
    assert window.calls[1] == ("on", "drop")


def test_the_drop_handler_reads_paths_from_the_compact_event(monkeypatch):
    seen: list[list[str]] = []
    monkeypatch.setattr(file_drop_import, "_notify_open_external", lambda window, paths: seen.append(paths))
    api = SimpleNamespace(read_import_drop_target=lambda: "", set_import_drop_target=lambda dest: None)
    event = {"type": "drop", "dataTransfer": {"files": [
        {"name": "shot.png", "size": 10, "type": "image/png", "pywebviewFullPath": "C:\\x\\shot.png"},
    ], "types": ["Files"]}}
    file_drop_import._on_drop(object(), api, event)
    assert seen == [["C:\\x\\shot.png"]]
