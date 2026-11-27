"""The Browser tab's native panes off Windows: Cocoa on macOS, GTK on Linux. ``browser_overlay``
imports this as its last line; importing a panes module rebinds its WebView2 helpers (``install``).
Plain imports, so PyInstaller bundles them (1.2.530 loaded them by a string name and died at start)."""

import sys

if sys.platform == "darwin":
    from frontend.native_host import cocoa_panes  # noqa: F401
elif sys.platform.startswith("linux"):
    from frontend.native_host import gtk_panes  # noqa: F401
