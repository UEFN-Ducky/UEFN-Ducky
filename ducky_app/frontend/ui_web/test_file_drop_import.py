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


def test_a_finder_drop_of_two_files_imports_both(monkeypatch, tmp_path):
    """macOS: pywebview's WKWebView host (performDragOperation_) queues (name, path) pairs from
    the drag pasteboard; its bridge matches them to the page's files by name, as for WebView2."""
    import threading

    from webview import util
    from webview.dom import _dnd_state

    a, b = tmp_path / "a.png", tmp_path / "notes.txt"
    imported: list[tuple[str, list[str]]] = []
    done = threading.Event()

    def fake_import(dest, paths):
        imported.append((dest, list(paths)))
        done.set()
        return {"paths": paths, "errors": []}

    import frontend.ui_web.project_files as project_files

    monkeypatch.setattr(project_files, "import_external_entries", fake_import)
    monkeypatch.setattr(file_drop_import, "_notify_ui", lambda window, result: None)
    monkeypatch.setitem(_dnd_state, "paths", [("a.png", str(a)), ("notes.txt", str(b))])  # what Cocoa queued
    target = {"dest": "Content/Imports"}
    api = SimpleNamespace(read_import_drop_target=lambda: target["dest"],
                          set_import_drop_target=lambda dest: target.update(dest=dest))
    window = SimpleNamespace(dom=SimpleNamespace(_elements={"doc": SimpleNamespace(
        _event_handlers={"drop": [lambda e: file_drop_import._on_drop(window, api, e)]})}))
    event = {"type": "drop", "dataTransfer": {"files": [{"name": "a.png"}, {"name": "notes.txt"}], "types": ["Files"]}}
    util.js_bridge_call(window, "pywebviewEventHandler", {"event": event, "nodeId": "doc"}, "v1")
    assert done.wait(5)
    assert imported == [("Content/Imports", [str(a), str(b)])]
    assert target["dest"] == "" and _dnd_state["paths"] == []  # both consumed, target cleared
