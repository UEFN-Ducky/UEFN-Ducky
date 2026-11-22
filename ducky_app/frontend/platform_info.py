"""One place for OS / display-session checks. New Linux code asks here, not ``sys.platform``."""

import os
import sys

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")


def session_type() -> str:
    """'wayland', 'x11', or '' (no graphical session / not Linux)."""
    kind = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if kind in ("wayland", "x11"):
        return kind
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return ""


def desktop() -> str:
    """Lower-cased ``XDG_CURRENT_DESKTOP`` (e.g. 'ubuntu:gnome', 'kde'); '' when unset.

    Test with ``"gnome" in desktop()``: the value can list several names separated by ':'.
    """
    return os.environ.get("XDG_CURRENT_DESKTOP", "").strip().lower()
