"""Import files dropped from Windows Explorer onto the Content tree.

pywebview (WebView2) is the only layer that sees a dropped file's real path
(`pywebviewFullPath`); it exposes it solely to a Python DOM ``drop`` handler, never
to page JS (browsers hide the path for security). So the copy is driven here: the
React sidebar reports which folder the drag is over via ``set_import_drop_target``,
this handler copies the real paths into it, then fires a DOM event so the UI refreshes.

Registered on ``document`` (drops bubble up to it) — that also flips pywebview's
``_dnd_state`` listener count on, which is what makes WebView2 collect the paths.
"""

from __future__ import annotations

import json
from typing import Any

from frontend.ui_web.ui_dispatch import schedule_evaluate_js

# Sentinel the editor drop zone reports via set_import_drop_target instead of a folder:
# a drop then OPENS the file(s) editable in place in the editor rather than copying into
# Content. Mirrored in web/src/utils/osFileDrag.ts.
OPEN_EXTERNAL_TARGET = ":open-external:"
# Chat pane reports this while attaching via FileList — native handler must not open/import.
CHAT_ATTACH_TARGET = ":chat-attach:"


def install_file_drop_import(window: Any, api: Any) -> None:
    """Register the OS-file drop handler; re-arms on every page load/reload."""
    try:
        from webview.dom import DOMEventHandler
    except Exception:
        return

    state = {"installed": False}

    def _register() -> None:
        if state["installed"]:
            return
        try:
            window.dom.document.on("drop", DOMEventHandler(lambda e: _on_drop(window, api, e)))
            state["installed"] = True
        except Exception:
            state["installed"] = False

    def _on_loaded() -> None:
        # `loaded` resets pywebview's DOM element cache, so the handler must be re-bound.
        state["installed"] = False
        _register()

    try:
        window.events.loaded += _on_loaded
    except Exception:
        pass


def _on_drop(window: Any, api: Any, event: Any) -> None:
    try:
        dest = api.read_import_drop_target()
    except Exception:
        dest = ""
    # Consume the target so it can't linger and mis-route a later drop that lands
    # somewhere without its own drop zone.
    try:
        api.set_import_drop_target("")
    except Exception:
        pass
    paths = _dropped_paths(event)
    if not paths:
        return

    # Chat composer already attached via JS FileList — do not open or copy.
    if dest == CHAT_ATTACH_TARGET:
        return

    # No drop-zone target → open in the editor (VS Code-style: drop anywhere opens).
    # Content tree sets a folder dest while hovering; editor sets OPEN_EXTERNAL_TARGET.
    if not dest:
        dest = OPEN_EXTERNAL_TARGET

    # Dropped on the editor (or elsewhere) → open editable in place instead of copying.
    if dest == OPEN_EXTERNAL_TARGET:
        _notify_open_external(window, paths)
        return

    try:
        from frontend.ui_web.project_files import import_external_entries

        result = import_external_entries(dest, paths)
    except Exception as exc:  # bad dest, permission error, etc.
        result = {"paths": [], "errors": [str(exc)]}

    _notify_ui(window, result)


def _notify_open_external(window: Any, paths: list[str]) -> None:
    payload = json.dumps({"paths": paths})
    schedule_evaluate_js(
        window,
        f"window.dispatchEvent(new CustomEvent('ducky:external-files-open', {{ detail: {payload} }}));",
    )


def _notify_deep_links(window: Any, links: list[str]) -> None:
    payload = json.dumps({"links": links})
    schedule_evaluate_js(
        window,
        f"window.dispatchEvent(new CustomEvent('ducky:deep-link', {{ detail: {payload} }}));",
    )


def _dropped_paths(event: Any) -> list[str]:
    try:
        files = (event.get("dataTransfer") or {}).get("files") or []
    except AttributeError:
        return []
    out: list[str] = []
    for f in files:
        if isinstance(f, dict):
            full = f.get("pywebviewFullPath")
            if full:
                out.append(str(full))
    return out


def _notify_ui(window: Any, result: dict[str, Any]) -> None:
    payload = json.dumps(
        {"paths": result.get("paths") or [], "errors": result.get("errors") or []}
    )
    schedule_evaluate_js(
        window,
        f"window.dispatchEvent(new CustomEvent('ducky:project-files-imported', {{ detail: {payload} }}));",
    )
