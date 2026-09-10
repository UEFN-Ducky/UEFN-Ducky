"""Store EXE WebView2 ship policy — no native right-click menu."""

from __future__ import annotations

from typing import Any

from frontend.bundle_root import is_packaged_runtime


def apply_shipped_webview2_settings(settings: Any) -> None:
    """Hide WebView2's Inspect/Back/Reload menu in the Store EXE.

    Custom React ``ContextMenu`` still works (JS ``contextmenu``). Dev
    ``python`` runs keep the native menu. ``open_devtools`` turns it back on.
    """
    if not is_packaged_runtime() or settings is None:
        return
    settings.AreDefaultContextMenusEnabled = False
