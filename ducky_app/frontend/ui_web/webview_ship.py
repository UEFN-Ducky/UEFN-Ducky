"""Store EXE WebView2 ship policy — no native right-click menu."""

from __future__ import annotations

from typing import Any

from frontend.bundle_root import is_packaged_runtime


def apply_shipped_webview2_settings(settings: Any) -> None:
    """Hide WebView2's Inspect/Back/Reload menu in the Store EXE.

    Custom React ``ContextMenu`` still works (JS ``contextmenu``). Dev
    ``python`` / ``--dev`` / Dev EXE keep the native menu. ``open_devtools``
    turns it back on for a packaged build.
    """
    if settings is None or not is_packaged_runtime():
        return
    from frontend.ui_web.web_dev import is_dev_panel

    if is_dev_panel():
        return
    settings.AreDefaultContextMenusEnabled = False
